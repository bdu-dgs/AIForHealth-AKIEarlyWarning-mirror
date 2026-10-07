import { useState } from 'react';
import { Plus, Upload, Save, Trash2, FolderInput } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Choice } from './choice';
import { api, post, nowMs, localTime, type Patient } from '@/lib/api';
type Row = {
  id: string;
  metric: string;
  label: string;
  unit: string;
  value: string;
  time: string;
};
const newRow = (): Row => ({
  id: crypto.randomUUID(),
  metric: '',
  label: '',
  unit: '',
  value: '',
  time: localTime(nowMs()),
});
export function InputPanel({
  patients,
  onSaved,
  onError,
  selected,
}: {
  patients: Patient[];
  onSaved: () => void;
  onError: (e: string) => void;
  selected: string;
}) {
  const [patientId, setPatientId] = useState(
    selected || patients[0]?.patient_id || '',
  );
  const patient = patients.find((p) => p.patient_id === patientId);
  const [rows, setRows] = useState<Row[]>([newRow()]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [actor, setActor] = useState('local-user');
  const [photo, setPhoto] = useState<File | null>(null);
  const [form, setForm] = useState({
    patient_id: '',
    encounter_id: '',
    name: '',
    bed: '',
    note: '',
    icu_admitted_at: localTime(nowMs()),
  });
  const [settings, setSettings] = useState<{
    input_directory: string;
    prediction_directory: string;
  } | null>(null);
  async function perform(action: () => Promise<string>) {
    setBusy(true);
    setMessage('');
    try {
      setMessage(await action());
      onSaved();
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Operation failed');
    } finally {
      setBusy(false);
    }
  }
  function updateRow(id: string, key: keyof Row, value: string) {
    setRows((list) =>
      list.map((r) => (r.id === id ? { ...r, [key]: value } : r)),
    );
  }
  async function importFile(file: File) {
    if (file.size > 16 * 1024 * 1024) {
      onError('File exceeds 16 MB; split it into smaller batches');
      return;
    }
    await perform(async () => {
      const upload = new FormData();
      upload.append('file', file);
      const result = await api<{ inserted: number; patients_changed: number }>(
        '/import-file',
        { method: 'POST', body: upload },
      );
      return (
        'Import complete: ' +
        result.inserted +
        ' new record(s), ' +
        result.patients_changed +
        ' patient(s) updated. Duplicate records are skipped automatically.'
      );
    });
  }
  return (
    <div className="input-layout">
      {message && (
        <div className="notice success" role="status">
          {message}
        </div>
      )}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">PATIENT REGISTRATION</p>
            <h2>Register patient</h2>
          </div>
          <Plus size={19} />
        </div>
        <form
          className="form-body"
          onSubmit={(e) => {
            e.preventDefault();
            void perform(async () => {
              await post('/import', {
                kind: 'input',
                patients: [
                  {
                    ...form,
                    icu_admitted_at: new Date(
                      form.icu_admitted_at,
                    ).toISOString(),
                  },
                ],
                actor,
              });
              let photoMessage = '';
              if (photo) {
                const data = new FormData();
                data.append('photo', photo);
                try {
                  await api('/patients/' + form.patient_id + '/photo', {
                    method: 'POST',
                    body: data,
                  });
                } catch {
                  photoMessage = ', but the photo upload failed; you can upload it again below';
                }
              }
              setPatientId(form.patient_id);
              setForm({
                ...form,
                patient_id: '',
                encounter_id: '',
                name: '',
                bed: '',
                note: '',
              });
              setPhoto(null);
              return 'Patient registered' + photoMessage;
            });
          }}
        >
          <div className="field-grid">
            {(['patient_id', 'encounter_id', 'name', 'bed'] as const).map(
              (key, i) => (
                <label key={key}>
                  {['Patient ID', 'Encounter ID', 'Patient name', 'Bed (optional)'][i]}
                  <Input
                    required={key !== 'bed'}
                    pattern={key.includes('id') ? '[A-Za-z0-9_-]+' : undefined}
                    maxLength={key === 'bed' ? 40 : 80}
                    value={form[key]}
                    onChange={(e) =>
                      setForm({ ...form, [key]: e.target.value })
                    }
                    placeholder={
                      key.includes('id') ? 'Letters, digits, underscores, or hyphens' : ''
                    }
                  />
                </label>
              ),
            )}
            <label>
              ICU admission time
              <Input
                type="datetime-local"
                required
                value={form.icu_admitted_at}
                max={localTime(nowMs())}
                onChange={(e) =>
                  setForm({ ...form, icu_admitted_at: e.target.value })
                }
              />
            </label>
            <label>
              Patient photo (optional)
              <Input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={(e) => setPhoto(e.target.files?.[0] ?? null)}
              />
            </label>
            <label className="span-two">
              Short note
              <Input
                maxLength={500}
                value={form.note}
                onChange={(e) => setForm({ ...form, note: e.target.value })}
                placeholder="Brief note shown on the patient card"
              />
            </label>
          </div>
          <Button type="submit" disabled={busy}>
            <Save size={16} />
            Save patient
          </Button>
        </form>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">CONTINUOUS OBSERVATIONS</p>
            <h2>Add observations</h2>
          </div>
          <span className="badge">Full history kept</span>
        </div>
        <form
          className="form-body"
          onSubmit={(e) => {
            e.preventDefault();
            if (!patient) return;
            void perform(async () => {
              const observations = rows.map((r) => ({
                record_id: r.id,
                patient_id: patient.patient_id,
                encounter_id: patient.encounter_id,
                metric: r.metric,
                label: r.label,
                unit: r.unit,
                value: Number(r.value),
                measured_at: new Date(r.time).toISOString(),
              }));
              await post('/import', { kind: 'input', observations, actor });
              setRows([newRow()]);
              return 'Observations added; the local model processing request was updated.';
            });
          }}
        >
          <div className="field-grid">
            <label>
              Select patient
              <Choice
                value={patientId}
                onChange={setPatientId}
                options={patients.map((p) => ({
                  value: p.patient_id,
                  label: p.name + ' · ' + p.patient_id,
                }))}
                label="Select patient"
              />
            </label>
            <label>
              Entered by (recorded in the background only)
              <Input
                value={actor}
                required
                maxLength={100}
                onChange={(e) => setActor(e.target.value)}
              />
            </label>
          </div>
          <p className="form-help">
            Keep variable codes and units consistent with the model input conventions. Measurement time can be earlier than entry time; the system keeps late-entry information.
          </p>
          <div className="observation-rows">
            {rows.map((r, i) => (
              <div className="observation-row" key={r.id}>
                <span className="row-number">{i + 1}</span>
                <label>
                  Variable code
                  <Input
                    required
                    value={r.metric}
                    onChange={(e) => updateRow(r.id, 'metric', e.target.value)}
                    placeholder="e.g. creatinine"
                  />
                </label>
                <label>
                  Display name
                  <Input
                    required
                    value={r.label}
                    onChange={(e) => updateRow(r.id, 'label', e.target.value)}
                    placeholder="e.g. Creatinine"
                  />
                </label>
                <label>
                  Value
                  <Input
                    required
                    type="number"
                    step="any"
                    value={r.value}
                    onChange={(e) => updateRow(r.id, 'value', e.target.value)}
                  />
                </label>
                <label>
                  Unit
                  <Input
                    value={r.unit}
                    onChange={(e) => updateRow(r.id, 'unit', e.target.value)}
                  />
                </label>
                <label>
                  Measurement time
                  <Input
                    type="datetime-local"
                    required
                    value={r.time}
                    max={localTime(nowMs())}
                    min={
                      patient ? localTime(patient.icu_admitted_at) : undefined
                    }
                    onChange={(e) => updateRow(r.id, 'time', e.target.value)}
                  />
                </label>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={rows.length === 1 || busy}
                  aria-label={'Remove row ' + (i + 1)}
                  onClick={() => setRows(rows.filter((p) => p.id !== r.id))}
                >
                  <Trash2 size={16} />
                </Button>
              </div>
            ))}
          </div>
          <div className="toolbar">
            <Button
              type="button"
              variant="outline"
              onClick={() => setRows([...rows, newRow()])}
              disabled={busy}
            >
              <Plus size={16} />
              Add row
            </Button>
            <Button type="submit" disabled={busy || !patient}>
              <Save size={16} />
              Save observations
            </Button>
          </div>
          {patient && (
            <label className="photo-update">
              Update this patient's photo
              <Input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                disabled={busy}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  void perform(async () => {
                    const data = new FormData();
                    data.append('photo', file);
                    await api('/patients/' + patient.patient_id + '/photo', {
                      method: 'POST',
                      body: data,
                    });
                    return 'Patient photo updated';
                  });
                  e.target.value = '';
                }}
              />
            </label>
          )}
        </form>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">LOCAL FILE EXCHANGE</p>
            <h2>File import and folder watching</h2>
          </div>
          <FolderInput size={20} />
        </div>
        <div className="form-body">
          <p>
            Import a JSON file that follows the data contract or a multi-batch ZIP
            exchange package exported from this site, or write files atomically into the watched folders. Prediction files are generated by a separate model.
          </p>
          <label className="file-drop">
            <Upload size={24} />
            <strong>Choose an input data or prediction result file</strong>
            <Input
              type="file"
              accept=".json,.zip,application/json,application/zip"
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void importFile(f);
                e.target.value = '';
              }}
            />
            <small>Max 16 MB per file · Automatic validation and deduplication · Conflicting records are never overwritten</small>
          </label>
          <Button
            variant="outline"
            onClick={() =>
              void api<{
                input_directory: string;
                prediction_directory: string;
              }>('/settings')
                .then(setSettings)
                .catch((e) => onError(e.message))
            }
          >
            Show local watched folders
          </Button>
          {settings && (
            <dl className="paths">
              <dt>Input observations</dt>
              <dd>{settings.input_directory}</dd>
              <dt>Model output</dt>
              <dd>{settings.prediction_directory}</dd>
            </dl>
          )}
        </div>
      </section>
    </div>
  );
}
