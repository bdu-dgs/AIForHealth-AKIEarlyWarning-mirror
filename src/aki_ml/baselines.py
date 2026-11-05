"""Adapter for the replaceable LR + ridge baselines. No automatic clinical-model fallback."""
import copy
import json
from pathlib import Path

import joblib
import numpy as np

from .evaluation import digest
from .research_data import ROOT, data_config, sha256


class ScoredRisks(dict):
    """Existing risk mapping plus optional trajectory arrays, without shared mutable state."""
    def __init__(self, values=None):
        super().__init__(values or {})
        self.trajectory = None


class BaselineModel:
    def __init__(self, experiment, trajectory=None):
        self.pipe, self.policy = experiment.model, copy.deepcopy(experiment.policy)
        self.version = self.policy["model"]["run_id"]
        self.model_id = self.policy["model"]["model_id"]
        self.target = self.policy["target"]
        self.data_cfg = data_config()
        self.trajectory_model = trajectory.model if trajectory else None
        self.trajectory_version = trajectory.metadata["run_id"] if trajectory else None
        self.trajectory_id = ("synthetic-" if trajectory and trajectory.metadata["data"]["source"] == "synthetic" else "") + "creatinine-ridge"
        if trajectory:
            self.policy = trajectory_policy(self.policy, self.trajectory_version)
        if self.trajectory_model and self.trajectory_model.feature_names != self.pipe.feature_names:
            raise ValueError("Risk and attached trajectory feature orders differ")
        self.labels = {f: f.replace("_", " ") for f in self.pipe.feature_names}
        self.units = {"creatinine_last": "mg/dL", "creatinine_delta_prev": "mg/dL",
                      "heart_rate_last": "bpm", "hours_since_icu": "h"}

    @property
    def window(self):
        return self.policy["monitoring_hours"][0], self.policy["monitoring_hours"][-1]

    def predict(self, X):
        output = ScoredRisks()
        for h in self.pipe.horizons:
            risk = self.pipe.predict(X, h)
            pre = self.pipe.preprocessors[h]
            contribution = pre.transform(X) * self.pipe.heads[h].coef_[0] * self.pipe.calibrators[h]["platt"].slope_
            drivers = [[{"feature": f, "label": self.labels[f] + " (LR log-odds)",
                         "value": "missing" if np.isnan(row[j]) else f"{float(row[j]):.4g}",
                         "unit": self.units.get(f, ""), "contribution": float(contribution[i, j])}
                        for j, f in enumerate(self.pipe.feature_names)] for i, row in enumerate(X)]
            output[h] = (risk, drivers)
        if self.trajectory_model:
            output.trajectory = {"metric": "creatinine", "label": "Creatinine baseline forecast", "unit": "mg/dL",
                                 "model_id": self.trajectory_id, "model_version": self.trajectory_version,
                                 "method": "ridge; no uncertainty interval", "values": self.trajectory_model.predict(X)}
        return output

    @classmethod
    def from_policy(cls, path):
        from .experiments import Experiment
        path = Path(path)
        policy = json.loads(path.read_text(encoding="utf-8"))
        base = path.parent if policy.get("artifact_base") == "policy_directory" else ROOT
        model_dir = (base / policy["model"]["model_dir"]).resolve()
        pipeline_path = model_dir / "pipeline.joblib"
        if sha256(pipeline_path) != policy["model"]["pipeline_sha256"]:
            raise ValueError("Baseline pipeline hash mismatch")
        if policy["data_config_sha256"] != sha256(ROOT / "configs/default.yaml"):
            raise ValueError("Baseline feature configuration mismatch")
        # joblib is executable serialization: load only locally trusted, hash-verified artifacts.
        pipe = joblib.load(pipeline_path)
        if digest(pipe.feature_names) != policy["model"]["feature_order_sha256"] or pipe.family != "lr":
            raise ValueError("Baseline family/feature order mismatch")
        return cls(Experiment(pipe, {"run_id": policy["model"]["run_id"]}, {}, policy))


def synthetic_demo_model():
    from .experiments import classification_experiment, trajectory_experiment
    from .synthetic import make_research_data
    data = make_research_data()
    return BaselineModel(classification_experiment(data), trajectory_experiment(data))


def trajectory_policy(policy, version):
    policy = copy.deepcopy(policy)
    policy["policy_version"] = policy["policy_version"] + "-traj-" + digest(version)[:10]
    if policy.get("threshold"):
        policy["threshold"]["policy_version"] = policy["policy_version"]
    return policy


class WithTrajectory:
    """Compose a risk adapter with an independently versioned trajectory model."""
    def __init__(self, risk_model, directory):
        directory = Path(directory)
        meta = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if meta["kind"] != "trajectory" or sha256(directory / "pipeline.joblib") != meta["pipeline_sha256"]:
            raise ValueError("Trajectory artifact kind/hash mismatch")
        if meta["data_config_sha256"] != sha256(ROOT / "configs/default.yaml"):
            raise ValueError("Trajectory feature configuration mismatch")
        self.risk_model = risk_model
        self.trajectory_model = joblib.load(directory / "pipeline.joblib")
        self.trajectory_version = meta["run_id"]
        self.trajectory_id = ("synthetic-" if meta["data"]["source"] == "synthetic" else "") + "creatinine-ridge"
        self.indices = [risk_model.pipe.feature_names.index(f) for f in self.trajectory_model.feature_names]
        if digest(self.trajectory_model.feature_names) != meta["feature_order_sha256"]:
            raise ValueError("Trajectory feature order mismatch")
        self.policy = trajectory_policy(risk_model.policy, self.trajectory_version)
        if meta["data"]["source"] == "synthetic":
            self.policy["intended_use"] = "synthetic_demo"

    def __getattr__(self, name):
        return getattr(self.risk_model, name)

    def predict(self, X):
        output = ScoredRisks(self.risk_model.predict(X))
        output.trajectory = {
            "metric": "creatinine", "label": "Creatinine baseline forecast", "unit": "mg/dL",
            "model_id": self.trajectory_id,
            "model_version": self.trajectory_version, "method": "ridge; no uncertainty interval",
            "values": self.trajectory_model.predict(X[:, self.indices])}
        return output
