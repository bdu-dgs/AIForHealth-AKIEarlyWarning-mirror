"""Stream a separate future-truth file from notebook 01's complete standardized lab view."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .research_data import sha256


def export_trajectory_source(data_root, lab_view, output, end_hour, *, complete_lab_view=False, batch_rows=100000):
    if not complete_lab_view:
        raise ValueError("Explicitly confirm that this is the complete standardized lab view, not the 72 h event export")
    if not np.isfinite(end_hour) or end_hour <= 0 or batch_rows <= 0:
        raise ValueError("Invalid target export limits")
    root, lab_view, output = Path(data_root), Path(lab_view), Path(output)
    manifest = json.loads((root / "dataset_manifest.json").read_text(encoding="utf-8"))
    cohort_file = root / "datasets/cohort.parquet"
    if sha256(cohort_file) != manifest["files"]["datasets/cohort.parquet"]["sha256"]:
        raise ValueError("Cohort artifact hash mismatch")
    cohort = pd.read_parquet(cohort_file, columns=["patient_id", "stay_id", "subject_id", "intime", "censor_hours", "split"],
                             filters=[("split", "in", ["train", "validation"])])
    if cohort["subject_id"].duplicated().any() or cohort["stay_id"].duplicated().any():
        raise ValueError("Expected one selected stay per source patient")
    cohort["intime"] = pd.to_datetime(cohort["intime"])
    source = pq.ParquetFile(lab_view)
    columns = ["subject_id", "charttime", "concept", "value", "itemid"]
    if not set(columns) <= set(source.schema_arrow.names):
        raise ValueError("Expected notebook 01's standardized lab view schema")
    sidecar, temporary = output.with_suffix(".json"), output.with_suffix(".parquet.partial")
    if any(p.exists() for p in [output, sidecar, temporary]):
        raise FileExistsError("Choose new output names; prior target exports are preserved")
    output.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([("stay_id", pa.string()), ("hours_since_icu", pa.float64()),
                        ("concept", pa.string()), ("value", pa.float64()), ("source_itemid", pa.int64())])
    rows = 0
    with pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
        for batch in source.iter_batches(batch_size=batch_rows, columns=columns):
            frame = batch.to_pandas()
            frame = frame[frame["concept"] == "creatinine"].merge(cohort, on="subject_id", how="inner", validate="many_to_one")
            hours = (pd.to_datetime(frame["charttime"]) - frame["intime"]) / pd.Timedelta(hours=1)
            keep = (hours >= 0) & (hours <= end_hour) & (hours < frame["censor_hours"])
            frame, hours = frame.loc[keep], hours.loc[keep]
            if not np.isfinite(frame["value"].to_numpy(float)).all():
                raise ValueError("Nonfinite value in the standardized lab view")
            out = pd.DataFrame({"stay_id": frame["stay_id"], "hours_since_icu": hours, "concept": "creatinine",
                                "value": frame["value"], "source_itemid": frame["itemid"]})
            writer.write_table(pa.Table.from_pandas(out, schema=schema, preserve_index=False))
            rows += len(out)
    temporary.rename(output)
    info = {"dataset_version": manifest["dataset_version"], "sha256": sha256(output),
            "export_start_hour": 0, "export_end_hour": float(end_hour), "rows": rows,
            "split_scope": ["train", "validation"], "complete_standardized_lab_view_confirmed": True,
            "source_sha256": sha256(lab_view), "cohort_sha256": sha256(cohort_file),
            "units": {"creatinine": "mg/dL"},
            "identity_rule": "original selected stay via subject_id and ICU-relative interval; no cohort reselection"}
    sidecar.write_text(json.dumps(info, indent=2, allow_nan=False), encoding="utf-8")
    return info
