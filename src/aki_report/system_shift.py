"""Plan item 6: train on one MIMIC-III charting system, test on the other (pseudo-external validation).

Settings: configs/system_shift_v1.yaml, frozen with input and code hashes before anything is trained or scored.
Reuses the robustness-retrain-v1 training procedure (RetrainStudy) with the training patients restricted to one
source system (size-matched). Outputs are aggregate tables; fitted heads stay local under artifacts/models/<run_id>/.
"""
from datetime import datetime, timezone
import gc
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.model_selection import StratifiedKFold

from aki_eval.data import dataset_paths
from aki_eval.policies import alert_events
from aki_eval.protocol import freeze_prespec, write_json
from aki_final.test_evaluation import KEYS, LockedEvaluation, PatientBootstrap, alert_summary, per_patient, snapshot_metrics
from aki_robustness.retrain import RetrainStudy, fast_auroc


class SystemShiftStudy(RetrainStudy):
    def __init__(self, root, config_path=None, n_jobs=None):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path or self.root / "configs" / "system_shift_v1.yaml")
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.ml = yaml.safe_load((self.root / "configs" / "ml.yaml").read_text(encoding="utf-8"))
        ref = self.config["reference"]
        self.run_dir = self.root / "artifacts" / "models" / ref["run_id"]
        self.ref_path = self.run_dir / f"{ref['family']}__{ref['feature_set']}" / "pipeline.joblib"
        self.paths = dataset_paths(self.root)
        data = self.paths["data_root"]
        self.cohort_path, self.dict_path = data / "datasets" / "cohort.parquet", data / "datasets" / "feature_dictionary.csv"
        self.out = self.root / self.config["outputs"]
        self.private = self.run_dir / self.config["version"]
        self.private.mkdir(parents=True, exist_ok=True)
        self.n_jobs = n_jobs or __import__("os").cpu_count()
        inputs = {"config": self.config_path, "ml_config": self.root / "configs" / "ml.yaml",
                  "reference_pipeline": self.ref_path, "snapshots": self.paths["snapshots"],
                  "cohort": self.cohort_path, "feature_dictionary": self.dict_path}
        sources = {p.relative_to(self.root).as_posix(): p for p in
                   sorted((self.root / "src" / "aki_robustness").glob("*.py"))
                   + [self.root / "src" / "aki_report" / "system_shift.py", self.root / "src" / "aki_ml" / "models.py"]}
        self.prespec = freeze_prespec(self.out, self.config, inputs, sources)
        self.horizons = [int(h) for h in self.config["horizons"]]
        self.seed = int(self.ml["run"]["seed"])

    # -------------------------------------------------------------------------------------------- training rows
    def load(self):
        super().load()
        self.split_all = self.split.copy()
        self.system = self.meta["source_system"].astype(str).to_numpy()
        tp = self.meta.loc[self.split_all == "train", ["patient_id", "source_system"]].drop_duplicates("patient_id")
        counts = tp["source_system"].value_counts()
        self.n_train_patients = int(min(counts[v["train_system"]] for v in self.config["variants"]))
        self.log(f"training patients per system: {counts.to_dict()}; size-matched n = {self.n_train_patients:,}")

    def use_training_system(self, system):
        """Point the inherited fold/row arrays at a size-matched training subset of one source system."""
        train = self.split_all == "train"
        tp = self.meta.loc[train & (self.system == system), "patient_id"].drop_duplicates().sort_values()
        rng = np.random.default_rng(int(self.config["seed"]))
        chosen = pd.Index(rng.choice(tp.to_numpy(), self.n_train_patients, replace=False)) if len(tp) > self.n_train_patients \
            else pd.Index(tp.to_numpy())
        cohort = pd.read_parquet(self.cohort_path, columns=["patient_id", "cr_aki_onset_hours"]).set_index("patient_id")
        ever = cohort["cr_aki_onset_hours"].notna().reindex(chosen).astype(int).to_numpy()
        fold = pd.Series(-1, index=chosen, dtype=np.int8)
        for k, (_, ho) in enumerate(StratifiedKFold(self.n_folds, shuffle=True, random_state=self.seed).split(ever, ever)):
            fold.iloc[ho] = k
        member = self.meta["patient_id"].isin(chosen).to_numpy() & train
        self.split = np.where(train & ~member, "train_excluded", self.split_all)
        self.FOLD = np.full(len(self.meta), -1, dtype=np.int8)
        self.FOLD[member] = fold.reindex(self.meta.loc[member, "patient_id"]).to_numpy()
        self.TRAIN_ROWS = np.flatnonzero(member)
        self.FOLD_ROWS = {k: np.flatnonzero(self.FOLD == k) for k in range(self.n_folds)}
        return {"system": system, "patients": len(chosen), "rows": len(self.TRAIN_ROWS), "aki_patients": int(ever.sum())}

    def run(self, variants=None):
        self.load()
        ref = self.reference_risks()
        heads, inventory = {}, []
        for v in self.config["variants"]:
            if variants and v["name"] not in variants:
                continue
            info = self.use_training_system(v["train_system"])
            for h in self.horizons:
                heads[(v["name"], h)] = self.fit_head(v, h)
            inventory.append({"variant": v["name"], **info})
            gc.collect()
        self.split = self.split_all
        self.report_shift(heads, ref, pd.DataFrame(inventory))

    # -------------------------------------------------------------------------------------------- evaluation
    def report_shift(self, heads, ref, inventory):
        self.out.mkdir(parents=True, exist_ok=True)
        names = [v["name"] for v in self.config["variants"]]
        within = {v["train_system"]: v["name"] for v in self.config["variants"]}
        targets = self.config["evaluation"]["target_systems"]
        n = int(self.config["bootstrap"]["resamples"])
        rows, deltas = [], []
        risks = {}                                       # (model, split, h) -> calibrated risks on that split's rows
        for h in self.horizons:
            for s in self.config["evaluation_splits"]:
                risks[("reference", s, h)] = ref[h][s]
                for name in names:
                    head = heads[(name, h)]
                    risks[(name, s, h)] = head["platt"].predict(head["raw"][s])
        for h in self.horizons:
            for s, r in self.EVAL_ROWS.items():
                el = self.ELIG[h][r]
                for target in targets:
                    m = el & (self.system[r] == target)
                    y = self.Y[h][r][m].astype(int)
                    for model in ["reference"] + names:
                        rows.append({"model": model, "split": s, "target_system": target, "horizon_h": h,
                                     **snapshot_metrics(y, risks[(model, s, h)][m])})
                    if s != "test":
                        continue
                    cross = [nm for sys_, nm in within.items() if sys_ != target][0]
                    P = {"reference": risks[("reference", s, h)][m].astype(float),
                         "within": risks[(within[target], s, h)][m].astype(float),
                         "cross": risks[(cross, s, h)][m].astype(float)}
                    boot = PatientBootstrap(self.PCODE[r][m], n, int(self.config["seed"]) + h)

                    def stat(idx, P=P, y=y):
                        a = {k: fast_auroc(y[idx], p[idx]) for k, p in P.items()}
                        return {"cross_minus_within": a["cross"] - a["within"],
                                "within_minus_reference": a["within"] - a["reference"],
                                "cross_minus_reference": a["cross"] - a["reference"]}

                    ci = boot.interval(stat)
                    point = stat(np.arange(len(y)))
                    for k, v in point.items():
                        deltas.append({"target_system": target, "horizon_h": h, "comparison": k,
                                       "cross_model": cross, "within_model": within[target], "auroc_difference": v,
                                       "ci_low": ci.loc[.025, k], "ci_high": ci.loc[.975, k]})
                    self.log(f"{h}h test {target}: bootstrap done")
        metrics, deltas = pd.DataFrame(rows), pd.DataFrame(deltas)
        metrics.to_csv(self.out / "snapshot_metrics.csv", index=False)
        deltas.to_csv(self.out / "auroc_differences.csv", index=False)
        inventory.to_csv(self.out / "training_sets.csv", index=False)
        alerts = self.alert_table(risks)
        variants = pd.DataFrame([{"variant": k[0], "horizon_h": k[1], "n_rounds": v["n_rounds"], "n_inputs": v["n_inputs"],
                                  "minutes": round(v["minutes"], 1)} for k, v in heads.items()])
        variants.to_csv(self.out / "variants.csv", index=False)
        write_json(self.out / "run_manifest.json", {
            "version": self.config["version"], "prespec_sha256": self.prespec["prespec_sha256"],
            "finished_at": datetime.now(timezone.utc).isoformat(), "size_matched_training_patients": self.n_train_patients,
            "rows": {s: int(len(r)) for s, r in self.EVAL_ROWS.items()}, "alert_rows": len(alerts),
            "outputs": sorted(p.name for p in self.out.iterdir())})
        self.log(f"reports -> {self.out}")

    def alert_table(self, risks):
        """Policy v3 applied unchanged to each model's risks, per target system (test split)."""
        ev = LockedEvaluation(self.root)
        f = ev.load()
        r = self.EVAL_ROWS["test"]
        keys = self.meta.iloc[r][["patient_id", "hours_since_icu"]].copy()
        keys["patient_id"] = keys["patient_id"].astype(str)
        keys["hours_since_icu"] = keys["hours_since_icu"].round().astype(int)
        rows = []
        for model in ["reference"] + [v["name"] for v in self.config["variants"]]:
            k = keys.copy()
            for h in self.horizons:
                k[f"m_{h}"] = risks[(model, "test", h)]
            g = f.merge(k, on=KEYS, how="left", validate="one_to_one")
            if g[[f"m_{h}" for h in self.horizons]].isna().any().any():
                raise RuntimeError("Missing variant risks for some test origins")
            for h in self.horizons:
                g[f"risk_{h}"] = g[f"m_{h}"]
            for target in self.config["evaluation"]["target_systems"]:
                sub = g[g["source_system"] == target].reset_index(drop=True)
                a = ev.alert_frame(sub)
                s = alert_summary(per_patient(a, alert_events(a, ev.threshold, ev.rule)))
                rows.append({"model": model, "target_system": target, **s})
        t = pd.DataFrame(rows)
        t.to_csv(self.out / "alert_metrics_policy_v3.csv", index=False)
        return t
