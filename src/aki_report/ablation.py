"""Feature ablation of the locked LightGBM no_dc model, scored on validation patients only.

Settings: configs/feature_ablation_v1.yaml, frozen with input and code hashes before anything is trained or scored.
Reuses the robustness-retrain-v1 training procedure (RetrainStudy); only the input columns change. Top-k variants keep
the k most important inputs, ranked by mean |TreeSHAP| of the locked model on training patients; group variants drop
one feature group. Outputs are aggregate tables; fitted heads stay local under artifacts/models/<run_id>/.
"""
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from aki_eval.data import dataset_paths
from aki_eval.protocol import freeze_prespec, write_json
from aki_final.test_evaluation import PatientBootstrap, snapshot_metrics
from aki_robustness.retrain import RetrainStudy, fast_auroc


class AblationStudy(RetrainStudy):
    def __init__(self, root, config_path=None, n_jobs=None):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path or self.root / "configs" / "feature_ablation_v1.yaml")
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
                   + [self.root / "src" / "aki_report" / "ablation.py", self.root / "src" / "aki_ml" / "models.py"]}
        self.prespec = freeze_prespec(self.out, self.config, inputs, sources)
        self.horizons = [int(h) for h in self.config["horizons"]]
        self.seed = int(self.ml["run"]["seed"])

    # -------------------------------------------------------------------------------------------- importance
    def importance(self):
        """Mean |TreeSHAP| share per feature on training rows, averaged over horizons (cached)."""
        path = self.private / "importance.parquet"
        if path.exists():
            self.ranking = pd.read_parquet(path)
            return self.ranking
        cfg = self.config["importance"]
        rng = np.random.default_rng(int(self.config["seed"]))
        shares = {}
        for h in self.horizons:
            rows = np.flatnonzero((self.split == "train") & self.ELIG[h])
            rows = np.sort(rng.choice(rows, min(len(rows), int(cfg["sample_rows_per_horizon"])), replace=False))
            booster = self.ref.heads[h]
            total = np.zeros(len(self.features))
            for s in range(0, len(rows), 20_000):
                c = booster.predict(self.X[rows[s:s + 20_000]], num_iteration=self.ref.n_rounds[h], pred_contrib=True)
                total += np.abs(c[:, :-1]).sum(axis=0)       # last column is the bias term
            mean = total / len(rows)
            shares[f"share_{h}h"] = mean / mean.sum()
            self.log(f"importance {h}h on {len(rows):,} training rows")
        table = pd.DataFrame({"feature": self.features, **shares})
        table["share_mean"] = table[[f"share_{h}h" for h in self.horizons]].mean(axis=1)
        table["group"] = self.dictionary["group"].to_numpy()
        table = table.sort_values("share_mean", ascending=False).reset_index(drop=True)
        table["rank"] = np.arange(1, len(table) + 1)
        table.to_parquet(path, index=False)
        self.ranking = table
        return table

    def keep_columns(self, variant):
        if "top_k" in variant:
            names = self.ranking["feature"].head(int(variant["top_k"])).tolist()
            return np.sort([self.features.index(n) for n in names])
        if "drop_groups" in variant:
            mask = ~self.dictionary["group"].isin(variant["drop_groups"]).to_numpy()
            return np.flatnonzero(mask)
        return np.arange(len(self.features))

    # -------------------------------------------------------------------------------------------- run
    def run(self, variants=None):
        self.load()
        self.importance()
        chosen = [v for v in self.config["variants"] if variants is None or v["name"] in variants]
        ref = {h: {"validation": self.ref.predict(self.X, h, "platt", rows=self.EVAL_ROWS["validation"])}
               for h in self.horizons}
        heads = {}
        for v in chosen:
            for h in self.horizons:
                heads[(v["name"], h)] = self.fit_head(v, h)
        self.report(chosen, heads, ref)

    def report(self, chosen, heads, ref):
        self.out.mkdir(parents=True, exist_ok=True)
        rows, deltas, inventory = [], [], []
        r = self.EVAL_ROWS["validation"]
        for h in self.horizons:
            el = self.ELIG[h][r]
            y = self.Y[h][r][el].astype(int)
            pr = ref[h]["validation"][el].astype(float)
            rows.append({"variant": "reference", "n_inputs": len(self.features), "horizon_h": h, **snapshot_metrics(y, pr)})
            bs = PatientBootstrap(self.PCODE[r][el], int(self.config["comparison"]["bootstrap"]["resamples"]),
                                  int(self.config["seed"]) + h)
            for v in chosen:
                head = heads[(v["name"], h)]
                pv = head["platt"].predict(head["raw"]["validation"][el]).astype(float)
                rows.append({"variant": v["name"], "n_inputs": head["n_inputs"], "horizon_h": h, **snapshot_metrics(y, pv)})

                def stat(idx, pv=pv):
                    return {"auroc": fast_auroc(y[idx], pv[idx]) - fast_auroc(y[idx], pr[idx])}

                ci = bs.interval(stat)
                deltas.append({"variant": v["name"], "horizon_h": h, "n_inputs": head["n_inputs"],
                               "auroc_difference": stat(np.arange(len(y)))["auroc"],
                               "ci_low": ci.loc[.025, "auroc"], "ci_high": ci.loc[.975, "auroc"]})
                inventory.append({"variant": v["name"], "horizon_h": h, "n_inputs": head["n_inputs"],
                                  "n_rounds": head["n_rounds"], "minutes": round(head["minutes"], 1)})
            self.log(f"{h}h: bootstrap done")
        pd.DataFrame(rows).to_csv(self.out / "validation_metrics.csv", index=False)
        pd.DataFrame(deltas).to_csv(self.out / "validation_differences.csv", index=False)
        pd.DataFrame(inventory).to_csv(self.out / "variants.csv", index=False)
        self.ranking.to_csv(self.out / "feature_importance.csv", index=False)
        lists = {v["name"]: [self.features[i] for i in self.keep_columns(v)] for v in chosen if "top_k" in v}
        write_json(self.out / "top_k_features.json", lists)
        write_json(self.out / "run_manifest.json", {
            "version": self.config["version"], "prespec_sha256": self.prespec["prespec_sha256"],
            "finished_at": datetime.now(timezone.utc).isoformat(), "variants": [v["name"] for v in chosen],
            "features_reference": len(self.features), "validation_rows": int(len(r)), "test_scored": False,
            "outputs": sorted(p.name for p in self.out.iterdir())})
        self.log(f"reports -> {self.out}")
