import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  Clock,
  ChevronLeft,
  ChevronRight,
  Pause,
  Play,
  RotateCcw,
  Download,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DateTimeInput } from './form-controls';
import { Slider } from '@/components/ui/slider';
import { Choice } from './choice';
import { ClinicalChart } from './chart';
import {
  api,
  history,
  fmt,
  duration,
  localTime,
  avatarLabel,
  groupKey,
  defaultGroup,
  nowMs,
  isDemoClock,
  isModelRunning,
  groupLabel,
  alertOf,
  type Patient,
  type Observation,
  type Prediction,
  type Quality,
} from '@/lib/api';

type ViewData = {
  observations: Observation[];
  predictions: Prediction[];
  truncated: boolean;
  current: Prediction[];
  inputFingerprint: string;
};
const cache = new Map<string, ViewData>();
function remember(key: string, value: ViewData) {
  cache.set(key, value);
  let count = Array.from(cache.values()).reduce(
    (n, x) => n + x.observations.length + x.predictions.length,
    0,
  );
  while (cache.size > 24 || count > 48000) {
    const k = cache.keys().next().value!;
    const old = cache.get(k)!;
    cache.delete(k);
    count -= old.observations.length + old.predictions.length;
  }
}
export function Avatar({
  patient,
  large = false,
}: {
  patient: Patient;
  large?: boolean;
}) {
  return (
    <div className={'avatar ' + (large ? 'large' : '')}>
      {patient.photo ? (
        <img
          src={
            '/api/patients/' + patient.patient_id + '/photo?v=' + patient.photo
          }
          alt={'Photo of ' + patient.name}
        />
      ) : (
        <span className="initials" title={patient.name}>
          {avatarLabel(patient.name)}
        </span>
      )}
    </div>
  );
}
export function PatientView({
  patient,
  revision,
  detail = false,
  onOpen,
  onError,
}: {
  patient: Patient;
  revision: number;
  detail?: boolean;
  onOpen?: () => void;
  onError: (message: string) => void;
}) {
  const [exports, setExports] = useState<{
    kind: string;
    parts: number;
    revision: number;
  } | null>(null);
  async function prepareExport(kind: string) {
    try {
      const plan = await api<{ parts: number; revision: number }>(
        '/patients/' + patient.patient_id + '/export-plan/' + kind,
      );
      setExports({ kind, ...plan });
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Unable to prepare export');
    }
  }
  const [range, setRange] = useState('all');
  const [replay, setReplay] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState('60');
  const [metric, setMetric] = useState('');
  const [group, setGroup] = useState('');
  const [clock, setClock] = useState(nowMs());
  const [loaded, setData] = useState<ViewData | null>(null);
  const [dataKey, setDataKey] = useState('');
  const [loadedQuality, setQuality] = useState<Quality | null>(null);
  const [qualityKey, setQualityKey] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const admitted = +new Date(patient.icu_admitted_at);
  const end = replay ?? clock;
  const start = Math.max(
    admitted,
    range === 'all' ? admitted : end - Number(range) * 3600000,
  );
  // Keep showing the last loaded data of this patient while a refresh is in flight (no blank flashes).
  const mine = (key: string) => key.startsWith(patient.patient_id + '|');
  const data = mine(dataKey) ? loaded : null;
  const quality = mine(qualityKey) ? loadedQuality : null;
  // Demo playback: fixed 0-72 h axis so each "+1 h" visibly extends the curves.
  const demoAxis = isDemoClock() && range === 'all';
  const chartEnd = demoAxis ? Math.max(end, admitted + 72 * 3600000) : end;
  const icuHourTick = demoAxis
    ? (t: number) => 'ICU h ' + Math.round((t - admitted) / 3600000)
    : undefined;
  useEffect(() => setClock(nowMs()), [revision]);
  useEffect(() => {
    const timer = setInterval(() => setClock(nowMs()), 30000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(
      () =>
        setReplay((old) => {
          const next = (old ?? admitted) + Number(speed) * 1000;
          if (next >= nowMs()) {
            setPlaying(false);
            return null;
          }
          return next;
        }),
      1000,
    );
    return () => clearInterval(timer);
  }, [playing, speed, admitted]);
  useEffect(() => {
    const control = new AbortController();
    setLoading(true);
    setError('');
    const key = [patient.patient_id, revision, start, end].join('|');
    const timer = setTimeout(
      () => {
        const stored = cache.get(key);
        const work = stored
          ? Promise.resolve(stored)
          : Promise.all([
              history<Observation>(
                patient.patient_id,
                'observation',
                start,
                end,
                end,
                control.signal,
              ),
              history<Prediction>(
                patient.patient_id,
                'prediction',
                start,
                end,
                end,
                control.signal,
              ),
              api<{ predictions: Prediction[]; input_fingerprint: string }>(
                '/patients/' +
                  patient.patient_id +
                  '/current-predictions?as_of=' +
                  encodeURIComponent(new Date(end).toISOString()),
                { signal: control.signal },
              ),
            ]).then(([o, p, c]) => ({
              observations: o.items,
              predictions: p.items,
              truncated: o.truncated || p.truncated,
              current: c.predictions,
              inputFingerprint: c.input_fingerprint,
            }));
        void work
          .then((value) => {
            if (!control.signal.aborted) {
              remember(key, value);
              setData(value);
              setDataKey(key);
              setLoading(false);
            }
          })
          .catch((e) => {
            if (!control.signal.aborted) {
              setData(null);
              setError(e.message);
              setLoading(false);
            }
          });
        if (detail) {
          void api<Quality>(
            '/patients/' +
              patient.patient_id +
              '/quality?as_of=' +
              encodeURIComponent(new Date(end).toISOString()),
            { signal: control.signal },
          )
            .then((q) => {
              if (!control.signal.aborted) {
                setQuality(q);
                setQualityKey(key);
              }
            })
            .catch(() => {
              if (!control.signal.aborted) setQuality(null);
            });
        }
      },
      replay === null ? 0 : 250,
    );
    return () => {
      clearTimeout(timer);
      control.abort();
    };
  }, [patient.patient_id, revision, start, end, detail, replay]);
  const metrics = useMemo(
    () =>
      Array.from(
        new Map(
          (data?.observations ?? []).map((p) => [
            p.metric + '|' + p.unit,
            {
              value: p.metric + '|' + p.unit,
              label: p.label + (p.unit ? ' (' + p.unit + ')' : ''),
            },
          ]),
        ).values(),
      ),
    [data],
  );
  const metricKey = metrics.some((m) => m.value === metric)
    ? metric
    : (['creatinine', 'heart_rate']
        .map((code) => metrics.find((m) => m.value.startsWith(code + '|')))
        .find(Boolean)?.value ??
      metrics[0]?.value ??
      '');
  const groups = useMemo(
    () =>
      Array.from(
        new Map(
          [...(data?.current ?? []), ...(data?.predictions ?? [])].map((p) => [
            groupKey(p),
            { value: groupKey(p), label: groupLabel(p) },
          ]),
        ).values(),
      ),
    [data],
  );
  const groupId = groups.some((g) => g.value === group)
    ? group
    : defaultGroup([...(data?.current ?? []), ...(data?.predictions ?? [])]);
  const predictions = (data?.predictions ?? [])
    .filter((p) => groupKey(p) === groupId)
    .sort(
      (a, b) =>
        +new Date(a.origin_time) - +new Date(b.origin_time) ||
        b.input_revision - a.input_revision,
    );
  const current = data?.current.find((p) => groupKey(p) === groupId) ?? null;
  const threshold =
    current?.threshold?.locked
      ? current.threshold.value
      : ([...predictions].reverse().find((p) => p.threshold?.locked)?.threshold?.value ?? null);
  const stale =
    !!current && current.input_fingerprint !== data?.inputFingerprint;
  // While the model worker is recalculating, keep the previous status instead of flashing "result pending".
  const updating = stale && isModelRunning();
  const status = alertOf(current, stale && !updating, end);
  const observations = (data?.observations ?? []).filter(
    (p) => p.metric + '|' + p.unit === metricKey,
  );
  const trajectory = current?.trajectories.find(
    (t) => t.metric + '|' + t.unit === metricKey,
  );
  const riskPoints = predictions.map((p) => ({
    t: +new Date(p.origin_time),
    v: p.risk,
    confidence: p.data_confidence,
  }));
  const content = (
    <>
      <div className="patient-identity">
        <Avatar patient={patient} large={detail} />
        <div className="patient-name">
          <div>
            <h2>{patient.name}</h2>
            {patient.bed && <span className="bed">{patient.bed}</span>}
          </div>
          <p>
            {patient.patient_id} <span>· {patient.encounter_id}</span>
          </p>
          <span
            className={
              'status-label ' +
              (status === 'AKI warning' ? 'warning' : '')
            }
          >
            {error
              ? 'Read failed'
              : !data
                ? 'Loading'
                : status + (updating ? ' · updating' : '')}
          </span>
          {patient.note && <p className="patient-note">{patient.note}</p>}
          {detail && <small>ICU admission: {fmt(patient.icu_admitted_at)}</small>}
        </div>
      </div>
      <section className="patient-chart">
        <div className="chart-title">
          <h3>Observation chart</h3>
          <div onClick={(e) => e.stopPropagation()}>
            <Choice
              value={metricKey}
              onChange={setMetric}
              options={metrics}
              label="Observation metric"
            />
          </div>
        </div>
        <ClinicalChart
          points={observations.map((p) => ({
            t: +new Date(p.measured_at),
            v: p.value,
          }))}
          start={start}
          end={chartEnd}
          formatTick={icuHourTick}
          unit={observations[0]?.unit}
          forecast={
            detail
              ? (trajectory?.points.map((p) => ({
                  t: +new Date(p.time),
                  v: p.value,
                })) ?? [])
              : []
          }
          empty="No observations available in this period"
        />
      </section>
      <section className="patient-chart risk-chart">
        <div className="chart-title">
          <h3>AKI prediction chart</h3>
          <div onClick={(e) => e.stopPropagation()}>
            <Choice
              value={groupId}
              onChange={setGroup}
              options={groups}
              label="Awaiting model result"
            />
          </div>
        </div>
        <ClinicalChart
          points={riskPoints}
          start={start}
          end={chartEnd}
          formatTick={icuHourTick}
          threshold={threshold}
          risk
          empty={current ? 'No prediction points in the current range' : 'The model has not provided predictions yet'}
        />
      </section>
    </>
  );
  if (!detail)
    return (
      <article
        className="patient-row panel"
        onClick={onOpen}
        onKeyDown={(e) => {
          if (e.target === e.currentTarget && e.key === 'Enter') onOpen?.();
        }}
        tabIndex={0}
        role="link"
        aria-label={'View full details for ' + patient.name}
      >
        {content}
        <div className="row-footer">
          <span>
            <Clock size={12} />
            Since ICU admission · measurement time
          </span>
          <span>{error || 'Click the card to view full history →'}</span>
        </div>
      </article>
    );
  return (
    <>
      <div className="panel detail-patient">{content}</div>
      <section className="panel replay-panel">
        <div className="toolbar spread">
          <div>
            <h2>{replay === null ? 'Live view' : 'History replay'}</h2>
            <p className="muted">
              {fmt(start)} — {fmt(end)} · Only data already available at that moment is shown
            </p>
          </div>
          <div className="toolbar">
            <Choice
              value={range}
              onChange={setRange}
              options={[
                { value: '1', label: 'Show 1 h' },
                { value: '6', label: 'Show 6 h' },
                { value: '24', label: 'Show 24 h' },
                { value: 'all', label: 'Show full history' },
              ]}
              label="Chart range"
            />
            <Button
              variant="outline"
              onClick={() => {
                setReplay(null);
                setPlaying(false);
                setClock(nowMs());
              }}
            >
              <RotateCcw size={15} />
              Back to live
            </Button>
          </div>
        </div>
        <div className="replay-slider">
          <Button
            variant="ghost"
            aria-label="Browse earlier"
            onClick={() =>
              setReplay(
                Math.max(
                  admitted,
                  end -
                    (range === 'all' ? 3600000 : Number(range) * 3600000) / 2,
                ),
              )
            }
          >
            <ChevronLeft />
          </Button>
          <Slider
            aria-label="History replay cutoff time"
            min={admitted}
            max={Math.max(admitted + 1, clock)}
            step={1000}
            value={[Math.max(admitted, Math.min(end, clock))]}
            onValueChange={(v) => {
              setPlaying(false);
              setReplay(Array.isArray(v) ? v[0] : v);
            }}
          />
          <Button
            variant="ghost"
            aria-label="Browse later"
            onClick={() =>
              setReplay(
                Math.min(
                  clock,
                  end +
                    (range === 'all' ? 3600000 : Number(range) * 3600000) / 2,
                ),
              )
            }
          >
            <ChevronRight />
          </Button>
        </div>
        <div className="toolbar spread">
          <div className="toolbar">
            <Button
              variant="outline"
              onClick={() => {
                if (replay === null) setReplay(admitted);
                setPlaying(!playing);
              }}
            >
              {playing ? <Pause size={15} /> : <Play size={15} />}{' '}
              {playing ? 'Pause' : 'Play replay'}
            </Button>
            <Choice
              value={speed}
              onChange={setSpeed}
              options={[
                { value: '60', label: '1 min per second' },
                { value: '300', label: '5 min per second' },
                { value: '900', label: '15 min per second' },
              ]}
              label="Replay speed"
            />
          </div>
          <label className="inline-label">
            View time
            <DateTimeInput
              value={localTime(end)}
              min={localTime(admitted)}
              max={localTime(clock)}
              onChange={(v) => {
                setPlaying(false);
                setReplay(Math.min(clock, Math.max(admitted, +new Date(v))));
              }}
            />
          </label>
        </div>
        <p className="form-help">
          Data loads by time range as you move into the past. Data without an original availability time uses the time this computer first received it; historical predictions are never back-filled.
        </p>
        {loading && (
          <p role="status" className="muted">
            Loading selected period…
          </p>
        )}
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        {data?.truncated && (
          <p className="error-text">
            The current range exceeds 24,000
            records, so only the loaded part is shown. Shorten the display range to keep browsing; the full records remain stored locally.
          </p>
        )}
      </section>
      <div className="detail-grid">
        <section className="panel">
          <div className="panel-heading">
            <h2>Prediction and contributing factors</h2>
            <span className="badge">
              {current ? 'External model result' : 'Model not yet connected'}
            </span>
          </div>
          <div className="form-body">
            {current ? (
              <>
                <div className="risk-reading">
                  <strong>
                    {(current.risk * 100).toFixed(1)}
                    <small>%</small>
                  </strong>
                  <span>{status + (updating ? ' · updating' : '')}</span>
                </div>
                <p className="muted">{groupLabel(current)}</p>
                <dl className="result-meta">
                  <dt>Data cutoff</dt>
                  <dd>{fmt(current.data_cutoff)}</dd>
                  <dt>Prediction origin</dt>
                  <dd>{fmt(current.origin_time)}</dd>
                  <dt>Prediction window end</dt>
                  <dd>{fmt(current.horizon_end)}</dd>
                  <dt>Data confidence</dt>
                  <dd>
                    {current.data_confidence === null
                      ? 'Not provided'
                      : (current.data_confidence * 100).toFixed(1) + '%'}
                    {current.confidence_definition && (
                      <small> · {current.confidence_definition}</small>
                    )}
                  </dd>
                  <dt>Alert threshold</dt>
                  <dd>
                    {current.threshold
                      ? current.threshold.comparison +
                        ' ' +
                        (current.threshold.value * 100).toFixed(1) +
                        '% · ' +
                        (current.threshold.locked ? 'Locked' : 'Not locked')
                      : 'Not yet selected'}
                  </dd>
                </dl>
                {current.threshold && (
                  <p className="form-help">
                    Validation run: {current.threshold.validation_run_id} · Policy:{' '}
                    {current.threshold.policy_version}
                    <br />
                    {current.threshold.selection_basis}
                  </p>
                )}
                <div className="drivers">
                  {current.drivers.length ? (
                    current.drivers.map((d, i) => (
                      <div key={d.feature + i}>
                        <span>
                          {d.label}
                          <small>
                            {d.value} {d.unit}
                          </small>
                        </span>
                        <strong
                          className={
                            d.contribution > 0 ? 'risk-up' : 'risk-down'
                          }
                        >
                          {d.contribution > 0 ? '+' : ''}
                          {d.contribution.toLocaleString('en-US', {
                            maximumFractionDigits: 4,
                          })}
                        </strong>
                      </div>
                    ))
                  ) : (
                    <p className="muted">The model did not provide contributing factors.</p>
                  )}
                </div>
                <p className="form-help">
                  Contribution values are shown as the raw model output; their meaning depends on the model's explanation method.
                </p>
              </>
            ) : (
              <div className="empty-copy">
                <Activity />
                <p>Awaiting model result file</p>
                <small>This stage builds the data and display features; no simulated risk probabilities are generated.</small>
              </div>
            )}
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>Data freshness and density</h2>
            <span className="badge">Quality rules pending</span>
          </div>
          <div className="form-body">
            <p className="form-help">
              These are data statistics, not prediction accuracy. Freshness is calculated relative to the current view time.
            </p>
            {quality?.metrics.length ? (
              <div className="quality-list">
                {quality.metrics.map((q) => (
                  <div key={q.metric + q.unit}>
                    <div>
                      <strong>{q.label}</strong>
                      <small>
                        {q.unit} · Latest measurement {fmt(q.latest)}
                      </small>
                    </div>
                    <div>
                      <strong>{duration(q.age_seconds * 1000)} ago</strong>
                      <small>{q.last_hour_count} in the last 1 h</small>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">No observations available at the current time.</p>
            )}
            <p className="form-help">
              Expected sampling frequency and validity periods for each variable are not yet defined, so no unvalidated overall confidence score is shown.
            </p>
          </div>
        </section>
      </div>
      <div className="export-row">
        <Button variant="outline" onClick={() => void prepareExport('input')}>
          <Download size={15} />
          Export input file
        </Button>
        <Button
          variant="outline"
          onClick={() => void prepareExport('prediction')}
        >
          <Download size={15} />
          Export prediction file
        </Button>
        <span className="muted">
          Files are saved on this computer and contain patient information; store them securely.
        </span>
      </div>
      {exports && (
        <div className="panel form-body">
          <p>
            {exports.parts}{' '}
            part(s) in total; save and import them in order. Regenerate the export list if the data changes.
          </p>
          <div className="toolbar">
            {Array.from({ length: exports.parts }, (_, i) => (
              <a
                key={i}
                className="download-link"
                href={
                  '/api/patients/' +
                  patient.patient_id +
                  '/export/' +
                  exports.kind +
                  '?part=' +
                  (i + 1) +
                  '&revision=' +
                  exports.revision
                }
                download
              >
                Download part {i + 1}
              </a>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
