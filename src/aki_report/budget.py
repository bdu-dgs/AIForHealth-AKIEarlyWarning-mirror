"""Alert-budget control on top of the locked risk model (configs/alert_budget_v1.yaml).

Step 1 recalibrates risks per patient group, step 2 picks the alert threshold that provably meets a false-alert budget
(Learn-then-Test fixed-sequence testing, patient-clustered), step 3 equalises detection across ethnicity groups under
the same budget. Calibration uses validation patients; test patients only evaluate. Aggregate outputs only.

Engine: for every candidate threshold the alert rule is replayed once over the hourly frame and per-patient counts are
stored (alerts, true and false alerts, detected event, lead time, exposure). Any policy (one threshold, or one
threshold per group) and any bootstrap of the calibration patients is then evaluated from these matrices.
"""
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml
from scipy.stats import norm
from sklearn.linear_model import LogisticRegression

from aki_eval.policies import alert_events
from aki_eval.protocol import freeze_prespec, write_json
from aki_final.test_evaluation import KEYS, LockedEvaluation, per_patient
from aki_ml.models import logit

HORIZONS = [6, 12, 24, 48]


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class Counts:
    """Per-patient alert counts for every candidate threshold of one risk frame."""

    def __init__(self, ev, frame, grid, groups):
        a = ev.alert_frame(frame)
        base = per_patient(a, np.zeros(len(a), bool))
        self.patients = base.index.to_numpy()
        self.days = base["rows"].to_numpy(float) / 24.0
        self.event = base["event"].to_numpy(bool)
        n, k = len(self.patients), len(grid)
        self.F = np.zeros((n, k), np.int32)
        self.T = np.zeros((n, k), np.int32)
        self.A = np.zeros((n, k), np.int32)
        self.L = np.full((n, k), np.nan, np.float32)
        for j, t in enumerate(grid):
            pp = per_patient(a, alert_events(a, float(t), ev.rule)).reindex(self.patients)
            self.F[:, j], self.T[:, j], self.A[:, j] = pp["false"], pp["true"], pp["alerts"]
            self.L[:, j] = pp["lead"].to_numpy(float)
        self.groups = {k_: groups[k_].reindex(self.patients).fillna("missing").to_numpy() for k_ in groups}
        self.grid = np.asarray(grid)

    def policy(self, idx, mask=None):
        """Metrics of a per-patient threshold index (array) over patients in mask."""
        m = np.ones(len(self.patients), bool) if mask is None else mask
        r = np.arange(len(self.patients))[m]
        j = np.broadcast_to(idx, (len(self.patients),))[m]
        F, T, A, L = self.F[r, j], self.T[r, j], self.A[r, j], self.L[r, j]
        days, ev = self.days[m].sum(), self.event[m]
        det = ev & (T > 0)
        return {"patients": int(m.sum()), "aki_events": int(ev.sum()),
                "false_alerts_per_patient_day": F.sum() / days, "alerts_per_patient_day": A.sum() / days,
                "event_detection_rate": det.sum() / ev.sum() if ev.sum() else np.nan,
                "alert_ppv": T.sum() / (T.sum() + F.sum()) if T.sum() + F.sum() else np.nan,
                "lead_time_median_h": float(np.nanmedian(L[det])) if det.any() else np.nan,
                "non_aki_patients_alerted": float((A[~ev] > 0).mean()) if (~ev).any() else np.nan}


def p_value(F, D, w, budget):
    """One-sided p-value for H0: sum(wF)/sum(wD) > budget (patient-clustered ratio, delta method)."""
    sw = w.sum()
    r = (w * F).sum() / (w * D).sum()
    u = F - r * D
    var = ((w * u * u).sum() / sw) / ((w * D).sum() / sw) ** 2 / sw
    if var <= 0:
        return 0.0 if r <= budget else 1.0
    return float(1 - norm.cdf((budget - r) / np.sqrt(var)))


def ltt_single(C, budget, delta, w=None):
    """Lowest threshold index whose false-alert budget is certified (fixed sequence from high to low thresholds)."""
    w = np.ones(len(C.patients)) if w is None else w
    chosen = None
    for j in range(len(C.grid) - 1, -1, -1):
        if p_value(C.F[:, j], C.days, w, budget) <= delta:
            chosen = j
        else:
            break
    return chosen


def parity_thresholds(C, base_j, targets, var, min_pat, min_ev, w=None):
    """Per-patient threshold indices for each detection target r (ethnicity groups large enough get their own)."""
    w = np.ones(len(C.patients)) if w is None else w
    g = C.groups[var]
    out = []
    eligible = {}
    for level in np.unique(g):
        m = g == level
        if w[m].sum() >= min_pat and (w[m] * C.event[m]).sum() >= min_ev:
            ev = C.event[m]
            det = ((C.T[m] > 0) & ev[:, None]).T @ w[m] / (w[m] * ev).sum()   # detection per threshold
            eligible[level] = (m, det)
    for r in targets:
        idx = np.full(len(C.patients), base_j)
        for level, (m, det) in eligible.items():
            ok = np.flatnonzero(det >= r)
            idx[m] = ok.max() if len(ok) else 0
        out.append(idx)
    return out


