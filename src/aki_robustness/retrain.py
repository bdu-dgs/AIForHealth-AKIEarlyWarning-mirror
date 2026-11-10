"""Feature-window and missingness sensitivity analyses that retrain the locked LightGBM no_dc model.

Every variant repeats the notebook-02 no_dc ablation procedure (same folds, weights, per-horizon hyperparameters, round
selection and Platt calibration on training out-of-fold scores) with one change to the inputs. The configuration is in
configs/robustness_retrain_v1.yaml and is frozen (with input and code hashes) before anything is trained or scored.
Outputs are aggregate tables; fitted heads and scores stay local under artifacts/models/<run_id>/.
"""
from datetime import datetime, timezone
import gc
import json
from pathlib import Path
import time

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml
from scipy.stats import rankdata
from sklearn.model_selection import StratifiedKFold

from aki_eval.data import dataset_paths
from aki_eval.protocol import freeze_prespec, sha256, write_json
from aki_final.test_evaluation import PatientBootstrap, snapshot_metrics
from aki_ml.models import PlattCalibrator, load_feature_matrix

SUMMARY_STATS = {"mean", "min", "max", "std", "slope", "count"}


def fast_auroc(y, s):
    r = rankdata(s)
    n1 = y.sum()
    n0 = len(y) - n1
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


class Inputs:
    """Feature transform of one variant: column subset, then optional median imputation and missing indicators.

    `fit(rows)` learns medians/indicator columns on a training partition; `apply(rows)` returns a float32 matrix.
    """

    def __init__(self, X, keep, impute=None, sample_rows=300_000, seed=0):
        self.X, self.keep, self.impute = X, np.asarray(keep), impute or {}
        self.sample_rows, self.seed = sample_rows, seed
        self.full = len(self.keep) == X.shape[1] and (self.keep == np.arange(X.shape[1])).all()

    def _take(self, rows):
        return self.X[rows] if self.full else self.X[np.ix_(rows, self.keep)]

    def fit(self, rows):
        if not self.impute:
            return self
        rng = np.random.default_rng(self.seed)
        sample = np.sort(rng.choice(rows, min(len(rows), self.sample_rows), replace=False))
        block = self._take(sample)
        missing = np.isnan(block).mean(axis=0)
        with np.errstate(all="ignore"):
            med = np.nanmedian(block, axis=0)
        self.median_ = np.where(np.isnan(med), 0.0, med).astype(np.float32)
        self.indicator_cols_ = (np.flatnonzero(missing >= self.impute.get("indicator_min_fraction", 0.01))
                                if self.impute.get("indicators") else np.array([], dtype=int))
        return self

    def apply(self, rows):
        block = self._take(rows)
        if not self.impute:
            return block
        miss = np.isnan(block)
        ind = miss[:, self.indicator_cols_].astype(np.float32) if len(self.indicator_cols_) else None
        np.copyto(block, np.broadcast_to(self.median_, block.shape), where=miss)
        del miss
        return block if ind is None else np.hstack([block, ind])

    @property
    def n_inputs(self):
        return len(self.keep) + (len(getattr(self, "indicator_cols_", [])) if self.impute else 0)


