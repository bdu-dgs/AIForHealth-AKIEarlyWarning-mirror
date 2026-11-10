"""Notebook 03: one locked evaluation of the model and alert policy on a held-out split.

Everything is fixed in configs/evaluation_test_v1.yaml and the locked policy before any outcome of the split is read.
The same code runs on the validation split as a dry run (`split="validation"`, separate output, no prespec record).
Outputs are aggregate tables only.
"""
from datetime import datetime, timezone
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from threadpoolctl import threadpool_limits

from aki_ml import alerts as legacy
from aki_ml.admission_inputs import admission_frame, reconstruct_baselines, write_admission_inputs
from aki_ml.features import STABLE_FEATURE_ENGINE, availability_times, compute_features
from aki_ml.models import logit
from aki_eval.data import dataset_paths
from aki_eval.policies import AlertRule, alert_events, prepare
from aki_eval.protocol import freeze_prespec, sha256, write_json
from aki_eval.replay import ReplayData

H = pd.Timedelta(hours=1)
KEYS = ["patient_id", "hours_since_icu"]
PATIENT_COLUMNS = ["age_years", "gender_male", "first_careunit", "source_system"]


# ------------------------------------------------------------------------------------------------- metrics
def calibration_fit(y, p):
    lr = LogisticRegression(penalty=None, solver="lbfgs", max_iter=1000).fit(logit(p).reshape(-1, 1), y)
    return float(lr.coef_[0, 0]), float(lr.intercept_[0])


def snapshot_metrics(y, p, slope=True):
    y, p = np.asarray(y, int), np.asarray(p, float)
    out = {"rows": len(y), "positives": int(y.sum()), "prevalence": float(y.mean()) if len(y) else np.nan}
    if len(y) == 0 or y.min() == y.max():
        return out | {k: np.nan for k in ["auroc", "auprc", "brier", "calibration_slope", "calibration_intercept",
                                         "observed_over_expected"]}
    out |= {"auroc": roc_auc_score(y, p), "auprc": average_precision_score(y, p), "brier": brier_score_loss(y, p),
            "observed_over_expected": float(y.mean() / p.mean())}
    s, i = calibration_fit(y, p) if slope else (np.nan, np.nan)
    return out | {"calibration_slope": s, "calibration_intercept": i}


def point_warning(y, warn):
    y, warn = np.asarray(y, bool), np.asarray(warn, bool)
    tp, fp, fn, tn = (int((warn & y).sum()), int((warn & ~y).sum()), int((~warn & y).sum()), int((~warn & ~y).sum()))
    sens = tp / (tp + fn) if tp + fn else np.nan
    ppv = tp / (tp + fp) if tp + fp else np.nan
    return {"rows": len(y), "sensitivity": sens, "specificity": tn / (tn + fp) if tn + fp else np.nan, "ppv": ppv,
            "f1": 2 * sens * ppv / (sens + ppv) if sens + ppv else np.nan}


def alert_summary(pp):
    """Original Demo I metric definitions plus repeat burden and time to first alert."""
    out = legacy.summarize(pp)
    alerted = pp["alerts"] > 0
    out["alerts_per_alerted_patient"] = float(pp.loc[alerted, "alerts"].mean()) if alerted.any() else np.nan
    out["hours_to_first_alert_median"] = float(pp.loc[alerted, "first_alert_h"].median()) if alerted.any() else np.nan
    return out


def per_patient(frame, flags, exposure_rows=None):
    pp = legacy.per_patient(frame, flags)
    hours = frame["hours_since_icu"].to_numpy(float)
    first = pd.Series(hours, index=frame.index).groupby(frame["patient_id"].to_numpy(), sort=False).min()
    hit = pd.Series(hours[flags], index=frame.index[flags]).groupby(frame["patient_id"].to_numpy()[flags], sort=False).min()
    pp["first_alert_h"] = (hit - first.reindex(hit.index)).reindex(pp.index)
    if exposure_rows is not None:              # event-driven grid: burden on the hourly monitored exposure
        pp["rows"] = exposure_rows.reindex(pp.index).fillna(0).to_numpy()
    return pp


