import { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  Plus,
  Search,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  Users,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { PatientView } from '@/components/clinical/patient-view';
import { InputPanel } from '@/components/clinical/input-panel';
import { DatasetPreview } from '@/components/clinical/dataset-preview';
import { Choice } from '@/components/clinical/choice';
import { api, alertOf, type Patient, type Health } from '@/lib/api';
type ModelContext = {
  registerTool: (
    tool: {
      name: string;
      description: string;
      inputSchema: object;
      annotations: object;
      execute: (input: unknown) => unknown;
    },
    options: { signal: AbortSignal },
  ) => void | Promise<void>;
};
export default function App() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState('');
  const [connected, setConnected] = useState(false);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [page, setPage] = useState(0);
  const [route, setRoute] = useState(window.location.pathname);
  const [refresh, setRefresh] = useState(0);
  const navigate = useCallback((path: string) => {
    window.history.pushState({}, '', path);
    setRoute(path);
    window.scrollTo({ top: 0 });
  }, []);
  useEffect(() => {
    const pop = () => setRoute(window.location.pathname);
    window.addEventListener('popstate', pop);
    return () => window.removeEventListener('popstate', pop);
  }, []);
  useEffect(() => {
    const stream = new EventSource('/api/events');
    stream.onopen = () => {
      setConnected(true);
      setRefresh((v) => v + 1);
    };
    stream.onmessage = (e) => {
      try {
        setRevision(JSON.parse(e.data).revision);
      } catch {}
    };
    stream.onerror = () => setConnected(false);
    return () => stream.close();
  }, []);
  useEffect(() => {
    const control = new AbortController();
    void api<Patient[]>('/patients', { signal: control.signal })
      .then(setPatients)
      .catch((e) => {
        if (!control.signal.aborted) setError(e.message);
      });
    return () => control.abort();
  }, [revision, refresh]);
  useEffect(() => {
    let active = true;
    const check = () =>
      void api<Health>('/health')
        .then((h) => {
          if (active) {
            setHealth(h);
            setRevision(h.revision);
          }
        })
        .catch(() => {
          if (active) setHealth(null);
        });
    check();
    const timer = setInterval(check, 10000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    setPage(0);
  }, [search, filter]);
  const input = route === '/input';
  const dataset = route === '/dataset';
  const detailId = route.startsWith('/patients/')
    ? decodeURIComponent(route.slice(10))
    : '';
  const detail = patients.find((p) => p.patient_id === detailId);
  const filtered = patients.filter(
    (p) =>
      (p.name + ' ' + p.patient_id + ' ' + p.bed)
        .toLowerCase()
        .includes(search.toLowerCase()) &&
      (filter === 'all' ||
        (filter === 'warning'
          ? alertOf(p.prediction, p.stale) === 'AKI 警告'
          : !p.prediction || p.stale)),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / 8));
  const currentPage = Math.min(page, pages - 1);
  useEffect(() => {
    const context = (document as Document & { modelContext?: ModelContext })
      .modelContext;
    if (!context?.registerTool) return;
    const life = new AbortController();
    const register = (tool: Parameters<ModelContext['registerTool']>[0]) => {
      try {
        void Promise.resolve(
          context.registerTool(tool, { signal: life.signal }),
        ).catch(() => {});
      } catch {}
    };
    register({
      name: 'read_workbench_status',
      description:
        'Read local connection and model availability; does not return patient identities.',
      inputSchema: {
        type: 'object',
        properties: {},
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true },
      execute: () => ({
        connected,
        patients: patients.length,
        modelStatus: health?.model_status ?? 'offline',
      }),
    });
    register({
      name: 'start_patient_registration',
      description:
        'Open the patient input screen. Does not save or submit patient data.',
      inputSchema: {
        type: 'object',
        properties: {},
        additionalProperties: false,
      },
      annotations: { readOnlyHint: false },
      execute: () => {
        navigate('/input');
        return { screen: 'input', saved: false };
      },
    });
    return () => life.abort();
  }, [connected, patients.length, health?.model_status, navigate]);
  return (
    <div>
      <header className="masthead">
        <button
          className="brand"
          onClick={() => navigate('/')}
          aria-label="返回患者总览"
        >
          <Activity size={28} />
          <div>
            <strong>
              AKI <span>ICU 工作台</span>
            </strong>
            <small>连续观测 · 风险预警</small>
          </div>
        </button>
        <div className="header-status">
          <span className={'connection ' + (!connected ? 'offline' : '')}>
            ●{' '}
            {connected
              ? '本地服务已连接'
              : health
                ? '正在恢复实时连接'
                : '本地服务未连接'}
          </span>
          <small>模型尚未接入</small>
        </div>
      </header>
      <main>
        <div className="page-heading">
          <div>
            <p className="eyebrow">
              {dataset ? 'LOCAL DATASET' : input
                ? 'DATA ENTRY'
                : detailId
                  ? 'PATIENT DETAIL'
                  : 'PATIENT MONITORING'}
            </p>
            <h1>{dataset ? '数据集预览' : input ? '信息录入' : detailId ? '患者详情' : '患者观察'}</h1>
            <p className="muted">
              {dataset ? '按患者匹配本地 CSV，查看入 ICU 前后的测量。' : input
                ? '登记患者、追加观测，或导入本地模型结果。'
                : detailId
                  ? '连续观测与完整历史，按实际可用时间回放。'
                  : '每位患者的观测与预测，在同一张卡片中呈现。'}
            </p>
          </div>
          <div className="toolbar">
            {detailId && (
              <Button variant="outline" onClick={() => navigate('/')}>
                <ArrowLeft size={16} />
                返回总览
              </Button>
            )}
            <Button onClick={() => navigate(input ? '/' : '/input')}>
              {input ? <Users size={16} /> : <Plus size={16} />}{' '}
              {input ? '患者观察' : '录入信息'}
            </Button>
          </div>
        </div>
        <Tabs
          value={dataset ? 'dataset' : input ? 'input' : 'observe'}
          onValueChange={(v) => navigate(v === 'dataset' ? '/dataset' : v === 'input' ? '/input' : '/')}
        >
          <TabsList>
            <TabsTrigger value="observe">观察信息</TabsTrigger>
            <TabsTrigger value="input">输入信息</TabsTrigger>
            <TabsTrigger value="dataset">数据集预览</TabsTrigger>
          </TabsList>
        </Tabs>
        {error && (
          <div className="notice error" role="alert">
            <span>{error}</span>
            <Button
              variant="ghost"
              aria-label="关闭错误提示"
              onClick={() => setError('')}
            >
              <X size={16} />
            </Button>
          </div>
        )}
        {health && health.file_writes_pending > 0 && (
          <div className="notice error">
            数据已入库，但有 {health.file_writes_pending}{' '}
            个批次尚未写入交换文件。系统会自动重试，请检查本地磁盘。
          </div>
        )}
        {health && health.watcher.errors.length > 0 && (
          <div className="notice error" role="status">
            有 {health.watcher.errors.length} 个文件未能导入：
            {health.watcher.errors
              .map((e) => e.message)
              .filter((v, i, a) => a.indexOf(v) === i)
              .join('；')}
            。请在输入信息页核对文件契约。
          </div>
        )}
        {dataset ? <DatasetPreview /> : input ? (
          <InputPanel
            patients={patients}
            selected={detailId}
            onSaved={() => setRefresh((v) => v + 1)}
            onError={setError}
          />
        ) : detailId ? (
          detail ? (
            <PatientView
              key={detail.patient_id}
              patient={detail}
              revision={revision}
              detail
              onError={setError}
            />
          ) : (
            <section className="panel empty-overview">
              <h2>{health ? '患者不存在或尚未加载' : '等待本地服务'}</h2>
              <Button variant="outline" onClick={() => navigate('/')}>
                返回总览
              </Button>
            </section>
          )
        ) : (
          <>
            <div className="overview-heading">
              <h2>
                <Users size={18} />
                患者总览 <span className="count">{patients.length}</span>
              </h2>
              <div className="toolbar">
                <div className="search-box">
                  <Search size={15} />
                  <Input
                    placeholder="搜索姓名、ID 或床位"
                    aria-label="搜索患者"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
                <Choice
                  value={filter}
                  onChange={setFilter}
                  options={[
                    { value: 'all', label: '全部患者' },
                    { value: 'warning', label: '有 AKI 警告' },
                    { value: 'pending', label: '等待结果更新' },
                  ]}
                  label="提醒筛选"
                />
                <Button
                  variant="outline"
                  aria-label="刷新数据"
                  onClick={() => setRefresh((v) => v + 1)}
                >
                  <RefreshCw size={16} />
                </Button>
              </div>
            </div>
            {filtered.length ? (
              <div className="patient-rows">
                {filtered
                  .slice(currentPage * 8, currentPage * 8 + 8)
                  .map((patient) => (
                    <PatientView
                      key={patient.patient_id}
                      patient={patient}
                      revision={revision}
                      onOpen={() => navigate('/patients/' + patient.patient_id)}
                      onError={setError}
                    />
                  ))}
              </div>
            ) : (
              <section className="panel empty-overview">
                <div className="empty-icon">
                  <Users />
                </div>
                <h3>
                  {patients.length ? '没有符合条件的患者' : '从第一位患者开始'}
                </h3>
                <p>
                  {patients.length
                    ? '调整搜索条件或提醒筛选。'
                    : '登记患者后，每张横向卡片都会包含照片、基本信息、观测曲线与预测曲线。'}
                </p>
                <Button
                  variant="outline"
                  onClick={() =>
                    patients.length
                      ? (setSearch(''), setFilter('all'))
                      : navigate('/input')
                  }
                >
                  {patients.length ? '清除筛选' : '登记患者'}
                </Button>
                {!patients.length && (
                  <div className="empty-layout-guide">
                    <div>
                      <strong>患者信息</strong>
                      <span>照片 · 姓名 · ID · 提醒</span>
                    </div>
                    <div>
                      <strong>观测曲线</strong>
                      <span>该患者的近期测量信息</span>
                    </div>
                    <div>
                      <strong>预测曲线</strong>
                      <span>该患者的 AKI 概率与窗口</span>
                    </div>
                  </div>
                )}
              </section>
            )}
            {filtered.length > 0 && (
              <div className="pagination">
                <span>共 {filtered.length} 位 · 每页最多 8 位</span>
                <div className="toolbar">
                  <Button
                    variant="outline"
                    aria-label="上一页"
                    disabled={currentPage === 0}
                    onClick={() => setPage(currentPage - 1)}
                  >
                    <ChevronLeft size={15} />
                  </Button>
                  <span>
                    {currentPage + 1} / {pages}
                  </span>
                  <Button
                    variant="outline"
                    aria-label="下一页"
                    disabled={currentPage === pages - 1}
                    onClick={() => setPage(currentPage + 1)}
                  >
                    <ChevronRight size={15} />
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
        <footer>
          本地课程项目 · 模型与临床有效性尚未验证 · {dataset ? '数据集时间按原文件显示（未提供时区）' : '时间以本机时区显示'}
        </footer>
      </main>
    </div>
  );
}
