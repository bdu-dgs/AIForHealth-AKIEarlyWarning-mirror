"""Threshold-crossing alert policy with a repeat-alert cooldown, and its event-level metrics.

Simulated on hourly predictions of one horizon, using only rows whose label is defined (eligible rows;
rows at or after AKI onset are never eligible). An alert at time t is correct when AKI onset falls in
(t, t + horizon], i.e. its row label is 1; otherwise it is a false alert.
"""
import numpy as np
import pandas as pd


def simulate_alerts(patient, hours, risk, threshold, cooldown_hours, comparison=">="):
    """Boolean alert flags for rows sorted by (patient, hours).

    Fires when risk crosses the threshold, then stays silent for `cooldown_hours` after each alert even if
    risk stays high; the next alert needs `hours - last_alert >= cooldown_hours`.
    """
    patient, hours, risk = np.asarray(patient), np.asarray(hours, np.float64), np.asarray(risk, np.float64)
    above = risk > threshold if comparison == ">" else risk >= threshold
    alerts = np.zeros(len(risk), dtype=bool)
    last_patient, last_alert = None, -np.inf
    for i in np.flatnonzero(above):                       # only rows above threshold can alert
        if patient[i] != last_patient:
            last_patient, last_alert = patient[i], -np.inf
        if hours[i] - last_alert >= cooldown_hours:
            alerts[i], last_alert = True, hours[i]
    return alerts


def alert_metrics(df, alerts):
    """Event-level alert summary for one threshold.

    df: eligible rows sorted by (patient_id, hours_since_icu) with columns patient_id, hours_since_icu,
    snapshot_time, creatinine_aki_onset_time, label (0/1).
    """
    y = df["label"].to_numpy() == 1
    patient_days = len(df) / 24.0                          # one row per monitored hour
    event_patients = df.loc[y, "patient_id"].unique()      # AKI onset within the horizon of a monitored hour
    true_alert = alerts & y
    first = df[true_alert].groupby("patient_id", observed=True).first()
    lead = (first["creatinine_aki_onset_time"] - first["snapshot_time"]) / pd.Timedelta(hours=1)
    non_event = ~df["patient_id"].isin(event_patients).to_numpy()
    n_non_event_patients = df.loc[non_event, "patient_id"].nunique()
    return {
        "alerts": int(alerts.sum()),
        "true_alerts": int(true_alert.sum()),
        "false_alerts": int((alerts & ~y).sum()),
        "patient_days": patient_days,
        "false_alerts_per_patient_day": float((alerts & ~y).sum() / patient_days),
        "alert_ppv": float(true_alert.sum() / alerts.sum()) if alerts.any() else np.nan,
        "aki_events": int(len(event_patients)),
        "events_detected": int(len(first)),
        "event_detection_rate": float(len(first) / len(event_patients)) if len(event_patients) else np.nan,
        "lead_time_mean_h": float(lead.mean()) if len(lead) else np.nan,
        "lead_time_median_h": float(lead.median()) if len(lead) else np.nan,
        "non_aki_patients_alerted_fraction": (float(df.loc[non_event & alerts, "patient_id"].nunique()
                                                    / n_non_event_patients) if n_non_event_patients else np.nan),
    }


def threshold_grid(df, thresholds, cooldown_hours, comparison=">="):
    """alert_metrics for each threshold (df prepared as for alert_metrics, with a `risk` column)."""
    rows = []
    for thr in thresholds:
        a = simulate_alerts(df["patient_id"].to_numpy(), df["hours_since_icu"].to_numpy(), df["risk"].to_numpy(),
                            thr, cooldown_hours, comparison)
        rows.append({"threshold": float(thr), **alert_metrics(df, a)})
    return pd.DataFrame(rows)


def select_threshold(grid, max_false_alerts_per_patient_day):
    """Highest event detection rate subject to the false-alert budget; ties go to the higher threshold."""
    ok = grid[grid["false_alerts_per_patient_day"] <= max_false_alerts_per_patient_day]
    if ok.empty:
        raise ValueError("no threshold meets the false-alert budget")
    best = ok["event_detection_rate"].max()
    return ok[ok["event_detection_rate"] == best].sort_values("threshold").iloc[-1]
