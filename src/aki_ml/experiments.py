"""Notebook/CLI orchestration. Shared logic lives in testable modules, never notebook cells."""
from dataclasses import dataclass
from pathlib import Path
import json
import time
import platform
import subprocess
from importlib.metadata import version

import joblib
import numpy as np
import yaml

from .evaluation import (classification_metrics, cutoff_stability, digest, first_warning_metrics,
                         regression_metrics, select_validation_threshold)
from .research_data import ROOT, data_config, development_data, feature_matrix, sha256, validate_data
from .targets import attach_timeline, combined_targets
from .training import fit_classifier
from .trajectory import TrajectoryRegressor, density_report, trajectory_targets


@dataclass
class Experiment:
    model: object
    metadata: dict
    report: dict
    policy: dict | None = None


def config(name):
    return yaml.safe_load((ROOT / "configs" / name).read_text(encoding="utf-8"))


def metadata(data, cfg, kind):
    sources = sorted((ROOT / "src/aki_ml").glob("*.py"))
    try:
        git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        commit = git.stdout.strip() if git.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        commit = None
    info = {"kind": kind, "configuration": cfg, "data": data.provenance,
            "feature_order": cfg["feature_names"], "feature_order_sha256": digest(cfg["feature_names"]),
            "data_config_sha256": sha256(ROOT / "configs/default.yaml"),
            "source_sha256": {p.name: sha256(p) for p in sources}, "test_used": False}
    info["git_commit"] = commit
    info["software"] = {"python": platform.python_version(),
                        **{name: version(name) for name in ["numpy", "pandas", "scikit-learn", "pyarrow", "joblib"]}}
    info["run_id"] = kind + "-" + digest(info)[:16]
    return info


def _prepare(data):
    audit = validate_data(data)
    expected = data.provenance.get("data_config_sha256")
    if expected and expected != sha256(ROOT / "configs/default.yaml"):
        raise ValueError("Dataset config differs from current default.yaml; use its exact version")
    return development_data(data), audit


def classification_experiment(data, cfg=None, endpoint="combined"):
    cfg = dict(cfg or config("combined_aki.yaml"))
    if endpoint not in ["combined", "creatinine"]:
        raise ValueError("Unknown classification endpoint")
    if endpoint == "creatinine":
        cfg.update(experiment_version="creatinine-baseline-evaluation-v1",
                   target_id="aki_creatinine_observed", target_label="AKI (KDIGO creatinine)")
    data, audit = _prepare(data)
    if endpoint == "combined":
        snapshots = combined_targets(data.snapshots, data.cohort, cfg["horizons_hours"])
        removed = int((~snapshots["combined_at_risk"]).sum())
        snapshots = snapshots[snapshots["combined_at_risk"]].reset_index(drop=True)
        label_prefix, onset_col = "aki_combined_within_", "combined_onset_hours"
    else:
        snapshots = attach_timeline(data.snapshots, data.cohort)
        valid = ((snapshots["hours_since_icu"] < snapshots["censor_hours"]) &
                 ~(snapshots["cr_aki_onset_hours"] <= snapshots["hours_since_icu"]))
        removed = int((~valid).sum())
        snapshots = snapshots[valid].reset_index(drop=True)
        label_prefix, onset_col = "aki_within_", "cr_aki_onset_hours"
    features = cfg["feature_names"]
    X, covered = feature_matrix(data, snapshots, data_config(), feature_names=features)
    # If stored baseline features exist, they must agree before any model is trained.
    if set(features) <= set(snapshots):
        if not np.allclose(X, snapshots[features].to_numpy(float), equal_nan=True, rtol=1e-4, atol=1e-4):
            raise ValueError("Shared feature engine does not reproduce stored baseline columns")
    removed_coverage = int((~covered).sum())
    X, snapshots = X[covered], snapshots.loc[covered].reset_index(drop=True)
    labels = {h: snapshots[f"{label_prefix}{h}h"].to_numpy(float, na_value=np.nan) for h in cfg["horizons_hours"]}
    pipe, fit_audit = fit_classifier(X, labels, snapshots["split"], snapshots["patient_id"],
                                     features, folds=cfg["folds"], seed=cfg["seed"])
    val = snapshots["split"].eq("validation").to_numpy()
    vv = snapshots.loc[val].reset_index(drop=True)
    risks = {h: pipe.predict(X[val], h) for h in pipe.horizons}
    variants = {h: {} for h in pipe.horizons}
    for lag in cfg["cutoff_lags_hours"]:
        xx, ok = feature_matrix(data, vv, data_config(), lag_hours=lag, feature_names=features)
        for h in pipe.horizons:
            p = pipe.predict(xx, h).astype(float)
            p[~ok] = np.nan
            variants[h][lag] = p
    h = cfg["alert_horizon_h"]
    if h not in labels:
        raise ValueError("Alert horizon is not a trained head")
    yy = labels[h][val]
    eligible = np.isfinite(yy)
    selected = select_validation_threshold(yy[eligible], risks[h][eligible],
        {lag: p[eligible] for lag, p in variants[h].items()}, vv.loc[eligible, "split"],
        cfg["threshold_candidates"], **cfg["selection"])
    meta = metadata(data, cfg, endpoint)
    is_synthetic = data.provenance["source"] == "synthetic"
    policy = {"policy_version": meta["run_id"], "adapter": "baseline_lr_v1",
              "intended_use": "synthetic_demo" if is_synthetic else "research",
              "model": {"model_id": ("synthetic-" if is_synthetic else "") + cfg["target_id"] + "-lr",
                        "run_id": meta["run_id"], "family": "lr", "feature_set": "extension_baseline",
                        "feature_order_sha256": digest(features)},
              "target": cfg["target_label"], "monitoring_hours": cfg["monitoring_hours"],
              "alert_horizon_h": h, "threshold": None,
              "stability_assessment_id": selected["assessment_id"],
              "data_config_sha256": meta["data_config_sha256"]}
    if selected["selected"]:
        policy["threshold"] = {
            "value": selected["selected"]["threshold"], "comparison": ">=", "locked": True,
            "validation_run_id": meta["run_id"] + "/validation",
            "policy_version": policy["policy_version"], "monitoring_window": "ICU hours " + "-".join(map(str, cfg["monitoring_hours"])),
            "calibration_version": "Platt on grouped training OOF",
            "selection_basis": "Development baseline: validation F1 with cutoff-stability constraints; " +
                               ("synthetic data only, no clinical performance claim." if is_synthetic else "not final test evaluation."),
            "stability_assessment_id": selected["assessment_id"]}
    metrics = {str(hh): classification_metrics(labels[hh][val], risks[hh],
               policy["threshold"]["value"] if hh == h and policy["threshold"] else None) for hh in pipe.horizons}
    report = {"scope": "synthetic development check" if is_synthetic else "local development baseline",
              "integrity": audit, "removed_not_at_risk": removed, "removed_input_coverage": removed_coverage,
              "training": fit_audit, "validation": metrics, "threshold_selection": selected,
              "cutoff_stability": {str(hh): cutoff_stability(risks[hh], variants[hh]) for hh in pipe.horizons},
              "test_evaluation": "not_run", "clinical_validation": "not_performed"}
    if policy["threshold"]:
        report["first_warning"] = first_warning_metrics(vv["patient_id"], vv["hours_since_icu"],
            vv[onset_col].to_numpy(float), risks[h], policy["threshold"]["value"], h)
    return Experiment(pipe, meta, report, policy)


