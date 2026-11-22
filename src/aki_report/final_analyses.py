"""Final-report secondary analyses on the test split (plan items 2-5): severity, baselines, trade-off, decision curves.

Settings: configs/final_analyses_v1.yaml, frozen with input and code hashes before anything is computed.
The locked system (model, feature engine v2 risks, policy v3) is reused from test-evaluation-v1 and never changed;
nothing here selects a model, threshold or rule. Outputs are aggregate tables only.
"""
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml
from scipy.stats import rankdata
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_recall_curve, roc_curve
from sklearn.model_selection import StratifiedKFold
from threadpoolctl import threadpool_limits

from aki_eval.policies import AlertRule, alert_events
from aki_eval.protocol import freeze_prespec, write_json
from aki_final.test_evaluation import KEYS, LockedEvaluation, PatientBootstrap, alert_summary, per_patient, snapshot_metrics
from aki_ml.models import LRPreprocessor, PlattCalibrator, load_feature_matrix

MODEL_LABELS = {
    "primary": "LightGBM no_dc (locked system)",
    "lightgbm__no_dc": "LightGBM no_dc (stored features)",
    "lightgbm__full": "LightGBM full",
    "xgboost__full": "XGBoost full",
    "rf__full": "Random forest full",
    "lr__full": "Logistic regression full",
    "creatinine_lr": "Creatinine-only logistic regression",
    "creatinine_rise": "Creatinine rise from 48 h minimum (score)",
}
PROBABILISTIC = ["primary", "lightgbm__no_dc", "lightgbm__full", "xgboost__full", "rf__full", "lr__full", "creatinine_lr"]


def fast_auroc(y, s):
    r = rankdata(s)
    n1 = y.sum()
    n0 = len(y) - n1
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def wilson(k, n, z=1.959964):
    if n == 0:
        return np.nan, np.nan
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


