"""Score dashboard patients with the locked Demo I model and import the results (Local Data Contract v1).

One-shot batch scorer (not a resident model process). For each patient registered in the running dashboard:
1. read its observations through the dashboard export API (highest revision of each record);
2. build the model features at every whole ICU hour 1-72 that has already passed, with the shared as-of engine
   `src/aki_ml/features.py` (labs usable 1 h after the draw, windows (t - W, t]);
3. score 6/12/24/48 h with LightGBM `no_dc` + Platt calibration; the 24 h prediction carries the locked
   `demo-v0` threshold from `artifacts/demo_i/policy.json`, the other horizons are display-only;
4. explain each prediction with LightGBM TreeSHAP: the five features with the largest |contribution|, in
   calibrated log-odds units (raw contribution x Platt slope);
5. fetch the input fingerprint for each data cutoff from the dashboard and import a PredictionBatch.

Predictions already in the dashboard (same record_id) are skipped, so re-running after new data only adds the
new or changed hours. Patient-level data stays local: the script talks only to the dashboard on 127.0.0.1.

Usage (dashboard running, e.g. `Start-AKI.cmd --data-dir <dir>`):
    python scripts/score_dashboard.py [--api http://127.0.0.1:8765] [--patient DEMO-002 ...] [--input-file batch.json]
"""
import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from aki_ml import logit  # noqa: E402
from aki_ml.features import compute_features, static_feature_names  # noqa: E402

MODEL_ID = "aki-lightgbm-no_dc"
TARGET = "AKI (KDIGO creatinine)"
MAX_HOUR = 72                      # model monitoring grid: ICU hours 1-72
N_DRIVERS = 5
BATCH_SIZE = 2000                  # predictions per import request (contract limit 10,000)
WINDOWED = {"mean", "min", "max", "std", "slope", "count", "sum"}


def api(base, path, payload=None):
    req = urllib.request.Request(base + path, method="POST" if payload is not None else "GET",
                                 data=None if payload is None else json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            if "json" not in r.headers.get("Content-Type", ""):
                raise RuntimeError(f"{path}: expected JSON, got {r.headers.get('Content-Type')}")
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:500]}") from None


def stamp(t):
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse(t):
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class LockedModel:
    """The Demo I pipeline, its locked policy, and display metadata for drivers."""

    def __init__(self, policy_path):
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
        out_root = (REPO_ROOT / self.data_cfg["paths"]["output_root"]).resolve()
        fd = pd.read_csv(out_root / "datasets" / "feature_dictionary.csv").set_index("feature")
        self.labels = {f: self._label(f, fd.loc[f]) if f in fd.index else f for f in self.pipe.feature_names}
        self.units = {f: str(fd.loc[f, "unit"]) if f in fd.index and pd.notna(fd.loc[f, "unit"]) else ""
                      for f in self.pipe.feature_names}

    @staticmethod
    def _label(feature, row):
        concept, stat, w = str(row["concept"]).replace("_", " "), str(row["statistic"]), row["window_hours"]
        text = f"{concept} · {stat}" + (f" ({int(w)} h)" if stat in WINDOWED and pd.notna(w) else "")
        return text[:120]

    def predict(self, X):
        """{horizon: (risk, drivers per row)} for the feature matrix X."""
        out = {}
        for h in self.pipe.horizons:
            raw = self.pipe.raw_score(X, h)
            platt = self.pipe.calibrators[h]["platt"]
            risk = platt.predict(raw)
            contrib = self.pipe.heads[h].predict(X, num_iteration=self.pipe.n_rounds[h], pred_contrib=True)
            # TreeSHAP is additive on the raw log-odds; check before rescaling to calibrated log-odds.
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


def patient_observations(base, pid):
    plan = api(base, f"/api/patients/{pid}/export-plan/input")
    obs = []
    for part in range(1, plan["parts"] + 1):
        batch = api(base, f"/api/patients/{pid}/export/input?part={part}&revision={plan['revision']}")
        obs += batch["observations"]
    return latest_revisions(obs)


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


