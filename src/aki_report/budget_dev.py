"""Validation-only development of fixes for alert-budget-v1 limitations (configs/alert_budget_v2_dev.yaml).

Reuses the alert-budget engine (Counts, certified threshold, parity) unchanged from aki_report.budget. The test split is
never loaded. Aggregate outputs only.
"""
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml
from sklearn.linear_model import LogisticRegression

from aki_eval.protocol import freeze_prespec, write_json
from aki_final.test_evaluation import KEYS, LockedEvaluation
from aki_ml.models import logit
from aki_report.budget import Counts, group_thresholds_for, ltt_parity, ltt_single, p_value, parity_thresholds

HORIZONS = [6, 12, 24, 48]
REFS = {"insurance": "Private", "ethnicity_group": "WHITE"}


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class GroupRecalibrator:
    """Per-horizon logistic recalibration with insurance and ethnicity offsets (same model as alert-budget-v1 step 1)."""

    def __init__(self, levels):
        self.levels = levels

    def design(self, f, h):
        X = [logit(f[f"risk_{h}"].to_numpy(float)).reshape(-1, 1)]
        for var, lv in self.levels.items():
            X += [(f[var].astype(str) == x).to_numpy(float).reshape(-1, 1) for x in lv if x != REFS[var]]
        return np.hstack(X)

    def fit(self, f, horizons=(12, 24)):
        self.models = {}
        for h in horizons:
            e = f[f"eligible_{h}h"].to_numpy(bool)
            self.models[h] = LogisticRegression(C=1.0, max_iter=2000).fit(
                self.design(f, h)[e], f[f"aki_within_{h}h"].to_numpy(float)[e].astype(int))
        return self

    def apply(self, f):
        g = f.copy()
        for h, m in self.models.items():
            g[f"risk_{h}"] = m.predict_proba(self.design(f, h))[:, 1]
        return g


