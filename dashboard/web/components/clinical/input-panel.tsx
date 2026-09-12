import { useState } from 'react';
import { Plus, Upload, Save, Trash2, FolderInput } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Choice } from './choice';
import { api, post, localTime, type Patient } from '@/lib/api';
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
  time: localTime(new Date()),
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
    icu_admitted_at: localTime(new Date()),
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
      onError(e instanceof Error ? e.message : '操作失败');
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
      onError('文件超过 16 MB，请拆分批次');
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
        '导入完成：新增 ' +
        result.inserted +
        ' 条记录，更新 ' +
        result.patients_changed +
        ' 位患者。重复记录会自动跳过。'
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
            <h2>登记患者</h2>
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
                  photoMessage = '，但照片上传失败，可在下方重新上传';
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
              return '患者已登记' + photoMessage;
            });
          }}
        >
          <div className="field-grid">
            {(['patient_id', 'encounter_id', 'name', 'bed'] as const).map(
              (key, i) => (
                <label key={key}>
                  {['患者 ID', '本次住院 ID', '患者姓名', '床位（可选）'][i]}
                  <Input
                    required={key !== 'bed'}
                    pattern={key.includes('id') ? '[A-Za-z0-9_-]+' : undefined}
                    maxLength={key === 'bed' ? 40 : 80}
                    value={form[key]}
                    onChange={(e) =>
                      setForm({ ...form, [key]: e.target.value })
                    }
                    placeholder={
                      key.includes('id') ? '字母、数字、下划线或连字符' : ''
                    }
                  />
                </label>
              ),
            )}
            <label>
              入 ICU 时间
              <Input
                type="datetime-local"
                required
                value={form.icu_admitted_at}
                max={localTime(new Date())}
                onChange={(e) =>
                  setForm({ ...form, icu_admitted_at: e.target.value })
                }
              />
            </label>
            <label>
              患者照片（可选）
              <Input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={(e) => setPhoto(e.target.files?.[0] ?? null)}
              />
            </label>
            <label className="span-two">
              简短提醒
              <Input
                maxLength={500}
                value={form.note}
                onChange={(e) => setForm({ ...form, note: e.target.value })}
                placeholder="供患者卡片展示的简短说明"
              />
            </label>
          </div>
          <Button type="submit" disabled={busy}>
            <Save size={16} />
            保存患者
          </Button>
        </form>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">CONTINUOUS OBSERVATIONS</p>
            <h2>追加观测信息</h2>
          </div>
          <span className="badge">保留全部历史</span>
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
              return '观测已追加，本地模型处理请求已更新。';
            });
          }}
        >
          <div className="field-grid">
            <label>
              选择患者
              <Choice
                value={patientId}
                onChange={setPatientId}
                options={patients.map((p) => ({
                  value: p.patient_id,
                  label: p.name + ' · ' + p.patient_id,
                }))}
                label="选择患者"
              />
            </label>
            <label>
              录入者（仅在后台记录）
              <Input
                value={actor}
                required
                maxLength={100}
                onChange={(e) => setActor(e.target.value)}
              />
            </label>
          </div>
          <p className="form-help">
            变量代码、单位与模型输入约定保持一致。测量时间可以早于录入时间；系统保留补录信息。
          </p>
          <div className="observation-rows">
            {rows.map((r, i) => (
              <div className="observation-row" key={r.id}>
                <span className="row-number">{i + 1}</span>
                <label>
                  变量代码
                  <Input
                    required
                    value={r.metric}
                    onChange={(e) => updateRow(r.id, 'metric', e.target.value)}
                    placeholder="例如 creatinine"
                  />
                </label>
                <label>
                  显示名称
                  <Input
                    required
                    value={r.label}
                    onChange={(e) => updateRow(r.id, 'label', e.target.value)}
                    placeholder="例如 肌酐"
                  />
                </label>
                <label>
                  数值
                  <Input
                    required
                    type="number"
                    step="any"
                    value={r.value}
                    onChange={(e) => updateRow(r.id, 'value', e.target.value)}
                  />
                </label>
                <label>
                  单位
                  <Input
                    value={r.unit}
                    onChange={(e) => updateRow(r.id, 'unit', e.target.value)}
                  />
                </label>
                <label>
                  测量时间
                  <Input
                    type="datetime-local"
                    required
                    value={r.time}
                    max={localTime(new Date())}
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
                  aria-label={'移除第' + (i + 1) + '行'}
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
              增加一条
            </Button>
            <Button type="submit" disabled={busy || !patient}>
              <Save size={16} />
              保存观测
            </Button>
          </div>
          {patient && (
            <label className="photo-update">
              更新这位患者的照片
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
                    return '患者照片已更新';
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
            <h2>文件导入与目录监听</h2>
          </div>
          <FolderInput size={20} />
        </div>
        <div className="form-body">
          <p>
            导入符合数据契约的 JSON 文件或本站导出的 ZIP
            分批交换包，或将文件原子写入监听目录。预测文件由独立模型生成。
          </p>
          <label className="file-drop">
            <Upload size={24} />
            <strong>选择输入数据或预测结果文件</strong>
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
            <small>单文件最大 16 MB · 自动校验与去重 · 不会覆盖冲突记录</small>
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
            显示本地监听目录
          </Button>
          {settings && (
            <dl className="paths">
              <dt>输入观测</dt>
              <dd>{settings.input_directory}</dd>
              <dt>模型输出</dt>
              <dd>{settings.prediction_directory}</dd>
            </dl>
          )}
        </div>
      </section>
    </div>
  );
}
