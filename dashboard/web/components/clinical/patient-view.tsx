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
import { Input } from '@/components/ui/input';
import { Slider } from '@/components/ui/slider';
import { Choice } from './choice';
import { ClinicalChart } from './chart';
import {
  api,
  history,
  fmt,
  duration,
  localTime,
  groupKey,
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
          alt={patient.name + '的照片'}
        />
      ) : (
        <span>{patient.name}</span>
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
      onError(e instanceof Error ? e.message : '无法准备导出');
    }
  }
  const [range, setRange] = useState('6');
  const [replay, setReplay] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState('60');
  const [metric, setMetric] = useState('');
  const [group, setGroup] = useState('');
  const [clock, setClock] = useState(Date.now());
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
  const queryKey = [patient.patient_id, revision, start, end].join('|');
  const data = dataKey === queryKey ? loaded : null;
  const quality = qualityKey === queryKey ? loadedQuality : null;
  useEffect(() => setClock(Date.now()), [revision]);
  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 30000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(
      () =>
        setReplay((old) => {
          const next = (old ?? admitted) + Number(speed) * 1000;
          if (next >= Date.now()) {
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
    : (metrics[0]?.value ?? '');
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
    : (groups[0]?.value ?? '');
  const predictions = (data?.predictions ?? [])
    .filter((p) => groupKey(p) === groupId)
    .sort(
      (a, b) =>
        +new Date(a.origin_time) - +new Date(b.origin_time) ||
        b.input_revision - a.input_revision,
    );
  const current = data?.current.find((p) => groupKey(p) === groupId) ?? null;
  const stale =
    !!current && current.input_fingerprint !== data?.inputFingerprint;
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
              (alertOf(current, stale, end) === 'AKI 警告' ? 'warning' : '')
            }
          >
            {error
              ? '读取失败'
              : loading || !data
                ? '正在读取'
                : alertOf(current, stale, end)}
          </span>
          {patient.note && <p className="patient-note">{patient.note}</p>}
          {detail && <small>入 ICU：{fmt(patient.icu_admitted_at)}</small>}
        </div>
      </div>
      <section className="patient-chart">
        <div className="chart-title">
          <h3>观测曲线</h3>
          <div onClick={(e) => e.stopPropagation()}>
            <Choice
              value={metricKey}
              onChange={setMetric}
              options={metrics}
              label="观测指标"
            />
          </div>
        </div>
        <ClinicalChart
          points={observations.map((p) => ({
            t: +new Date(p.measured_at),
            v: p.value,
          }))}
          start={start}
          end={end}
          unit={observations[0]?.unit}
          forecast={
            detail
              ? (trajectory?.points.map((p) => ({
                  t: +new Date(p.time),
                  v: p.value,
                })) ?? [])
              : []
          }
          empty="当前时段没有可用观测"
        />
      </section>
      <section className="patient-chart risk-chart">
        <div className="chart-title">
          <h3>AKI 预测曲线</h3>
          <div onClick={(e) => e.stopPropagation()}>
            <Choice
              value={groupId}
              onChange={setGroup}
              options={groups}
              label="等待模型结果"
            />
          </div>
        </div>
        <ClinicalChart
          points={riskPoints}
          start={start}
          end={end}
          risk
          empty={current ? '当前显示范围内没有预测点' : '模型尚未提供预测结果'}
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
        aria-label={'查看' + patient.name + '的完整详情'}
      >
        {content}
        <div className="row-footer">
          <span>
            <Clock size={12} />
            最近 6 小时 · 测量时间
          </span>
          <span>{error || '点击卡片查看完整历史 →'}</span>
        </div>
      </article>
    );
  return (
    <>
      <div className="panel detail-patient">{content}</div>
      <section className="panel replay-panel">
        <div className="toolbar spread">
          <div>
            <h2>{replay === null ? '实时观察' : '历史回放'}</h2>
            <p className="muted">
              {fmt(start)} — {fmt(end)} · 仅显示截至该时刻已经可用的数据
            </p>
          </div>
          <div className="toolbar">
            <Choice
              value={range}
              onChange={setRange}
              options={[
                { value: '1', label: '显示 1 小时' },
                { value: '6', label: '显示 6 小时' },
                { value: '24', label: '显示 24 小时' },
                { value: 'all', label: '显示全部历史' },
              ]}
              label="曲线范围"
            />
            <Button
              variant="outline"
              onClick={() => {
                setReplay(null);
                setPlaying(false);
                setClock(Date.now());
              }}
            >
              <RotateCcw size={15} />
              回到实时
            </Button>
          </div>
        </div>
        <div className="replay-slider">
          <Button
            variant="ghost"
            aria-label="向前浏览"
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
            aria-label="历史回放截止时间"
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
            aria-label="向后浏览"
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
              {playing ? '暂停' : '播放回放'}
            </Button>
            <Choice
              value={speed}
              onChange={setSpeed}
              options={[
                { value: '60', label: '每秒推进 1 分钟' },
                { value: '300', label: '每秒推进 5 分钟' },
                { value: '900', label: '每秒推进 15 分钟' },
              ]}
              label="回放速度"
            />
          </div>
          <label className="inline-label">
            查看时刻
            <Input
              type="datetime-local"
              value={localTime(end)}
              min={localTime(admitted)}
              max={localTime(clock)}
              onChange={(e) => {
                if (e.target.value) {
                  setPlaying(false);
                  setReplay(
                    Math.min(
                      clock,
                      Math.max(admitted, +new Date(e.target.value)),
                    ),
                  );
                }
              }}
            />
          </label>
        </div>
        <p className="form-help">
          向过去滑动时按时间范围加载。缺少原始可用时间的数据，以本机首次接收时间为准；不会补造历史预测。
        </p>
        {loading && (
          <p role="status" className="muted">
            正在加载所选时段…
          </p>
        )}
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        {data?.truncated && (
          <p className="error-text">
            当前范围超过 24,000
            条记录，画面只包含已加载部分。请缩短显示范围继续浏览；完整记录仍保存在本地。
          </p>
        )}
      </section>
      <div className="detail-grid">
        <section className="panel">
          <div className="panel-heading">
            <h2>预测结果与贡献因素</h2>
            <span className="badge">
              {current ? '外部模型结果' : '模型尚未接入'}
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
                  <span>{alertOf(current, stale, end)}</span>
                </div>
                <p className="muted">{groupLabel(current)}</p>
                <dl className="result-meta">
                  <dt>数据截止</dt>
                  <dd>{fmt(current.data_cutoff)}</dd>
                  <dt>预测起点</dt>
                  <dd>{fmt(current.origin_time)}</dd>
                  <dt>预测窗口结束</dt>
                  <dd>{fmt(current.horizon_end)}</dd>
                  <dt>数据置信度</dt>
                  <dd>
                    {current.data_confidence === null
                      ? '未提供'
                      : (current.data_confidence * 100).toFixed(1) + '%'}
                    {current.confidence_definition && (
                      <small> · {current.confidence_definition}</small>
                    )}
                  </dd>
                  <dt>预警阈值</dt>
                  <dd>
                    {current.threshold
                      ? current.threshold.comparison +
                        ' ' +
                        (current.threshold.value * 100).toFixed(1) +
                        '% · ' +
                        (current.threshold.locked ? '已锁定' : '未锁定')
                      : '尚未选择'}
                  </dd>
                </dl>
                {current.threshold && (
                  <p className="form-help">
                    验证批次：{current.threshold.validation_run_id} · 策略：
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
                          {d.contribution.toLocaleString('zh-CN', {
                            maximumFractionDigits: 4,
                          })}
                        </strong>
                      </div>
                    ))
                  ) : (
                    <p className="muted">模型未提供贡献因素。</p>
                  )}
                </div>
                <p className="form-help">
                  贡献数值按模型原始输出展示，其含义取决于模型解释方法。
                </p>
              </>
            ) : (
              <div className="empty-copy">
                <Activity />
                <p>等待模型结果文件</p>
                <small>当前阶段搭建数据与显示功能，不生成模拟风险概率。</small>
              </div>
            )}
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>数据新鲜度与密度</h2>
            <span className="badge">质量规则待确定</span>
          </div>
          <div className="form-body">
            <p className="form-help">
              以下为数据统计，不等于预测正确率。新鲜度相对于当前查看时刻计算。
            </p>
            {quality?.metrics.length ? (
              <div className="quality-list">
                {quality.metrics.map((q) => (
                  <div key={q.metric + q.unit}>
                    <div>
                      <strong>{q.label}</strong>
                      <small>
                        {q.unit} · 最近测量 {fmt(q.latest)}
                      </small>
                    </div>
                    <div>
                      <strong>{duration(q.age_seconds * 1000)}前</strong>
                      <small>过去 1 小时 {q.last_hour_count} 条</small>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">当前时刻没有可用观测。</p>
            )}
            <p className="form-help">
              尚未确定各变量的期望采样频率和有效期，因此不输出未经验证的综合置信度分数。
            </p>
          </div>
        </section>
      </div>
      <div className="export-row">
        <Button variant="outline" onClick={() => void prepareExport('input')}>
          <Download size={15} />
          导出输入文件
        </Button>
        <Button
          variant="outline"
          onClick={() => void prepareExport('prediction')}
        >
          <Download size={15} />
          导出预测文件
        </Button>
        <span className="muted">
          文件保存在本机；包含患者信息，请自行妥善保管。
        </span>
      </div>
      {exports && (
        <div className="panel form-body">
          <p>
            共 {exports.parts}{' '}
            个分卷，请按序保存和导入。数据变化后请重新生成导出列表。
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
                下载第 {i + 1} 卷
              </a>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