def trajectory_experiment(data, cfg=None):
    cfg = dict(cfg or config("trajectory.yaml"))
    if cfg["metric"] != "creatinine" or cfg["origin_scope"] != "before_either_known_aki" or cfg["target_scope"] != "before_discharge_death_rrt":
        raise ValueError("This baseline implements only the documented creatinine target scope")
    data, audit = _prepare(data)
    timeline = attach_timeline(data.snapshots, data.cohort)
    at_risk = ((timeline["hours_since_icu"] < timeline["censor_hours"]) &
               ~(timeline["combined_onset_hours"] <= timeline["hours_since_icu"]))
    removed_at_risk = int((~at_risk).sum())
    snapshots = data.snapshots.loc[at_risk.to_numpy()].reset_index(drop=True)
    X, covered = feature_matrix(data, snapshots, data_config(), feature_names=cfg["feature_names"])
    snapshots, X = snapshots.loc[covered].reset_index(drop=True), X[covered]
    targets = trajectory_targets(snapshots, data.cohort, data.target_events, cfg["leads_hours"],
                                 cfg["matching_tolerance_hours"], data.target_export_end_hour)
    model = TrajectoryRegressor(cfg["feature_names"], cfg["leads_hours"], cfg["ridge_alpha"]).fit(X, targets)
    val = snapshots["split"].eq("validation").to_numpy()
    metrics, stability = {}, {}
    for method in ["persistence", "ridge"]:
        predictions = model.predict(X, method)
        metrics[method], stability[method] = {}, {}
        for lead in cfg["leads_hours"]:
            yy = targets.loc[targets["lead_hours"] == lead, "target_value"].to_numpy(float)
            metrics[method][str(lead)] = regression_metrics(yy[val], predictions[lead][val])
        variants = {h: {} for h in cfg["leads_hours"]}
        for lag in cfg["cutoff_lags_hours"]:
            xx, ok = feature_matrix(data, snapshots.loc[val], data_config(), lag_hours=lag, feature_names=cfg["feature_names"])
            result = model.predict(xx, method)
            for lead, values in result.items():
                values[~ok] = np.nan
                variants[lead][lag] = values
        for lead in cfg["leads_hours"]:
            stability[method][str(lead)] = cutoff_stability(predictions[lead][val], variants[lead])
    report = {"scope": "development only", "integrity": audit, "removed_not_at_risk": removed_at_risk,
              "density": {split: density_report(targets[targets["split"] == split]) for split in ["train", "validation"]},
              "validation": metrics, "cutoff_stability": stability, "intervals": "not_estimated",
              "training_split": "train", "test_evaluation": "not_run", "clinical_validation": "not_performed"}
    return Experiment(model, metadata(data, cfg, "trajectory"), report)


def save_experiment(result, output_root=None):
    """Local-only versioned output. Never replace an existing run or write row-level data."""
    root = Path(output_root or ROOT / "artifacts/extensions")
    directory = root / (result.metadata["run_id"] + "-" + str(time.time_ns()))
    directory.mkdir(parents=True, exist_ok=False)
    joblib.dump(result.model, directory / "pipeline.joblib")
    manifest = {**result.metadata, "pipeline_sha256": sha256(directory / "pipeline.joblib")}
    for name, value in [("manifest.json", manifest), ("report.json", result.report)]:
        (directory / name).write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    if result.policy:
        policy = json.loads(json.dumps(result.policy))
        policy["model"].update(model_dir=".", pipeline_sha256=sha256(directory / "pipeline.joblib"))
        policy["artifact_base"] = "policy_directory"
        (directory / "policy.json").write_text(json.dumps(policy, indent=2, allow_nan=False), encoding="utf-8")
    return directory