def ltt_parity(C, base_j, targets, var, budget, delta, min_pat, min_ev, w=None):
    w = np.ones(len(C.patients)) if w is None else w
    chosen, chosen_r = None, None
    for r, idx in zip(targets, parity_thresholds(C, base_j, targets, var, min_pat, min_ev, w)):
        F = C.F[np.arange(len(C.patients)), idx]
        if p_value(F, C.days, w, budget) <= delta:
            chosen, chosen_r = idx, r
        else:
            break
    return chosen, chosen_r


def group_thresholds_for(C_cal, idx_cal, C_eval, var):
    """Map per-patient calibration thresholds to evaluation patients through their group (same level -> same index)."""
    g_cal, g_eval = C_cal.groups[var], C_eval.groups[var]
    lut = pd.Series(idx_cal).groupby(g_cal).agg(lambda s: int(s.mode().iloc[0]))
    default = int(pd.Series(idx_cal).mode().iloc[0])
    return np.array([lut.get(x, default) for x in g_eval])


class BudgetStudy:
    def __init__(self, root, config_path=None):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path or self.root / "configs" / "alert_budget_v1.yaml")
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.ev_test = LockedEvaluation(self.root, self.root / self.config["evaluation_config"])
        self.ev_val = LockedEvaluation(self.root, self.ev_test.config_path, split="validation")
        self.cohort_path = self.ev_test.paths["data_root"] / "datasets" / "cohort.parquet"
        self.heads_dir = self.root / "artifacts" / "models" / self.ev_test.policy["model"]["run_id"] / "system-shift-v1"
        self.out = self.root / self.config["outputs"]
        g = self.config["threshold_grid"]
        self.grid = np.round(np.arange(g["start"], g["stop"] + 1e-9, g["step"]), 4)
        inputs = {"config": self.config_path, "evaluation_config": self.ev_test.config_path, "cohort": self.cohort_path,
                  "policy": self.root / self.ev_test.config["policy"]}
        inputs |= {f"head_{v}_{h}h": self.heads_dir / v / f"head_{h}h.joblib" for v in ["train_carevue", "train_metavision"]
                   for h in HORIZONS}
        sources = {p.relative_to(self.root).as_posix(): p for p in
                   [self.root / "src" / "aki_report" / "budget.py", self.root / "src" / "aki_final" / "test_evaluation.py"]}
        self.prespec = freeze_prespec(self.out, self.config, inputs, sources)
        self.B, self.delta = float(self.config["budget"]["primary"]), float(self.config["budget"]["delta"])
        self.seed = int(self.config["seed"])
        par = self.config["groups"]["parity"]
        t = par["detection_targets"]
        self.targets = np.round(np.arange(t["start"], t["stop"] + 1e-9, t["step"]), 4)
        self.par_var = par["variable"]

    # -------------------------------------------------------------------------------------------- data
    def frames(self):
        c = pd.read_parquet(self.cohort_path, columns=["patient_id", "insurance", "ethnicity_group"])
        c["patient_id"] = c["patient_id"].astype(str)
        self.groups = {v: c.set_index("patient_id")[v].astype(str) for v in ["insurance", "ethnicity_group"]}
        self.val = self.ev_val.load().merge(c, on="patient_id", how="left")
        self.test = self.ev_test.load().merge(c, on="patient_id", how="left")

    def recalibrate(self, cal, frames):
        """Step 1: per-horizon logistic recalibration with insurance and ethnicity offsets, fitted on `cal`."""
        spec = self.config["groups"]["variables"]

        def design(f, h):
            X = [logit(f[f"risk_{h}"].to_numpy(float)).reshape(-1, 1)]
            for var, levels in spec.items():
                ref = "Private" if var == "insurance" else "WHITE"
                X += [(f[var].astype(str) == lv).to_numpy(float).reshape(-1, 1) for lv in levels if lv != ref]
            return np.hstack(X)

        out = [f.copy() for f in frames]
        for h in HORIZONS:
            e = cal[f"eligible_{h}h"].to_numpy(bool)
            lr = LogisticRegression(C=1.0, max_iter=2000).fit(design(cal, h)[e], cal[f"aki_within_{h}h"].to_numpy(float)[e].astype(int))
            for f, o in zip(frames, out):
                o[f"risk_{h}"] = lr.predict_proba(design(f, h))[:, 1]
        return out

    def shift_frames(self, target, model):
        """Hourly frames (validation, test) whose risks come from the cross-system model (source Platt calibration)."""
        labels = ["patient_id", "split", "source_system", "hours_since_icu"]
        meta = pq.read_table(self.ev_test.paths["snapshots"], columns=labels).to_pandas()
        meta["patient_id"] = meta["patient_id"].astype(str)
        meta["hours_since_icu"] = meta["hours_since_icu"].round().astype(int)
        split = meta["split"].astype(str).to_numpy()
        heads = {h: joblib.load(self.heads_dir / model / f"head_{h}h.joblib") for h in HORIZONS}
        res = {}
        for s, frame in [("validation", self.val), ("test", self.test)]:
            k = meta.iloc[np.flatnonzero(split == s)][["patient_id", "hours_since_icu"]].reset_index(drop=True)
            for h in HORIZONS:
                k[f"m_{h}"] = heads[h]["platt"].predict(heads[h]["raw"][s])
            g = frame.drop(columns=[f"risk_{h}" for h in HORIZONS]).merge(k, on=KEYS, how="inner", validate="one_to_one")
            g = g.rename(columns={f"m_{h}": f"risk_{h}" for h in HORIZONS})
            res[s] = g
        return res

    def counts(self, frame, label):
        log(f"replaying {len(self.grid)} thresholds: {label} ({frame['patient_id'].nunique():,} patients)")
        return Counts(self.ev_test, frame.sort_values(KEYS).reset_index(drop=True), self.grid, self.groups)

    # -------------------------------------------------------------------------------------------- evaluation helpers
    def j_of(self, t):
        return int(np.argmin(np.abs(self.grid - t)))

    def report(self, setting, method, C_eval, idx, extra=None):
        row = {"setting": setting, "method": method, "budget": self.B, **(extra or {}), **C_eval.policy(idx)}
        row["budget_met"] = bool(row["false_alerts_per_patient_day"] <= row["budget"])
        self.rows.append(row)
        for var in ["insurance", "ethnicity_group"]:
            g = C_eval.groups[var]
            for level in np.unique(g):
                self.group_rows.append({"setting": setting, "method": method, "variable": var, "level": level,
                                        **C_eval.policy(idx, g == level)})

    def oe(self, setting, method, frame):
        for var in ["insurance", "ethnicity_group"]:
            for level, d in frame.groupby(var):
                e = d["eligible_24h"].to_numpy(bool)
                y, p = d["aki_within_24h"].to_numpy(float)[e], d["risk_24"].to_numpy(float)[e]
                self.oe_rows.append({"setting": setting, "method": method, "variable": var, "level": level,
                                     "observed_over_expected_24h": y.mean() / p.mean() if len(y) else np.nan})

    def coverage(self, setting, method, select, C_cal, C_eval, to_eval):
        rng = np.random.default_rng(self.seed)
        n = len(C_cal.patients)
        met, fa, det = [], [], []
        for _ in range(int(self.config["coverage"]["resamples"])):
            w = np.bincount(rng.integers(0, n, n), minlength=n).astype(float)
            idx = select(w)
            if idx is None:
                continue
            m = C_eval.policy(to_eval(idx))
            fa.append(m["false_alerts_per_patient_day"])
            det.append(m["event_detection_rate"])
            met.append(m["false_alerts_per_patient_day"] <= self.B)
        self.cov_rows.append({"setting": setting, "method": method, "budget": self.B, "resamples": len(met),
                              "coverage": float(np.mean(met)), "false_alerts_median": float(np.median(fa)),
                              "detection_median": float(np.median(det)), "target_coverage": 1 - self.delta})

    # -------------------------------------------------------------------------------------------- run
    def run(self):
        self.frames()
        self.rows, self.group_rows, self.oe_rows, self.cov_rows, self.sweep_rows = [], [], [], [], []
        par = self.config["groups"]["parity"]
        mp, me = 100, 20
        # ---------------- setting 1: in distribution (calibrate on validation, evaluate on test)
        rv, rt = self.recalibrate(self.val, [self.val, self.test])
        self.oe("in_distribution", "pooled Platt", self.test)
        self.oe("in_distribution", "group recalibrated", rt)
        Cv, Ct = self.counts(self.val, "validation, locked risks"), self.counts(self.test, "test, locked risks")
        Gv, Gt = self.counts(rv, "validation, group-recalibrated"), self.counts(rt, "test, group-recalibrated")
        j0 = self.j_of(self.ev_test.threshold)
        self.report("in_distribution", "M0_fixed", Ct, j0, {"threshold": self.grid[j0]})
        j1 = ltt_single(Cv, self.B, self.delta)
        self.report("in_distribution", "M1_budget", Ct, j1, {"threshold": self.grid[j1]})
        j2 = ltt_single(Gv, self.B, self.delta)
        self.report("in_distribution", "M2_group_budget", Gt, j2, {"threshold": self.grid[j2]})
        idx3, r3 = ltt_parity(Gv, j2, self.targets, self.par_var, self.B, self.delta, mp, me)
        if idx3 is not None:
            tmap = pd.Series(self.grid[idx3]).groupby(Gv.groups[self.par_var]).first().to_dict()
            self.report("in_distribution", "M3_group_budget_parity", Gt, group_thresholds_for(Gv, idx3, Gt, self.par_var),
                        {"threshold": np.nan, "detection_target": r3, "group_thresholds": str({k: float(v) for k, v in tmap.items()})})
        log("in-distribution methods done; coverage bootstrap")
        self.coverage("in_distribution", "M1_budget", lambda w: ltt_single(Cv, self.B, self.delta, w), Cv, Ct, lambda j: j)
        self.coverage("in_distribution", "M2_group_budget", lambda w: ltt_single(Gv, self.B, self.delta, w), Gv, Gt, lambda j: j)

        def sel3(w):
            jb = ltt_single(Gv, self.B, self.delta, w)
            return None if jb is None else ltt_parity(Gv, jb, self.targets, self.par_var, self.B, self.delta, mp, me, w)[0]

        self.coverage("in_distribution", "M3_group_budget_parity", sel3, Gv, Gt,
                      lambda idx: group_thresholds_for(Gv, idx, Gt, self.par_var))
        for b in self.config["budget"]["sweep"]:
            for name, Cc, Ce in [("M1_budget", Cv, Ct), ("M2_group_budget", Gv, Gt)]:
                j = ltt_single(Cc, b, self.delta)
                if j is not None:
                    self.sweep_rows.append({"setting": "in_distribution", "method": name, "budget": b,
                                            "threshold": self.grid[j], **Ce.policy(j)})
            jb = ltt_single(Gv, b, self.delta)
            if jb is not None:
                idx, r = ltt_parity(Gv, jb, self.targets, self.par_var, b, self.delta, mp, me)
                if idx is not None:
                    self.sweep_rows.append({"setting": "in_distribution", "method": "M3_group_budget_parity", "budget": b,
                                            "detection_target": r, **Gt.policy(group_thresholds_for(Gv, idx, Gt, self.par_var))})
        # ---------------- setting 2: system shift (frozen cross-system models)
        for target, model in self.config["settings"]["system_shift"]["pairs"].items():
            source = "carevue" if target == "metavision" else "metavision"
            fr = self.shift_frames(target, model)
            sv = fr["validation"][fr["validation"]["source_system"] == source]
            tv = fr["validation"][fr["validation"]["source_system"] == target]
            tt = fr["test"][fr["test"]["source_system"] == target]
            rtv, rtt = self.recalibrate(tv, [tv, tt])
            S = f"shift_to_{target}"
            self.oe(S, "source calibration", tt)
            self.oe(S, "local group recalibration", rtt)
            Csv, Ctv, Ctt = self.counts(sv, f"{S}: source validation"), self.counts(tv, f"{S}: target validation"), self.counts(tt, f"{S}: target test")
            Gtv, Gtt = self.counts(rtv, f"{S}: target validation, recalibrated"), self.counts(rtt, f"{S}: target test, recalibrated")
            self.report(S, "M0_fixed", Ctt, j0, {"threshold": self.grid[j0]})
            for name, Cc, Ce in [("M1_naive", Csv, Ctt), ("M1_local", Ctv, Ctt), ("M2_local", Gtv, Gtt)]:
                j = ltt_single(Cc, self.B, self.delta)
                self.report(S, name, Ce, j, {"threshold": self.grid[j]})
                self.coverage(S, name, lambda w, Cc=Cc: ltt_single(Cc, self.B, self.delta, w), Cc, Ce, lambda j: j)
                for b in self.config["budget"]["sweep"]:
                    jj = ltt_single(Cc, b, self.delta)
                    if jj is not None:
                        self.sweep_rows.append({"setting": S, "method": name, "budget": b, "threshold": self.grid[jj], **Ce.policy(jj)})
            log(f"{S} done")
        self.out.mkdir(parents=True, exist_ok=True)
        for name, rows in [("results", self.rows), ("results_by_group", self.group_rows), ("calibration_by_group", self.oe_rows),
                           ("coverage", self.cov_rows), ("budget_sweep", self.sweep_rows)]:
            pd.DataFrame(rows).to_csv(self.out / f"{name}.csv", index=False)
        write_json(self.out / "run_manifest.json", {"version": self.config["version"], "prespec_sha256": self.prespec["prespec_sha256"],
                   "finished_at": datetime.now(timezone.utc).isoformat(), "grid_size": len(self.grid),
                   "outputs": sorted(p.name for p in self.out.iterdir())})
        log(f"reports -> {self.out}")
