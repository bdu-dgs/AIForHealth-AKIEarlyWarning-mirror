"""Versioned admission inputs; never recover a nonlinear denominator from rounded features."""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .features import STABLE_FEATURE_ENGINE, static_feature_names

ADMISSION_INPUT_VERSION = "admission_inputs_v2"
CENSORED = r"^\s*(?:<|>|LESS\s*TH[AE]N|GREATE\w*\s*TH[AE]N)\s*=?\s*(\d[\d,]*\.?\d*|\.\d+)"
CEILING = r"^\s*(\d+\.?\d*)\s+IS\s+HIGHEST\s+MEASURED"


def file_hash(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def reconstruct_baselines(raw_path, snapshots, config, progress=None):
    """Original baseline_v1 rule at float64, after the source-view float32/decimal normalization.

    Returns subject-indexed baseline, pre-ICU minimum, history median, and scanned
    row count. Missing baselines remain missing. No patient rows are logged.
    """
    required = {"patient_id", "subject_id", "stay_id", "icu_intime"}
    if not required <= set(snapshots):
        raise ValueError("Baseline reconstruction requires explicit patient/stay/admission keys")
    first = snapshots[list(required)].drop_duplicates()
    if first["subject_id"].duplicated().any() or first["patient_id"].duplicated().any() or first["stay_id"].duplicated().any():
        raise ValueError("Expected one selected stay per patient/subject")
    first = first.set_index("subject_id")
    spec = config["sources"]["labs"]["creatinine"]
    days = config["labels"]["baseline_creatinine"]["history_window_days"]
    pre_days = config["labels"]["creatinine_kdigo"]["rel_window_days"]
    parts, scanned = [], 0
    for chunk in pd.read_csv(raw_path, compression="infer", usecols=["SUBJECT_ID", "ITEMID", "CHARTTIME", "VALUE", "VALUENUM"],
            dtype={"SUBJECT_ID": "int32", "ITEMID": "int32", "CHARTTIME": "string", "VALUE": "string", "VALUENUM": "float32"},
            chunksize=1_000_000):
        scanned += len(chunk)
        selected = chunk[chunk["ITEMID"].isin(spec["itemids"]) & chunk["SUBJECT_ID"].isin(first.index)].copy()
        if len(selected):
            text = selected["VALUE"].str.upper()
            parsed = pd.to_numeric(text.str.extract(CENSORED, expand=False).str.replace(",", ""), errors="coerce")
            parsed = parsed.fillna(pd.to_numeric(text.str.extract(CEILING, expand=False), errors="coerce"))
            values = selected["VALUENUM"].astype(float).fillna(parsed)
            good = values.between(*spec["range"])
            selected = selected[good]
            # Match the original standardized view, then decimal-safe creatinine record.
            values = values[good].astype(np.float32).astype(float).round(2)
            dates = pd.to_datetime(selected["CHARTTIME"], errors="coerce")
            if dates.isna().any():
                raise ValueError("Selected baseline lab rows contain invalid measurement times")
            origin = selected["SUBJECT_ID"].map(first["icu_intime"])
            hours = (dates-origin)/pd.Timedelta(hours=1)
            inside = hours.between(-24*days[1], 0)
            parts.append(pd.DataFrame({"subject_id": selected.loc[inside, "SUBJECT_ID"].to_numpy(),
                                      "hours": hours[inside].to_numpy(float), "value": values[inside].to_numpy(float)}))
        if progress is not None and scanned % 8_000_000 == 0:
            progress(scanned)
    if parts:
        past = pd.concat(parts, ignore_index=True).groupby(["subject_id", "hours"], as_index=False)["value"].mean()
        pre = past[past["hours"].between(-24*pre_days, 0)].groupby("subject_id")["value"].min()
        history = past[(past["hours"] >= -24*days[1]) & (past["hours"] < -24*(days[0]-1))].groupby("subject_id")["value"].median()
    else:
        pre = history = pd.Series(dtype=float)
    baseline = pre.reindex(first.index).fillna(history.reindex(first.index)).astype(np.float64)
    return baseline, pre, history, scanned


def admission_frame(snapshots, baselines, config):
    names = static_feature_names(config)
    if (snapshots.groupby("stay_id", observed=True)[names].nunique(dropna=False) > 1).any().any():
        raise ValueError("Admission-static inputs vary within a stay")
    rows = snapshots.drop_duplicates("stay_id")[["stay_id", "patient_id", "subject_id", *names]].copy()
    if rows["patient_id"].duplicated().any() or rows["subject_id"].duplicated().any():
        raise ValueError("Admission inputs require one stay per patient")
    for name in names:
        rows[name] = rows[name].astype(np.float64)
    raw = rows["subject_id"].map(baselines).to_numpy(np.float64)
    saved = rows["baseline_cr_admission"].to_numpy(np.float32)
    exported = raw.astype(np.float32)
    if not ((saved == exported) | (np.isnan(saved) & np.isnan(exported))).all():
        raise ValueError("Reconstructed baseline does not match the frozen snapshot baseline")
    rows["baseline_cr_admission"] = raw
    return rows.reset_index(drop=True)


def write_admission_inputs(directory, frame, provenance):
    """Create a new private input artifact; refuse to replace an existing artifact."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise FileExistsError("Admission artifact directory must be new and empty")
    if frame["baseline_cr_admission"].dtype != np.dtype("float64"):
        raise ValueError("Admission baseline must be retained as float64")
    path = directory/"inputs.parquet"
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), path, compression="zstd")
    manifest = {"schema_version": ADMISSION_INPUT_VERSION, "feature_engine_version": STABLE_FEATURE_ENGINE,
                "baseline_rule": "baseline_v1_float64", "baseline_input_dtype": "float64",
                "rows": len(frame), "sha256": file_hash(path), "provenance": provenance}
    with (directory/"manifest.json").open("x", encoding="utf-8") as target:
        json.dump(manifest, target, indent=2, ensure_ascii=False, allow_nan=False)
    return directory


def read_admission_inputs(directory, snapshots, config):
    directory = Path(directory)
    metadata = json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
    path = directory/"inputs.parquet"
    if (metadata["schema_version"] != ADMISSION_INPUT_VERSION or
            metadata["feature_engine_version"] != STABLE_FEATURE_ENGINE or
            metadata["sha256"] != file_hash(path)):
        raise ValueError("Admission input version/hash mismatch")
    if pq.ParquetFile(path).schema_arrow.field("baseline_cr_admission").type != pa.float64():
        raise ValueError("Rounded baseline feature cannot replace float64 admission input")
    frame = pd.read_parquet(path)
    if len(frame) != metadata["rows"]:
        raise ValueError("Admission input row count differs from its manifest")
    names = static_feature_names(config)
    if not {"stay_id", "patient_id", "subject_id", *names} <= set(frame) or frame["stay_id"].duplicated().any() or frame["patient_id"].duplicated().any():
        raise ValueError("Invalid admission input schema or patient/stay mapping")
    first = snapshots.drop_duplicates("stay_id").set_index("stay_id")
    table = frame.set_index("stay_id")
    if not first.index.isin(table.index).all():
        raise ValueError("Admission input artifact lacks requested stays")
    table = table.reindex(first.index)
    if not table["patient_id"].astype(str).eq(first["patient_id"].astype(str)).all():
        raise ValueError("Admission input patient mapping mismatch")
    if "subject_id" in first and not table["subject_id"].eq(first["subject_id"]).all():
        raise ValueError("Admission input subject mapping mismatch")
    saved, actual = first[names].to_numpy(np.float32), table[names].to_numpy(np.float32)
    if not ((saved == actual) | (np.isnan(saved) & np.isnan(actual))).all():
        raise ValueError("Admission input disagrees with frozen snapshot features")
    return table.reset_index()
