import { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, RefreshCw, Users } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ClinicalChart } from './chart';
import { Choice } from './choice';
import { api } from '@/lib/api';

type Measurement = { measured_at: string; hours_from_icu: number; value: number };
type Metric = { label: string; unit: string; points: Measurement[] };
type DatasetPatient = {
  subject_id: string; hadm_id: string; icustay_id: string; icu_admitted_at: string;
  metrics: Record<string, Metric>;
};
type Preview = {
  status: 'ready' | 'missing'; missing_files?: string[]; patients: DatasetPatient[];
  stats: Record<string, number>;
};
const hasData = (patient: DatasetPatient) => Object.values(patient.metrics).some((m) => m.points.length);
const relativeTime = (ms: number) => (ms / 3600000).toLocaleString('en-US', { maximumFractionDigits: 2 }) + ' h';

function DatasetCard({ patient }: { patient: DatasetPatient }) {
  const [selected, setSelected] = useState(patient.metrics.heart_rate.points.length ? 'heart_rate' : 'spo2');
  const [recordsOpen, setRecordsOpen] = useState(false);
  const [recordPage, setRecordPage] = useState(0);
  const metric = patient.metrics[selected];
  const points = metric.points.map((p) => ({ t: p.hours_from_icu * 3600000, v: p.value, label: p.measured_at }));
  const start = Math.min(-3600000, points[0]?.t ?? 0);
  const end = Math.max(0, points.at(-1)?.t ?? 0);
  const recordPages = Math.max(1, Math.ceil(metric.points.length / 100));
  const currentRecordPage = Math.min(recordPage, recordPages - 1);
  return (
    <article className="panel patient-row dataset-row">
      <div className="patient-identity">
        <div className="avatar"><span>ID<br />{patient.subject_id}</span></div>
        <div className="patient-name">
          <h2>Patient {patient.subject_id}</h2>
          <p>Admission ID: {patient.hadm_id}</p>
          <p>ICU stay ID: {patient.icustay_id}</p>
          <p>ICU admission: {patient.icu_admitted_at}</p>
          <p className="patient-note">Source files provide no name or photo</p>
          <span className="badge">Historical data · Read-only</span>
        </div>
      </div>
      <div className="patient-chart">
        <div className="chart-title">
          <h3>Observation chart</h3>
          <Choice label={'Observation variable for patient ' + patient.subject_id} value={selected} onChange={(v) => { setSelected(v); setRecordPage(0); }}
            options={Object.entries(patient.metrics).map(([value, m]) => ({ value, label: m.label + ' · ' + m.points.length + ' records' }))} />
        </div>
        <ClinicalChart points={points} start={start} end={end} unit={metric.unit}
          formatTick={relativeTime} empty="This ICU stay has no measurements of this type" />
        <p className="dataset-axis-note">Time from ICU admission: negative is before admission, 0 is the admission time</p>
        {!!metric.points.length && <details className="dataset-records" onToggle={(e) => setRecordsOpen(e.currentTarget.open)}>
          <summary>View {metric.points.length} measurement records</summary>
          {recordsOpen && <><div className="dataset-table-scroll"><table>
            <thead><tr><th>Measurement time (source file)</th><th>{metric.label} ({metric.unit})</th></tr></thead>
            <tbody>{metric.points.slice(currentRecordPage * 100, currentRecordPage * 100 + 100).map((point, i) => <tr key={i}><td>{point.measured_at}</td><td>{point.value}</td></tr>)}</tbody>
          </table></div>
          {recordPages > 1 && <div className="toolbar">
            <Button variant="outline" disabled={currentRecordPage === 0} onClick={() => setRecordPage(currentRecordPage - 1)}>Previous records</Button>
            <span>{currentRecordPage + 1} / {recordPages}</span>
            <Button variant="outline" disabled={currentRecordPage === recordPages - 1} onClick={() => setRecordPage(currentRecordPage + 1)}>Next records</Button>
          </div>}</>}
        </details>}
      </div>
      <div className="patient-chart risk-chart">
        <div className="chart-title"><h3>Prediction chart</h3><span className="badge">Model not yet connected</span></div>
        <ClinicalChart points={[]} start={start} end={end} risk empty="No AKI predictions yet" />
      </div>
      <div className="row-footer"><span>Matched on patient, admission, and ICU stay</span><span>Original measurement times and values preserved</span></div>
    </article>
  );
}