class PatientBootstrap:
    """Patient-cluster resampling of row-level metrics; draws are fixed by the seed."""

    def __init__(self, patients, n, seed):
        codes, uniques = pd.factorize(np.asarray(patients))
        order = np.argsort(codes, kind="stable")
        bounds = np.r_[0, np.cumsum(np.bincount(codes, minlength=len(uniques)))]
        self.rows = [order[bounds[i]:bounds[i + 1]] for i in range(len(uniques))]
        rng = np.random.default_rng(seed)
        self.draws = [rng.integers(0, len(uniques), len(uniques)) for _ in range(n)]

    def interval(self, function):
        values = []
        for draw in self.draws:
            values.append(function(np.concatenate([self.rows[i] for i in draw])))
        return pd.DataFrame(values).quantile([.025, .975])


def bootstrap_patients(pp, n, seed, summary=alert_summary):
    rng = np.random.default_rng(seed)
    draws = pd.DataFrame([summary(pp.iloc[rng.integers(0, len(pp), len(pp))]) for _ in range(n)])
    return draws.quantile([.025, .975])


def with_ci(point, ci, prefix=""):
    rows = []
    for key, value in point.items():
        rows.append({"metric": prefix + key, "estimate": value,
                     "ci_low": ci.loc[.025, key] if key in ci else np.nan, "ci_high": ci.loc[.975, key] if key in ci else np.nan})
    return rows