def score_patient(base, model, patient, generated_at):
    pid, enc = patient["patient_id"], patient["encounter_id"]
    admit = parse(patient["icu_admitted_at"])
    last_hour = min(MAX_HOUR, int((generated_at - admit) / timedelta(hours=1)))
    if last_hour < 1:
        return []
    hours = np.arange(1, last_hour + 1, dtype=np.float64)
    obs = patient_observations(base, pid)
    ev, static, surgical = build_inputs(obs, admit, hours, model.data_cfg)
    X = compute_features(ev, static, surgical, np.zeros(len(hours), np.int64), hours, model.data_cfg,
                         model.pipe.feature_names)
    scored = model.predict(X)

    existing, offset = set(), 0
    while offset is not None:
        page = api(base, f"/api/patients/{pid}/history?kind=prediction&limit=10000&offset={offset}")
        existing |= {p["record_id"] for p in page["items"]}
        offset = page["next_offset"]

    policy, alert_h = model.policy, model.policy["alert_horizon_h"]
    out = []
    for k, hour in enumerate(hours.astype(int)):
        origin = admit + timedelta(hours=int(hour))
        q = urllib.parse.urlencode({"as_of": stamp(generated_at), "cutoff": stamp(origin)})
        fp = api(base, f"/api/patients/{pid}/snapshot?{q}")["input_fingerprint"]
        for h, (risk, drivers) in scored.items():
            rid = f"{pid}-{policy['policy_version']}-{h}h-t{hour:02d}-{fp[:12]}"
            if len(rid) > 80:
                rid = "aki-" + hashlib.sha256(rid.encode()).hexdigest()[:40]
            if rid in existing:
                continue
            p = {"record_id": rid, "revision": 1, "patient_id": pid, "encounter_id": enc,
                 "input_revision": patient["input_revision"], "input_fingerprint": fp,
                 "model_id": MODEL_ID, "model_version": model.version, "target": TARGET,
                 "origin_time": stamp(origin), "data_cutoff": stamp(origin),
                 "horizon_end": stamp(origin + timedelta(hours=h)), "generated_at": stamp(generated_at),
                 "risk": round(float(risk[k]), 6), "drivers": drivers[k]}
            if h == alert_h:
                p["threshold"] = policy["threshold"]
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--api", default="http://127.0.0.1:8765")
    ap.add_argument("--policy", default=str(REPO_ROOT / "artifacts" / "demo_i" / "policy.json"))
    ap.add_argument("--patient", action="append", help="patient_id to score (default: all)")
    ap.add_argument("--input-file", help="InputBatch JSON to import before scoring (e.g. synthetic patients)")
    ap.add_argument("--out", help="also write the PredictionBatch JSON here")
    ap.add_argument("--dry-run", action="store_true", help="score but do not import")
    args = ap.parse_args()
    base = args.api.rstrip("/")

    if args.input_file:
        res = api(base, "/api/import", json.loads(Path(args.input_file).read_text(encoding="utf-8")))
        print(f"input import: {res}")
    model = LockedModel(args.policy)
    # generated_at must not be later than the dashboard clock used for the fingerprint check.
    generated_at = parse(api(base, "/api/health")["server_time"]).replace(microsecond=0)
    patients = [p for p in api(base, "/api/patients") if not args.patient or p["patient_id"] in args.patient]
    predictions = []
    for patient in patients:
        rows = score_patient(base, model, patient, generated_at)
        alerts = [p for p in rows if "threshold" in p and p["risk"] >= p["threshold"]["value"]]
        print(f"{patient['patient_id']}: {len(rows)} new predictions, {len(alerts)} hourly 24h results above threshold")
        predictions += rows
    if args.out:
        Path(args.out).write_text(json.dumps({"schema_version": 1, "kind": "prediction", "predictions": predictions},
                                             ensure_ascii=False, indent=1), encoding="utf-8")
    if args.dry_run or not predictions:
        print("nothing imported" if not predictions else f"dry run: {len(predictions)} predictions not imported")
        return
    for s in range(0, len(predictions), BATCH_SIZE):
        res = api(base, "/api/import", {"schema_version": 1, "kind": "prediction",
                                        "predictions": predictions[s:s + BATCH_SIZE]})
        print(f"imported {res['inserted']} predictions")


if __name__ == "__main__":
    main()
