export type Patient = {
  patient_id: string;
  encounter_id: string;
  name: string;
  bed: string;
  note: string;
  icu_admitted_at: string;
  input_revision: number;
  input_fingerprint: string;
  photo: string | null;
  latest_measurement?: string;
  prediction?: Prediction | null;
  stale?: boolean;
};
export type Observation = {
  record_id: string;
  revision: number;
  patient_id: string;
  encounter_id: string;
  metric: string;
  label: string;
  unit: string;
  value: number;
  measured_at: string;
  available_at: string;
  availability_basis: string;
};
export type Prediction = {
  record_id: string;
  revision: number;
  patient_id: string;
  encounter_id: string;
  input_revision: number;
  input_fingerprint: string;
  model_id: string;
  model_version: string;
  target: string;
  origin_time: string;
  data_cutoff: string;
  horizon_end: string;
  generated_at: string;
  available_at: string;
  risk: number;
  data_confidence: number | null;
  confidence_definition: string | null;
  threshold: null | {
    value: number;
    comparison: string;
    locked: boolean;
    validation_run_id: string;
    policy_version: string;
    monitoring_window: string;
    selection_basis: string;
    stability_assessment_id: string | null;
  };
  drivers: {
    feature: string;
    label: string;
    contribution: number;
    value: string;
    unit: string;
  }[];
  trajectories: {
    metric: string;
    label: string;
    unit: string;
    points: {
      time: string;
      value: number;
      lower: number | null;
      upper: number | null;
    }[];
  }[];
  stability_assessment_id: string | null;
};
export type Quality = {
  metrics: {
    metric: string;
    label: string;
    unit: string;
    count: number;
    last_hour_count: number;
    latest: string;
    age_seconds: number;
  }[];
  score: null;
  score_status: string;
  reference_time: string;
};
export type Health = {
  status: string;
  revision: number;
  model_status: string;
  watcher: { running: boolean; errors: { file: string; message: string }[] };
  file_writes_pending: number;
};
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api' + path, init);
  if (!response.ok) {
    let message = '本地服务请求失败';
    try {
      const body = (await response.json()) as { detail: unknown };
      message =
        typeof body.detail === 'string'
          ? body.detail
          : JSON.stringify(body.detail);
    } catch {}
    throw new Error(message);
  }
  return response.json();
}
export function post<T>(path: string, body: unknown) {
  return api<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
export function localTime(value: string | number | Date) {
  const d = new Date(value);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
export function fmt(value: string | number) {
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}
export function duration(ms: number) {
  if (ms < 3600000)
    return (
      (ms / 60000).toLocaleString('zh-CN', { maximumFractionDigits: 1 }) +
      ' 分钟'
    );
  if (ms < 86400000)
    return (
      (ms / 3600000).toLocaleString('zh-CN', { maximumFractionDigits: 1 }) +
      ' 小时'
    );
  return (
    (ms / 86400000).toLocaleString('zh-CN', { maximumFractionDigits: 1 }) +
    ' 天'
  );
}
export function groupKey(p: Prediction) {
  return JSON.stringify([
    p.model_id,
    p.model_version,
    p.target,
    new Date(p.horizon_end).getTime() - new Date(p.origin_time).getTime(),
  ]);
}
export function groupLabel(p: Prediction) {
  return (
    p.target +
    ' · 未来 ' +
    duration(+new Date(p.horizon_end) - +new Date(p.origin_time)) +
    ' · ' +
    p.model_id +
    '/' +
    p.model_version
  );
}
export function alertOf(
  p?: Prediction | null,
  stale = false,
  referenceTime = Date.now(),
) {
  if (!p) return '等待模型结果';
  if (stale) return '数据已更新 · 结果待更新';
  if (+new Date(p.horizon_end) <= referenceTime) return '预测窗口已结束';
  if (!p.threshold?.locked) return '阈值未锁定';
  const hit =
    p.threshold.comparison === '>'
      ? p.risk > p.threshold.value
      : p.risk >= p.threshold.value;
  return hit ? 'AKI 警告' : '未触发阈值';
}
export async function history<T>(
  id: string,
  kind: string,
  start: number,
  end: number,
  asOf: number,
  signal: AbortSignal,
) {
  const items: T[] = [];
  let offset: number | null = 0;
  const cap = 24000;
  do {
    const q: URLSearchParams = new URLSearchParams({
      kind,
      start: new Date(start).toISOString(),
      end: new Date(end).toISOString(),
      as_of: new Date(asOf).toISOString(),
      offset: String(offset),
      limit: '3000',
    });
    const page: { items: T[]; next_offset: number | null } = await api(
      '/patients/' + id + '/history?' + q,
      { signal },
    );
    items.push(...page.items);
    offset = page.next_offset;
  } while (offset !== null && items.length < cap);
  return { items, truncated: offset !== null };
}
