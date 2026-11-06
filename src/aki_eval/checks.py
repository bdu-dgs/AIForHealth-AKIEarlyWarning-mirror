"""Reusable aggregate-only artifact/leakage checks. No patient keys enter details."""
from pathlib import Path
import hashlib

import numpy as np
import pandas as pd
import pyarrow as pa


def _result(ok, **counts):
    return bool(ok), "; ".join(f"{key}={value}" for key, value in counts.items())


def unique_keys(frame, keys):
    missing = set(keys) - set(frame)
    if missing:
        return _result(False, missing_columns=len(missing))
    duplicates = int(frame.duplicated(list(keys)).sum())
    nulls = int(frame[list(keys)].isna().any(axis=1).sum())
    return _result(duplicates == nulls == 0, rows=len(frame), duplicate_keys=duplicates, null_keys=nulls)


def probability_bounds(frame, columns=("risk_raw", "risk_platt", "risk_isotonic")):
    if not set(columns) <= set(frame):
        return _result(False, missing_probability_columns=len(set(columns) - set(frame)))
    values = frame[list(columns)].to_numpy(float)
    bad = int((~np.isfinite(values) | (values < 0) | (values > 1)).sum())
    return _result(bad == 0, probability_values=values.size, invalid_values=bad)


def _dtype_matches(actual, recorded):
    if recorded == "category":
        return pa.types.is_dictionary(actual)
    if recorded in ("string", "object"):
        return pa.types.is_string(actual) or pa.types.is_large_string(actual)
    if recorded.startswith("datetime64"):
        return pa.types.is_timestamp(actual) and actual.unit == "ns"
    expected = {"float32": pa.float32(), "float64": pa.float64(), "bool": pa.bool_(),
                "int64": pa.int64(), "int16": pa.int16(), "int8": pa.int8(), "Int8": pa.int8()}
    return recorded in expected and actual == expected[recorded]


def schema_and_versions(schema, dictionary, dataset_manifest, run_manifest, policy,
                        model_path, *, config_path=None, versions=(), prediction_versions=()):
    required = []
    for value in dataset_manifest["columns"].values():
        required.extend(value if isinstance(value, list) else [value])
    missing = len(set(required) - set(schema.names))
    mismatches = 0
    for row in dictionary.itertuples(index=False):
        mismatches += row.feature not in schema.names or not _dtype_matches(
            schema.field(row.feature).type, str(row.dtype))
    expected = dataset_manifest["dataset_version"]
    version_ok = (run_manifest["dataset"]["version"] == expected and
                  policy["model"]["run_id"] == run_manifest["run_id"] and
                  all(str(v) == expected for v in versions) and
                  all(str(v) == run_manifest["run_id"] for v in prediction_versions))
    p = Path(model_path)
    model_ok = p.is_file() and hashlib.file_digest(p.open("rb"), "sha256").hexdigest() == policy["model"]["pipeline_sha256"]
    config_ok = dataset_manifest["config_sha256"] == run_manifest["dataset"]["config_sha256"]
    if config_path is not None:
        config_ok = config_ok and hashlib.file_digest(Path(config_path).open("rb"), "sha256").hexdigest() == dataset_manifest["config_sha256"]
    return _result(missing == mismatches == 0 and version_ok and model_ok and config_ok,
                   missing_columns=missing, dtype_mismatches=mismatches, versions_match=version_ok,
                   model_hash_matches=model_ok, config_hash_matches=config_ok)


def missing_predictions(snapshots, predictions, horizons):
    keys = ["patient_id", "hours_since_icu"]
    if snapshots.duplicated(keys).any():
        return _result(False, duplicate_snapshot_keys=True)
    expected = pd.MultiIndex.from_frame(snapshots[keys])
    counts = {}
    for h in horizons:
        available = pd.MultiIndex.from_frame(predictions.loc[predictions["horizon_h"] == h, keys])
        needed = expected[snapshots[f"eligible_{h}h"].to_numpy(bool)]
        counts[f"missing_{h}h"] = int((~needed.isin(available)).sum())
    return _result(not any(counts.values()), **counts)


def split_disjointness(manifest, *frames):
    if manifest["patient_id"].isna().any() or manifest["patient_id"].duplicated().any():
        return _result(False, manifest_unique=False)
    if not manifest["split"].isin(["train", "validation", "test"]).all():
        return _result(False, invalid_split=True)
    lookup = manifest.set_index("patient_id")["split"].astype(str)
    wrong = sum(int((frame["patient_id"].map(lookup).astype("string") !=
                     frame["split"].astype("string")).fillna(True).sum()) for frame in frames)
    return _result(wrong == 0, patients=len(manifest), mismatched_or_unassigned_rows=wrong)


def one_stay_per_patient(manifest, snapshots):
    cols = ["patient_id", "stay_id", "icustay_id"]
    if not set(cols) <= set(manifest) or not set(cols) <= set(snapshots):
        return _result(False, missing_identity_columns=True)
    pairs = manifest[cols].drop_duplicates()
    bad = (pairs.isna().any().any() or pairs["patient_id"].duplicated().any() or
           pairs["stay_id"].duplicated().any() or pairs["icustay_id"].duplicated().any())
    if bad:
        return _result(False, frozen_mapping_not_one_to_one=True)
    expected = pairs.set_index("patient_id")
    wrong = 0
    for column in cols[1:]:
        wrong += int((snapshots[column].astype("string") !=
                      snapshots["patient_id"].map(expected[column]).astype("string")).fillna(True).sum())
    return _result(wrong == 0, mismatched_identity_rows=wrong)


