"""Recalibrating a transported model on local data (configs/system_recalibration_v1.yaml).

Reads the frozen system-shift-v1 heads (raw scores on validation and test, source Platt), refits Platt on target-system
validation patients and evaluates on target-system test patients. No model is retrained. Aggregate outputs only.
"""
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml

from aki_eval.policies import alert_events
from aki_eval.protocol import freeze_prespec, write_json
from aki_final.test_evaluation import KEYS, LockedEvaluation, alert_summary, per_patient, snapshot_metrics
from aki_ml.models import PlattCalibrator

ALERT_KEYS = ["event_detection_rate", "false_alerts_per_patient_day", "alert_ppv", "alerts_per_patient_day"]


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class RecalibrationStudy:
    def __init__(self, root, config_path=None):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path or self.root / "configs" / "system_recalibration_v1.yaml")
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.ev = LockedEvaluation(self.root)
        self.heads_dir = self.root / "artifacts" / "models" / self.ev.policy["model"]["run_id"] / self.config["source_study"]
        self.out = self.root / self.config["outputs"]
        self.horizons = [int(h) for h in self.config["evaluation"]["horizons"]]
        inputs = {"config": self.config_path, "snapshots": self.ev.paths["snapshots"],
                  "policy": self.root / self.ev.config["policy"]}
        inputs |= {f"head_{v}_{h}h": self.heads_dir / v / f"head_{h}h.joblib"
                   for v in ["train_carevue", "train_metavision"] for h in self.horizons}
        sources = {p.relative_to(self.root).as_posix(): p for p in
                   [self.root / "src" / "aki_report" / "recalibration.py", self.root / "src" / "aki_ml" / "models.py"]}
        self.prespec = freeze_prespec(self.out, self.config, inputs, sources)
        self.seed = int(self.config["seed"])

    def load(self):
        labels = [f"{p}_{h}h" for h in self.horizons for p in ("eligible", "aki_within")]
        meta = pq.read_table(self.ev.paths["snapshots"],
                             columns=["patient_id", "split", "source_system", "hours_since_icu"] + labels).to_pandas()
        meta["patient_id"] = meta["patient_id"].astype(str)
        meta["hours_since_icu"] = meta["hours_since_icu"].round().astype(int)
        split = meta["split"].astype(str).to_numpy()
        self.rows = {s: np.flatnonzero(split == s) for s in ["validation", "test"]}   # order of the cached raw scores
        self.meta = meta
        self.heads = {(v, h): joblib.load(self.heads_dir / v / f"head_{h}h.joblib")
                      for v in ["train_carevue", "train_metavision"] for h in self.horizons}
        self.test_frame = self.ev.load()

    def part(self, s, system):
        m = self.meta.iloc[self.rows[s]].reset_index(drop=True)
        return m, (m["source_system"] == system).to_numpy()

    def risks(self, model, h, s, platt):
        return platt.predict(self.heads[(model, h)]["raw"][s])

    def fit_platt(self, model, h, system, patients=None):
        m, sel = self.part("validation", system)
        el = sel & m[f"eligible_{h}h"].to_numpy(bool)
        if patients is not None:
            el &= m["patient_id"].isin(patients).to_numpy()
        y = m[f"aki_within_{h}h"].to_numpy(float)[el]
        raw = self.heads[(model, h)]["raw"]["validation"][el]
        return PlattCalibrator().fit(raw, y)

    def evaluate(self, target, label, risks_by_h):
        """Snapshot metrics per horizon and policy-v3 alert metrics on target-system test patients."""
        m, sel = self.part("test", target)
        rows = []
        for h in self.horizons:
            el = sel & m[f"eligible_{h}h"].to_numpy(bool)
            rows.append({"target_system": target, "model": label, "horizon_h": h,
                         **snapshot_metrics(m[f"aki_within_{h}h"].to_numpy(float)[el], risks_by_h[h][el])})
        k = m.loc[sel, ["patient_id", "hours_since_icu"]].copy()
        for h in self.horizons:
            k[f"m_{h}"] = risks_by_h[h][sel]
        g = self.test_frame.merge(k, on=KEYS, how="inner", validate="one_to_one")
        for h in self.horizons:
            g[f"risk_{h}"] = g[f"m_{h}"]
        a = self.ev.alert_frame(g)
        al = alert_summary(per_patient(a, alert_events(a, self.ev.threshold, self.ev.rule)))
        return rows, {"target_system": target, "model": label, **{x: al[x] for x in ALERT_KEYS}}

    def run(self):
        self.load()
        snap, alerts, curve = [], [], []
        for target, pair in self.config["pairs"].items():
            cross, within = pair["cross"], pair["within"]
            variants = {
                f"{cross} (source calibration)": {h: self.risks(cross, h, "test", self.heads[(cross, h)]["platt"]) for h in self.horizons},
                f"{cross} (recalibrated on target validation)": {h: self.risks(cross, h, "test", self.fit_platt(cross, h, target))
                                                                  for h in self.horizons},
                f"{within} (within-system)": {h: self.risks(within, h, "test", self.heads[(within, h)]["platt"]) for h in self.horizons},
            }
            for label, r in variants.items():
                s, a = self.evaluate(target, label, r)
                snap += s
                alerts.append(a)
                log(f"{target}: {label}")
            # How much local data is enough? Random subsets of target validation patients.
            m, sel = self.part("validation", target)
            pats = np.sort(m.loc[sel, "patient_id"].unique())
            rng = np.random.default_rng(self.seed)
            for n in self.config["recalibration"]["sample_sizes"]:
                reps = 1 if n == "all" else int(self.config["recalibration"]["repeats"])
                for rep in range(reps):
                    chosen = None if n == "all" else rng.choice(pats, int(n), replace=False)
                    r = {h: self.risks(cross, h, "test", self.fit_platt(cross, h, target, chosen)) for h in self.horizons}
                    s, a = self.evaluate(target, "subset", r)
                    s24 = next(x for x in s if x["horizon_h"] == 24)
                    curve.append({"target_system": target, "cross_model": cross, "n_patients": len(pats) if n == "all" else int(n),
                                  "repeat": rep, "observed_over_expected_24h": s24["observed_over_expected"],
                                  "calibration_slope_24h": s24["calibration_slope"], "brier_24h": s24["brier"],
                                  **{x: a[x] for x in ALERT_KEYS}})
                log(f"{target}: n={n} done")
        self.out.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(snap).to_csv(self.out / "snapshot_metrics.csv", index=False)
        pd.DataFrame(alerts).to_csv(self.out / "alert_metrics_policy_v3.csv", index=False)
        c = pd.DataFrame(curve)
        c.to_csv(self.out / "sample_size_repeats.csv", index=False)
        c.drop(columns=["repeat", "cross_model"]).groupby(["target_system", "n_patients"]).quantile([.1, .5, .9]) \
            .rename_axis(["target_system", "n_patients", "quantile"]).reset_index() \
            .to_csv(self.out / "sample_size_summary.csv", index=False)
        write_json(self.out / "run_manifest.json", {"version": self.config["version"],
                   "prespec_sha256": self.prespec["prespec_sha256"], "finished_at": datetime.now(timezone.utc).isoformat(),
                   "outputs": sorted(p.name for p in self.out.iterdir())})
        log(f"reports -> {self.out}")
