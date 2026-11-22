/**
 * Unit alert setting: the locked alert policy v3, or a false-alert budget from the alert-budget control study
 * (alert-budget-v1, docs/FINAL-ANALYSES.md section 3.2).
 *
 * The rule shape never changes: warn when the 24 h risk >= t and the 12 h risk >= t / 2. Policy v3 uses t = 21%.
 * For a budget B (false alerts per patient-day), t is the most sensitive threshold that Learn-then-Test testing
 * certified on the validation patients to stay within B with 95% confidence (method M1, locked model). The rates
 * below are that threshold's results on the 4,959 test patients (959 AKI events), copied from
 * artifacts/reports/alert_budget_v1/results.csv (policy v3) and budget_sweep.csv (budgets); tests/alert-budget.test.ts
 * checks them against those files. The setting changes only how this browser displays warnings; stored predictions
 * and exported files keep the locked policy.
 */
import type { Prediction } from './api.ts';

export type AlertSetting = {
  id: string;
  budget: number | null; // null = locked policy v3
  threshold: number; // 24 h risk threshold t (the 12 h confirmation is t / 2)
  falseAlertsPerPatientDay: number;
  detection: number; // share of AKI events warned 0-24 h before onset
  ppv: number; // share of alerts that were followed by AKI
  leadHours: number; // median warning time before onset
};

export const POLICY_VERSION = 'validation-policy-v3';

export const ALERT_SETTINGS: AlertSetting[] = [
  { id: 'policy', budget: null, threshold: 0.21, falseAlertsPerPatientDay: 0.227, detection: 0.717, ppv: 0.335, leadHours: 11.6 },
  { id: '0.15', budget: 0.15, threshold: 0.285, falseAlertsPerPatientDay: 0.139, detection: 0.615, ppv: 0.403, leadHours: 11.0 },
  { id: '0.2', budget: 0.2, threshold: 0.24, falseAlertsPerPatientDay: 0.185, detection: 0.682, ppv: 0.366, leadHours: 11.1 },
  { id: '0.25', budget: 0.25, threshold: 0.205, falseAlertsPerPatientDay: 0.234, detection: 0.721, ppv: 0.33, leadHours: 11.7 },
  { id: '0.3', budget: 0.3, threshold: 0.18, falseAlertsPerPatientDay: 0.277, detection: 0.765, ppv: 0.31, leadHours: 12.1 },
  { id: '0.4', budget: 0.4, threshold: 0.135, falseAlertsPerPatientDay: 0.378, detection: 0.833, ppv: 0.269, leadHours: 12.5 },
];

export function settingLabel(s: AlertSetting) {
  const t = (s.threshold * 100).toFixed(1).replace(/\.0$/, '') + '%';
  return s.budget === null
    ? 'Locked policy v3 · threshold ' + t
    : 'Budget ' + s.budget.toFixed(2) + ' false alerts / patient-day · threshold ' + t;
}

function initial(): AlertSetting {
  try {
    const budget = new URLSearchParams(globalThis.location?.search ?? '').get('budget');
    return ALERT_SETTINGS.find((s) => s.budget !== null && s.budget === Number(budget)) ?? ALERT_SETTINGS[0];
  } catch {
    return ALERT_SETTINGS[0];
  }
}

let selected = initial();
const listeners = new Set<() => void>();

export function getAlertSetting() {
  return selected;
}
export function setAlertSetting(id: string) {
  const next = ALERT_SETTINGS.find((s) => s.id === id);
  if (!next || next === selected) return;
  selected = next;
  for (const listener of listeners) listener();
}
export function subscribeAlertSetting(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * The prediction as the selected setting would alert on it. Only locked policy-v3 thresholds are replaced; the
 * confirmation threshold keeps its ratio to the main threshold (12 h >= t / 2).
 */
export function withAlertSetting<T extends Prediction | null | undefined>(p: T, s: AlertSetting = selected): T {
  const threshold = p?.threshold;
  if (!p || !threshold?.locked || s.budget === null || threshold.policy_version !== POLICY_VERSION) return p;
  const scale = s.threshold / threshold.value;
  const confirmation = threshold.confirmation;
  return {
    ...p,
    threshold: {
      ...threshold,
      value: s.threshold,
      confirmation: confirmation && { ...confirmation, value: confirmation.value * scale },
      policy_version: 'alert-budget-v1 · budget ' + s.budget.toFixed(2),
      validation_run_id: 'alert-budget-v1',
      selection_basis:
        'Policy v3 rule with a budget threshold: the most sensitive threshold that Learn-then-Test testing certified on ' +
        'validation patients to stay within ' +
        s.budget.toFixed(2) +
        ' false alerts per patient-day with 95% confidence. Test split: ' +
        s.falseAlertsPerPatientDay.toFixed(3) +
        ' false alerts per patient-day, ' +
        (s.detection * 100).toFixed(1) +
        '% of AKI events warned. Display setting only; not a clinical threshold.',
    },
  };
}
