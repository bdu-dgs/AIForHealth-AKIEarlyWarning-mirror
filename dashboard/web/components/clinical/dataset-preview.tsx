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
const relativeTime = (ms: number) => (ms / 3600000).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' h';

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
          <h2>患者 {patient.subject_id}</h2>
          <p>住院 ID：{patient.hadm_id}</p>
          <p>ICU ID：{patient.icustay_id}</p>
          <p>入 ICU：{patient.icu_admitted_at}</p>
          <p className="patient-note">源文件未提供姓名和照片</p>
          <span className="badge">历史数据 · 只读</span>
        </div>
      </div>
      <div className="patient-chart">
        <div className="chart-title">
          <h3>观测曲线</h3>
          <Choice label={'患者 ' + patient.subject_id + ' 的观测变量'} value={selected} onChange={(v) => { setSelected(v); setRecordPage(0); }}
            options={Object.entries(patient.metrics).map(([value, m]) => ({ value, label: m.label + ' · ' + m.points.length + ' 条' }))} />
        </div>
        <ClinicalChart points={points} start={start} end={end} unit={metric.unit}
          formatTick={relativeTime} empty="该 ICU 记录没有此项测量" />
        <p className="dataset-axis-note">距入 ICU 的时间：负数为入 ICU 前，0 为入 ICU 时刻</p>
        {!!metric.points.length && <details className="dataset-records" onToggle={(e) => setRecordsOpen(e.currentTarget.open)}>
          <summary>查看 {metric.points.length} 条测量记录</summary>
          {recordsOpen && <><div className="dataset-table-scroll"><table>
            <thead><tr><th>测量时间（原文件）</th><th>{metric.label}（{metric.unit}）</th></tr></thead>
            <tbody>{metric.points.slice(currentRecordPage * 100, currentRecordPage * 100 + 100).map((point, i) => <tr key={i}><td>{point.measured_at}</td><td>{point.value}</td></tr>)}</tbody>
          </table></div>
          {recordPages > 1 && <div className="toolbar">
            <Button variant="outline" disabled={currentRecordPage === 0} onClick={() => setRecordPage(currentRecordPage - 1)}>上一页记录</Button>
            <span>{currentRecordPage + 1} / {recordPages}</span>
            <Button variant="outline" disabled={currentRecordPage === recordPages - 1} onClick={() => setRecordPage(currentRecordPage + 1)}>下一页记录</Button>
          </div>}</>}
        </details>}
      </div>
      <div className="patient-chart risk-chart">
        <div className="chart-title"><h3>预测曲线</h3><span className="badge">模型尚未接入</span></div>
        <ClinicalChart points={[]} start={start} end={end} risk empty="尚无 AKI 预测结果" />
      </div>
      <div className="row-footer"><span>按患者、住院、ICU 记录共同匹配</span><span>保留原始测量时间与数值</span></div>
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
        if (!control.signal.aborted) setError(e instanceof Error ? e.message : '读取失败');
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
        <h2><Users size={18} />本地数据集预览</h2>
        <div className="toolbar">
          <Input className="search" aria-label="搜索数据集患者或住院 ID" placeholder="搜索患者、住院或 ICU ID" value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(0); }} />
          <Choice label="数据集记录筛选" value={filter} onChange={(v) => { setFilter(v); setPage(0); }} options={[
            { value: 'with-data', label: '有心率或血氧测量' }, { value: 'all', label: '全部入选 ICU 记录' },
          ]} />
          <Button variant="outline" onClick={() => setRefresh((v) => v + 1)}><RefreshCw size={15} />重新读取</Button>
        </div>
      </div>
      <div className="notice dataset-notice">
        <div>读取项目内 icu_pre_admission_data，每 10 秒检查一次。日期按原文件显示，未提供时区；不以本机当前时间判断过期。
          <br />本页展示历史测量，不计算 AKI 风险或数据置信度。</div>
      </div>
      {error ? <div className="notice error" role="alert">{error}。本次读取失败，请检查文件后重试。</div>
        : !data ? <div className="panel empty-overview" role="status">正在读取本地文件…</div>
        : data.status === 'missing' ? <div className="panel empty-overview">请在项目的 icu_pre_admission_data 文件夹放入：{data.missing_files?.join('、')}</div>
        : <>
          <p className="dataset-summary">{data.stats.selected_stays} 条入选 ICU 记录中，{data.stats.stays_with_data} 条含有这两项测量，共 {data.stats.selected_points} 个观测点。
            其中 {data.stats.before_icu} 个点位于入 ICU 前，{data.stats.at_or_after_icu} 个点位于入 ICU 时或之后。
            {!!(data.stats.unmatched + data.stats.invalid) && <> 未显示：{data.stats.unmatched} 条无法匹配，{data.stats.invalid} 条数值、时间、单位或错误标记不符合读取规则。</>}
          </p>
          <div className="patient-rows">
            {filtered.slice(current * 8, current * 8 + 8).map((patient) =>
              <DatasetCard key={[patient.subject_id, patient.hadm_id, patient.icustay_id].join('-')} patient={patient} />)}
          </div>
          {!filtered.length && <div className="panel empty-overview">没有符合条件的记录。没有这两项测量，不代表该患者没有其他数据。</div>}
          <div className="pagination"><span>共 {filtered.length} 条 ICU 记录 · 每页最多 8 条</span><div className="toolbar">
            <Button variant="outline" aria-label="数据集上一页" disabled={current === 0} onClick={() => setPage(current - 1)}><ChevronLeft size={15} /></Button>
            <span>{current + 1} / {pages}</span>
            <Button variant="outline" aria-label="数据集下一页" disabled={current === pages - 1} onClick={() => setPage(current + 1)}><ChevronRight size={15} /></Button>
          </div></div>
        </>}
    </section>
  );
}
