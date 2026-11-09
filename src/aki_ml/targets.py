"""Independent extension targets. Never overwrite notebook 01's endpoint columns."""
import numpy as np
import pandas as pd


KEYS = ["patient_id", "stay_id", "hours_since_icu"]


def attach_timeline(snapshots, cohort):
    required = ["stay_id", "patient_id", "censor_hours", "cr_aki_onset_hours", "uo_aki_onset_hours"]
    if not set(required) <= set(cohort):
        raise ValueError("Cohort is missing required identity/onset/censor columns")
    if cohort["stay_id"].duplicated().any() or cohort["patient_id"].duplicated().any():
        raise ValueError("Expected one selected stay per patient")
    if snapshots.duplicated(KEYS).any():
        raise ValueError("Duplicate snapshot keys")
    timeline = cohort[required].rename(columns={"patient_id": "timeline_patient"})
    out = snapshots.merge(timeline, on="stay_id", how="left", validate="many_to_one", sort=False)
    if not out["patient_id"].eq(out["timeline_patient"]).all():
        raise ValueError("Snapshot and cohort patient/stay mapping differ")
    numeric = out[["hours_since_icu", "censor_hours"]].to_numpy(float)
    if not np.isfinite(numeric).all():
        raise ValueError("Origin and censor times must be finite")
    for name in ["cr_aki_onset_hours", "uo_aki_onset_hours"]:
        if np.isinf(out[name].to_numpy(float)).any():
            raise ValueError("Missing onset must be NaN, not infinity")
        # Notebook 01 censors at discharge/death/RRT. Events at the boundary are not observed outcomes.
        out[name] = out[name].where(out[name] < out["censor_hours"])
    out["combined_onset_hours"] = out[["cr_aki_onset_hours", "uo_aki_onset_hours"]].min(axis=1)
    return out.drop(columns="timeline_patient")


def combined_targets(snapshots, cohort, horizons):
    """Observed CR OR UO onset; negatives need BOTH original endpoints observable.

    A known event at/before origin removes the snapshot for every horizon. Unknown UO
    cannot become a negative. A known positive can establish the union even when the
    other endpoint is unknown. This is an observed-onset research definition, not proof
    that undocumented AKI was absent. Original CR/UO eligibility is retained.
    """
    out = attach_timeline(snapshots, cohort)
    k, onset, censor = (out[c].to_numpy(float) for c in
                         ["hours_since_icu", "combined_onset_hours", "censor_hours"])
    at_risk = (k < censor) & ~(onset <= k)
    out["combined_at_risk"] = at_risk
    for h in horizons:
        if h <= 0:
            raise ValueError("Horizons must be positive")
        cols = [f"eligible_{h}h", f"eligible_uo_{h}h", f"aki_within_{h}h", f"aki_uo_within_{h}h"]
        if not set(cols) <= set(out):
            raise ValueError(f"Missing original endpoint columns for horizon {h}")
        cr_ok, uo_ok = (out[c].fillna(False).astype(bool).to_numpy() for c in cols[:2])
        cr_y, uo_y = (out[c].to_numpy(float, na_value=np.nan) for c in cols[2:])
        for ok, y in [(cr_ok, cr_y), (uo_ok, uo_y)]:
            if not np.isin(y[ok], [0, 1]).all():
                raise ValueError("An eligible endpoint needs a binary label")
        positive = at_risk & (onset > k) & (onset <= k + h)
        negative = at_risk & (censor >= k + h) & cr_ok & uo_ok & (cr_y == 0) & (uo_y == 0) & ~positive
        eligible = positive | negative
        out[f"eligible_combined_{h}h"] = eligible
        out[f"aki_combined_within_{h}h"] = pd.array(
            np.where(eligible, positive.astype(int), np.nan), dtype="Int8")
    return out
