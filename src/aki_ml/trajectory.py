"""Observed-value trajectory targets and replaceable regression baselines."""
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from .targets import attach_timeline
from .training import BaselinePreprocessor


def trajectory_targets(snapshots, cohort, events, leads, tolerance_hours, export_end_hour):
    """Nearest observed draw in target +/- tolerance; earlier draw wins a tie.

    Origins must precede either known AKI onset and discharge/death/RRT. Future draws
    may follow AKI onset but must precede censoring. Missing measurements stay missing.
    An incomplete extraction window is explicitly unevaluable, even if a nearer draw
    could have been outside the exported file.
    """
    if not leads or len(set(leads)) != len(leads) or min(leads) <= 0 or tolerance_hours < 0:
        raise ValueError("Invalid trajectory matching configuration")
    table = attach_timeline(snapshots, cohort)
    rows = table.loc[table.index.repeat(len(leads)), ["patient_id", "stay_id", "split", "hours_since_icu",
                                                     "censor_hours", "combined_onset_hours"]].reset_index(drop=True)
    rows["snapshot_index"] = np.repeat(np.arange(len(table)), len(leads))
    rows["lead_hours"] = np.tile(leads, len(table))
    rows["target_hour"] = rows["hours_since_icu"] + rows["lead_hours"]
    rows["target_measured_hour"], rows["target_value"] = np.nan, np.nan
    rows["reason"] = "no_measurement"
    past_event = rows["combined_onset_hours"] <= rows["hours_since_icu"]
    censored = rows["target_hour"] >= rows["censor_hours"]
    truncated = rows["target_hour"] + tolerance_hours > export_end_hour
    rows.loc[truncated, "reason"] = "export_window_truncated"
    rows.loc[censored, "reason"] = "censored"
    rows.loc[past_event | (rows["hours_since_icu"] >= rows["censor_hours"]), "reason"] = "origin_not_at_risk"
    draws = events[events["concept"] == "creatinine"].copy()
    if not np.isfinite(draws[["hours_since_icu", "value"]].to_numpy(float)).all():
        raise ValueError("Trajectory measurements must be finite")
    draws = draws.groupby(["stay_id", "hours_since_icu"], as_index=False, sort=True)["value"].mean()
    by_stay = dict(tuple(draws.groupby("stay_id", sort=False)))
    for stay, idx in rows.groupby("stay_id", sort=False).groups.items():
        if stay not in by_stay:
            continue
        idx = np.asarray(idx)
        idx = idx[rows.loc[idx, "reason"].eq("no_measurement").to_numpy()]
        if not len(idx):
            continue
        d = by_stay[stay]
        d = d[d["hours_since_icu"] < rows.loc[idx[0], "censor_hours"]]
        if d.empty:
            continue
        times, values = d["hours_since_icu"].to_numpy(), d["value"].to_numpy()
        targets, origins = rows.loc[idx, "target_hour"].to_numpy(), rows.loc[idx, "hours_since_icu"].to_numpy()
        right = np.searchsorted(times, targets)
        positions = np.stack([np.clip(right - 1, 0, len(times) - 1), np.clip(right, 0, len(times) - 1)], axis=1)
        distances = np.abs(times[positions] - targets[:, None])
        distances[times[positions] <= origins[:, None]] = np.inf
        chosen = positions[np.arange(len(idx)), np.argmin(distances, axis=1)]
        ok = np.min(distances, axis=1) <= tolerance_hours
        rows.loc[idx[ok], "target_measured_hour"] = times[chosen[ok]]
        rows.loc[idx[ok], "target_value"] = values[chosen[ok]]
        rows.loc[idx[ok], "reason"] = "observed"
    rows["eligible"] = rows["reason"].eq("observed")
    return rows


def density_report(targets):
    return [{"lead_hours": float(lead), "requested_points": len(g), "observed_points": int(g["eligible"].sum()),
             "observed_fraction": float(g["eligible"].mean()),
             "reasons": {str(k): int(v) for k, v in g["reason"].value_counts().items()},
             "median_matching_offset_hours": float((g.loc[g["eligible"], "target_measured_hour"] -
                                                       g.loc[g["eligible"], "target_hour"]).abs().median())
                 if g["eligible"].any() else None}
            for lead, g in targets.groupby("lead_hours", sort=True)]


class TrajectoryRegressor:
    """One ridge head per lead; persistence is a separate, always-reported comparator."""
    def __init__(self, feature_names, leads, alpha=1.0):
        self.feature_names, self.leads, self.alpha = list(feature_names), list(leads), alpha
        self.heads, self.preprocessors = {}, {}

    def fit(self, X, targets):
        for lead in self.leads:
            usable = targets[(targets["split"] == "train") & targets["eligible"] & (targets["lead_hours"] == lead)]
            if len(usable) < 2 or usable["patient_id"].nunique() < 2:
                raise ValueError(f"Insufficient observed training trajectory targets at lead {lead}")
            rows = usable["snapshot_index"].to_numpy(int)
            pre = BaselinePreprocessor().fit(X[rows])
            self.preprocessors[lead] = pre
            self.heads[lead] = Ridge(alpha=self.alpha).fit(pre.transform(X[rows]), usable["target_value"])
        return self

    def predict(self, X, method="ridge"):
        if method == "persistence":
            return {h: X[:, self.feature_names.index("creatinine_last")].astype(float).copy() for h in self.leads}
        if method != "ridge":
            raise ValueError("Unknown trajectory method")
        # Creatinine is nonnegative; this simple baseline has no fitted uncertainty interval.
        return {h: np.maximum(0, self.heads[h].predict(self.preprocessors[h].transform(X))) for h in self.leads}
