"""Score dashboard observations with the locked AKI model (shared by the live model worker and batch scripts).

Input: one patient's dashboard observations (Local Data Contract v1) and ICU admission time.
Output: Platt-calibrated risk for every horizon at the requested ICU hours, with LightGBM TreeSHAP drivers, and
ready-to-import prediction records. Features come from the as-of engine in `features.py` (labs usable 1 h after the
draw, windows (t - W, t]); hours failing the notebook 01 coverage rule are not scored.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml

from .features import compute_features, static_feature_names
from .models import logit

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = REPO_ROOT / "artifacts" / "demo_i" / "policy.json"
MODEL_ID = "aki-lightgbm-no_dc"
TARGET = "AKI (KDIGO creatinine)"
MAX_HOUR = 72                      # model monitoring grid: ICU hours 1-72
N_DRIVERS = 5
WINDOWED = {"mean", "min", "max", "std", "slope", "count", "sum"}


def stamp(t):
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse(t):
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class LockedModel:
    """The locked pipeline, its policy (threshold, monitoring window), and display metadata for drivers."""

    def __init__(self, policy_path=DEFAULT_POLICY):
        self.policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
        m = self.policy["model"]
        model_dir = REPO_ROOT / m["model_dir"]
        if sha256(model_dir / "pipeline.joblib") != m["pipeline_sha256"]:
            raise RuntimeError("pipeline.joblib does not match the model hashed in policy.json")
        if not self.policy["threshold"]["locked"]:
            raise RuntimeError("policy threshold is not locked")
        self.pipe = joblib.load(model_dir / "pipeline.joblib")
        assert (self.pipe.family, self.pipe.feature_set) == (m["family"], m["feature_set"])
        self.version = m["run_id"]
        self.data_cfg = yaml.safe_load((REPO_ROOT / "configs" / "default.yaml").read_text(encoding="utf-8"))
        dictionary = (REPO_ROOT / self.data_cfg["paths"]["output_root"]).resolve() / "datasets" / "feature_dictionary.csv"
        fd = pd.read_csv(dictionary).set_index("feature") if dictionary.exists() else pd.DataFrame()
        self.labels = {f: self._label(fd.loc[f]) if f in fd.index else f.replace("_", " ") for f in self.pipe.feature_names}
        self.units = {f: str(fd.loc[f, "unit"]) if f in fd.index and pd.notna(fd.loc[f, "unit"]) else ""
                      for f in self.pipe.feature_names}

    @staticmethod
    def _label(row):
        concept, stat, w = str(row["concept"]).replace("_", " "), str(row["statistic"]), row["window_hours"]
        return (f"{concept} · {stat}" + (f" ({int(w)} h)" if stat in WINDOWED and pd.notna(w) else ""))[:120]

    @property
    def window(self):
        return self.policy["monitoring_hours"][0], self.policy["monitoring_hours"][-1]

    def predict(self, X):
        """{horizon: (risk array, drivers per row)} for the feature matrix X."""
        out = {}
        for h in self.pipe.horizons:
            raw = self.pipe.raw_score(X, h)
            platt = self.pipe.calibrators[h]["platt"]
            risk = platt.predict(raw)
            contrib = self.pipe.heads[h].predict(X, num_iteration=self.pipe.n_rounds[h], pred_contrib=True)
            # TreeSHAP is additive on the raw log-odds; check, then rescale to calibrated log-odds.
            assert np.allclose(contrib.sum(axis=1), logit(raw), atol=1e-3), "TreeSHAP does not add up to the score"
            contrib = contrib[:, :-1] * platt.slope_
            drivers = []
            for i in range(len(X)):
                top = np.argsort(-np.abs(contrib[i]))[:N_DRIVERS]
                drivers.append([self._driver(j, X[i, j], contrib[i, j]) for j in top])
            out[h] = (risk, drivers)
        return out

    def _driver(self, j, value, contribution):
        f = self.pipe.feature_names[j]
        shown = "missing" if np.isnan(value) else f"{float(value):.4g}"
        return {"feature": f, "label": self.labels[f], "contribution": round(float(contribution), 4),
                "value": shown, "unit": self.units[f][:40]}


def latest_revisions(observations):
    best = {}
    for o in observations:
        if o["record_id"] not in best or o["revision"] > best[o["record_id"]]["revision"]:
            best[o["record_id"]] = o
    return list(best.values())


def build_inputs(obs, admit, hours, data_cfg):
    """Event frame, one-row static frame and per-hour surgical_service from dashboard observations."""
    static_cols = static_feature_names(data_cfg)
    rows = pd.DataFrame([{"metric": o["metric"], "value": float(o["value"]),
                          "t": (parse(o["measured_at"]) - admit) / timedelta(hours=1)} for o in obs])
    if rows.empty:
        rows = pd.DataFrame(columns=["metric", "value", "t"])
    is_static = rows["metric"].isin(static_cols)
    static = {c: np.nan for c in static_cols}
    for c in static_cols:                                  # one-hot / flag defaults: absent means 0
        if c.startswith(("admission_type_", "admission_location_group_", "first_careunit_")) or c.endswith("_dc"):
            static[c] = 0.0
    for _, r in rows[is_static].sort_values("t").iterrows():
        static[r["metric"]] = r["value"]
    if np.isnan(static["age_over_89"]):
        static["age_over_89"] = float(static["age_years"] > 89) if not np.isnan(static["age_years"]) else np.nan
    if np.isnan(static["baseline_cr_available"]):
        static["baseline_cr_available"] = float(not np.isnan(static["baseline_cr_admission"]))
    static = pd.DataFrame([static], index=[0])

    svc = rows[rows["metric"] == "surgical_service"].sort_values("t")
    surgical = np.array([svc.loc[svc["t"] <= h, "value"].iloc[-1] if (svc["t"] <= h).any() else 0.0 for h in hours])

    ev = rows[~is_static & (rows["metric"] != "surgical_service")].sort_values("t", kind="stable").reset_index(drop=True)
    ev = pd.DataFrame({"stay_idx": np.zeros(len(ev), np.int64), "concept": ev["metric"].to_numpy(),
                       "value": ev["value"].to_numpy(np.float64), "itemid": np.zeros(len(ev), np.int64),
                       "t": ev["t"].to_numpy(np.float64)})
    return ev, static, surgical


def coverage(ev, hours, data_cfg):
    """Notebook 01 snapshot coverage: >= 1 creatinine usable in the prior 7 days and >= 1 ICU heart rate.

    Hours failing it were never in the training or validation data, so they are not scored.
    """
    delay = float(data_cfg["features"]["lab_result_delay_hours"])
    lookback = 24.0 * data_cfg["coverage"]["prior_creatinine_lookback_days"]
    cr = ev.loc[ev["concept"] == "creatinine", "t"].to_numpy() + delay
    hr = ev.loc[(ev["concept"] == "heart_rate") & (ev["t"] > 0), "t"].to_numpy()
    need_hr = data_cfg["coverage"]["require_prior_heart_rate"]
    return np.array([((cr >= h - lookback) & (cr <= h)).any() and ((hr <= h).any() or not need_hr) for h in hours],
                    dtype=bool)


def score_hours(model, obs, admit, hours):
    """Scorable hours (inside 1-72 h and coverage) and model output for them: (hours, {h: (risk, drivers)})."""
    hours = np.asarray(hours, np.float64)
    hours = hours[(hours >= 1) & (hours <= MAX_HOUR)]
    if not len(hours):
        return hours, {}
    ev, static, surgical = build_inputs(obs, admit, hours, model.data_cfg)
    ok = coverage(ev, hours, model.data_cfg)
    hours, surgical = hours[ok], surgical[ok]
    if not len(hours):
        return hours, {}
    X = compute_features(ev, static, surgical, np.zeros(len(hours), np.int64), hours, model.data_cfg,
                         model.pipe.feature_names)
    return hours, model.predict(X)


def prediction_records(model, patient, origin, fingerprint, input_revision, generated_at, scored, k, key):
    """Data-contract prediction dicts (one per horizon) for row k of `scored`; record ids are `<key>-<h>h`.

    The locked threshold is attached to the alert horizon only inside the monitoring window.
    """
    policy = model.policy
    lo, hi = model.window
    hour = (origin - parse(patient["icu_admitted_at"])) / timedelta(hours=1)
    out = []
    for h, (risk, drivers) in scored.items():
        rid = f"{key}-{h}h"
        if len(rid) > 80:
            rid = "aki-" + hashlib.sha256(rid.encode()).hexdigest()[:40]
        p = {"record_id": rid, "revision": 1, "patient_id": patient["patient_id"],
             "encounter_id": patient["encounter_id"], "input_revision": int(input_revision),
             "input_fingerprint": fingerprint, "model_id": MODEL_ID, "model_version": model.version,
             "target": TARGET, "origin_time": stamp(origin), "data_cutoff": stamp(origin),
             "horizon_end": stamp(origin + timedelta(hours=h)), "generated_at": stamp(generated_at),
             "risk": round(float(risk[k]), 6), "drivers": drivers[k]}
        if h == policy["alert_horizon_h"] and lo <= hour <= hi:
            p["threshold"] = policy["threshold"]
        out.append(p)
    return out