class FinalAnalyses:
    def __init__(self, root, config_path=None, dev_dir=None, dev_patients=None):
        """`dev_dir`: code check on a patient subset, written outside the repo and without a prespec record."""
        self.root = Path(root).resolve()
        self.config_path = Path(config_path or self.root / "configs" / "final_analyses_v1.yaml")
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.ml = yaml.safe_load((self.root / "configs" / "ml.yaml").read_text(encoding="utf-8"))
        self.ev = LockedEvaluation(self.root, self.root / self.config["evaluation_config"])
        self.run_dir = self.root / "artifacts" / "models" / self.config["baselines"]["frozen_run"]
        self.cohort_path = self.ev.paths["data_root"] / "datasets" / "cohort.parquet"
        self.dict_path = self.ev.paths["data_root"] / "datasets" / "feature_dictionary.csv"
        self.out = Path(dev_dir) if dev_dir else self.root / self.config["outputs"]
        self.dev_patients = dev_patients
        inputs = {"config": self.config_path, "evaluation_config": self.ev.config_path,
                  "policy": self.root / self.ev.config["policy"], "snapshots": self.ev.paths["snapshots"],
                  "cohort": self.cohort_path, "risks_v2": self.root / "artifacts" / "predictions" /
                  f"{self.ev.policy['model']['run_id']}_test_hourly_risks_v2.parquet"}
        inputs |= {f"pipeline_{p}": self.run_dir / p / "pipeline.joblib" for p in self.config["baselines"]["frozen_pipelines"]}
        sources = {p.relative_to(self.root).as_posix(): p for p in [self.root / "src" / "aki_report" / "final_analyses.py",
                                                                    self.root / "src" / "aki_final" / "test_evaluation.py", self.root / "src" / "aki_ml" / "models.py"]}
        self.prespec = {"prespec_sha256": None} if dev_dir else freeze_prespec(self.out, self.config, inputs, sources)
        self.out.mkdir(parents=True, exist_ok=True)
        self.horizons = [int(h) for h in self.config["horizons"]]
        self.seed = int(self.config["seed"])
        self.frame = None

    def log(self, message):
        print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)

    # -------------------------------------------------------------------------------------------- scores
    def load(self):
        """Locked hourly test frame plus every baseline's risk per horizon (columns `<model>_<h>`)."""
        if self.frame is not None:
            return self.frame
        f = self.ev.load().copy()
        for h in self.horizons:
            f[f"primary_{h}"] = f[f"risk_{h}"]
        snaps = self.ev.paths["snapshots"]
        test = [("split", "=", self.config["split"])]
        keys = pq.read_table(snaps, columns=KEYS, filters=test).to_pandas()
        keys["patient_id"] = keys["patient_id"].astype(str)
        keys["hours_since_icu"] = keys["hours_since_icu"].round().astype(int)
        full = joblib.load(self.run_dir / "lightgbm__full" / "pipeline.joblib")
        names = list(full.feature_names)
        X = load_feature_matrix(snaps, names, filters=test)
        scores = keys.copy()
        with threadpool_limits(limits=8):
            for p in self.config["baselines"]["frozen_pipelines"]:
                pipe = full if p == "lightgbm__full" else joblib.load(self.run_dir / p / "pipeline.joblib")
                cols = None if pipe.feature_names == names else np.array([names.index(c) for c in pipe.feature_names])
                if pipe.family == "lr" and cols is not None:
                    raise RuntimeError("LR pipelines index the full stored feature order")
                for h in self.horizons:
                    scores[f"{p}_{h}"] = pipe.predict(X, h, "platt", cols=cols)
                self.log(f"scored {p}")
                del pipe
        rise = X[:, names.index("creatinine_rise_from_48h_min")].astype(float)
        rise = np.where(np.isnan(rise), 0.0, rise)
        del X
        for h in self.horizons:                       # monotone map to [0, 1] so the alert engine accepts it
            scores[f"creatinine_rise_{h}"] = (rankdata(rise) / len(rise)).astype(np.float32)
        cl = self.creatinine_lr(keys)
        scores = scores.merge(cl, on=KEYS, how="left", validate="one_to_one")
        f = f.merge(scores, on=KEYS, how="left", validate="one_to_one")
        if f[[f"{m}_{h}" for m in MODEL_LABELS for h in self.horizons]].isna().any().any():
            raise RuntimeError("Missing baseline scores for some test origins")
        if self.dev_patients:
            keep = np.sort(f["patient_id"].unique())[:self.dev_patients]
            f = f[f["patient_id"].isin(keep)].reset_index(drop=True)
        self.frame = f
        return f

    def creatinine_lr(self, test_keys):
        """Notebook-02 cell 5.5 recipe, refitted on training patients; returns test risks and checks validation."""
        d = pd.read_csv(self.dict_path)
        feats = d.loc[d["role"] == "feature", "feature"].tolist()
        group = d.set_index("feature")["group"]
        cols = [c for c in feats if group[c] == "creatinine" or c == "hours_since_icu"]
        snaps = self.ev.paths["snapshots"]
        labels = [f"{p}_{h}h" for h in self.horizons for p in ("eligible", "aki_within")]
        meta = pq.read_table(snaps, columns=["patient_id", "split", "source_system"] + labels).to_pandas()
        X = load_feature_matrix(snaps, cols)
        split = meta["split"].astype(str).to_numpy()
        pcode = pd.factorize(meta["patient_id"])[0]
        train = split == "train"
        tp = meta.loc[train, ["patient_id", "source_system"]].drop_duplicates("patient_id").set_index("patient_id")
        cohort = pd.read_parquet(self.cohort_path, columns=["patient_id", "cr_aki_onset_hours"])
        ever = cohort.set_index("patient_id")["cr_aki_onset_hours"].notna().reindex(tp.index)
        strata = tp["source_system"].astype(str) + "|" + ever.astype(str)
        seed = int(self.ml["run"]["seed"])
        fold = pd.Series(-1, index=tp.index, dtype=np.int8)
        for k, (_, ho) in enumerate(StratifiedKFold(int(self.ml["cv"]["n_folds"]), shuffle=True,
                                                    random_state=seed).split(strata, strata)):
            fold.iloc[ho] = k
        FOLD = np.full(len(meta), -1, dtype=np.int8)
        FOLD[train] = fold.reindex(meta.loc[train, "patient_id"]).to_numpy()
        C = float(self.ml["checks"]["baseline_lr_C"])

        def weights(rows, y):
            _, inv, cnt = np.unique(pcode[rows], return_inverse=True, return_counts=True)
            w = 1.0 / cnt[inv]
            pos, neg = w[y == 1].sum(), w[y == 0].sum()
            w = np.where(y == 1, w * (pos + neg) / (2 * pos), w * (pos + neg) / (2 * neg))
            return (w / w.mean()).astype(np.float32)

        def fit(rows, y):
            pre = LRPreprocessor(cols, seed=seed).fit(X, rows)
            clf = LogisticRegression(C=C, solver="newton-cholesky", max_iter=100)
            clf.fit(pre.transform(X, rows), y, sample_weight=weights(rows, y))
            return pre, clf

        out = meta.loc[split == self.config["split"], []].copy()
        check = []
        for h in self.horizons:
            el = meta[f"eligible_{h}h"].to_numpy(bool)
            Y = meta[f"aki_within_{h}h"].astype("Float32").to_numpy(dtype=np.float32, na_value=np.nan)
            tr_el = np.flatnonzero(train & el)
            oof = np.full(len(meta), np.nan, np.float32)
            for k in range(int(self.ml["cv"]["n_folds"])):
                tr, ho = tr_el[FOLD[tr_el] != k], tr_el[FOLD[tr_el] == k]
                pre, clf = fit(tr, Y[tr])
                oof[ho] = clf.predict_proba(pre.transform(X, ho))[:, 1]
            platt = PlattCalibrator().fit(oof[tr_el], Y[tr_el])
            pre, clf = fit(tr_el, Y[tr_el])
            va = np.flatnonzero((split == "validation") & el)
            pv = platt.predict(clf.predict_proba(pre.transform(X, va))[:, 1])
            check.append({"horizon_h": h, "validation_auroc_refit": fast_auroc(Y[va].astype(int), pv)})
            te = np.flatnonzero(split == self.config["split"])
            out[f"creatinine_lr_{h}"] = platt.predict(clf.predict_proba(pre.transform(X, te))[:, 1])
        stored = pd.read_csv(self.run_dir / "reports" / "baselines.csv")
        stored = stored[(stored["baseline"] == "creatinine_lr") & (stored["set"] == "validation")]
        check = pd.DataFrame(check).merge(stored[["horizon_h", "auroc"]].rename(columns={"auroc": "validation_auroc_notebook02"}),
                                          on="horizon_h")
        self.out.mkdir(parents=True, exist_ok=True)
        check.to_csv(self.out / "creatinine_lr_refit_check.csv", index=False)
        self.log(f"creatinine LR refit check:\n{check.round(4).to_string(index=False)}")
        result = test_keys.copy()
        if len(result) != len(out):
            raise RuntimeError("Row count mismatch between test keys and creatinine LR scores")
        for h in self.horizons:
            result[f"creatinine_lr_{h}"] = out[f"creatinine_lr_{h}"].to_numpy()
        return result

    # -------------------------------------------------------------------------------------------- item 2 + 3
    def severity(self):
        f, ev = self.load(), self.ev
        cohort = pd.read_parquet(self.cohort_path, columns=["patient_id", "cr_aki_max_stage", "cr_aki_onset_stage",
                                                            "rrt_start_hours", "cr_aki_onset_hours"])
        cohort["patient_id"] = cohort["patient_id"].astype(str)
        cohort = cohort.set_index("patient_id")
        a = ev.alert_frame(f)
        to_onset = a["hours_to_onset"].to_numpy(float)
        rows, ops = [], []
        for name, rule, thr in [("policy_v3", ev.rule, ev.threshold), ("demo_v1", ev.comparator_rule, ev.comparator_threshold)]:
            flags = alert_events(a, thr, rule)
            pp = per_patient(a, flags)
            s = alert_summary(pp)
            ops.append({"policy": name, "alert_ppv": s["alert_ppv"],
                        "false_alerts_per_true_alert": (1 - s["alert_ppv"]) / s["alert_ppv"],
                        "event_detection_rate": s["event_detection_rate"], "lead_time_median_h": s["lead_time_median_h"],
                        "alerts_per_patient_day": s["alerts_per_patient_day"]})
            events = pp.index[pp["event"].to_numpy(bool)]
            c = cohort.reindex(events)
            caught = {}
            for L in self.config["severity"]["lookahead_hours"]:
                hit = pd.Series(flags & (to_onset > 0) & (to_onset <= L)).groupby(a["patient_id"].to_numpy()).any()
                caught[L] = hit.reindex(events).fillna(False).to_numpy(bool)
            lead = pp.loc[events, "lead"].to_numpy(float)
            stage = c["cr_aki_max_stage"].to_numpy(float)
            rrt = (c["rrt_start_hours"] >= c["cr_aki_onset_hours"]).fillna(False).to_numpy(bool)
            strata = [("all events", np.ones(len(events), bool)), ("max stage 1", stage == 1), ("max stage 2", stage == 2),
                      ("max stage 3", stage == 3), ("max stage 2-3", stage >= 2), ("went on to RRT", rrt)]
            for label, m in strata:
                row = {"policy": name, "stratum": label, "events": int(m.sum())}
                for L, hit in caught.items():
                    k = int(hit[m].sum())
                    lo, hi = wilson(k, int(m.sum()))
                    row |= {f"detected_{L}h": k, f"detection_rate_{L}h": k / m.sum() if m.sum() else np.nan,
                            f"detection_{L}h_ci_low": lo, f"detection_{L}h_ci_high": hi}
                row["lead_time_median_h"] = float(np.nanmedian(lead[m & caught[24]])) if (m & caught[24]).any() else np.nan
                rows.append(row)
        sev, ops = pd.DataFrame(rows), pd.DataFrame(ops)
        sev.to_csv(self.out / "severity_detection.csv", index=False)
        ops.to_csv(self.out / "operating_points.csv", index=False)
        return sev, ops

    # -------------------------------------------------------------------------------------------- item 4
    def baselines(self):
        f = self.load()
        n = int(self.config["bootstrap"]["resamples"])
        rows, deltas = [], []
        for h in self.horizons:
            e = f[f"eligible_{h}h"].to_numpy(bool)
            y = f[f"aki_within_{h}h"].to_numpy(float)[e].astype(int)
            P = {m: f[f"{m}_{h}"].to_numpy(float)[e] for m in MODEL_LABELS}
            for m, p in P.items():
                met = snapshot_metrics(y, p, slope=m in PROBABILISTIC)
                if m not in PROBABILISTIC:
                    met |= {k: np.nan for k in ["brier", "observed_over_expected", "calibration_slope", "calibration_intercept"]}
                rows.append({"model": m, "label": MODEL_LABELS[m], "horizon_h": h, **met})
            boot = PatientBootstrap(f["patient_id"].to_numpy()[e], n, self.seed + h)
            self.log(f"baselines {h}h: bootstrap ({n} draws)")

            def stat(idx):
                yy = y[idx]
                aucs = {m: fast_auroc(yy, p[idx]) for m, p in P.items()}
                return {**{f"auroc|{m}": v for m, v in aucs.items()},
                        **{f"delta|{m}": aucs[m] - aucs["primary"] for m in P if m != "primary"}}

            ci = boot.interval(stat)
            point = stat(np.arange(len(y)))
            for m in P:
                d = {"model": m, "horizon_h": h, "auroc": point[f"auroc|{m}"],
                     "auroc_ci_low": ci.loc[.025, f"auroc|{m}"], "auroc_ci_high": ci.loc[.975, f"auroc|{m}"]}
                if m != "primary":
                    d |= {"auroc_minus_primary": point[f"delta|{m}"], "delta_ci_low": ci.loc[.025, f"delta|{m}"],
                          "delta_ci_high": ci.loc[.975, f"delta|{m}"]}
                deltas.append(d)
        table, deltas = pd.DataFrame(rows), pd.DataFrame(deltas)
        table.to_csv(self.out / "baseline_metrics.csv", index=False)
        deltas.to_csv(self.out / "baseline_auroc_differences.csv", index=False)
        return table, deltas

    def tradeoff(self):
        f, ev, cfg = self.load(), self.ev, self.config["alert_tradeoff"]
        h = int(cfg["horizon_h"])
        rule = AlertRule("sweep", sustain_hours=int(cfg["rule"]["sustain_hours"]), cooldown_hours=float(cfg["rule"]["cooldown_hours"]))
        e = f[f"eligible_{h}h"].to_numpy(bool)
        rows = []
        for m in MODEL_LABELS:
            g = f.copy()
            g[f"risk_{h}"] = f[f"{m}_{h}"]
            a = ev.alert_frame(g)
            for q in np.linspace(0.50, 0.995, 40):
                thr = float(np.quantile(f[f"{m}_{h}"].to_numpy(float)[e], q))
                s = alert_summary(per_patient(a, alert_events(a, thr, rule)))
                rows.append({"model": m, "quantile": q, "threshold": thr, **s})
            self.log(f"trade-off {m}")
        a = ev.alert_frame(f)
        for name, rule_, thr in [("policy_v3", ev.rule, ev.threshold), ("demo_v1", ev.comparator_rule, ev.comparator_threshold)]:
            rows.append({"model": name, "quantile": np.nan, "threshold": thr,
                         **alert_summary(per_patient(a, alert_events(a, thr, rule_)))})
        t = pd.DataFrame(rows)
        t.to_csv(self.out / "alert_tradeoff_24h.csv", index=False)
        return t

    # -------------------------------------------------------------------------------------------- item 5 + curves
    def decision_curves(self):
        f, cfg = self.load(), self.config["decision_curve"]
        th = np.round(np.arange(cfg["thresholds"]["start"], cfg["thresholds"]["stop"] + 1e-9, cfg["thresholds"]["step"]), 4)
        rows = []
        for h in [int(x) for x in cfg["horizons"]]:
            e = f[f"eligible_{h}h"].to_numpy(bool)
            y = f[f"aki_within_{h}h"].to_numpy(float)[e].astype(bool)
            N, prev = len(y), y.mean()
            for pt in th:
                w = pt / (1 - pt)
                rows.append({"horizon_h": h, "threshold": pt, "model": "treat_all", "net_benefit": prev - (1 - prev) * w})
                rows.append({"horizon_h": h, "threshold": pt, "model": "treat_none", "net_benefit": 0.0})
                for m in PROBABILISTIC:
                    p = f[f"{m}_{h}"].to_numpy(float)[e]
                    pos = p >= pt
                    tp, fp = (pos & y).sum(), (pos & ~y).sum()
                    rows.append({"horizon_h": h, "threshold": pt, "model": m, "net_benefit": tp / N - fp / N * w,
                                 "flagged_fraction": pos.mean()})
        t = pd.DataFrame(rows)
        t.to_csv(self.out / "decision_curves.csv", index=False)
        return t

    def curves(self):
        f, cfg = self.load(), self.config["curves"]
        grid_f = np.linspace(0, 1, int(cfg["roc_fpr_grid"]))
        grid_r = np.linspace(0, 1, int(cfg["pr_recall_grid"]))
        roc, pr, cal = [], [], []
        for h in self.horizons:
            e = f[f"eligible_{h}h"].to_numpy(bool)
            y = f[f"aki_within_{h}h"].to_numpy(float)[e].astype(int)
            for m in MODEL_LABELS:
                p = f[f"{m}_{h}"].to_numpy(float)[e]
                fpr, tpr, _ = roc_curve(y, p)
                roc += [{"model": m, "horizon_h": h, "fpr": a, "tpr": b} for a, b in zip(grid_f, np.interp(grid_f, fpr, tpr))]
                prec, rec, _ = precision_recall_curve(y, p)
                # interpolated precision: best precision at recall >= r
                order = np.argsort(rec)
                rec_s, prec_s = rec[order], np.maximum.accumulate(prec[order][::-1])[::-1]
                idx = np.searchsorted(rec_s, grid_r, side="left").clip(0, len(rec_s) - 1)
                pr += [{"model": m, "horizon_h": h, "recall": r, "precision": q, "prevalence": y.mean()}
                       for r, q in zip(grid_r, prec_s[idx])]
                if m in PROBABILISTIC:
                    bins = pd.qcut(pd.Series(p).rank(method="first"), int(cfg["calibration_bins"]), labels=False)
                    g = pd.DataFrame({"b": bins, "p": p, "y": y}).groupby("b")
                    for b, d in g:
                        lo, hi = wilson(int(d["y"].sum()), len(d))
                        cal.append({"model": m, "horizon_h": h, "bin": int(b), "n": len(d), "mean_predicted": d["p"].mean(),
                                    "observed": d["y"].mean(), "observed_ci_low": lo, "observed_ci_high": hi})
        for name, t in [("roc_points", roc), ("pr_points", pr), ("calibration_bins", cal)]:
            pd.DataFrame(t).to_csv(self.out / f"{name}.csv", index=False)

    # -------------------------------------------------------------------------------------------- run
    def run(self):
        self.load()
        self.log("severity");          self.severity()
        self.log("curves");            self.curves()
        self.log("decision curves");   self.decision_curves()
        self.log("alert trade-off");   self.tradeoff()
        self.log("baselines");         self.baselines()
        write_json(self.out / "run_manifest.json", {
            "version": self.config["version"], "prespec_sha256": self.prespec["prespec_sha256"],
            "finished_at": datetime.now(timezone.utc).isoformat(), "split": self.config["split"],
            "patients": int(self.frame["patient_id"].nunique()), "origins": len(self.frame),
            "outputs": sorted(p.name for p in self.out.iterdir())})
        self.log(f"reports -> {self.out}")
