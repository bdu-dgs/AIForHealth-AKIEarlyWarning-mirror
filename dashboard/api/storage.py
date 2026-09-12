"""SQLite is the local index; durable outbox mirrors accepted batches to files."""
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .schemas import InputBatch, PredictionBatch, now, stamp


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8') as handle:
            handle.write(encode(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        for folder in ('inbox/input', 'inbox/prediction', 'accepted/input', 'accepted/prediction', 'requests', 'photos'):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'aki.sqlite3'
        self.lock = threading.RLock()
        with self.connection() as db:
            # DELETE journaling avoids WAL shared-memory files in this cloud-drive workspace.
            db.executescript('''
              CREATE TABLE IF NOT EXISTS patients (id TEXT PRIMARY KEY, encounter TEXT NOT NULL,
                body TEXT NOT NULL, input_revision INTEGER NOT NULL DEFAULT 0, photo TEXT, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, patient TEXT NOT NULL,
                id TEXT NOT NULL, revision INTEGER NOT NULL, event_at TEXT NOT NULL,
                available_at TEXT NOT NULL, recorded_at TEXT NOT NULL, actor TEXT NOT NULL,
                body TEXT NOT NULL, fingerprint TEXT NOT NULL,
                PRIMARY KEY(kind,patient,id,revision), FOREIGN KEY(patient) REFERENCES patients(id));
              CREATE INDEX IF NOT EXISTS records_history ON records(patient,kind,event_at,available_at);
              CREATE INDEX IF NOT EXISTS records_versions ON records(patient,kind,id,available_at,revision);
              CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS revisions (patient TEXT NOT NULL, version INTEGER NOT NULL,
                available_at TEXT NOT NULL, PRIMARY KEY(patient,version));
              CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
              INSERT OR IGNORE INTO meta VALUES ('revision',0);
            ''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(patients)')}
            if 'input_fingerprint' not in columns:
                db.execute("ALTER TABLE patients ADD COLUMN input_fingerprint TEXT NOT NULL DEFAULT ''")
            for row in db.execute("SELECT id FROM patients WHERE input_fingerprint='' ").fetchall():
                moment = now()
                db.execute('UPDATE patients SET input_fingerprint=? WHERE id=?',
                           (self.snapshot(db, row['id'], moment, moment), row['id']))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @property
    def revision(self):
        with self.connection() as db:
            return db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]

    def ingest(self, batch, source='api'):
        moment = now()
        changed = set()
        inserted = 0
        with self.lock, self.connection() as db:
            if isinstance(batch, InputBatch):
                for patient in batch.patients:
                    body = patient.model_dump(mode='json')
                    body['icu_admitted_at'] = stamp(patient.icu_admitted_at)
                    if body['icu_admitted_at'] > moment:
                        raise ValueError('入 ICU 时间不能在未来')
                    old = db.execute('SELECT body FROM patients WHERE id=?', (patient.patient_id,)).fetchone()
                    if old and old['body'] != encode(body):
                        raise ValueError('患者 ID 已存在且资料不同；保留原资料，请核对患者与住院标识')
                    if not old:
                        db.execute('INSERT INTO patients(id,encounter,body,created_at) VALUES(?,?,?,?)',
                                   (patient.patient_id, patient.encounter_id, encode(body), moment))
                        changed.add(patient.patient_id)
                records = batch.observations
                actor = batch.actor
                kind = 'observation'
            else:
                records = batch.predictions
                actor = 'model-output'
                kind = 'prediction'
            for record in records:
                patient = db.execute('SELECT * FROM patients WHERE id=?', (record.patient_id,)).fetchone()
                if not patient or patient['encounter'] != record.encounter_id:
                    raise ValueError('患者不存在或 encounter_id 不匹配；请先登记患者')
                body = record.model_dump(mode='json')
                time_keys = ('measured_at', 'available_at') if kind == 'observation' else (
                    'origin_time', 'data_cutoff', 'horizon_end', 'generated_at', 'available_at')
                for key in time_keys:
                    if body[key] is not None:
                        body[key] = stamp(body[key])
                fingerprint = hashlib.sha256(encode(body).encode()).hexdigest()
                previous = db.execute('SELECT fingerprint,body FROM records WHERE kind=? AND patient=? AND id=? AND revision=?',
                                      (kind, record.patient_id, record.record_id, record.revision)).fetchone()
                if previous:
                    old_body = json.loads(previous['body'])
                    old_body.pop('availability_basis', None)
                    candidate = dict(body)
                    if candidate['available_at'] is None:
                        candidate['available_at'] = old_body['available_at']
                    if encode(old_body) != encode(candidate):
                        raise ValueError('相同记录 ID 与 revision 对应不同内容；请增加 revision')
                    continue
                event_at = body['measured_at' if kind == 'observation' else 'origin_time']
                if event_at < json.loads(patient['body'])['icu_admitted_at'] or event_at > moment:
                    raise ValueError('观测／预测起点必须位于入 ICU 后至当前时间之间')
                available = body['available_at'] or moment
                if available < event_at or available > moment:
                    raise ValueError('available_at 必须位于测量／预测起点与当前时间之间')
                if kind == 'prediction':
                    if body['generated_at'] > available:
                        raise ValueError('预测可用时间不能早于生成时间')
                    snapshot = self.snapshot(db, record.patient_id, body['generated_at'], body['data_cutoff'])
                    if snapshot != record.input_fingerprint:
                        raise ValueError('预测引用的输入快照与当时可用观测不匹配；请核对 input_fingerprint 与时间')
                # A correction may change event time, but cannot reverse its availability ordering.
                conflict = db.execute('''SELECT 1 FROM records WHERE kind=? AND patient=? AND id=?
                    AND ((revision<? AND available_at>?) OR (revision>? AND available_at<?)) LIMIT 1''',
                    (kind, record.patient_id, record.record_id, record.revision, available,
                     record.revision, available)).fetchone()
                if conflict:
                    raise ValueError('记录修订的 available_at 顺序与 revision 冲突')
                body['available_at'] = available
                body['availability_basis'] = 'source' if record.available_at else 'local_received'
                db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (kind, record.patient_id, record.record_id, record.revision, event_at,
                            available, moment, actor, encode(body), fingerprint))
                inserted += 1
                changed.add(record.patient_id)
            if isinstance(batch, InputBatch):
                for patient_id in changed:
                    db.execute('UPDATE patients SET input_revision=input_revision+1 WHERE id=?', (patient_id,))
                    version = db.execute('SELECT input_revision FROM patients WHERE id=?', (patient_id,)).fetchone()[0]
                    db.execute('INSERT INTO revisions VALUES(?,?,?)', (patient_id, version, moment))
                    digest = self.snapshot(db, patient_id, moment, moment)
                    db.execute('UPDATE patients SET input_fingerprint=? WHERE id=?', (digest, patient_id))
            if changed:
                db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
                durable = batch.model_dump(mode='json')
                durable.pop('actor', None)
                record_key = 'observations' if isinstance(batch, InputBatch) else 'predictions'
                for record in durable[record_key]:
                    stored = db.execute('SELECT body FROM records WHERE kind=? AND patient=? AND id=? AND revision=?',
                        (kind, record['patient_id'], record['record_id'], record['revision'])).fetchone()
                    if stored:
                        record['available_at'] = json.loads(stored[0])['available_at']
                db.execute('INSERT INTO outbox VALUES(?,?,?)',
                           (uuid.uuid4().hex, batch.kind, encode(durable)))
        self.flush_outbox()
        return {'inserted': inserted, 'patients_changed': len(changed), 'revision': self.revision}

    def flush_outbox(self):
        # Failed file writes leave durable outbox entries; the watcher retries them.
        with self.lock, self.connection() as db:
            for row in db.execute('SELECT * FROM outbox').fetchall():
                try:
                    atomic_json(self.root / 'accepted' / row['kind'] / (row['id'] + '.json'), json.loads(row['body']))
                    if row['kind'] == 'input':
                        batch = json.loads(row['body'])
                        ids = {p['patient_id'] for p in batch['patients']} | {o['patient_id'] for o in batch['observations']}
                        for patient_id in ids:
                            patient = self.patient(patient_id)
                            atomic_json(self.root / 'requests' / (patient_id + '.json'), {
                                'schema_version': 1, 'patient_id': patient_id,
                                'encounter_id': patient['encounter_id'], 'input_revision': patient['input_revision'],
                                'input_fingerprint': patient['input_fingerprint'],
                                'status': 'awaiting_model', 'history_api': '/api/patients/' + patient_id + '/history',
                                'input_export_api': '/api/patients/' + patient_id + '/export/input', 'updated_at': now()})
                    db.execute('DELETE FROM outbox WHERE id=?', (row['id'],))
                except OSError:
                    break

    def patient(self, patient_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM patients WHERE id=?', (patient_id,)).fetchone()
            if row is None:
                raise KeyError(patient_id)
            return {**json.loads(row['body']), 'input_revision': row['input_revision'],
                    'input_fingerprint': row['input_fingerprint'], 'photo': row['photo']}

    def snapshot(self, db, patient_id, as_of, cutoff):
        rows = db.execute('''WITH visible AS (
          SELECT body,event_at,id,ROW_NUMBER() OVER(PARTITION BY id ORDER BY revision DESC) AS rank
          FROM records WHERE patient=? AND kind='observation' AND available_at<=?)
          SELECT body FROM visible WHERE rank=1 AND event_at<=? ORDER BY id''', (patient_id, as_of, cutoff)).fetchall()
        values = []
        for row in rows:
            body = json.loads(row[0]); body.pop('availability_basis', None); values.append(body)
        return hashlib.sha256(encode({'patient_id': patient_id, 'observations': values}).encode()).hexdigest()

    def input_snapshot(self, patient_id, as_of, cutoff):
        self.patient(patient_id)
        with self.connection() as db:
            return self.snapshot(db, patient_id, as_of, cutoff)

    def current_predictions(self, patient_id, as_of):
        self.patient(patient_id)
        with self.connection() as db:
            digest = self.snapshot(db, patient_id, as_of, as_of)
            rows = db.execute('''WITH visible AS (
                SELECT body,event_at,ROW_NUMBER() OVER(PARTITION BY id ORDER BY revision DESC) AS rank
                FROM records WHERE patient=? AND kind='prediction' AND available_at<=?)
                SELECT body FROM visible WHERE rank=1 AND event_at<=?''', (patient_id, as_of, as_of)).fetchall()
        groups = {}
        for row in rows:
            p = json.loads(row[0])
            key = (p['model_id'], p['model_version'], p['target'],
                   datetime.fromisoformat(p['horizon_end']) - datetime.fromisoformat(p['origin_time']))
            old = groups.get(key)
            order = lambda item: (item['input_fingerprint'] == digest, item['origin_time'], item['available_at'])
            if old is None or order(p) > order(old):
                groups[key] = p
        return {'input_fingerprint': digest, 'predictions': list(groups.values()), 'as_of': as_of}

    def patients(self):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM patients ORDER BY id').fetchall()
            result = []
            for row in rows:
                latest = db.execute('''WITH visible AS (SELECT event_at,ROW_NUMBER() OVER(PARTITION BY id ORDER BY revision DESC) AS rank
                    FROM records WHERE patient=? AND kind='observation') SELECT MAX(event_at) FROM visible WHERE rank=1''', (row['id'],)).fetchone()[0]
                predictions = db.execute('''WITH visible AS (SELECT body,ROW_NUMBER() OVER(PARTITION BY id ORDER BY revision DESC) AS rank
                    FROM records WHERE patient=? AND kind='prediction') SELECT body FROM visible WHERE rank=1''', (row['id'],)).fetchall()
                candidates = [json.loads(p[0]) for p in predictions]
                prediction = max(candidates, key=lambda p: (p['input_fingerprint'] == row['input_fingerprint'], p['origin_time'], p['available_at']), default=None)
                result.append({**json.loads(row['body']), 'input_revision': row['input_revision'],
                               'input_fingerprint': row['input_fingerprint'], 'photo': row['photo'], 'latest_measurement': latest,
                               'prediction': prediction, 'stale': bool(prediction and prediction['input_fingerprint'] != row['input_fingerprint'])})
            return result

    def history(self, patient_id, start, end, as_of, kind, offset=0, limit=3000):
        self.patient(patient_id)
        # Rank revisions at knowledge time BEFORE filtering event time; corrections can move a point.
        with self.connection() as db:
            rows = db.execute('''WITH visible AS (
              SELECT body,event_at,id,ROW_NUMBER() OVER(PARTITION BY id ORDER BY revision DESC) AS rank
              FROM records WHERE patient=? AND kind=? AND available_at<=?)
              SELECT body FROM visible WHERE rank=1 AND event_at>=? AND event_at<=?
              ORDER BY event_at,id LIMIT ? OFFSET ?''',
              (patient_id, kind, as_of, start, min(end, as_of), limit + 1, offset)).fetchall()
        return {'items': [json.loads(r[0]) for r in rows[:limit]],
                'next_offset': offset + limit if len(rows) > limit else None}

    def quality(self, patient_id, as_of):
        self.patient(patient_id)
        with self.connection() as db:
            rows = db.execute('''WITH visible AS (
              SELECT body,event_at,ROW_NUMBER() OVER(PARTITION BY id ORDER BY revision DESC) AS rank
              FROM records WHERE patient=? AND kind='observation' AND available_at<=?)
              SELECT body FROM visible WHERE rank=1 AND event_at<=?''', (patient_id, as_of, as_of)).fetchall()
        metrics = {}
        cutoff = datetime.fromisoformat(as_of)
        for row in rows:
            point = json.loads(row[0])
            key = point['metric'] + '|' + point['unit']
            item = metrics.setdefault(key, {'metric': point['metric'], 'label': point['label'], 'unit': point['unit'], 'count': 0, 'last_hour_count': 0, 'latest': point['measured_at']})
            item['count'] += 1
            item['latest'] = max(item['latest'], point['measured_at'])
            age = (cutoff - datetime.fromisoformat(point['measured_at'])).total_seconds()
            if 0 <= age < 3600:
                item['last_hour_count'] += 1
        for item in metrics.values():
            item['age_seconds'] = (cutoff - datetime.fromisoformat(item['latest'])).total_seconds()
        return {'metrics': list(metrics.values()), 'score': None, 'score_status': 'policy_not_configured',
                'reference_time': as_of}

    def export(self, patient_id, kind):
        patient = self.patient(patient_id)
        with self.connection() as db:
            rows = db.execute('SELECT body FROM records WHERE patient=? AND kind=? ORDER BY available_at,revision',
                              (patient_id, 'observation' if kind == 'input' else 'prediction')).fetchall()
        items = []
        for row in rows:
            value = json.loads(row[0]); value.pop('availability_basis', None); items.append(value)
        if kind == 'prediction':
            return {'schema_version': 1, 'kind': kind, 'predictions': items}
        patient.pop('photo'); patient.pop('input_revision'); patient.pop('input_fingerprint')
        return {'schema_version': 1, 'kind': kind, 'patients': [patient], 'observations': items, 'actor': 'export'}

    def set_photo(self, patient_id, filename):
        self.patient(patient_id)
        with self.lock, self.connection() as db:
            db.execute('UPDATE patients SET photo=? WHERE id=?', (filename, patient_id))
            db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")

    def outbox_pending(self):
        with self.connection() as db:
            return db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]
