"""Which inputs make a moved model over- or under-estimate risk? (follow-up to system-shift-v1)

Settings: configs/shift_drivers_v1.yaml, frozen with input and code hashes before anything is computed. Uses training
and validation patients only; the test split is not loaded. Nothing is retrained: the frozen system-shift-v1 heads are
reused. Part A compares each input between CareVue and MetaVision; part B splits the moved model's calibration error
into input components (TreeSHAP sums per component, refitted with one coefficient each).
"""
from datetime import datetime, timezone
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml
from sklearn.linear_model import LogisticRegression

from aki_eval.data import dataset_paths
from aki_eval.protocol import freeze_prespec, write_json
from aki_ml.models import load_feature_matrix

SYSTEMS = ["carevue", "metavision"]


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


class ShiftDrivers:
    def __init__(self, root, config_path=None):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path or self.root / "configs" / "shift_drivers_v1.yaml")
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        run = self.config["system_shift_run"]
        self.run_dir = self.root / "artifacts" / "models" / run["run_id"]
        self.heads_dir = self.run_dir / run["study"]
        self.variants = run["variants"]                      # source system -> variant name
        self.paths = dataset_paths(self.root)
        self.dict_path = self.paths["data_root"] / "datasets" / "feature_dictionary.csv"
        self.ref_path = self.run_dir / "lightgbm__no_dc" / "pipeline.joblib"
        self.horizons = [int(h) for h in self.config["horizons"]]
        self.out = self.root / self.config["outputs"]
        inputs = {"config": self.config_path, "snapshots": self.paths["snapshots"], "feature_dictionary": self.dict_path,
                  "reference_pipeline": self.ref_path,
                  "system_shift_prespec": self.root / "artifacts" / "reports" / "system_shift_v1" / "prespec.json"}
        for system, name in self.variants.items():
            for h in self.horizons:
                inputs[f"head_{name}_{h}h"] = self.heads_dir / name / f"head_{h}h.joblib"
        sources = {"src/aki_report/shift_drivers.py": self.root / "src" / "aki_report" / "shift_drivers.py"}
        self.prespec = freeze_prespec(self.out, self.config, inputs, sources)
        self.rng = np.random.default_rng(int(self.config["seed"]))

    def log(self, message):
        print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)

    # -------------------------------------------------------------------------------------------- data
    def load(self):
        self.features = list(joblib.load(self.ref_path).feature_names)
        filters = [("split", "in", ["train", "validation"])]
        cols = ["patient_id", "split", "source_system"] + [f"{p}_{h}h" for h in self.horizons for p in ("eligible", "aki_within")]
        self.meta = pq.read_table(self.paths["snapshots"], columns=cols, filters=filters).to_pandas()
        assert set(self.meta["split"].astype(str)) <= {"train", "validation"}, "test rows must not be loaded"
        self.X = load_feature_matrix(self.paths["snapshots"], self.features, filters=filters)
        self.split = self.meta["split"].astype(str).to_numpy()
        self.system = self.meta["source_system"].astype(str).to_numpy()
        self.pcode = pd.factorize(self.meta["patient_id"])[0]
        d = pd.read_csv(self.dict_path).set_index("feature").loc[self.features]
        self.component = self.assign_components(d)
        self.names = list(self.config["components"])
        self.log(f"loaded {len(self.meta):,} train/validation rows, {len(self.features)} features")

    def assign_components(self, d):
        out = pd.Series(index=d.index, dtype=object)
        for name, rule in self.config["components"].items():
            m = out.isna()
            hit = pd.Series(False, index=d.index)
            for pattern in rule.get("name_patterns", []):
                hit |= d.index.str.contains(pattern, regex=False)
            if "groups" in rule:
                hit |= d["group"].isin(rule["groups"])
            out[m & hit] = name
        assert out.notna().all(), f"inputs without a component: {list(out[out.isna()].index[:5])}"
        return out.to_numpy()

    def head(self, system, h):
        head = joblib.load(self.heads_dir / self.variants[system] / f"head_{h}h.joblib")
        return lgb.Booster(model_str=head["booster"]), head["platt"], head["n_rounds"]

    def contributions(self, booster, rows, rounds):
        out = np.empty((len(rows), len(self.features) + 1), dtype=np.float64)
        for s in range(0, len(rows), 20_000):
            out[s:s + 20_000] = booster.predict(self.X[rows[s:s + 20_000]], num_iteration=rounds, pred_contrib=True)
        return out

    # -------------------------------------------------------------------------------------------- part A
    def input_shift(self, importance):
        n = 300_000
        stats = {}
        for system in SYSTEMS:
            rows = np.flatnonzero((self.split == "train") & (self.system == system))
            rows = np.sort(self.rng.choice(rows, min(n, len(rows)), replace=False))
            block = self.X[rows].astype(np.float64)
            miss = np.isnan(block)
            with np.errstate(all="ignore"):
                stats[system] = {"missing": miss.mean(axis=0), "median": np.nanmedian(block, axis=0),
                                 "mean": np.nanmean(block, axis=0), "var": np.nanvar(block, axis=0)}
            del block, miss
        a, b = stats["carevue"], stats["metavision"]
        with np.errstate(all="ignore"):
            smd = (b["mean"] - a["mean"]) / np.sqrt((a["var"] + b["var"]) / 2)
        table = pd.DataFrame({"feature": self.features, "component": self.component,
                              "missing_carevue": a["missing"], "missing_metavision": b["missing"],
                              "missing_difference": b["missing"] - a["missing"],
                              "median_carevue": a["median"], "median_metavision": b["median"],
                              "standardized_difference": smd,
                              "importance_share_carevue_model": importance["carevue"],
                              "importance_share_metavision_model": importance["metavision"]})
        imp = (table["importance_share_carevue_model"] + table["importance_share_metavision_model"]) / 2
        table["shift_x_importance"] = np.nan_to_num(np.abs(smd)) * imp
        table["missing_shift_x_importance"] = np.abs(table["missing_difference"]) * imp
        return table.sort_values("shift_x_importance", ascending=False)

    # -------------------------------------------------------------------------------------------- part B
    def design(self, contrib, platt):
        Z = np.column_stack([platt.slope_ * contrib[:, :-1][:, self.component == c].sum(axis=1) for c in self.names])
        offset = platt.slope_ * contrib[:, -1] + platt.intercept_
        return Z, offset

    @staticmethod
    def fit(Z, y):
        lr = LogisticRegression(penalty=None, solver="lbfgs", max_iter=500)
        lr.fit(Z, y)
        return lr.intercept_[0], lr.coef_[0]

    def run(self):
        self.load()
        boot_n = int(self.config["part_B_component_recalibration"]["bootstrap"]["resamples"])
        coef_rows, oe_rows, mean_rows = [], [], []
        importance = {s: np.zeros(len(self.features)) for s in SYSTEMS}
        for h in self.horizons:
            for source in SYSTEMS:
                target = [s for s in SYSTEMS if s != source][0]
                booster, platt, rounds = self.head(source, h)
                for where in [source, target]:
                    rows = np.flatnonzero((self.split == "validation") & (self.system == where)
                                          & self.meta[f"eligible_{h}h"].to_numpy(bool))
                    y = self.meta[f"aki_within_{h}h"].to_numpy()[rows].astype(int)
                    contrib = self.contributions(booster, rows, rounds)
                    if where == source:
                        imp = np.abs(contrib[:, :-1]).mean(axis=0)
                        importance[source] += imp / imp.sum() / len(self.horizons)
                    Z, offset = self.design(contrib, platt)
                    z0 = offset + Z.sum(axis=1)
                    p0 = sigmoid(z0)
                    b0, beta = self.fit(Z, y)
                    alpha = b0 - offset.mean()                  # offset is the same constant on every row
                    pats = self.pcode[rows]
                    upats = np.unique(pats)
                    index = pd.Series(np.arange(len(rows))).groupby(pats).indices
                    draws = []
                    for _ in range(boot_n):
                        pick = self.rng.choice(upats, len(upats), replace=True)
                        idx = np.concatenate([index[p] for p in pick])
                        bb, be = self.fit(Z[idx], y[idx])
                        draws.append(np.r_[bb - offset.mean(), be])
                    draws = np.array(draws)
                    lo, hi = np.percentile(draws, [2.5, 97.5], axis=0)
                    role = "source (trained here)" if where == source else "target (model moved)"
                    base = {"model": f"trained_on_{source}", "evaluated_on": where, "role": role, "horizon_h": h,
                            "rows": len(rows), "patients": len(upats)}
                    coef_rows.append({**base, "term": "intercept", "estimate": alpha, "ci_low": lo[0], "ci_high": hi[0],
                                      "neutral_value": 0.0})
                    for j, c in enumerate(self.names):
                        coef_rows.append({**base, "term": c, "estimate": beta[j], "ci_low": lo[j + 1],
                                          "ci_high": hi[j + 1], "neutral_value": 1.0})
                        mean_rows.append({**base, "component": c, "mean_logit_contribution": Z[:, j].mean(),
                                          "mean_abs_logit_contribution": np.abs(Z[:, j]).mean()})
                    observed = y.sum()
                    oe = {"uncorrected": observed / p0.sum(),
                          "intercept_only": observed / sigmoid(z0 + alpha).sum(),
                          "full_correction": observed / sigmoid(b0 + Z @ beta).sum()}
                    for j, c in enumerate(self.names):
                        oe[f"only_{c}"] = observed / sigmoid(z0 + (beta[j] - 1) * Z[:, j]).sum()
                    oe_rows += [{**base, "correction": k, "observed_over_expected": v} for k, v in oe.items()]
                    self.log(f"{h}h trained on {source}, evaluated on {where}: O/E {oe['uncorrected']:.3f}")
        self.out.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(coef_rows).to_csv(self.out / "component_coefficients.csv", index=False)
        pd.DataFrame(oe_rows).to_csv(self.out / "component_observed_over_expected.csv", index=False)
        pd.DataFrame(mean_rows).to_csv(self.out / "component_mean_contributions.csv", index=False)
        shift = self.input_shift(importance)
        shift.to_csv(self.out / "input_shift.csv", index=False)
        comp = shift.groupby("component").agg(
            inputs=("feature", "size"), mean_abs_standardized_difference=("standardized_difference", lambda s: np.nanmean(np.abs(s))),
            mean_missing_difference=("missing_difference", "mean"),
            importance_share=("importance_share_carevue_model", "sum")).reset_index()
        comp.to_csv(self.out / "input_shift_by_component.csv", index=False)
        write_json(self.out / "run_manifest.json", {
            "version": self.config["version"], "prespec_sha256": self.prespec["prespec_sha256"],
            "finished_at": datetime.now(timezone.utc).isoformat(), "test_loaded": False,
            "rows_loaded": int(len(self.meta)), "outputs": sorted(p.name for p in self.out.iterdir())})
        self.log(f"reports -> {self.out}")
