"""Alert policies on hourly risk, and their event-level metrics.

Simulation is deployment-like: the policy runs on every hourly prediction inside the monitoring window, whether or
not the row has a label. Metrics then use the outcome:
- correct alert: AKI onset within (t, t + horizon];
- false alert: label 0 at t (no AKI within the horizon, follow-up complete);
- unlabelled alert: neither (follow-up ended before the horizon without AKI) - counted in the alert burden only.
An AKI event is a patient whose first onset is within the horizon of a monitored hour; it is detected when it had at
least one correct alert. Lead time = onset - first correct alert.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Policy:
    sustain_hours: int = 1          # risk >= threshold for this many consecutive hourly predictions
    cooldown_hours: float = 12      # no repeat alert within this many hours of the previous one
    rise_ratio: float | None = None  # also require risk >= ratio x the minimum risk of the prior `rise_window_hours`
    rise_window_hours: int = 12

    @property
    def name(self):
        rise = f", rise x{self.rise_ratio:g}/{self.rise_window_hours}h" if self.rise_ratio else ""
        return f"sustain {self.sustain_hours}h, cooldown {self.cooldown_hours:g}h{rise}"


def prepare(df, horizon_h):
    """Sort by (patient, hour) and add hours_to_onset (NaN without onset)."""
    df = df.sort_values(["patient_id", "hours_since_icu"]).reset_index(drop=True)
    df["hours_to_onset"] = (df["creatinine_aki_onset_time"] - df["snapshot_time"]) / pd.Timedelta(hours=1)
    df.attrs["horizon_h"] = horizon_h
    return df


def prior_min(df, window_hours):
    """Minimum risk over the previous `window_hours` hourly predictions of the same patient (NaN at the first)."""
    prev = df.groupby("patient_id", sort=False, observed=True)["risk"].shift(1)
    return (prev.groupby(df["patient_id"], sort=False, observed=True).rolling(window_hours, min_periods=1).min()
            .reset_index(level=0, drop=True).sort_index())


def policy_alerts(df, threshold, policy, comparison=">="):
    """Boolean alert flags for prepared rows (needs column `prior_min` when policy.rise_ratio is set)."""
    pid = df["patient_id"].to_numpy()
    hours = df["hours_since_icu"].to_numpy(np.float64)
    risk = df["risk"].to_numpy(np.float64)
    trigger = risk > threshold if comparison == ">" else risk >= threshold
    if policy.sustain_hours > 1:
        n = len(df)
        same = np.r_[False, (pid[1:] == pid[:-1]) & (np.diff(hours) == 1)]   # continues the previous hour
        start = trigger & ~(np.r_[False, trigger[:-1]] & same)
        first = np.maximum.accumulate(np.where(start, np.arange(n), 0))
        trigger = trigger & (np.arange(n) - first + 1 >= policy.sustain_hours)
    if policy.rise_ratio:
        pm = df["prior_min"].to_numpy(np.float64)
        trigger = trigger & (np.isnan(pm) | (risk >= policy.rise_ratio * pm))
    alerts = np.zeros(len(df), dtype=bool)
    last_pid, last_alert = None, -np.inf
    for i in np.flatnonzero(trigger):
        if pid[i] != last_pid:
            last_pid, last_alert = pid[i], -np.inf
        if hours[i] - last_alert >= policy.cooldown_hours:
            alerts[i], last_alert = True, hours[i]
    return alerts


def per_patient(df, alerts):
    """Per-patient alert tallies (the unit for metrics and patient-level bootstrap)."""
    h = df.attrs["horizon_h"]
    to_onset = df["hours_to_onset"].to_numpy()
    true = alerts & (to_onset > 0) & (to_onset <= h)
    false = alerts & df["eligible"].to_numpy() & (df["label"].to_numpy() == 0)
    in_horizon = (to_onset > 0) & (to_onset <= h)
    t = pd.DataFrame({"patient_id": df["patient_id"].to_numpy(), "rows": 1, "alerts": alerts, "true": true,
                      "false": false, "event": in_horizon,
                      "lead": np.where(true, to_onset, np.nan)})
    g = t.groupby("patient_id", sort=False, observed=True)
    out = g[["rows", "alerts", "true", "false"]].sum()
    out["event"] = g["event"].any()
    out["lead"] = g["lead"].max()          # first correct alert = largest time to onset
    return out


def summarize(pp):
    """Aggregate metrics from per_patient output."""
    days = pp["rows"].sum() / 24.0
    alerts, true, false = pp["alerts"].sum(), pp["true"].sum(), pp["false"].sum()
    ev = pp["event"]
    detected = ev & (pp["true"] > 0)
    lead = pp.loc[detected, "lead"]
    return {
        "patient_days": days,
        "alerts_per_patient_day": alerts / days,
        "false_alerts_per_patient_day": false / days,
        "unlabelled_alerts_per_patient_day": (alerts - true - false) / days,
        "alert_ppv": true / (true + false) if true + false else np.nan,
        "aki_events": int(ev.sum()),
        "event_detection_rate": detected.sum() / ev.sum() if ev.sum() else np.nan,
        "lead_time_median_h": float(lead.median()) if len(lead) else np.nan,
        "lead_time_mean_h": float(lead.mean()) if len(lead) else np.nan,
        "non_aki_patients_alerted": float((pp.loc[~ev, "alerts"] > 0).mean()) if (~ev).any() else np.nan,
    }


def evaluate(df, threshold, policy, comparison=">="):
    return summarize(per_patient(df, policy_alerts(df, threshold, policy, comparison)))


def grid(df, thresholds, policies, comparison=">="):
    rows = []
    for p in policies:
        for thr in thresholds:
            rows.append({"policy": p.name, "sustain_hours": p.sustain_hours, "cooldown_hours": p.cooldown_hours,
                         "rise_ratio": p.rise_ratio or 0.0, "threshold": float(thr),
                         **evaluate(df, thr, p, comparison)})
    return pd.DataFrame(rows)


def select(results, min_ppv=None, max_alerts_per_day=None, max_false_per_day=None, min_median_lead_h=None,
           detection_tolerance=0.005):
    """Highest event detection under the constraints; within `detection_tolerance` of the best, the lowest alert
    burden wins, then the simplest policy (shortest sustain, no rise filter), then the higher threshold."""
    ok = results.copy()
    if min_ppv is not None:
        ok = ok[ok["alert_ppv"] >= min_ppv]
    if max_alerts_per_day is not None:
        ok = ok[ok["alerts_per_patient_day"] <= max_alerts_per_day]
    if max_false_per_day is not None:
        ok = ok[ok["false_alerts_per_patient_day"] <= max_false_per_day]
    if min_median_lead_h is not None:
        ok = ok[ok["lead_time_median_h"] >= min_median_lead_h]
    if ok.empty:
        return None
    best = ok["event_detection_rate"].max()
    near = ok[ok["event_detection_rate"] >= best - detection_tolerance]
    return near.sort_values(["alerts_per_patient_day", "sustain_hours", "rise_ratio", "threshold"],
                            ascending=[True, True, True, False]).iloc[0]


def bootstrap(pp, n=1000, seed=0):
    """Patient-level bootstrap percentiles (2.5, 97.5) of the summarize metrics."""
    rng = np.random.default_rng(seed)
    idx = np.arange(len(pp))
    draws = [summarize(pp.iloc[rng.choice(idx, len(idx))]) for _ in range(n)]
    d = pd.DataFrame(draws)
    return d.quantile([0.025, 0.975]).T.rename(columns={0.025: "ci_low", 0.975: "ci_high"})