def chronology(snapshots, tolerance_hours=1e-8):
    required = ["icu_intime", "snapshot_time", "censoring_time", "hours_since_icu"]
    if not set(required) <= set(snapshots):
        return _result(False, missing_time_columns=True)
    start, t, end = (pd.to_datetime(snapshots[c]) for c in required[:3])
    hours = snapshots["hours_since_icu"].to_numpy(float)
    derived = (t - start).dt.total_seconds().to_numpy() / 3600
    bad = (start.isna() | t.isna() | end.isna() | (t < start) | (t >= end)).to_numpy()
    bad |= ~np.isfinite(hours) | ~np.isclose(hours, derived, rtol=0, atol=tolerance_hours)
    return _result(not bad.any(), invalid_chronology_rows=int(bad.sum()))


def label_definition(snapshots, predictions, horizons):
    onset = pd.to_datetime(snapshots["creatinine_aki_onset_time"])
    t = pd.to_datetime(snapshots["snapshot_time"])
    followup = snapshots["followup_hours"].to_numpy(float)
    keys = ["patient_id", "hours_since_icu"]
    if snapshots.duplicated(keys).any():
        return _result(False, duplicate_snapshot_keys=True)
    bad_labels = bad_predictions = 0
    for h in horizons:
        positive = ((onset > t) & (onset <= t + pd.Timedelta(hours=h))).to_numpy()
        eligible = positive | (followup >= h)
        stored_eligible = snapshots[f"eligible_{h}h"].to_numpy(bool)
        y = snapshots[f"aki_within_{h}h"].to_numpy(float, na_value=np.nan)
        bad = (stored_eligible != eligible) | (eligible & (y != positive.astype(int))) | (~eligible & ~np.isnan(y))
        bad_labels += int(bad.sum())
        expected = snapshots[keys].copy()
        expected["expected_eligible"] = stored_eligible
        expected["expected_label"] = y
        merged = predictions[predictions["horizon_h"] == h].merge(expected, on=keys, how="left", validate="many_to_one")
        yy = merged["label"].to_numpy(float, na_value=np.nan)
        ey = merged["expected_label"].to_numpy(float, na_value=np.nan)
        aligned = merged["expected_eligible"].notna().to_numpy()
        aligned &= merged["eligible"].to_numpy(bool) == merged["expected_eligible"].fillna(False).to_numpy(bool)
        aligned &= (yy == ey) | (np.isnan(yy) & np.isnan(ey))
        bad_predictions += int((~aligned).sum())
    return _result(bad_labels == bad_predictions == 0, invalid_snapshot_labels=bad_labels,
                   inconsistent_prediction_labels=bad_predictions)


def pre_onset_snapshots(snapshots):
    t, onset = (pd.to_datetime(snapshots[c]) for c in ["snapshot_time", "creatinine_aki_onset_time"])
    bad = onset.notna() & (t >= onset)
    return _result(not bad.any(), at_or_after_onset_rows=int(bad.sum()))


def features_use_past(stored, replayed, future_injected, *, min_rows=2000, rtol=1e-5, atol=1e-4):
    arrays = [np.asarray(v, float) for v in [stored, replayed, future_injected]]
    if any(a.shape != arrays[0].shape for a in arrays) or arrays[0].ndim != 2 or len(arrays[0]) < min_rows:
        return _result(False, insufficient_or_unaligned_sample=True)
    parity = np.isclose(arrays[0], arrays[1], equal_nan=True, rtol=rtol, atol=atol)
    future = np.isclose(arrays[1], arrays[2], equal_nan=True, rtol=rtol, atol=atol)
    return _result(parity.all() and future.all(), sampled_rows=len(arrays[0]),
                   compared_features=arrays[0].shape[1], replay_mismatches=int((~parity).sum()),
                   future_injection_mismatches=int((~future).sum()))


def no_post_snapshot_urine(original, urine_truncated, feature_names, *, rtol=1e-5, atol=1e-4):
    columns = [i for i, f in enumerate(feature_names) if f.startswith("urine_") or f == "fluid_balance_ml_24h"]
    original, truncated = np.asarray(original, float), np.asarray(urine_truncated, float)
    if not columns or original.ndim != 2 or original.shape != truncated.shape or len(original) == 0:
        return _result(False, no_aligned_urine_features=True)
    ok = np.isclose(original[:, columns], truncated[:, columns], equal_nan=True, rtol=rtol, atol=atol)
    return _result(ok.all(), urine_related_features=len(columns), changed_values=int((~ok).sum()))


def no_test_fitting(run_manifest):
    # This checks the provenance declaration, not inaccessible historical training code/data.
    test = run_manifest.get("test_split")
    unused = test == "never loaded in notebook 02" or (
        isinstance(test, dict) and test.get("loaded") is False)
    calibration = run_manifest.get("calibration", {}).get("fit_on") == "train_oof"
    checks = run_manifest.get("checks", {})
    loaded = checks.get("part3_loaded_data", {}).get("no test rows loaded") is True
    cv = checks.get("part4_folds_and_features", {}).get("validation rows never enter CV folds") is True
    return _result(unused and calibration and loaded and cv, test_declared_unused=unused,
                   calibration_on_train_oof=calibration, historical_training_checks_recorded=loaded and cv,
                   evidence="manifest_declaration_only")