class RetrainStudy:
    def __init__(self, root, config_path=None, n_jobs=None):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path or self.root / "configs" / "robustness_retrain_v1.yaml")
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
                   sorted((self.root / "src" / "aki_robustness").glob("*.py")) + [self.root / "src" / "aki_ml" / "models.py"]}
        self.prespec = freeze_prespec(self.out, self.config, inputs, sources)
        self.horizons = [int(h) for h in self.config["horizons"]]
        self.seed = int(self.ml["run"]["seed"])

    def log(self, message):
        print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)

    # -------------------------------------------------------------------------------------------- data
    def load(self):
        self.ref = joblib.load(self.ref_path)
        self.features = list(self.ref.feature_names)
        ids = ["patient_id", "split", "source_system"]
        labels = [f"{p}_{h}h" for h in self.horizons for p in ("eligible", "aki_within")]
        meta = pq.read_table(self.paths["snapshots"], columns=ids + ["hours_since_icu"] + labels).to_pandas()
        self.X = load_feature_matrix(self.paths["snapshots"], self.features)
        lo, hi = self.config["monitoring_hours"]
        hours = meta["hours_since_icu"].to_numpy()
        assert ((hours >= lo) & (hours <= hi)).all(), "snapshots outside the monitoring window"
        self.meta = meta
        self.split = meta["split"].astype(str).to_numpy()
        self.Y = {h: meta[f"aki_within_{h}h"].astype("Float32").to_numpy(dtype=np.float32, na_value=np.nan)
                  for h in self.horizons}
        self.ELIG = {h: meta[f"eligible_{h}h"].to_numpy(dtype=bool) for h in self.horizons}
        self.PCODE = pd.factorize(meta["patient_id"])[0].astype(np.int32)
        train = self.split == "train"
        # Notebook-02 folds: same patient order, strata and seed.
        tp = meta.loc[train, ["patient_id", "source_system"]].drop_duplicates("patient_id").set_index("patient_id")
        cohort = pd.read_parquet(self.cohort_path, columns=["patient_id", "cr_aki_onset_hours"])
        ever = cohort.set_index("patient_id")["cr_aki_onset_hours"].notna().reindex(tp.index)
        strata = tp["source_system"].astype(str) + "|" + ever.astype(str)
        fold = pd.Series(-1, index=tp.index, dtype=np.int8)
        n_folds = int(self.ml["cv"]["n_folds"])
        for k, (_, ho) in enumerate(StratifiedKFold(n_folds, shuffle=True, random_state=self.seed).split(strata, strata)):
            fold.iloc[ho] = k
        self.FOLD = np.full(len(meta), -1, dtype=np.int8)
        self.FOLD[train] = fold.reindex(meta.loc[train, "patient_id"]).to_numpy()
        self.n_folds = n_folds
        self.TRAIN_ROWS = np.flatnonzero(train)
        self.FOLD_ROWS = {k: np.flatnonzero(self.FOLD == k) for k in range(n_folds)}
        self.EVAL_ROWS = {s: np.flatnonzero(self.split == s) for s in self.config["evaluation_splits"]}
        d = pd.read_csv(self.dict_path).set_index("feature").loc[self.features]
        self.dictionary = d
        self.log(f"loaded {len(meta):,} rows, X {self.X.nbytes / 1e9:.2f} GB, {len(self.features)} features")

    def keep_columns(self, variant):
        drop = variant.get("drop")
        if not drop:
            return np.arange(len(self.features))
        d = self.dictionary
        mask = np.ones(len(d), bool)
        if "groups" in drop:
            m = d["group"].isin(drop["groups"]).to_numpy() & d["statistic"].isin(drop["statistics"]).to_numpy()
            w = d["window_hours"].to_numpy(float)
            if "window_hours_above" in drop:
                m &= w > drop["window_hours_above"]
            if "window_hours_equal" in drop:
                m &= w == drop["window_hours_equal"]
            mask &= ~m
        for pattern in drop.get("name_patterns", []):
            mask &= ~d.index.str.contains(pattern, regex=False)
        keep = np.flatnonzero(mask)
        assert self.features.index("hours_since_icu") in keep
        return keep

    def inputs(self, variant):
        return Inputs(self.X, self.keep_columns(variant), variant.get("impute"),
                      int(self.config["fit_sample_rows"]), self.seed)

    # -------------------------------------------------------------------------------------------- training
    def weights(self, rows, h):
        y = self.Y[h][rows]
        _, inverse, counts = np.unique(self.PCODE[rows], return_inverse=True, return_counts=True)
        w = 1.0 / counts[inverse]
        pos, neg = w[y == 1].sum(), w[y == 0].sum()
        w = np.where(y == 1, w * (pos + neg) / (2 * pos), w * (pos + neg) / (2 * neg))
        return (w / w.mean()).astype(np.float32)

    def params(self, h):
        cfg = joblib.load(self.run_dir / "_heads" / f"lightgbm__full__{h}h.summary.joblib")["config"]
        lgbc = self.ml["families"]["lightgbm"]
        return {**lgbc["fixed"], **cfg, "num_threads": self.n_jobs, "seed": self.seed}, cfg

    def fit_head(self, variant, h):
        path = self.private / variant["name"] / f"head_{h}h.joblib"
        if path.exists():
            return joblib.load(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        lgbc = self.ml["families"]["lightgbm"]
        params, cfg = self.params(h)
        dparams = {"max_bin": lgbc["fixed"]["max_bin"], "feature_pre_filter": False, "verbose": -1}
        curves, models = [], []
        for k in range(self.n_folds):
            tr = np.flatnonzero((self.FOLD >= 0) & (self.FOLD != k) & self.ELIG[h])
            ho = np.flatnonzero((self.FOLD == k) & self.ELIG[h])
            tf = self.inputs(variant).fit(tr)
            dtr = lgb.Dataset(tf.apply(tr), label=self.Y[h][tr], weight=self.weights(tr, h), params=dparams)
            dho = lgb.Dataset(tf.apply(ho), label=self.Y[h][ho], weight=self.weights(ho, h), reference=dtr)
            hist = {}
            bst = lgb.train(params, dtr, num_boost_round=lgbc["num_boost_round"], valid_sets=[dho], valid_names=["heldout"],
                            callbacks=[lgb.early_stopping(lgbc["early_stopping_rounds"], verbose=False),
                                       lgb.record_evaluation(hist)])
            curves.append(np.asarray(hist["heldout"]["binary_logloss"]))
            models.append((bst, tf))
            del dtr, dho
            gc.collect()
        n = min(len(c) for c in curves)
        rounds = int(np.argmin(np.mean([c[:n] for c in curves], axis=0))) + 1
        oof = np.full(len(self.meta), np.nan, dtype=np.float32)
        for k, (bst, tf) in enumerate(models):
            oof[self.FOLD_ROWS[k]] = self.predict(bst, tf, self.FOLD_ROWS[k], rounds)
        del models
        rows = np.flatnonzero((self.split == "train") & self.ELIG[h])
        tf = self.inputs(variant).fit(rows)
        bst = lgb.train(params, lgb.Dataset(tf.apply(rows), label=self.Y[h][rows], weight=self.weights(rows, h),
                                            params=dparams), num_boost_round=rounds)
        platt = PlattCalibrator(float(self.ml["calibration"]["clip_eps"])).fit(oof[rows], self.Y[h][rows])
        scores = {s: self.predict(bst, tf, r, rounds) for s, r in self.EVAL_ROWS.items()}
        head = {"variant": variant["name"], "horizon_h": h, "config": cfg, "n_rounds": rounds, "n_inputs": tf.n_inputs,
                "platt": platt, "oof": oof[self.TRAIN_ROWS], "raw": scores, "minutes": (time.time() - t0) / 60,
                "booster": bst.model_to_string(num_iteration=rounds)}
        joblib.dump(head, path, compress=3)
        self.log(f"{variant['name']}/{h}h: {tf.n_inputs} inputs, {rounds} rounds ({head['minutes']:.1f} min)")
        return head

    @staticmethod
    def predict(bst, tf, rows, rounds):
        out = np.empty(len(rows), dtype=np.float32)
        for s in range(0, len(rows), 100_000):
            out[s:s + 100_000] = bst.predict(tf.apply(rows[s:s + 100_000]), num_iteration=rounds)
        return out

    # -------------------------------------------------------------------------------------------- evaluation
    def reference_risks(self):
        return {h: {s: self.ref.predict(self.X, h, "platt", rows=r) for s, r in self.EVAL_ROWS.items()}
                for h in self.horizons}

    def run(self, variants=None):
        self.load()
        chosen = [v for v in self.config["variants"] if variants is None or v["name"] in variants]
        ref = self.reference_risks()
        heads = {}
        for v in chosen:
            for h in self.horizons:
                heads[(v["name"], h)] = self.fit_head(v, h)
            gc.collect()
        self.report(chosen, heads, ref)

    def report(self, chosen, heads, ref):
        self.out.mkdir(parents=True, exist_ok=True)
        rows, deltas, inventory = [], [], []
        harness = {}
        boot = {}
        for h in self.horizons:
            for s, r in self.EVAL_ROWS.items():
                el = self.ELIG[h][r]
                y = self.Y[h][r][el].astype(int)
                rows.append({"variant": "reference", "split": s, "horizon_h": h, **snapshot_metrics(y, ref[h][s][el])})
                for v in chosen:
                    head = heads[(v["name"], h)]
                    p = head["platt"].predict(head["raw"][s][el])
                    rows.append({"variant": v["name"], "split": s, "horizon_h": h, **snapshot_metrics(y, p)})
            # Paired patient bootstrap of the test differences (variant minus reference), same draws for every variant.
            r = self.EVAL_ROWS["test"]
            el = self.ELIG[h][r]
            y = self.Y[h][r][el].astype(int)
            pr = ref[h]["test"][el].astype(float)
            pats = self.PCODE[r][el]
            bs = PatientBootstrap(pats, int(self.config["comparison"]["bootstrap"]["resamples"]), int(self.config["seed"]) + h)
            for v in chosen:
                head = heads[(v["name"], h)]
                pv = head["platt"].predict(head["raw"]["test"][el]).astype(float)

                def stat(idx, pv=pv):
                    yy = y[idx]
                    return {"auroc": fast_auroc(yy, pv[idx]) - fast_auroc(yy, pr[idx]),
                            "brier": np.mean((pv[idx] - yy) ** 2) - np.mean((pr[idx] - yy) ** 2)}

                ci = bs.interval(stat)
                point = stat(np.arange(len(y)))
                for m in ["auroc", "brier"]:
                    deltas.append({"variant": v["name"], "horizon_h": h, "metric": f"{m}_difference",
                                   "estimate": point[m], "ci_low": ci.loc[.025, m], "ci_high": ci.loc[.975, m]})
                inventory.append({"variant": v["name"], "horizon_h": h, "n_inputs": head["n_inputs"],
                                  "n_rounds": head["n_rounds"], "minutes": round(head["minutes"], 1)})
                if v["name"] == "reference_refit":
                    vr = self.EVAL_ROWS["validation"]
                    locked = self.ref.raw_score(self.X, h, rows=vr)
                    harness[f"{h}h"] = {"max_abs_raw_difference_validation": float(np.abs(locked - head["raw"]["validation"]).max()),
                                        "n_rounds": head["n_rounds"], "locked_n_rounds": int(self.ref.n_rounds[h])}
            self.log(f"{h}h: bootstrap done")
        metrics = pd.DataFrame(rows)
        metrics.to_csv(self.out / "snapshot_metrics.csv", index=False)
        pd.DataFrame(deltas).to_csv(self.out / "test_differences.csv", index=False)
        pd.DataFrame(inventory).to_csv(self.out / "variants.csv", index=False)
        tol = 1e-5
        harness_ok = all(v["max_abs_raw_difference_validation"] < tol for v in harness.values()) if harness else None
        write_json(self.out / "harness_check.json", {"tolerance": tol, "passed": harness_ok, "horizons": harness})
        write_json(self.out / "run_manifest.json", {
            "version": self.config["version"], "prespec_sha256": self.prespec["prespec_sha256"],
            "finished_at": datetime.now(timezone.utc).isoformat(), "variants": [v["name"] for v in chosen],
            "features_reference": len(self.features), "rows": {s: int(len(r)) for s, r in self.EVAL_ROWS.items()},
            "harness_check_passed": harness_ok,
            "outputs": sorted(p.name for p in self.out.iterdir())})
        self.log(f"reports -> {self.out}")
