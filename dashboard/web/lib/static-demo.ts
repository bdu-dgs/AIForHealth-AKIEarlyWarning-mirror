/**
 * Static demo: answers the website's API requests in the browser from a recorded demo playback.
 *
 * `scripts/export_static_demo.py` runs the real backend and model worker on the synthetic patients, one hour at a
 * time, and writes every observation, prediction, input revision and input fingerprint with its availability time
 * to `public/static-demo/demo.json`. This module applies the same visibility rules as dashboard/api/storage.py
 * (latest revision known at `as_of`, event time window, the demo clock as the knowledge limit), so each view shows
 * what the live demo showed at that clock time. The site is read-only; nothing leaves the browser.
 */
import type { Clock, Health, Observation, Patient, Prediction, Quality } from './api';

type Body = Record<string, unknown> & {
  record_id: string;
  revision: number;
  patient_id: string;
  available_at: string;
};
type Entry<T> = { id: string; revision: number; event: number; available: number; body: T };
type Demo = {
  clock: { start: string; hours: number };
  model: Health['model'];
  patients: Omit<Patient, 'input_revision' | 'input_fingerprint' | 'photo'>[];
  revisions: Record<string, [number, string][]>;
  fingerprints: Record<string, [string, string][]>;
  observations: (Observation & Body)[];
  predictions: (Prediction & Body)[];
};
type Indexed = {
  raw: Demo;
  start: number;
  end: number;
  observations: Map<string, Entry<Observation>[]>;
  predictions: Map<string, Entry<Prediction>[]>;
  revisions: Map<string, number[]>;
  fingerprints: Map<string, { at: number; value: string }[]>;
};

const HOUR = 3600000;
export const READ_ONLY =
  'This public demo is read-only and uses synthetic patients. Data entry, import, export and the dataset preview need the local workbench.';

let loading: Promise<Indexed> | null = null;
let clock = 0;
let revision = 1;

const ms = (value: string) => Date.parse(value);
const iso = (value: number) => new Date(value).toISOString();

