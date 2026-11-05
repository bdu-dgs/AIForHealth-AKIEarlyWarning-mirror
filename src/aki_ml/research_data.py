"""Research I/O and as-of features, separate from notebooks and website storage."""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .features import compute_features, static_feature_names
from .scoring import coverage

ROOT = Path(__file__).resolve().parents[2]
BASELINE_FEATURES = ["hours_since_icu", "creatinine_last", "creatinine_delta_prev", "heart_rate_last"]


def sha256(path):
    value = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


@dataclass
class ResearchData:
    snapshots: pd.DataFrame
    cohort: pd.DataFrame
    splits: pd.DataFrame
    events: pd.DataFrame
    target_events: pd.DataFrame
    target_export_end_hour: float
    provenance: dict = field(default_factory=dict)


def validate_data(data):
    s, c, m = data.snapshots, data.cohort, data.splits
    if m["patient_id"].duplicated().any() or not m["split"].isin(["train", "validation", "test"]).all():
        raise ValueError("Invalid fixed patient split manifest")
    if c["patient_id"].duplicated().any() or c["stay_id"].duplicated().any():
        raise ValueError("Expected one selected ICU stay per patient")
    expected = m.set_index("patient_id")["split"]
    if not s["split"].eq(s["patient_id"].map(expected)).all():
        raise ValueError("Snapshot split disagrees with fixed patient manifest")
    if s.duplicated(["patient_id", "stay_id", "hours_since_icu"]).any():
        raise ValueError("Duplicate snapshot keys")
    mapping = c.set_index("stay_id")["patient_id"]
    if not s["patient_id"].eq(s["stay_id"].map(mapping)).all():
        raise ValueError("Patient/stay join mismatch")
    if not np.isfinite(s["hours_since_icu"].to_numpy(float)).all():
        raise ValueError("Nonfinite snapshot time")
    for events in [data.events, data.target_events]:
        if not events["stay_id"].isin(c["stay_id"]).all():
            raise ValueError("Event references an unknown stay")
        if not np.isfinite(events[["hours_since_icu", "value"]].to_numpy(float)).all():
            raise ValueError("Nonfinite event")
    return {"patients": len(c), "snapshots": len(s), "fixed_split_verified": True,
            "split_counts": {str(k): int(v) for k, v in s.groupby("split")["patient_id"].nunique().items()}}


def development_data(data):
    """Remove test outcomes/events before modelling. Preserve the original split manifest."""
    validate_data(data)
    snapshots = data.snapshots[data.snapshots["split"].isin(["train", "validation"])].reset_index(drop=True)
    stays = snapshots["stay_id"].unique()
    return ResearchData(snapshots, data.cohort[data.cohort["stay_id"].isin(stays)].copy(), data.splits.copy(),
                        data.events[data.events["stay_id"].isin(stays)].copy(),
                        data.target_events[data.target_events["stay_id"].isin(stays)].copy(),
                        data.target_export_end_hour, data.provenance)


def feature_matrix(data, snapshots, cfg, lag_hours=0, feature_names=None):
    """Baseline features at input cutoff; prediction origin remains fixed.

    Only latest values/deltas are supported here (no shifted rolling windows). Model
    age is the fixed origin hour. Reuses notebook 01's lab-delay and feature engine.
    """
    feature_names = feature_names or BASELINE_FEATURES
    if not set(feature_names) <= set(BASELINE_FEATURES) or lag_hours < 0:
        raise ValueError("Unsupported baseline features or negative cutoff lag")
    cohort = data.cohort.reset_index(drop=True)
    lookup = dict(zip(cohort["stay_id"], range(len(cohort))))
    ev = data.events.rename(columns={"hours_since_icu": "t", "source_itemid": "itemid"}).copy()
    ev["stay_idx"] = ev["stay_id"].map(lookup).astype(int)
    if "itemid" not in ev:
        ev["itemid"] = 0
    static = pd.DataFrame(np.nan, index=cohort.index, columns=static_feature_names(cfg))
    for name in static:
        if name in cohort:
            static[name] = cohort[name].to_numpy()
    origins = snapshots["hours_since_icu"].to_numpy(float)
    cutoffs = origins - lag_hours
    qs = snapshots["stay_id"].map(lookup).to_numpy(int)
    X = compute_features(ev, static, np.zeros(len(snapshots)), qs, cutoffs, cfg, feature_names)
    if "hours_since_icu" in feature_names:
        X[:, feature_names.index("hours_since_icu")] = origins
    ok = np.zeros(len(snapshots), bool)
    groups = {stay: group for stay, group in ev.groupby("stay_idx", sort=False)}
    for stay in np.unique(qs):
        rows = np.flatnonzero(qs == stay)
        ok[rows] = coverage(groups.get(stay, ev.iloc[:0]), cutoffs[rows], cfg)
    ok &= cutoffs >= 0
    return X, ok


