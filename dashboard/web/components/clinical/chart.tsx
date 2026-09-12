import { useId, useState } from 'react';
import { Activity } from 'lucide-react';
import { fmt } from '@/lib/api';
export type Point = {
  t: number;
  v: number;
  confidence?: number | null;
  label?: string;
};
// Preserve first/last/min/max in each display bucket; never average away spikes.
function reduce(points: Point[], buckets = 300) {
  if (points.length <= buckets * 4) return points;
  const result: Point[] = [];
  const size = Math.ceil(points.length / buckets);
  for (let i = 0; i < points.length; i += size) {
    const a = points.slice(i, i + size);
    const selected = [
      a[0],
      a[a.length - 1],
      a.reduce((p, q) => (p.v < q.v ? p : q)),
      a.reduce((p, q) => (p.v > q.v ? p : q)),
    ];
    result.push(...Array.from(new Set(selected)).sort((a, b) => a.t - b.t));
  }
  return result;
}
export function ClinicalChart({
  points,
  start,
  end,
  risk = false,
  unit = '',
  empty,
  forecast = [],
  formatTick,
}: {
  points: Point[];
  start: number;
  end: number;
  risk?: boolean;
  unit?: string;
  empty: string;
  forecast?: Point[];
  formatTick?: (time: number) => string;
}) {
  const id = useId().replaceAll(':', '');
  const [hover, setHover] = useState<Point | null>(null);
  if (!points.length && !forecast.length)
    return (
      <div className="empty-chart">
        <Activity />
        <p>{empty}</p>
        <small>
          {risk
            ? '窗口、阈值与贡献因素均由模型提供'
            : '新的观测将按照测量时间追加到历史'}
        </small>
      </div>
    );
  const data = reduce(points);
  const all = [...data, ...forecast];
  const min = risk ? 0 : Math.min(...all.map((p) => p.v));
  const max = risk ? 1 : Math.max(...all.map((p) => p.v));
  const pad = risk
    ? 0
    : Math.max((max - min) * 0.12, Math.abs(max) * 0.02, 0.01);
  const lo = min - pad,
    hi = max + pad;
  const right = Math.max(end, ...forecast.map((p) => p.t));
  const x = (t: number) =>
    58 +
    Math.max(0, Math.min(1, (t - start) / Math.max(1, right - start))) * 682;
  const y = (v: number) => 218 - ((v - lo) / Math.max(0.00001, hi - lo)) * 186;
  const value = (v: number) =>
    risk
      ? (v * 100).toFixed(1) + '%'
      : v.toLocaleString('zh-CN', { maximumFractionDigits: 3 }) +
        (unit ? ' ' + unit : '');
  return (
    <div className="chart-wrap">
      <svg
        viewBox="0 0 770 262"
        role="img"
        aria-label={
          risk
            ? 'AKI 概率随预测起点变化，高处红色低处绿色'
            : '按测量时间排列的观测曲线，点间连线仅辅助阅读'
        }
      >
        <defs>
          <linearGradient
            id={id}
            gradientUnits="userSpaceOnUse"
            x1="0"
            y1="32"
            x2="0"
            y2="218"
          >
            <stop offset="0" stopColor="#cf424d" />
            <stop offset=".5" stopColor="#bd8631" />
            <stop offset="1" stopColor="#239478" />
          </linearGradient>
        </defs>
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <g key={v}>
            <line
              x1="58"
              x2="740"
              y1={32 + 186 * v}
              y2={32 + 186 * v}
              stroke="#e9eef1"
              strokeDasharray="3 4"
            />
            <text
              x="49"
              y={36 + 186 * v}
              textAnchor="end"
              className="axis-text"
            >
              {risk
                ? Math.round(100 * (1 - v)) + '%'
                : (hi - (hi - lo) * v).toLocaleString('zh-CN', {
                    maximumFractionDigits: 2,
                  })}
            </text>
          </g>
        ))}
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <text
            key={v}
            x={58 + 682 * v}
            y="248"
            textAnchor="middle"
            className="axis-text"
          >
            {formatTick ? formatTick(start + (right - start) * v) : new Date(start + (right - start) * v).toLocaleTimeString('zh-CN', {
              hour: '2-digit',
              minute: '2-digit',
            })}
          </text>
        ))}
        {risk ? (
          <>
            {data.slice(1).map((p, i) => (
              <line
                key={i}
                x1={x(data[i].t)}
                y1={y(data[i].v)}
                x2={x(p.t)}
                y2={y(p.v)}
                stroke={'url(#' + id + ')'}
                strokeWidth="3"
                opacity={0.55 + 0.45 * (p.confidence ?? 0.3)}
              />
            ))}
            {data.map((p, i) => (
              <circle
                key={i}
                cx={x(p.t)}
                cy={y(p.v)}
                r={3 + (p.confidence ?? 0) * 1.5}
                fill={'url(#' + id + ')'}
                stroke="white"
                opacity={0.55 + 0.45 * (p.confidence ?? 0.3)}
              />
            ))}
          </>
        ) : (
          <polyline
            points={data.map((p) => x(p.t) + ',' + y(p.v)).join(' ')}
            fill="none"
            stroke="#248b88"
            strokeWidth="2"
          />
        )}
        {forecast.length > 0 && (
          <polyline
            points={forecast.map((p) => x(p.t) + ',' + y(p.v)).join(' ')}
            fill="none"
            stroke="#6682c2"
            strokeWidth="2"
            strokeDasharray="6 4"
          />
        )}
        {data.length === 1 && !risk && (
          <circle cx={x(data[0].t)} cy={y(data[0].v)} r="4" fill="#248b88" />
        )}
        {all.map((p, i) => (
          <circle
            key={'hit' + i}
            cx={x(p.t)}
            cy={y(p.v)}
            r="9"
            fill="transparent"
            onMouseEnter={() => setHover(p)}
            onMouseLeave={() => setHover(null)}
          >
            <title>{(p.label ?? fmt(p.t)) + ' · ' + value(p.v)}</title>
          </circle>
        ))}
      </svg>
      <div className="chart-caption">
        {hover ? (
          (hover.label ?? fmt(hover.t)) + ' · ' + value(hover.v)
        ) : (
          <>
            <span className="legend-dot" />
            {risk
              ? '每点表示一个预测起点的概率；显眼程度参考所提供的数据置信度'
              : '实测数据 · 连线不代表期间存在测量'}
            {forecast.length > 0 ? '　┄ 模型预测轨迹' : ''}
          </>
        )}
      </div>
    </div>
  );
}
