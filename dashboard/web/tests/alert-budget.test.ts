import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { alertOf, type Prediction } from '../lib/api.ts';
import { ALERT_SETTINGS, POLICY_VERSION, withAlertSetting } from '../lib/alert-budget.ts';

const reference = Date.parse('2020-01-01T01:00:00Z');
const prediction = (risk24: number, risk12: number, policy = POLICY_VERSION) =>
  ({
    risk: risk24,
    origin_time: '2020-01-01T00:59:00Z',
    horizon_end: '2020-01-02T00:59:00Z',
    target: 'AKI',
    model_id: 'synthetic',
    model_version: 'fixture',
    threshold: {
      value: 0.21,
      comparison: '>=',
      locked: true,
      policy_version: policy,
      confirmation: { horizon_h: 12, value: 0.105, risk: risk12 },
    },
  }) as Prediction;
const setting = (id: string) => ALERT_SETTINGS.find((s) => s.id === id)!;

test('the locked policy setting leaves every prediction unchanged', () => {
  const p = prediction(0.22, 0.11);
  assert.equal(withAlertSetting(p, setting('policy')), p);
  assert.equal(withAlertSetting(null, setting('0.2')), null);
});

test('a budget replaces the threshold and keeps the 12 h confirmation at half of it', () => {
  const p = withAlertSetting(prediction(0.25, 0.12), setting('0.2'));
  assert.equal(p.threshold!.value, 0.24);
  assert.ok(Math.abs(p.threshold!.confirmation!.value - 0.12) < 1e-12);
  assert.equal(alertOf(p, false, reference), 'AKI warning');
  // Same risks: a tighter budget (28.5%) silences the warning, a looser one (13.5%) keeps it.
  assert.equal(alertOf(withAlertSetting(prediction(0.25, 0.12), setting('0.15')), false, reference), 'Below threshold');
  assert.equal(alertOf(withAlertSetting(prediction(0.15, 0.07), setting('0.4')), false, reference), 'AKI warning');
  assert.equal(alertOf(prediction(0.15, 0.07), false, reference), 'Below threshold');
});

test('thresholds of other policies and unlocked thresholds are never replaced', () => {
  const other = prediction(0.22, 0.11, 'some-other-policy');
  assert.equal(withAlertSetting(other, setting('0.15')), other);
  const unlocked = { ...prediction(0.22, 0.11) };
  unlocked.threshold = { ...unlocked.threshold!, locked: false };
  assert.equal(withAlertSetting(unlocked, setting('0.15')), unlocked);
});

test('displayed rates match the alert-budget-v1 report tables', () => {
  const dir = new URL('../../../artifacts/reports/alert_budget_v1/', import.meta.url);
  const rows = (name: string) => {
    const [head, ...lines] = readFileSync(new URL(name, dir), 'utf8').trim().split(/\r?\n/);
    const keys = head.split(',');
    return lines.map((line) => Object.fromEntries(line.split(',').map((v, i) => [keys[i], v])));
  };
  const close = (a: number, b: string, digits: number) =>
    assert.ok(Math.abs(a - Number(b)) <= 0.5 * 10 ** -digits + 1e-9, a + ' vs ' + b);
  const check = (s: (typeof ALERT_SETTINGS)[number], row: Record<string, string>) => {
    close(s.threshold, row.threshold, 3);
    close(s.falseAlertsPerPatientDay, row.false_alerts_per_patient_day, 3);
    close(s.detection, row.event_detection_rate, 3);
    close(s.ppv, row.alert_ppv, 3);
    close(s.leadHours, row.lead_time_median_h, 1);
  };
  const policy = rows('results.csv').find((r) => r.setting === 'in_distribution' && r.method === 'M0_fixed')!;
  check(setting('policy'), policy);
  const sweep = rows('budget_sweep.csv').filter((r) => r.setting === 'in_distribution' && r.method === 'M1_budget');
  assert.equal(sweep.length, ALERT_SETTINGS.length - 1);
  for (const row of sweep) check(ALERT_SETTINGS.find((s) => s.budget === Number(row.budget))!, row);
});