function index(raw: Demo): Indexed {
  const start = ms(raw.clock.start);
  const group = <T extends Body>(items: T[], eventKey: keyof T) => {
    const out = new Map<string, Entry<T>[]>();
    for (const body of items) {
      const list = out.get(body.patient_id) ?? [];
      list.push({
        id: body.record_id,
        revision: body.revision,
        event: ms(body[eventKey] as string),
        available: ms(body.available_at),
        body,
      });
      out.set(body.patient_id, list);
    }
    for (const list of out.values())
      list.sort((a, b) => a.event - b.event || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    return out;
  };
  const initial = Number(new URLSearchParams(window.location.search).get('hour'));
  clock =
    start + HOUR * Math.min(raw.clock.hours, Math.max(0, Number.isFinite(initial) ? Math.round(initial) : 0));
  return {
    raw,
    start,
    end: start + raw.clock.hours * HOUR,
    observations: group(raw.observations, 'measured_at') as Map<string, Entry<Observation>[]>,
    predictions: group(raw.predictions, 'origin_time') as Map<string, Entry<Prediction>[]>,
    revisions: new Map(
      Object.entries(raw.revisions).map(([id, list]) => [id, list.map(([, at]) => ms(at))]),
    ),
    fingerprints: new Map(
      Object.entries(raw.fingerprints).map(([id, list]) => [
        id,
        list.map(([at, value]) => ({ at: ms(at), value })),
      ]),
    ),
  };
}

function load() {
  loading ??= fetch('/static-demo/demo.json')
    .then((r) => {
      if (!r.ok) throw new Error('Demo data could not be loaded');
      return r.json() as Promise<Demo>;
    })
    .then(index);
  return loading;
}

/** Latest revision of every record known at `asOf` (revisions are ranked before the event-time filter). */
function visible<T>(entries: Entry<T>[] | undefined, asOf: number) {
  const best = new Map<string, Entry<T>>();
  for (const e of entries ?? []) {
    if (e.available > asOf) continue;
    const old = best.get(e.id);
    if (!old || e.revision > old.revision) best.set(e.id, e);
  }
  return [...best.values()].sort(
    (a, b) => a.event - b.event || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );
}

function fingerprint(d: Indexed, id: string, asOf: number) {
  let value = '';
  for (const step of d.fingerprints.get(id) ?? []) {
    if (step.at > asOf) break;
    value = step.value;
  }
  return value;
}

function known(asOf: string | null) {
  return asOf ? Math.min(ms(asOf), clock) : clock;
}

function clockStatus(d: Indexed): Clock {
  let remaining = 0;
  for (const list of d.observations.values()) for (const e of list) if (e.available > clock) remaining++;
  return {
    mode: 'demo',
    now: iso(clock),
    start: iso(d.start),
    hours_elapsed: (clock - d.start) / HOUR,
    staged_remaining: remaining,
  };
}

function health(d: Indexed): Health {
  return {
    status: 'ok',
    revision,
    model_status: 'running',
    model: d.raw.model && { ...d.raw.model, status: 'ready' },
    clock: clockStatus(d),
    watcher: { running: false, errors: [] },
    file_writes_pending: 0,
    server_time: iso(clock),
  };
}

function locked(p: Prediction) {
  return Boolean(p.threshold?.locked);
}

function patients(d: Indexed): Patient[] {
  return d.raw.patients.map((base) => {
    const id = base.patient_id;
    const digest = fingerprint(d, id, clock);
    const obs = visible(d.observations.get(id), clock);
    const candidates = visible(d.predictions.get(id), clock).map((e) => e.body);
    const order = (p: Prediction) =>
      [Number(locked(p)), Number(p.input_fingerprint === digest), ms(p.origin_time), ms(p.available_at)] as const;
    let prediction: Prediction | null = null;
    for (const p of candidates) {
      if (!prediction) prediction = p;
      else {
        const a = order(p);
        const b = order(prediction);
        const i = a.findIndex((v, k) => v !== b[k]);
        if (i >= 0 && a[i] > b[i]) prediction = p;
      }
    }
    return {
      ...base,
      input_revision: (d.revisions.get(id) ?? []).filter((t) => t <= clock).length,
      input_fingerprint: digest,
      photo: null,
      latest_measurement: obs.length ? obs[obs.length - 1].body.measured_at : undefined,
      prediction,
      stale: Boolean(prediction && prediction.input_fingerprint !== digest),
    };
  });
}

function history(d: Indexed, id: string, q: URLSearchParams) {
  const asOf = known(q.get('as_of'));
  const start = ms(q.get('start') ?? '1970-01-01T00:00:00Z');
  const end = Math.min(q.get('end') ? ms(q.get('end')!) : asOf, asOf);
  if (end < start) throw new Error('End time must be later than start time');
  const source = q.get('kind') === 'prediction' ? d.predictions : d.observations;
  const items = visible<Observation | Prediction>(source.get(id), asOf).filter(
    (e) => e.event >= start && e.event <= end,
  );
  const offset = Number(q.get('offset') ?? 0);
  const limit = Number(q.get('limit') ?? 3000);
  return {
    items: items.slice(offset, offset + limit).map((e) => e.body),
    next_offset: items.length > offset + limit ? offset + limit : null,
  };
}

function currentPredictions(d: Indexed, id: string, q: URLSearchParams) {
  const asOf = known(q.get('as_of'));
  const digest = fingerprint(d, id, asOf);
  const groups = new Map<string, Prediction>();
  for (const e of visible(d.predictions.get(id), asOf)) {
    if (e.event > asOf) continue;
    const p = e.body;
    const key = [p.model_id, p.model_version, p.target, ms(p.horizon_end) - ms(p.origin_time)].join('|');
    const old = groups.get(key);
    const rank = (x: Prediction) => [Number(x.input_fingerprint === digest), ms(x.origin_time), ms(x.available_at)];
    if (!old) groups.set(key, p);
    else {
      const a = rank(p);
      const b = rank(old);
      const i = a.findIndex((v, k) => v !== b[k]);
      if (i >= 0 && a[i] > b[i]) groups.set(key, p);
    }
  }
  return { input_fingerprint: digest, predictions: [...groups.values()], as_of: iso(asOf) };
}

function quality(d: Indexed, id: string, q: URLSearchParams): Quality {
  const asOf = known(q.get('as_of'));
  const metrics = new Map<string, Quality['metrics'][number]>();
  for (const e of visible(d.observations.get(id), asOf)) {
    if (e.event > asOf) continue;
    const o = e.body;
    const key = o.metric + '|' + o.unit;
    const item = metrics.get(key) ?? {
      metric: o.metric,
      label: o.label,
      unit: o.unit,
      count: 0,
      last_hour_count: 0,
      latest: o.measured_at,
      age_seconds: 0,
    };
    item.count += 1;
    if (ms(o.measured_at) > ms(item.latest)) item.latest = o.measured_at;
    const age = (asOf - ms(o.measured_at)) / 1000;
    if (age >= 0 && age < 3600) item.last_hour_count += 1;
    metrics.set(key, item);
  }
  for (const item of metrics.values()) item.age_seconds = (asOf - ms(item.latest)) / 1000;
  return { metrics: [...metrics.values()], score: null, score_status: 'policy_not_configured', reference_time: iso(asOf) };
}

/** Same contract as `fetch('/api' + path)` followed by `.json()` in lib/api.ts. */
export async function handle(path: string, init?: RequestInit): Promise<unknown> {
  const d = await load();
  if (init?.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
  const url = new URL(path, 'https://demo.invalid');
  const parts = url.pathname.split('/').filter(Boolean).map(decodeURIComponent);
  const method = (init?.method ?? 'GET').toUpperCase();
  if (method === 'POST' && url.pathname === '/demo/advance') {
    const hours = Math.min(24, Math.max(1, Number(url.searchParams.get('hours') ?? 1)));
    if (clock >= d.end) throw new Error('The recorded demo ends at ICU hour ' + d.raw.clock.hours + '. Use Restart to replay it.');
    clock = Math.min(d.end, clock + hours * HOUR);
    revision += 1;
    return clockStatus(d);
  }
  if (method === 'POST' && url.pathname === '/demo/restart') {
    clock = d.start;
    revision += 1;
    return clockStatus(d);
  }
  if (method !== 'GET') throw new Error(READ_ONLY);
  if (url.pathname === '/health') return health(d);
  if (url.pathname === '/clock') return clockStatus(d);
  if (url.pathname === '/patients') return patients(d);
  if (parts[0] === 'patients' && parts[1]) {
    const id = parts[1];
    if (!d.raw.patients.some((p) => p.patient_id === id)) throw new Error('Patient not found');
    if (parts.length === 2) return patients(d).find((p) => p.patient_id === id);
    if (parts[2] === 'history') return history(d, id, url.searchParams);
    if (parts[2] === 'current-predictions') return currentPredictions(d, id, url.searchParams);
    if (parts[2] === 'quality') return quality(d, id, url.searchParams);
  }
  throw new Error(READ_ONLY);
}
