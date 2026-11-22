import { useSyncExternalStore } from 'react';
import { Choice } from './choice';
import {
  ALERT_SETTINGS,
  getAlertSetting,
  setAlertSetting,
  settingLabel,
  subscribeAlertSetting,
} from '@/lib/alert-budget';

/** The selected unit alert setting; components that call this re-render when it changes. */
export function useAlertSetting() {
  return useSyncExternalStore(subscribeAlertSetting, getAlertSetting, getAlertSetting);
}

const pct = (v: number) => (v * 100).toFixed(1).replace(/\.0$/, '') + '%';

export function AlertBudgetPanel() {
  const s = useAlertSetting();
  return (
    <section className="panel budget-panel" aria-label="Unit alert setting">
      <div className="budget-head">
        <div>
          <h2>Unit alert setting</h2>
          <p className="muted">
            Choose how many false alerts the unit accepts. The threshold is the most sensitive one certified on
            validation patients to stay within that budget with 95% confidence (Learn-then-Test).
          </p>
        </div>
        <Choice
          value={s.id}
          onChange={setAlertSetting}
          options={ALERT_SETTINGS.map((o) => ({ value: o.id, label: settingLabel(o) }))}
          label="False-alert budget"
        />
      </div>
      <dl className="budget-stats">
        <div>
          <dt>Warning rule</dt>
          <dd>
            24 h risk ≥ {pct(s.threshold)}
            <small>and 12 h risk ≥ {pct(s.threshold / 2)}</small>
          </dd>
        </div>
        <div>
          <dt>False alerts per patient-day</dt>
          <dd>
            {s.falseAlertsPerPatientDay.toFixed(3)}
            <small>{s.budget === null ? 'no stated budget' : 'budget ' +
                  s.budget.toFixed(2) +
                  (s.falseAlertsPerPatientDay <= s.budget ? ' · met' : ' · exceeded')}</small>
          </dd>
        </div>
        <div>
          <dt>AKI events warned</dt>
          <dd>
            {pct(s.detection)}
            <small>0-24 h before onset</small>
          </dd>
        </div>
        <div>
          <dt>Alerts followed by AKI</dt>
          <dd>
            {pct(s.ppv)}
            <small>median warning {s.leadHours.toFixed(1)} h ahead</small>
          </dd>
        </div>
      </dl>
      <p className="form-help">
        Rates are from the held-out test split (4,959 ICU patients, 959 AKI events). Changing the setting re-applies the
        rule to the patients shown here; stored predictions and exported files keep the locked policy v3.
      </p>
    </section>
  );
}