# ------------------------------------------------------------------------------------------------- study
class LockedEvaluation:
    def __init__(self, root, config_path=None, split=None):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path or self.root / "configs" / "evaluation_test_v1.yaml")
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.split = split or self.config["split"]
        self.dry_run = self.split != self.config["split"]
        self.paths = dataset_paths(self.root)
        self.data_cfg = yaml.safe_load((self.root / "configs" / "default.yaml").read_text(encoding="utf-8"))
        self.policy = json.loads((self.root / self.config["policy"]).read_text(encoding="utf-8"))
        self.comparator = json.loads((self.root / self.config["comparator_policy"]).read_text(encoding="utf-8"))
        m = self.policy["model"]
        self.model_path = self.root / m["model_dir"] / "pipeline.joblib"
        if sha256(self.model_path) != m["pipeline_sha256"] or not self.policy["threshold"]["locked"]:
            raise RuntimeError("The evaluation needs the locked policy and its exact model")
        if m.get("feature_engine_version") != STABLE_FEATURE_ENGINE:
            raise RuntimeError("The locked policy declares another feature engine")
        ap = self.policy["alert_policy"]
        self.threshold = float(self.policy["threshold"]["value"])
        self.rule = AlertRule("policy-v3", sustain_hours=ap["sustain_hours"], cooldown_hours=ap["cooldown_hours"],
                              repeat_mode=ap["repeat_mode"], confirm_horizon_h=ap["confirm_horizon_h"],
                              confirm_ratio=ap["confirm_ratio"])
        cp = self.comparator["alert_policy"]
        self.comparator_threshold = float(self.comparator["threshold"]["value"])
        self.comparator_rule = AlertRule("demo-v1", sustain_hours=cp["sustain_hours"], cooldown_hours=cp["cooldown_hours"])
        name = self.config["outputs"] if not self.dry_run else f"artifacts/reports/dry_run_{self.split}"
        self.out = self.root / name
        self.private = self.root / "artifacts" / "feature_v2"
        self.pipe = None
        self.frame = None
        if not self.dry_run:
            # Immutable record of protocol, inputs and code, written before any outcome of the split is read.
            inputs = {"config": self.config_path, "policy": self.root / self.config["policy"],
                      "comparator_policy": self.root / self.config["comparator_policy"], "model": self.model_path,
                      "snapshots": self.paths["snapshots"], "events": self.paths["events"],
                      "dataset_config": self.root / "configs" / "default.yaml"}
            sources = {p.relative_to(self.root).as_posix(): p for p in
                       sorted((self.root / "src" / "aki_eval").glob("*.py")) +
                       sorted((self.root / "src" / "aki_final").glob("*.py")) +
                       [self.root / "src" / "aki_ml" / n for n in ["features.py", "feature_windows.py",
                                                                    "admission_inputs.py", "models.py", "alerts.py"]]}
            self.prespec = freeze_prespec(self.out, self.config, inputs, sources)
        self.out.mkdir(parents=True, exist_ok=True)

    def log(self, message):
        print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)

    def model(self):
        if self.pipe is None:
            self.pipe = joblib.load(self.model_path)
        return self.pipe

    # -------------------------------------------------------------------------------------------- data
    def _admission_dir(self, snap):
        directory = self.private / f"admission_inputs_{self.split}"
        if self.split == "validation":
            directory = self.private / "admission_inputs"
        if not (directory / "manifest.json").exists():
            raw = next(p for p in [self.paths["mimic_root"] / "LABEVENTS.csv", self.paths["mimic_root"] / "LABEVENTS.csv.gz"]
                       if p.exists())
            self.log(f"reconstructing float64 admission baselines ({self.split})")
            baselines, _, _, scanned = reconstruct_baselines(raw, snap, self.data_cfg)
            write_admission_inputs(directory, admission_frame(snap, baselines, self.data_cfg),
                                   {"raw_source": raw.name, "raw_rows_scanned": int(scanned), "split": self.split,
                                    "created_at": datetime.now(timezone.utc).isoformat()})
        return directory

    def load(self):
        """Hourly frame: identifiers, labels, subgroup variables and v2-feature risks (Platt and isotonic)."""
        if self.frame is not None:
            return self.frame
        snap = pq.read_table(self.paths["snapshots"], filters=[("split", "=", self.split)]).to_pandas()
        for c in ["patient_id", "stay_id"]:
            snap[c] = snap[c].astype(str)
        snap["hours_since_icu"] = snap["hours_since_icu"].round().astype(int)
        lo, hi = self.config["monitoring_hours"]
        snap = snap[snap["hours_since_icu"].between(lo, hi)].reset_index(drop=True)
        self.snapshots = snap
        cache = self.root / "artifacts" / "predictions" / f"{self.policy['model']['run_id']}_{self.split}_hourly_risks_v2.parquet"
        if cache.exists():
            risks = pd.read_parquet(cache)
        else:
            risks = self._score(snap, cache)
        features = [c for c in self.model().feature_names if c not in PATIENT_COLUMNS + KEYS]
        frame = snap.drop(columns=features, errors="ignore").merge(
            risks, on=KEYS, how="left", validate="one_to_one")
        if frame[[f"risk_{h}" for h in self.config["horizons"]]].isna().any().any():
            raise RuntimeError("Missing risks for some origins")
        self.frame = frame.sort_values(KEYS).reset_index(drop=True)
        self.log(f"{self.split}: {frame['patient_id'].nunique():,} patients, {len(frame):,} hourly origins")
        return self.frame

    def _score(self, snap, cache):
        pipe, names = self.model(), self.model().feature_names
        admission = self._admission_dir(snap)
        patients = np.sort(snap["patient_id"].unique())
        parts = []
        with threadpool_limits(limits=4):
            for start in range(0, len(patients), 1000):
                pool = snap[snap["patient_id"].isin(patients[start:start + 1000])].reset_index(drop=True)
                ctx = ReplayData.load(self.paths["events"], pool, self.data_cfg, names,
                                      engine_version=STABLE_FEATURE_ENGINE, admission_inputs=admission)
                X, covered = ctx.matrix(pool)
                if not covered.all():
                    raise RuntimeError("A stored origin fails the coverage rule")
                out = pool[KEYS].copy()
                for h in self.config["horizons"]:
                    raw = pipe.raw_score(X, h)
                    out[f"risk_{h}"] = pipe.calibrators[h]["platt"].predict(raw)
                    out[f"isotonic_{h}"] = pipe.calibrators[h]["isotonic"].predict(raw)
                parts.append(out)
                self.log(f"scored {min(start + 1000, len(patients)):,}/{len(patients):,} patients")
        risks = pd.concat(parts, ignore_index=True)
        pq.write_table(pa.Table.from_pandas(risks, preserve_index=False), cache, compression="zstd")
        return risks

    # -------------------------------------------------------------------------------------------- pieces
    def alert_frame(self, frame, onset="creatinine_aki_onset_time", label=None, eligible=None):
        h = self.policy["alert_horizon_h"]
        c = self.rule.confirm_horizon_h
        f = frame[["patient_id", "hours_since_icu", "snapshot_time"]].copy()
        f["creatinine_aki_onset_time"] = frame[onset].to_numpy()
        f["eligible"] = frame[eligible or f"eligible_{h}h"].to_numpy(bool)
        f["label"] = frame[label or f"aki_within_{h}h"].to_numpy(float)
        f["risk"] = frame[f"risk_{h}"].to_numpy(float)
        f[f"risk_{c}h"] = frame[f"risk_{c}"].to_numpy(float)
        return prepare(f, h)

    def warning(self, frame, which="policy"):
        h, c = self.policy["alert_horizon_h"], self.rule.confirm_horizon_h
        if which == "policy":
            return (frame[f"risk_{h}"].to_numpy() >= self.threshold) & (
                frame[f"risk_{c}"].to_numpy() >= self.rule.confirm_ratio * self.threshold)
        return frame[f"risk_{h}"].to_numpy() >= self.comparator_threshold

    def alerts(self, frame, n, seed, **kwargs):
        a = self.alert_frame(frame, **kwargs)
        rows = []
        pps = {}
        for name, rule, thr in [("policy_v3", self.rule, self.threshold),
                                ("demo_v1", self.comparator_rule, self.comparator_threshold)]:
            pp = per_patient(a, alert_events(a, thr, rule))
            pps[name] = pp
            rows += [r | {"policy": name} for r in with_ci(alert_summary(pp), bootstrap_patients(pp, n, seed))]
        diff = lambda p: {k: v - w for (k, v), w in zip(alert_summary(p[0]).items(), alert_summary(p[1]).values())}
        rng = np.random.default_rng(seed)
        draws = []
        for _ in range(n):
            idx = rng.integers(0, len(pps["policy_v3"]), len(pps["policy_v3"]))
            draws.append(diff((pps["policy_v3"].iloc[idx], pps["demo_v1"].iloc[idx])))
        ci = pd.DataFrame(draws).quantile([.025, .975])
        rows += [r | {"policy": "difference_v3_minus_demo_v1"} for r in with_ci(diff((pps["policy_v3"], pps["demo_v1"])), ci)]
        return pd.DataFrame(rows)

    # -------------------------------------------------------------------------------------------- analyses
    def snapshot_table(self):
        f, cfg = self.load(), self.config
        n, seed = cfg["bootstrap"]["overall_resamples"], cfg["seed"]
        boot = PatientBootstrap(f["patient_id"], n, seed)
        rows = []
        for h in cfg["horizons"]:
            e = f[f"eligible_{h}h"].to_numpy(bool)
            y = f[f"aki_within_{h}h"].to_numpy(float)
            for cal, column in [("platt", f"risk_{h}"), ("isotonic", f"isotonic_{h}")]:
                p = f[column].to_numpy(float)
                point = snapshot_metrics(y[e], p[e])
                row = {"horizon_h": h, "scope": "hours 1-72", "calibration": cal, **point}
                if cal == "platt":
                    self.log(f"bootstrap {h}h ({n} draws)")
                    def metric(idx):
                        k = idx[e[idx]]
                        return {kk: v for kk, v in snapshot_metrics(y[k], p[k]).items()
                                if kk in ["auroc", "auprc", "brier", "calibration_slope", "observed_over_expected"]}
                    ci = boot.interval(metric)
                    row |= {f"{k}_ci_low": ci.loc[.025, k] for k in ci} | {f"{k}_ci_high": ci.loc[.975, k] for k in ci}
                rows.append(row)
            for lo, hi in cfg["segments"]:
                m = e & f["hours_since_icu"].between(lo, hi).to_numpy()
                rows.append({"horizon_h": h, "scope": f"{lo}-{hi}h", "calibration": "platt",
                             **snapshot_metrics(y[m], f[f"risk_{h}"].to_numpy(float)[m])})
        table = pd.DataFrame(rows)
        table.to_csv(self.out / "snapshot_metrics.csv", index=False)
        h = self.policy["alert_horizon_h"]
        e = f[f"eligible_{h}h"].to_numpy(bool)
        y = f[f"aki_within_{h}h"].to_numpy(float)[e]
        warn = pd.DataFrame([{"policy": name, **point_warning(y, self.warning(f, which)[e])}
                             for name, which in [("policy_v3", "policy"), ("demo_v1", "comparator")]])
        warn.to_csv(self.out / "point_warning_24h.csv", index=False)
        return table, warn

    def alert_table(self):
        table = self.alerts(self.load(), self.config["bootstrap"]["overall_resamples"], self.config["seed"])
        table.to_csv(self.out / "alert_metrics.csv", index=False)
        return table

    def subgroup_table(self):
        f, cfg = self.load(), self.config
        n, seed, h = cfg["bootstrap"]["subgroup_resamples"], cfg["seed"], self.policy["alert_horizon_h"]
        groups = []
        spec = cfg["subgroups"]
        age = pd.cut(f["age_years"], bins=spec["age_years"]["bins"], labels=spec["age_years"]["labels"], right=False)
        groups += [("age_years", level, (age == level).to_numpy()) for level in spec["age_years"]["labels"]]
        groups += [("sex", name, (f["gender_male"] == int(v)).to_numpy()) for v, name in spec["gender_male"]["levels"].items()]
        groups += [("first_careunit", u, (f["first_careunit"] == u).to_numpy()) for u in spec["first_careunit"]["levels"]]
        groups += [("source_system", s, (f["source_system"] == s).to_numpy()) for s in spec["source_system"]["levels"]]
        rows, alert_rows = [], []
        for variable, level, mask in groups:
            sub = f[mask].reset_index(drop=True)
            row = {"variable": variable, "level": level, "patients": int(sub["patient_id"].nunique())}
            for hh in cfg["horizons"]:
                e = sub[f"eligible_{hh}h"].to_numpy(bool)
                m = snapshot_metrics(sub[f"aki_within_{hh}h"].to_numpy(float)[e], sub[f"risk_{hh}"].to_numpy(float)[e],
                                     slope=hh == h)
                row |= {f"auroc_{hh}h": m["auroc"], f"positives_{hh}h": m["positives"]}
                if hh == h:
                    row |= {"calibration_slope_24h": m["calibration_slope"], "observed_over_expected_24h": m["observed_over_expected"]}
                    y, p = sub[f"aki_within_{hh}h"].to_numpy(float), sub[f"risk_{hh}"].to_numpy(float)
                    boot = PatientBootstrap(sub["patient_id"], n, seed)
                    ci = boot.interval(lambda idx: {"auroc": snapshot_metrics(y[idx[e[idx]]], p[idx[e[idx]]], slope=False)["auroc"]})
                    row |= {"auroc_24h_ci_low": ci.loc[.025, "auroc"], "auroc_24h_ci_high": ci.loc[.975, "auroc"]}
            rows.append(row)
            a = self.alert_frame(sub)
            pp = per_patient(a, alert_events(a, self.threshold, self.rule))
            alert_rows += [r | {"variable": variable, "level": level} for r in with_ci(alert_summary(pp), bootstrap_patients(pp, n, seed))]
            self.log(f"subgroup {variable}={level}")
        snapshot, alerts = pd.DataFrame(rows), pd.DataFrame(alert_rows)
        snapshot.to_csv(self.out / "subgroups_snapshot.csv", index=False)
        alerts.to_csv(self.out / "subgroups_alerts.csv", index=False)
        return snapshot, alerts

    def _relabel(self, f, onset):
        """Labels/eligibility from an alternative onset; origins at or after that onset leave the risk set."""
        t = f["snapshot_time"]
        o = f[onset]
        keep = ~(o.notna() & (o <= t))
        g = f[keep].copy()
        for h in self.config["horizons"]:
            pos = g[onset].notna() & (g[onset] > g["snapshot_time"]) & (g[onset] <= g["snapshot_time"] + h * H)
            g[f"aki_within_{h}h"] = pos.astype(float)
            g[f"eligible_{h}h"] = pos | ((g["censoring_time"] - g["snapshot_time"]) >= h * H)
        return g

    def robustness_table(self):
        f, cfg = self.load(), self.config
        h = self.policy["alert_horizon_h"]
        # The relabelling rule must reproduce the primary labels before it is applied to alternative onsets.
        check = self._relabel(f, "creatinine_aki_onset_time")
        mismatch = {hh: int(((check[f"eligible_{hh}h"] != f[f"eligible_{hh}h"]) |
                             (check[f"eligible_{hh}h"] & (check[f"aki_within_{hh}h"] != f[f"aki_within_{hh}h"]))).sum())
                    for hh in cfg["horizons"]}
        rows, alert_rows = [], []

        def add(name, frame, label, eligible, onset):
            for hh in cfg["horizons"]:
                e = frame[eligible.format(hh)].fillna(False).to_numpy(bool)
                for cal, column in [("platt", f"risk_{hh}"), ("isotonic", f"isotonic_{hh}")]:
                    rows.append({"analysis": name, "horizon_h": hh, "calibration": cal,
                                 **snapshot_metrics(frame[label.format(hh)].to_numpy(float)[e], frame[column].to_numpy(float)[e])})
            a = self.alert_frame(frame, onset=onset, label=label.format(h), eligible=eligible.format(h))
            alert_rows.append({"analysis": name, **alert_summary(per_patient(a, alert_events(a, self.threshold, self.rule)))})

        add("primary (creatinine KDIGO)", f, "aki_within_{}h", "eligible_{}h", "creatinine_aki_onset_time")
        uo = cfg["robustness"]["urine_output_endpoint"]
        u = f[~(f[uo["onset"]].notna() & (f[uo["onset"]] <= f["snapshot_time"]))].copy()
        for hh in cfg["horizons"]:
            u[f"eligible_uo_{hh}h"] = u[f"eligible_uo_{hh}h"].fillna(False).astype(bool)
        add("urine-output KDIGO endpoint", u, uo["labels"] + "{}h", uo["eligible"] + "{}h", uo["onset"])
        for onset in cfg["robustness"]["baseline_definitions"]:
            add(f"baseline: {onset.replace('creatinine_aki_onset_time_alt_', '')}", self._relabel(f, onset),
                "aki_within_{}h", "eligible_{}h", onset)
        snapshot, alerts = pd.DataFrame(rows), pd.DataFrame(alert_rows)
        snapshot.to_csv(self.out / "robustness_snapshot.csv", index=False)
        alerts.to_csv(self.out / "robustness_alerts.csv", index=False)
        write_json(self.out / "relabel_check.json", {"primary_label_mismatches_by_horizon": mismatch})
        return snapshot, alerts, mismatch

    def event_replay_table(self, chunk=250):
        f = self.load()
        snap, pipe, names = self.snapshots, self.model(), self.model().feature_names
        admission = self._admission_dir(snap)
        horizons = self.config["event_replay"]["horizons"]
        lo, hi = self.config["monitoring_hours"]
        first = f.groupby("patient_id", sort=False).first()
        stay_rows = []
        patients = np.sort(f["patient_id"].unique())
        with threadpool_limits(limits=4):
            for start in range(0, len(patients), chunk):
                pool = snap[snap["patient_id"].isin(patients[start:start + chunk])].reset_index(drop=True)
                ctx = ReplayData.load(self.paths["events"], pool, self.data_cfg, names,
                                      engine_version=STABLE_FEATURE_ENGINE, admission_inputs=admission)
                stays = pd.Series(ctx.stay_lookup)
                info = first.reset_index().set_index("stay_id").reindex(stays.index)
                intime, onset, censor = (pd.to_datetime(info["icu_intime"]), info["creatinine_aki_onset_time"], info["censoring_time"])
                onset_h = ((onset - intime) / H).to_numpy(float)
                censor_h = ((censor - intime) / H).to_numpy(float)
                avail = availability_times(ctx.events, self.data_cfg).drop_duplicates()
                s, t = avail["stay_idx"].to_numpy(int), avail["t"].to_numpy(float)
                last_hour = pool.groupby("stay_id")["hours_since_icu"].max().reindex(stays.index).to_numpy(float)
                ok = (t >= lo) & (t <= np.minimum(hi, last_hour[s] + 1)) & ~(t >= onset_h[s]) & (t < censor_h[s])
                q = pd.DataFrame({"stay_idx": s[ok], "t": t[ok]}).sort_values(["stay_idx", "t"]).reset_index(drop=True)
                qs, qt = q["stay_idx"].to_numpy(int), q["t"].to_numpy(float)
                covered = ctx._covered(ctx.events, qs, qt)
                qs, qt = qs[covered], qt[covered]
                # Surgical service: the latest hourly snapshot value at or before the event time.
                svc = np.zeros(len(qs))
                for stay, idx in pd.Series(np.arange(len(qs))).groupby(qs).groups.items():
                    hist = pool[pool["stay_id"] == stays.index[stay]].sort_values("hours_since_icu")
                    pos = np.searchsorted(hist["hours_since_icu"].to_numpy(float), qt[idx], side="right") - 1
                    svc[idx] = np.where(pos >= 0, hist["surgical_service"].to_numpy(float)[np.maximum(pos, 0)], 0.0)
                X = compute_features(ctx.events, ctx.static, svc, qs, qt, self.data_cfg, names, engine_version=STABLE_FEATURE_ENGINE)
                out = pd.DataFrame({"stay_id": stays.index[qs], "patient_id": info["patient_id"].to_numpy()[qs], "t": qt,
                                    "onset_h": onset_h[qs], "censor_h": censor_h[qs]})
                for h in horizons:
                    out[f"risk_{h}"] = pipe.predict(X, h, "platt")
                stay_rows.append(out)
                self.log(f"event replay {min(start + chunk, len(patients)):,}/{len(patients):,} patients ({len(out):,} event times)")
        ev = pd.concat(stay_rows, ignore_index=True)
        rows = []
        for h in horizons:
            pos = (ev["onset_h"] > ev["t"]) & (ev["onset_h"] <= ev["t"] + h)
            elig = pos | (ev["censor_h"] - ev["t"] >= h)
            hourly = f[f[f"eligible_{h}h"]]
            rows.append({"horizon_h": h, "grid": "event-driven", **snapshot_metrics(pos[elig].astype(int), ev.loc[elig, f"risk_{h}"], slope=False)})
            rows.append({"horizon_h": h, "grid": "hourly", **snapshot_metrics(hourly[f"aki_within_{h}h"], hourly[f"risk_{h}"], slope=False)})
        ah, c = self.policy["alert_horizon_h"], self.rule.confirm_horizon_h
        a = pd.DataFrame({"patient_id": ev["patient_id"], "hours_since_icu": ev["t"],
                          "snapshot_time": pd.to_datetime(first.loc[ev["patient_id"], "icu_intime"]).to_numpy() + pd.to_timedelta(ev["t"], unit="h"),
                          "creatinine_aki_onset_time": first.loc[ev["patient_id"], "creatinine_aki_onset_time"].to_numpy(),
                          "eligible": ((ev["onset_h"] > ev["t"]) & (ev["onset_h"] <= ev["t"] + ah)) | (ev["censor_h"] - ev["t"] >= ah),
                          "label": ((ev["onset_h"] > ev["t"]) & (ev["onset_h"] <= ev["t"] + ah)).astype(float),
                          "risk": ev[f"risk_{ah}"], f"risk_{c}h": ev[f"risk_{c}"]})
        a = prepare(a, ah)
        exposure = f.groupby("patient_id").size()
        alerts = []
        for grid, frame, rows_ in [("event-driven", a, exposure), ("hourly", self.alert_frame(f), None)]:
            pp = per_patient(frame, alert_events(frame, self.threshold, self.rule), rows_)
            alerts.append({"grid": grid, **alert_summary(pp)})
        updates = ev.groupby("patient_id").size()
        delta = ev.groupby("patient_id")[f"risk_{ah}"].diff().abs().dropna()
        meta = {"event_times": len(ev), "updates_per_patient_median": float(updates.median()),
                "abs_risk_change_between_updates_median": float(delta.median()),
                "abs_risk_change_between_updates_p95": float(delta.quantile(.95))}
        disc, alerts = pd.DataFrame(rows), pd.DataFrame(alerts)
        disc.to_csv(self.out / "event_replay_discrimination.csv", index=False)
        alerts.to_csv(self.out / "event_replay_alerts.csv", index=False)
        write_json(self.out / "event_replay_summary.json", meta)
        return disc, alerts, meta

    def shap_table(self):
        f, cfg = self.load(), self.config
        pipe, names = self.model(), self.model().feature_names
        rng = np.random.default_rng(cfg["seed"])
        ids = np.sort(f["patient_id"].unique())
        chosen = rng.choice(ids, min(cfg["shap"]["patients"], len(ids)), replace=False)
        pool = self.snapshots[self.snapshots["patient_id"].isin(chosen)].reset_index(drop=True)
        ctx = ReplayData.load(self.paths["events"], pool, self.data_cfg, names, engine_version=STABLE_FEATURE_ENGINE,
                              admission_inputs=self._admission_dir(self.snapshots))
        X, _ = ctx.matrix(pool)
        rows = []
        for h in cfg["horizons"]:
            raw = pipe.raw_score(X, h)
            contrib = pipe.heads[h].predict(X, num_iteration=pipe.n_rounds[h], pred_contrib=True, num_threads=4)
            if not np.allclose(contrib.sum(axis=1), logit(raw), atol=1e-3):
                raise RuntimeError("TreeSHAP does not add up to the score")
            contrib = contrib[:, :-1] * pipe.calibrators[h]["platt"].slope_
            mean_abs = np.abs(contrib).mean(axis=0)
            order = np.argsort(-mean_abs)[:cfg["shap"]["top"]]
            rows += [{"horizon_h": h, "rank": r + 1, "feature": names[j], "mean_abs_shap": float(mean_abs[j]),
                      "share_of_total": float(mean_abs[j] / mean_abs.sum())} for r, j in enumerate(order)]
        table = pd.DataFrame(rows)
        table.to_csv(self.out / "shap_top20.csv", index=False)
        return table

    def run_manifest(self):
        write_json(self.out / "run_manifest.json", {"version": self.config["version"], "split": self.split,
                   "dry_run": self.dry_run, "finished_at": datetime.now(timezone.utc).isoformat(),
                   "patients": int(self.load()["patient_id"].nunique()), "origins": len(self.load()),
                   "policy": self.config["policy"], "model_pipeline_sha256": self.policy["model"]["pipeline_sha256"],
                   "prespec_sha256": None if self.dry_run else self.prespec["prespec_sha256"]})
