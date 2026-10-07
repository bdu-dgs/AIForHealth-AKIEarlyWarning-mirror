import { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  Plus,
  Search,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  FastForward,
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
import {
  api,
  post,
  fmt,
  alertOf,
  setServiceClock,
  type Clock,
  type Patient,
  type Health,
} from '@/lib/api';
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
  // Views reload on viewRevision, which follows revision only after the service clock has been refreshed.
  const [viewRevision, setViewRevision] = useState(0);
  const [advancing, setAdvancing] = useState(false);
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
  }, [viewRevision, refresh]);
  useEffect(() => {
    let active = true;
    void api<Health>('/health')
      .then((h) => {
        if (!active) return;
        setServiceClock(h.clock);
        setHealth(h);
        setViewRevision(h.revision);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [revision]);
  const advance = async (hours: number) => {
    setAdvancing(true);
    try {
      setServiceClock(await post<Clock>('/demo/advance?hours=' + hours, {}));
      const h = await api<Health>('/health');
      setHealth(h);
      setViewRevision(h.revision);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not advance the demo clock');
    } finally {
      setAdvancing(false);
    }
  };
  const demo = health?.clock.mode === 'demo' ? health.clock : null;
  const modelText =
    health?.model_status === 'running'
      ? 'Model running · ' + health.model?.policy_version
      : health?.model_status === 'error'
        ? 'Model error · ' + (health.model?.detail ?? '')
        : health?.model_status === 'offline'
          ? 'Model offline'
          : 'Model not connected';
  useEffect(() => {
    let active = true;
    const check = () =>
      void api<Health>('/health')
        .then((h) => {
          if (active) {
            setServiceClock(h.clock);
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
          ? alertOf(p.prediction, p.stale) === 'AKI warning'
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
          aria-label="Back to patient overview"
        >
          <Activity size={28} />
          <div>
            <strong>
              AKI <span>ICU Workbench</span>
            </strong>
            <small>Continuous monitoring · Risk alerts</small>
          </div>
        </button>
        <div className="header-status">
          <span className={'connection ' + (!connected ? 'offline' : '')}>
            ●{' '}
            {connected
              ? 'Local service connected'
              : health
                ? 'Restoring live connection'
                : 'Local service not connected'}
          </span>
          <small title={health?.model?.detail || undefined}>{modelText}</small>
          {demo && (
            <div className="demo-clock">
              <small>
                Demo clock · ICU hour {Math.round(demo.hours_elapsed ?? 0)} ·{' '}
                {fmt(demo.now)}
              </small>
              <Button
                size="sm"
                variant="outline"
                disabled={advancing}
                onClick={() => void advance(1)}
                aria-label="Advance the demo clock by 1 hour"
              >
                <FastForward size={14} />
                +1 h
              </Button>
            </div>
          )}
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
            <h1>{dataset ? 'Dataset preview' : input ? 'Data entry' : detailId ? 'Patient detail' : 'Patient monitoring'}</h1>
            <p className="muted">
              {dataset ? 'Match local CSV files by patient and review measurements before and after ICU admission.' : input
                ? 'Register patients, add observations, or import local model results.'
                : detailId
                  ? 'Continuous observations and full history, replayed by actual availability time.'
                  : 'Observations and predictions for each patient, shown together on one card.'}
            </p>
          </div>
          <div className="toolbar">
            {detailId && (
              <Button variant="outline" onClick={() => navigate('/')}>
                <ArrowLeft size={16} />
                Back to overview
              </Button>
            )}
            <Button onClick={() => navigate(input ? '/' : '/input')}>
              {input ? <Users size={16} /> : <Plus size={16} />}{' '}
              {input ? 'Patient monitoring' : 'Enter data'}
            </Button>
          </div>
        </div>
        <Tabs
          value={dataset ? 'dataset' : input ? 'input' : 'observe'}
          onValueChange={(v) => navigate(v === 'dataset' ? '/dataset' : v === 'input' ? '/input' : '/')}
        >
          <TabsList>
            <TabsTrigger value="observe">Monitoring</TabsTrigger>
            <TabsTrigger value="input">Data entry</TabsTrigger>
            <TabsTrigger value="dataset">Dataset preview</TabsTrigger>
          </TabsList>
        </Tabs>
        {error && (
          <div className="notice error" role="alert">
            <span>{error}</span>
            <Button
              variant="ghost"
              aria-label="Dismiss error"
              onClick={() => setError('')}
            >
              <X size={16} />
            </Button>
          </div>
        )}
        {health && health.file_writes_pending > 0 && (
          <div className="notice error">
            Data was saved, but {health.file_writes_pending}{' '}
            batch(es) have not been written to exchange files yet. The system will retry automatically; check the local disk.
          </div>
        )}
        {health && health.watcher.errors.length > 0 && (
          <div className="notice error" role="status">
            {health.watcher.errors.length} file(s) could not be imported:{' '}
            {health.watcher.errors
              .map((e) => e.message)
              .filter((v, i, a) => a.indexOf(v) === i)
              .join('; ')}
            . Check the file contract on the Data entry tab.
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
              revision={viewRevision}
              detail
              onError={setError}
            />
          ) : (
            <section className="panel empty-overview">
              <h2>{health ? 'Patient not found or not yet loaded' : 'Waiting for local service'}</h2>
              <Button variant="outline" onClick={() => navigate('/')}>
                Back to overview
              </Button>
            </section>
          )
        ) : (
          <>
            <div className="overview-heading">
              <h2>
                <Users size={18} />
                Patient overview <span className="count">{patients.length}</span>
              </h2>
              <div className="toolbar">
                <div className="search-box">
                  <Search size={15} />
                  <Input
                    placeholder="Search name, ID, or bed"
                    aria-label="Search patients"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
                <Choice
                  value={filter}
                  onChange={setFilter}
                  options={[
                    { value: 'all', label: 'All patients' },
                    { value: 'warning', label: 'With AKI warning' },
                    { value: 'pending', label: 'Awaiting result update' },
                  ]}
                  label="Alert filter"
                />
                <Button
                  variant="outline"
                  aria-label="Refresh data"
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
                      revision={viewRevision}
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
                  {patients.length ? 'No matching patients' : 'Start with your first patient'}
                </h3>
                <p>
                  {patients.length
                    ? 'Adjust the search or alert filter.'
                    : 'After you register a patient, each card shows the photo, basic details, observation chart, and prediction chart.'}
                </p>
                <Button
                  variant="outline"
                  onClick={() =>
                    patients.length
                      ? (setSearch(''), setFilter('all'))
                      : navigate('/input')
                  }
                >
                  {patients.length ? 'Clear filters' : 'Register patient'}
                </Button>
                {!patients.length && (
                  <div className="empty-layout-guide">
                    <div>
                      <strong>Patient information</strong>
                      <span>Photo · Name · ID · Alert</span>
                    </div>
                    <div>
                      <strong>Observation chart</strong>
                      <span>Recent measurements for this patient</span>
                    </div>
                    <div>
                      <strong>Prediction chart</strong>
                      <span>AKI probability and window for this patient</span>
                    </div>
                  </div>
                )}
              </section>
            )}
            {filtered.length > 0 && (
              <div className="pagination">
                <span>{filtered.length} total · up to 8 per page</span>
                <div className="toolbar">
                  <Button
                    variant="outline"
                    aria-label="Previous page"
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
                    aria-label="Next page"
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
          Local course project · Model and clinical validity not yet verified · {dataset ? 'Dataset times shown as in source files (no timezone provided)' : 'Times shown in the local timezone'}
        </footer>
      </main>
    </div>
  );
}
