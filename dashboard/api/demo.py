"""Demo playback: a stepped clock that releases a staged ICU stay into the live store one hour at a time.

Loading a demo InputBatch aligns every patient's ICU admission to the demo start, registers the patients, and
stages their observations. The service clock then stays frozen at the demo time; each advance moves it forward by
one hour and imports the observations that became available in that hour, exactly as live data would arrive. All
time checks of the store (availability, prediction origins, fingerprints) run against this clock.
"""
import json
from datetime import datetime, timedelta, timezone

from .schemas import InputBatch, set_clock, stamp

RELEASE_CHUNK = 5000


def _parse(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


class DemoPlayback:
    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS demo_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS demo_staged (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                release_at TEXT NOT NULL, body TEXT NOT NULL, released INTEGER NOT NULL DEFAULT 0);
            ''')
            state = dict(db.execute('SELECT key, value FROM demo_state').fetchall())
        self.start = _parse(state['start']) if 'start' in state else None
        self.clock = _parse(state['clock']) if 'clock' in state else None
        if self.active:
            set_clock(lambda: stamp(self.clock))

    @property
    def active(self):
        return self.clock is not None

    def load(self, batch: InputBatch, start: datetime):
        """Stage a whole-stay InputBatch with every admission moved to `start` (an empty store is required)."""
        if self.active:
            raise ValueError('Demo playback is already loaded in this data directory')
        if self.store.patients():
            raise ValueError('Demo playback needs an empty data directory')
        shift = {p.patient_id: start - p.icu_admitted_at for p in batch.patients}
        missing = {o.patient_id for o in batch.observations} - set(shift)
        if missing:
            raise ValueError(f'Observations for unregistered patients: {sorted(missing)[:5]}')
        patients = [{**p.model_dump(mode='json'), 'icu_admitted_at': stamp(start)} for p in batch.patients]
        staged = []
        for o in batch.observations:
            body = o.model_dump(mode='json')
            body['measured_at'] = stamp(o.measured_at + shift[o.patient_id])
            # Data without a source availability time is treated as available when measured.
            body['available_at'] = stamp((o.available_at or o.measured_at) + shift[o.patient_id])
            staged.append((body['available_at'], json.dumps(body)))
        self.start, self.clock = start, start
        with self.store.connection() as db:
            db.executemany('INSERT INTO demo_staged(release_at, body) VALUES(?,?)', staged)
            db.executemany('INSERT OR REPLACE INTO demo_state VALUES(?,?)',
                           [('start', stamp(start)), ('clock', stamp(start))])
        set_clock(lambda: stamp(self.clock))
        self.store.ingest(InputBatch(patients=patients, actor='demo-playback'))
        self._release()

    def advance(self, hours=1):
        """Move the clock forward hour by hour, importing each hour's newly available observations separately."""
        if not self.active:
            raise ValueError('Demo playback is not active; start the website with --demo')
        for _ in range(hours):
            self.clock += timedelta(hours=1)
            with self.store.connection() as db:
                db.execute("UPDATE demo_state SET value=? WHERE key='clock'", (stamp(self.clock),))
            self._release()
        return self.status()

    def _release(self):
        upto = stamp(self.clock)
        with self.store.connection() as db:
            rows = db.execute('SELECT seq, body FROM demo_staged WHERE released=0 AND release_at<=? '
                              'ORDER BY release_at, seq', (upto,)).fetchall()
        for s in range(0, len(rows), RELEASE_CHUNK):
            chunk = rows[s:s + RELEASE_CHUNK]
            self.store.ingest(InputBatch(observations=[json.loads(r['body']) for r in chunk], actor='demo-playback'))
            with self.store.connection() as db:
                db.executemany('UPDATE demo_staged SET released=1 WHERE seq=?', [(r['seq'],) for r in chunk])

    def status(self):
        if not self.active:
            return {'mode': 'real', 'now': datetime.now(timezone.utc).isoformat()}
        with self.store.connection() as db:
            remaining = db.execute('SELECT COUNT(*) FROM demo_staged WHERE released=0').fetchone()[0]
        return {'mode': 'demo', 'now': stamp(self.clock), 'start': stamp(self.start),
                'hours_elapsed': (self.clock - self.start) / timedelta(hours=1), 'staged_remaining': remaining}
