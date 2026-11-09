"""Rescore every validation snapshot with the batch-independent feature engine (patient_window_v2).

The fitted weights and Platt/isotonic calibrators of the locked model are unchanged; only the feature arithmetic
changes. The legacy engine centres and prefix-sums values over the whole batch, so a patient's features depend on
which other patients and future rows were in the same call; v2 computes every window from that window's values only.

Steps (all local; test split never read):
1. Admission inputs: recover the float64 pre-ICU creatinine baseline from the raw LABEVENTS file (the snapshot stores
   it as float32, which is not enough to recompute creatinine_ratio_to_baseline exactly).
2. Rescore all validation origins for 6/12/24/48 h from the event stream.
3. Invariance check on a patient sample: whole batch, one patient at a time, and events cut at each origin
   (what live scoring sees) must give bit-identical features. The legacy engine is run the same way for contrast.
4. Aggregate comparison with the stored (legacy) validation risks.

    python scripts/rescore_validation_feature_v2.py

Private outputs (gitignored): artifacts/feature_v2/admission_inputs/, artifacts/predictions/<run>_validation_feature_v2.parquet.
Aggregate report (tracked): artifacts/handoff/feature_v2/.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.metrics import brier_score_loss, roc_auc_score
from threadpoolctl import threadpool_limits

from aki_eval.data import dataset_paths
from aki_eval.protocol import sha256, write_json
from aki_eval.replay import ReplayData, sample_patients
from aki_ml.admission_inputs import admission_frame, reconstruct_baselines, write_admission_inputs
from aki_ml.features import LEGACY_FEATURE_ENGINE, STABLE_FEATURE_ENGINE

HORIZONS = [6, 12, 24, 48]
CHUNK_PATIENTS = 1000
INVARIANCE_PATIENTS, SEED = 60, 20261109
KEYS = ["patient_id", "hours_since_icu"]


def log(message):
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def same(a, b):
    return np.array_equal(a, b, equal_nan=True)


def main():
    import yaml
    data_cfg = yaml.safe_load((ROOT / "configs" / "default.yaml").read_text(encoding="utf-8"))
    paths = dataset_paths(ROOT)
    policy = json.loads((ROOT / "artifacts" / "demo_i" / "policy.json").read_text(encoding="utf-8"))
    model_dir = ROOT / policy["model"]["model_dir"]
    if sha256(model_dir / "pipeline.joblib") != policy["model"]["pipeline_sha256"]:
        raise RuntimeError("pipeline.joblib differs from the locked Demo I model")
    pipe = joblib.load(model_dir / "pipeline.joblib")
    names = pipe.feature_names
    run_id = policy["model"]["run_id"]
    private, report = ROOT / "artifacts" / "feature_v2", ROOT / "artifacts" / "handoff" / "feature_v2"
    pred_out = ROOT / "artifacts" / "predictions" / f"{run_id}_validation_feature_v2.parquet"
    report.mkdir(parents=True, exist_ok=True)
    pa.set_cpu_count(4)

    snap = pq.read_table(paths["snapshots"], filters=[("split", "=", "validation")]).to_pandas()
    for c in ["patient_id", "stay_id"]:
        snap[c] = snap[c].astype(str)
    snap["hours_since_icu"] = snap["hours_since_icu"].round().astype(int)
    log(f"validation: {snap['patient_id'].nunique():,} patients, {len(snap):,} origins")

    # 1. Float64 admission baseline from the raw lab file, checked against the stored float32 value.
    admission_dir = private / "admission_inputs"
    if not (admission_dir / "manifest.json").exists():
        raw = next(p for p in [paths["mimic_root"] / "LABEVENTS.csv", paths["mimic_root"] / "LABEVENTS.csv.gz"] if p.exists())
        log(f"reconstructing admission baselines from {raw.name}")
        baselines, pre, history, scanned = reconstruct_baselines(raw, snap, data_cfg)
        frame = admission_frame(snap, baselines, data_cfg)      # raises unless float32(baseline) == stored value
        write_admission_inputs(admission_dir, frame, {"raw_source": raw.name, "raw_rows_scanned": int(scanned),
                                                      "split": "validation", "created_at": datetime.now(timezone.utc).isoformat()})
    admission = json.loads((admission_dir / "manifest.json").read_text(encoding="utf-8"))

    # 2. Rescore in patient chunks (v2 results do not depend on the chunking; step 3 checks that).
    stored = pq.read_table(paths["predictions"], filters=[("family", "=", policy["model"]["family"]),
                           ("feature_set", "=", policy["model"]["feature_set"]), ("prediction_set", "=", "validation")]).to_pandas()
    stored["patient_id"] = stored["patient_id"].astype(str)
    patients = np.sort(snap["patient_id"].unique())
    t0, parts = time.time(), []
    with threadpool_limits(limits=4):
        for start in range(0, len(patients), CHUNK_PATIENTS):
            pool = snap[snap["patient_id"].isin(patients[start:start + CHUNK_PATIENTS])].reset_index(drop=True)
            ctx = ReplayData.load(paths["events"], pool, data_cfg, names, engine_version=STABLE_FEATURE_ENGINE,
                                  admission_inputs=admission_dir)
            X, covered = ctx.matrix(pool)
            if not covered.all():
                raise RuntimeError("A stored origin fails the coverage rule under v2 inputs")
            out = pool[KEYS].copy()
            for h in HORIZONS:
                raw_score = pipe.raw_score(X, h)
                out[f"raw_{h}"] = raw_score
                out[f"platt_{h}"] = pipe.calibrators[h]["platt"].predict(raw_score)
                out[f"isotonic_{h}"] = pipe.calibrators[h]["isotonic"].predict(raw_score)
            parts.append(out)
            log(f"rescored {min(start + CHUNK_PATIENTS, len(patients)):,}/{len(patients):,} patients ({time.time() - t0:.0f}s)")
    scores = pd.concat(parts, ignore_index=True)
    rows = []
    for h in HORIZONS:
        s = scores[KEYS + [f"raw_{h}", f"platt_{h}", f"isotonic_{h}"]].rename(
            columns={f"raw_{h}": "risk_raw", f"platt_{h}": "risk_platt", f"isotonic_{h}": "risk_isotonic"})
        base = stored[stored["horizon_h"] == h].drop(columns=["risk_raw", "risk_platt", "risk_isotonic"])
        merged = base.merge(s, on=KEYS, how="inner", validate="1:1")
        if len(merged) != len(base) or len(merged) != len(s):
            raise RuntimeError(f"{h}h: rescored origins do not match the stored validation predictions")
        rows.append(merged)
    new = pd.concat(rows, ignore_index=True)
    new["prediction_set"] = "validation_feature_v2"
    new["feature_engine_version"] = STABLE_FEATURE_ENGINE
    for c in ["risk_raw", "risk_platt", "risk_isotonic"]:
        new[c] = new[c].astype(np.float32)
    pq.write_table(pa.Table.from_pandas(new, preserve_index=False), pred_out, compression="zstd")
    log(f"wrote {pred_out.relative_to(ROOT)} ({len(new):,} rows)")

    # 3. Invariance: whole batch vs one patient vs events cut at each origin, for both engines.
    sample = sample_patients(snap, INVARIANCE_PATIENTS, SEED)
    invariance = {}
    for version in [LEGACY_FEATURE_ENGINE, STABLE_FEATURE_ENGINE]:
        kwargs = {"engine_version": version, "admission_inputs": admission_dir if version == STABLE_FEATURE_ENGINE else None}
        whole = ReplayData.load(paths["events"], snap, data_cfg, names, **kwargs).matrix(sample)[0] if version == STABLE_FEATURE_ENGINE \
            else sample[names].to_numpy(np.float32)            # legacy: the stored features came from 4,000-stay batches
        single_aligned = np.empty((len(sample), len(names)), np.float32)
        for _, g in sample.groupby("patient_id", sort=False):
            one = g.reset_index(drop=True)
            single_aligned[g.index] = ReplayData.load(paths["events"], one, data_cfg, names, **kwargs).matrix(one)[0]
        cut = ReplayData.load(paths["events"], sample, data_cfg, names, **kwargs).matrix(sample, clip="all")[0]
        invariance[version] = {
            "rows": len(sample), "patients": int(sample["patient_id"].nunique()),
            "batch_vs_single_patient_identical": same(whole, single_aligned),
            "batch_vs_origin_cut_identical": same(whole, cut),
            "rows_differing_batch_vs_single": int((~np.isclose(whole, single_aligned, rtol=0, atol=0, equal_nan=True)).any(axis=1).sum()),
            "rows_differing_batch_vs_origin_cut": int((~np.isclose(whole, cut, rtol=0, atol=0, equal_nan=True)).any(axis=1).sum()),
        }
        log(f"{version}: {invariance[version]}")
    if not (invariance[STABLE_FEATURE_ENGINE]["batch_vs_single_patient_identical"]
            and invariance[STABLE_FEATURE_ENGINE]["batch_vs_origin_cut_identical"]):
        raise RuntimeError("v2 features depend on batch composition or future rows")

    # 4. Aggregate comparison with the stored legacy risks.
    comparison = []
    for h in HORIZONS:
        a = stored[stored["horizon_h"] == h].set_index(KEYS)
        b = new[new["horizon_h"] == h].set_index(KEYS).reindex(a.index)
        e = a["eligible"].to_numpy(bool)
        y = a["label"].to_numpy(float)[e].astype(int)
        diff = np.abs(b["risk_platt"].to_numpy(float) - a["risk_platt"].to_numpy(float))
        row = {"horizon_h": h, "rows": len(a), "eligible_rows": int(e.sum()),
               "rows_risk_changed_gt_1e6": int((diff > 1e-6).sum()), "max_abs_risk_change": float(diff.max()),
               "p99_abs_risk_change": float(np.quantile(diff, .99))}
        for label, frame in [("legacy", a), ("v2", b)]:
            p = frame["risk_platt"].to_numpy(float)[e]
            row[f"auroc_{label}"] = roc_auc_score(y, p)
            row[f"brier_{label}"] = brier_score_loss(y, p)
            row[f"mean_risk_minus_prevalence_{label}"] = float(p.mean() - y.mean())
        comparison.append(row)
    comparison = pd.DataFrame(comparison)
    comparison.to_csv(report / "legacy_vs_v2_validation.csv", index=False)
    write_json(report / "rescore_report.json", {
        "created_at": datetime.now(timezone.utc).isoformat(), "model_run_id": run_id,
        "model_pipeline_sha256": policy["model"]["pipeline_sha256"], "weights_and_calibration_changed": False,
        "feature_engine_version": STABLE_FEATURE_ENGINE, "data": "validation only; test split not read",
        "origins": len(snap), "patients": int(snap["patient_id"].nunique()),
        "admission_inputs": {k: admission[k] for k in ["schema_version", "baseline_rule", "rows", "sha256"]},
        "predictions_sha256": sha256(pred_out), "invariance": invariance,
        "comparison": comparison.to_dict(orient="records")})
    print(comparison.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