export function DatasetPreview() {
  const [data, setData] = useState<Preview | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('with-data');
  const [page, setPage] = useState(0);
  useEffect(() => {
    const control = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      try {
        const result = await api<Preview>('/datasets/icu-preview', { signal: control.signal });
        if (!control.signal.aborted) { setData(result); setError(''); }
      } catch (e) {
        if (!control.signal.aborted) setError(e instanceof Error ? e.message : 'Read failed');
      } finally {
        if (!control.signal.aborted) timer = setTimeout(read, 10000);
      }
    };
    void read();
    return () => { control.abort(); clearTimeout(timer); };
  }, [refresh]);
  const filtered = (data?.patients ?? []).filter((p) =>
    (filter === 'all' || hasData(p)) && [p.subject_id, p.hadm_id, p.icustay_id].some((id) => id.includes(search.trim())));
  const pages = Math.max(1, Math.ceil(filtered.length / 8));
  const current = Math.min(page, pages - 1);
  return (
    <section>
      <div className="overview-heading">
        <h2><Users size={18} />Local dataset preview</h2>
        <div className="toolbar">
          <Input className="search" aria-label="Search dataset patient or admission ID" placeholder="Search patient, admission, or ICU stay ID" value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(0); }} />
          <Choice label="Dataset record filter" value={filter} onChange={(v) => { setFilter(v); setPage(0); }} options={[
            { value: 'with-data', label: 'With heart rate or SpO2 data' }, { value: 'all', label: 'All selected ICU stays' },
          ]} />
          <Button variant="outline" onClick={() => setRefresh((v) => v + 1)}><RefreshCw size={15} />Reload</Button>
        </div>
      </div>
      <div className="notice dataset-notice">
        <div>Reads icu_pre_admission_data in the project and checks every 10 seconds. Dates are shown as in the source files with no timezone; staleness is not judged against this computer's current time.
          <br />This page shows historical measurements and does not compute AKI risk or data confidence.</div>
      </div>
      {error ? <div className="notice error" role="alert">{error}. This read failed; check the files and try again.</div>
        : !data ? <div className="panel empty-overview" role="status">Reading local files…</div>
        : data.status === 'missing' ? <div className="panel empty-overview">Place these files in the project's icu_pre_admission_data folder: {data.missing_files?.join(', ')}</div>
        : <>
          <p className="dataset-summary">Of {data.stats.selected_stays} selected ICU stays, {data.stats.stays_with_data} have these two measurements, with {data.stats.selected_points} observation points in total.
            Of these, {data.stats.before_icu} points are before ICU admission and {data.stats.at_or_after_icu} are at or after ICU admission.
            {!!(data.stats.unmatched + data.stats.invalid) && <> Not shown: {data.stats.unmatched} unmatched rows and {data.stats.invalid} rows whose value, time, unit, or error flag fails the read rules.</>}
          </p>
          <div className="patient-rows">
            {filtered.slice(current * 8, current * 8 + 8).map((patient) =>
              <DatasetCard key={[patient.subject_id, patient.hadm_id, patient.icustay_id].join('-')} patient={patient} />)}
          </div>
          {!filtered.length && <div className="panel empty-overview">No matching records. Lacking these two measurements does not mean the patient has no other data.</div>}
          <div className="pagination"><span>{filtered.length} ICU stays total · up to 8 per page</span><div className="toolbar">
            <Button variant="outline" aria-label="Previous dataset page" disabled={current === 0} onClick={() => setPage(current - 1)}><ChevronLeft size={15} /></Button>
            <span>{current + 1} / {pages}</span>
            <Button variant="outline" aria-label="Next dataset page" disabled={current === pages - 1} onClick={() => setPage(current + 1)}><ChevronRight size={15} /></Button>
          </div></div>
        </>}
    </section>
  );
}