def load_data(data_root, *, trajectory_source=None):
    """Read versioned notebook 01 artifacts. Test snapshot/event rows are not loaded."""
    root = Path(data_root)
    manifest_path = root / "dataset_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Notebook 01 dataset_manifest.json is required; use --synthetic for a development check")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    needed = ["datasets/snapshot_dataset.parquet", "datasets/cohort.parquet",
              "splits/patient_split.parquet", "datasets/event_stream.parquet"]
    for rel in needed:
        if rel not in manifest["files"] or sha256(root / rel) != manifest["files"][rel]["sha256"]:
            raise ValueError(f"Dataset hash mismatch: {rel}")
    splits = pd.read_parquet(root / needed[2])
    dev_ids = splits.loc[splits["split"].isin(["train", "validation"]), "patient_id"].tolist()
    cohort = pd.read_parquet(root / needed[1], filters=[("patient_id", "in", dev_ids)])
    columns = ["patient_id", "stay_id", "split", *BASELINE_FEATURES]
    for h in manifest["config"]["labels"]["horizons_hours"]:
        columns += [f"eligible_{h}h", f"aki_within_{h}h", f"eligible_uo_{h}h", f"aki_uo_within_{h}h"]
    snapshots = pd.read_parquet(root / needed[0], columns=columns, filters=[("split", "in", ["train", "validation"])])
    stays = cohort["stay_id"].tolist()
    events = pd.read_parquet(root / needed[3], filters=[("stay_id", "in", stays),
                            ("concept", "in", ["creatinine", "heart_rate"])])
    target_events = events
    end = manifest["config"]["monitoring"]["last_snapshot_hour"]
    provenance = {"source": "local_notebook_01", "dataset_version": manifest["dataset_version"],
                  "dataset_manifest_sha256": sha256(manifest_path), "data_config_sha256": manifest["config_sha256"], "files": {rel: manifest["files"][rel]["sha256"] for rel in needed}}
    if trajectory_source is not None:
        source = Path(trajectory_source)
        # Sidecar declares extraction completeness, not the time of the last sparse measurement.
        info = json.loads(source.with_suffix(".json").read_text(encoding="utf-8"))
        if info["dataset_version"] != manifest["dataset_version"] or info["sha256"] != sha256(source):
            raise ValueError("Trajectory source version/hash mismatch")
        if (info.get("cohort_sha256") != manifest["files"]["datasets/cohort.parquet"]["sha256"]
                or info.get("units", {}).get("creatinine") != "mg/dL"
                or info.get("complete_standardized_lab_view_confirmed") is not True):
            raise ValueError("Trajectory source must declare matching cohort, units and complete extraction")
        end = float(info["export_end_hour"])
        if not np.isfinite(end) or end <= 0:
            raise ValueError("Invalid trajectory export endpoint")
        target_events = pd.read_parquet(source, filters=[("stay_id", "in", stays), ("concept", "==", "creatinine")])
        provenance["trajectory_source"] = info
    data = ResearchData(snapshots, cohort, splits, events, target_events, end, provenance)
    validate_data(data)
    return data


def data_config():
    return yaml.safe_load((ROOT / "configs/default.yaml").read_text(encoding="utf-8"))
