"""Local-only loading and frozen study setup. Test features/labels are not read."""
from importlib import metadata
from pathlib import Path
import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from .policies import AlertRule, prepare
from .protocol import digest, freeze_prespec, sha256, threshold_grid, validation_roles, write_json


PRED_COLUMNS = ["run_id","family","feature_set","horizon_h","prediction_set","patient_id","stay_id","icustay_id",
                "split","cv_fold","snapshot_time","hours_since_icu","creatinine_aki_onset_time",
                "eligible","label","risk_raw","risk_platt","risk_isotonic"]


def _source_files(root):
    paths = sorted((root/"src/aki_eval").glob("*.py"))
    paths += [root/"src/aki_ml"/name for name in ["models.py","features.py","alerts.py"]]
    return {p.relative_to(root).as_posix():p for p in paths}


class StudyData:
    def __init__(self, root, config_path=None, data_root=None, output_root=None):
        self.root = Path(root).resolve()
        config_path = Path(config_path or self.root/"configs/alert_policy_v1.yaml")
        self.config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.data_root = Path(data_root or self.root.parent/"Cleaned").resolve()
        self.policy_path = self.root/"artifacts/demo_i/policy.json"
        self.policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        cfg = self.config
        if cfg["selection_data"] != "validation_selection" or cfg["confirmation_data"] != "validation_confirmation":
            raise ValueError("This experiment selects on validation, with a fixed separate patient confirmation role")
        if cfg["training_oof_role"] != "diagnostic_only" or cfg["feature_set"] != "no_dc":
            raise ValueError("Unexpected data role or deployable feature set")
        self.rules = [AlertRule(**rule) for rule in cfg["candidates"]]
        if len({r.name for r in self.rules}) != len(self.rules):
            raise ValueError("Duplicate rule names")
        self.thresholds = threshold_grid(cfg["thresholds"])
        run = cfg["model_run_id"]
        self.paths = {
            "snapshots":next((p for p in [self.data_root/"datasets/snapshot_dataset_validation.parquet",
                                         self.data_root/"datasets/snapshot_dataset.parquet"] if p.is_file()),
                             self.data_root/"datasets/snapshot_dataset_validation.parquet"),
            "events":next((p for p in [self.data_root/"datasets/event_stream_validation.parquet",
                                      self.data_root/"datasets/event_stream.parquet"] if p.is_file()),
                          self.data_root/"datasets/event_stream_validation.parquet"),
            "splits":self.data_root/"splits/patient_split.parquet",
            "dictionary":self.data_root/"datasets/feature_dictionary.csv",
            "dataset_manifest":self.data_root/"dataset_manifest.json",
            "run_manifest":self.root/"artifacts/models"/run/"run_manifest.json",
            "predictions":self.root/"artifacts/predictions"/(run+".parquet"),
            "model":self.root/self.policy["model"]["model_dir"]/"pipeline.joblib",
            "feature_order":self.root/self.policy["model"]["model_dir"]/"feature_order.json",
            "policy":self.policy_path,"dataset_config":self.root/"configs/default.yaml",
            "evaluation_config":config_path,
        }
        for key,path in self.paths.items():
            if not path.is_file():
                raise FileNotFoundError("Required local artifact missing: "+key)
        if sha256(self.paths["model"]) != self.policy["model"]["pipeline_sha256"]:
            raise ValueError("Model hash differs from fixed Demo I")
        if sha256(self.paths["feature_order"]) != self.policy["model"]["feature_order_sha256"]:
            raise ValueError("Feature order hash differs from fixed Demo I")
        self.dataset_manifest = json.loads(self.paths["dataset_manifest"].read_text(encoding="utf-8"))
        self.run_manifest = json.loads(self.paths["run_manifest"].read_text(encoding="utf-8"))
        expected_software = self.run_manifest.get("software", {})
        wrong = {name: metadata.version(name) for name in
                 ["numpy","pandas","pyarrow","scikit-learn","lightgbm","scipy","joblib"]
                 if metadata.version(name) != expected_software.get(name)}
        if wrong:
            raise RuntimeError("Model runtime versions differ from training: "+str(wrong))
        if (cfg["model_run_id"],cfg["family"],cfg["feature_set"]) != (
                self.policy["model"]["run_id"],self.policy["model"]["family"],self.policy["model"]["feature_set"]):
            raise ValueError("Evaluation identity differs from the fixed model")
        self.data_config = yaml.safe_load(self.paths["dataset_config"].read_text(encoding="utf-8"))
        self.dictionary = pd.read_csv(self.paths["dictionary"])
        order = json.loads(self.paths["feature_order"].read_text(encoding="utf-8"))
        self.features = order if isinstance(order,list) else order.get("feature_names",order.get("features"))
        self.inputs_hash = {name:sha256(path) for name,path in self.paths.items()}
        sources = _source_files(self.root)
        self.source_hash = {name:sha256(path) for name,path in sources.items()}
        run_key = digest({"config":cfg,"inputs":self.inputs_hash,"source":self.source_hash})[:16]
        self.output_root = Path(output_root or self.root/"artifacts/handoff/kejin")
        self.out = self.output_root/(cfg["version"]+"-"+run_key)
        self.prespec = freeze_prespec(self.out,cfg,self.paths,sources)
        write_json(self.output_root/"LATEST.json",{"run_directory":self.out.name,
                  "prespec_sha256":self.prespec["prespec_sha256"]})
        self.snapshots = self.predictions = self.split_manifest = None
        self.frames = None
        self.baseline = AlertRule(name="demo-v1",**{
            k:self.policy["alert_policy"][k] for k in ["sustain_hours","cooldown_hours","rise_ratio","rise_window_hours"]})
        pa.set_cpu_count(2);pa.set_io_thread_count(2)

    def load(self):
        if self.frames is not None:
            return self.frames
        cfg = self.config
        # Outcome/snapshot data remain restricted to validation; only saved OOF predictions are used for train diagnostics.
        self.snapshots = pq.read_table(self.paths["snapshots"],filters=[("split","=","validation")]).to_pandas()
        self.split_manifest = pd.read_parquet(self.paths["splits"])
        self.predictions = pq.read_table(self.paths["predictions"],
            filters=[("family","=",cfg["family"]),("feature_set","=",cfg["feature_set"]),
                     ("prediction_set","in",["train_oof","validation"]),("horizon_h","in",cfg["horizons"])],
            columns=PRED_COLUMNS).to_pandas()
        for table in [self.snapshots,self.split_manifest,self.predictions]:
            table["patient_id"] = table["patient_id"].astype(str)
            if "stay_id" in table:table["stay_id"] = table["stay_id"].astype(str)
        role = validation_roles(self.snapshots["patient_id"],cfg["seed"],cfg["validation_selection_fraction"])
        self.snapshots["evaluation_role"] = role
        lookup = self.snapshots.drop_duplicates("patient_id").set_index("patient_id")["evaluation_role"]
        h = cfg["alert_horizon_h"]
        primary = self.predictions[self.predictions["horizon_h"]==h].copy()
        lo,hi=cfg["monitoring_hours"]
        primary=primary[primary["hours_since_icu"].between(lo,hi)]
        primary["risk"]=primary["risk_platt"]
        val=primary[primary["prediction_set"]=="validation"].copy()
        train=primary[primary["prediction_set"]=="train_oof"].copy()
        if not set(train["split"])=={"train"} or not set(val["split"])=={"validation"}:
            raise ValueError("Unexpected split/prediction-set role")
        if set(train["patient_id"]) & set(val["patient_id"]):
            raise ValueError("Training and validation patients overlap")
        val["evaluation_role"]=val["patient_id"].map(lookup)
        if val["evaluation_role"].isna().any():
            raise ValueError("Validation predictions lack a snapshot role")
        self.frames={"train_oof":prepare(train,h),
                     "validation":prepare(val,h),
                     "selection":prepare(val[val["evaluation_role"]=="selection"],h),
                     "confirmation":prepare(val[val["evaluation_role"]=="confirmation"],h)}
        return self.frames

    def metadata(self):
        frames = self.load()
        return {"prespec_sha256":self.prespec["prespec_sha256"],"output_directory":str(self.out),
                "patients":{role:int(frame["patient_id"].nunique()) for role,frame in frames.items()},
                "rows":{role:len(frame) for role,frame in frames.items()},
                "baseline_validation_previously_viewed":True,
                "confirmation_interpretation":"internal patient holdout for this candidate; not an untouched final test",
                "static_service_timing":"provided admission inputs and stored hourly service states; raw source timing not independently available",
                "metric_denominator":"scorable monitored hours / 24; non_aki means no target event within evaluated horizon"}