class BudgetDev:
    def __init__(self, root, config_path=None):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path or self.root / "configs" / "alert_budget_v2_dev.yaml")
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.base = yaml.safe_load((self.root / self.config["base"]).read_text(encoding="utf-8"))
        self.ev = LockedEvaluation(self.root, self.root / self.base["evaluation_config"], split="validation")
        self.cohort_path = self.ev.paths["data_root"] / "datasets" / "cohort.parquet"
        self.run_id = self.ev.policy["model"]["run_id"]
        self.pred_path = self.root / "artifacts" / "predictions" / f"{self.run_id}.parquet"
        self.heads_dir = self.root / "artifacts" / "models" / self.run_id / "system-shift-v1"
        self.out = self.root / self.config["outputs"]
        inputs = {"config": self.config_path, "base_config": self.root / self.config["base"], "cohort": self.cohort_path,
                  "predictions": self.pred_path, "policy": self.root / self.ev.config["policy"]}
        sources = {p.relative_to(self.root).as_posix(): p for p in
                   [self.root / "src" / "aki_report" / "budget_dev.py", self.root / "src" / "aki_report" / "budget.py"]}
        self.prespec = freeze_prespec(self.out, self.config, inputs, sources)
        g = self.base["threshold_grid"]
        self.grid = np.round(np.arange(g["start"], g["stop"] + 1e-9, g["step"]), 4)
        self.B, self.delta = float(self.base["budget"]["primary"]), float(self.base["budget"]["delta"])
        t = self.base["groups"]["parity"]["detection_targets"]
        self.targets = np.round(np.arange(t["start"], t["stop"] + 1e-9, t["step"]), 4)
        self.levels = self.base["groups"]["variables"]
        self.seed = int(self.config["seed"])

    # -------------------------------------------------------------------------------------------- data
    def load(self):
        c = pd.read_parquet(self.cohort_path, columns=["patient_id", "insurance", "ethnicity_group"])
        c["patient_id"] = c["patient_id"].astype(str)
        self.groups = {v: c.set_index("patient_id")[v].astype(str) for v in ["insurance", "ethnicity_group"]}
        self.val = self.ev.load().merge(c, on="patient_id", how="left")
        t = pq.read_table(self.pred_path, columns=["patient_id", "hours_since_icu", "snapshot_time", "creatinine_aki_onset_time",
                                                   "horizon_h", "eligible", "label", "risk_platt"],
                          filters=[("family", "=", "lightgbm"), ("feature_set", "=", "no_dc"), ("split", "=", "train")]).to_pandas()
        t["patient_id"] = t["patient_id"].astype(str)
        t["hours_since_icu"] = t["hours_since_icu"].round().astype(int)
        base = t[t["horizon_h"] == 24][["patient_id", "hours_since_icu", "snapshot_time", "creatinine_aki_onset_time"]]
        for h in HORIZONS:
            x = t[t["horizon_h"] == h][KEYS + ["eligible", "label", "risk_platt"]].rename(
                columns={"eligible": f"eligible_{h}h", "label": f"aki_within_{h}h", "risk_platt": f"risk_{h}"})
            base = base.merge(x, on=KEYS, how="left", validate="one_to_one")
        for h in HORIZONS:
            base[f"eligible_{h}h"] = base[f"eligible_{h}h"].fillna(False).astype(bool)
            base[f"aki_within_{h}h"] = base[f"aki_within_{h}h"].astype(float)
        self.train = base.merge(c, on="patient_id", how="left").sort_values(KEYS).reset_index(drop=True)
        log(f"validation {self.val['patient_id'].nunique():,} patients; training OOF {self.train['patient_id'].nunique():,}")

    def counts(self, frame, grid=None):
        return Counts(self.ev, frame.sort_values(KEYS).reset_index(drop=True), self.grid if grid is None else grid, self.groups)

    @staticmethod
    def oe_rows(frame, patients, label, rep):
        f = frame[frame["patient_id"].isin(patients)]
        rows = []
        for var in ["insurance", "ethnicity_group"]:
            for level, d in f.groupby(var):
                e = d["eligible_24h"].to_numpy(bool)
                y, p = d["aki_within_24h"].to_numpy(float)[e], d["risk_24"].to_numpy(float)[e]
                rows.append({"repeat": rep, "variant": label, "variable": var, "level": level,
                             "events": int(y.sum()), "oe": y.mean() / p.mean() if len(y) else np.nan})
        return rows

    # -------------------------------------------------------------------------------------------- fixes A and B
    def run_in_distribution(self):
        oof_recal = GroupRecalibrator(self.levels).fit(self.train)
        val_oofrecal = oof_recal.apply(self.val)
        C0 = self.counts(self.val)
        C2 = self.counts(val_oofrecal)
        log("training OOF counts (parity learning source)")
        T2 = self.counts(oof_recal.apply(self.train))
        patients = C0.patients
        rng = np.random.default_rng(self.seed)
        oe, pol = [], []
        frac = float(self.config["splits"]["fit_fraction"])
        for rep in range(int(self.config["splits"]["repeats"])):
            v1 = rng.random(len(patients)) < frac
            w1, v2 = v1.astype(float), ~v1
            p1, p2 = set(patients[v1]), set(patients[v2])
            rec1 = GroupRecalibrator(self.levels).fit(self.val[self.val["patient_id"].isin(p1)])
            val_v1recal = rec1.apply(self.val)
            C1 = self.counts(val_v1recal)
            oe += self.oe_rows(self.val, p2, "P0_none", rep) + self.oe_rows(val_v1recal, p2, "P1_offsets_V1", rep) \
                + self.oe_rows(val_oofrecal, p2, "P2_offsets_trainOOF", rep)
            j0 = ltt_single(C0, self.B, self.delta, w1)
            j1 = ltt_single(C1, self.B, self.delta, w1)
            j2 = ltt_single(C2, self.B, self.delta, w1)
            idx3, r3 = ltt_parity(C1, j1, self.targets, "ethnicity_group", self.B, self.delta, 100 * frac, 20 * frac, w1)
            # P4: thresholds per group learned on training OOF, target r certified against the budget on V1
            par_oof = parity_thresholds(T2, j2, self.targets, "ethnicity_group", 100, 20)
            idx4, r4 = None, None
            for r, idx_t in zip(self.targets, par_oof):
                idx_v = group_thresholds_for(T2, idx_t, C2, "ethnicity_group")
                F = C2.F[np.arange(len(patients)), idx_v]
                if p_value(F, C2.days, w1, self.B) <= self.delta:
                    idx4, r4 = idx_v, r
                else:
                    break
            variants = {"P0_single_threshold": (C0, j0), "P1_offsets_V1": (C1, j1), "P2_offsets_trainOOF": (C2, j2),
                        "P3_parity_V1": (C1, idx3), "P4_parity_trainOOF": (C2, idx4)}
            for name, (C, idx) in variants.items():
                if idx is None:
                    continue
                row = {"repeat": rep, "variant": name, **C.policy(idx, v2)}
                g = C.groups["ethnicity_group"]
                dets = {}
                for level in np.unique(g):
                    m = v2 & (g == level)
                    d = C.policy(idx, m)
                    row[f"detection_{level}"] = d["event_detection_rate"]
                    row[f"events_{level}"] = d["aki_events"]
                    if d["aki_events"] >= 10:
                        dets[level] = d["event_detection_rate"]
                row["detection_range"] = max(dets.values()) - min(dets.values())
                row["detection_worst_group"] = min(dets.values())
                row["budget_met"] = row["false_alerts_per_patient_day"] <= self.B
                pol.append(row)
            log(f"split {rep + 1}: done")
        return pd.DataFrame(oe), pd.DataFrame(pol)

    # -------------------------------------------------------------------------------------------- fix C
    def run_shift(self):
        cfg = self.config["fixes"]["C_sample_splitting"]
        grid = np.round(np.arange(self.grid[0], self.grid[-1] + 1e-9, float(cfg["threshold_grid_step"])), 4)
        meta = pq.read_table(self.ev.paths["snapshots"], columns=["patient_id", "split", "source_system", "hours_since_icu"]).to_pandas()
        meta["patient_id"] = meta["patient_id"].astype(str)
        meta["hours_since_icu"] = meta["hours_since_icu"].round().astype(int)
        k = meta[meta["split"].astype(str) == "validation"][["patient_id", "hours_since_icu"]].reset_index(drop=True)
        rows = []
        rng = np.random.default_rng(self.seed + 1)
        for target, model in self.base["settings"]["system_shift"]["pairs"].items():
            heads = {h: joblib.load(self.heads_dir / model / f"head_{h}h.joblib") for h in HORIZONS}
            kk = k.copy()
            for h in HORIZONS:
                kk[f"m_{h}"] = heads[h]["platt"].predict(heads[h]["raw"]["validation"])
            f = self.val.drop(columns=[f"risk_{h}" for h in HORIZONS]).merge(kk, on=KEYS, how="inner", validate="one_to_one")
            f = f.rename(columns={f"m_{h}": f"risk_{h}" for h in HORIZONS})
            f = f[f["source_system"] == target]
            pats = np.sort(f["patient_id"].unique())
            for rep in range(int(cfg["repeats"])):
                perm = rng.permutation(pats)
                n_fit = int(round(.6 * len(pats)))
                fit, ev_ = set(perm[:n_fit]), set(perm[n_fit:])
                half_a, half_b = set(perm[:n_fit // 2]), set(perm[n_fit // 2:n_fit])
                for name, rec_set, thr_set in [("double_use", fit, fit), ("split", half_a, half_b)]:
                    rec = GroupRecalibrator(self.levels).fit(f[f["patient_id"].isin(rec_set)])
                    C = Counts(self.ev, rec.apply(f).sort_values(KEYS).reset_index(drop=True), grid, self.groups)
                    w = np.isin(C.patients, list(thr_set)).astype(float)
                    j = ltt_single(C, self.B, self.delta, w)
                    if j is None:
                        continue
                    m = C.policy(j, np.isin(C.patients, list(ev_)))
                    rows.append({"target_system": target, "repeat": rep, "variant": name, "threshold": grid[j], **m,
                                 "budget_met": m["false_alerts_per_patient_day"] <= self.B})
                if (rep + 1) % 20 == 0:
                    log(f"shift to {target}: {rep + 1} repeats")
        return pd.DataFrame(rows)

    def run(self):
        self.load()
        oe, pol = self.run_in_distribution()
        shift = self.run_shift()
        self.out.mkdir(parents=True, exist_ok=True)
        oe.to_csv(self.out / "fixA_calibration_by_group_repeats.csv", index=False)
        pol.to_csv(self.out / "fixB_policies_repeats.csv", index=False)
        shift.to_csv(self.out / "fixC_sample_splitting_repeats.csv", index=False)
        # summaries
        big = oe[oe["events"] >= 20].assign(abs_log_oe=lambda d: np.abs(np.log(d["oe"])))
        a = big.groupby(["variant", "repeat"])["abs_log_oe"].mean().groupby("variant").mean().rename("mean_abs_log_oe").reset_index()
        lv = oe.groupby(["variant", "variable", "level"]).agg(oe_mean=("oe", "mean"), events_mean=("events", "mean")).reset_index()
        a.to_csv(self.out / "fixA_summary.csv", index=False)
        lv.to_csv(self.out / "fixA_by_level.csv", index=False)
        keep = ["false_alerts_per_patient_day", "event_detection_rate", "alert_ppv", "detection_range", "detection_worst_group", "budget_met"]
        keep += [c for c in pol.columns if c.startswith("detection_") and c not in ("detection_range", "detection_worst_group")]
        pol.groupby("variant")[keep].mean().reset_index().to_csv(self.out / "fixB_summary.csv", index=False)
        shift.groupby(["target_system", "variant"]).agg(coverage=("budget_met", "mean"), repeats=("budget_met", "size"),
                                                         false_alerts_mean=("false_alerts_per_patient_day", "mean"),
                                                         detection_mean=("event_detection_rate", "mean")).reset_index() \
            .to_csv(self.out / "fixC_summary.csv", index=False)
        write_json(self.out / "run_manifest.json", {"version": self.config["version"], "prespec_sha256": self.prespec["prespec_sha256"],
                   "finished_at": datetime.now(timezone.utc).isoformat(), "test_split_loaded": False,
                   "outputs": sorted(p.name for p in self.out.iterdir())})
        log(f"reports -> {self.out}")
