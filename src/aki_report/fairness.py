"""Fairness audit of the locked AKI system by insurance and recorded ethnicity (configs/fairness_structured_v1.yaml).

Descriptive and secondary: the locked model and policy v3 are used unchanged; outputs are aggregate tables only.
"""
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from aki_eval.policies import alert_events
from aki_eval.protocol import freeze_prespec, write_json
from aki_final.test_evaluation import LockedEvaluation, PatientBootstrap, alert_summary, per_patient, snapshot_metrics
from aki_robustness.retrain import fast_auroc

ALERT_KEYS = ["event_detection_rate", "false_alerts_per_patient_day", "alert_ppv", "non_aki_patients_alerted",
              "lead_time_median_h"]


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class FairnessAudit:
    def __init__(self, root, config_path=None):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path or self.root / "configs" / "fairness_structured_v1.yaml")
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.out = self.root / self.config["outputs"]
        self.ev = LockedEvaluation(self.root, self.root / self.config["evaluation_config"])
        self.cohort_path = self.ev.paths["data_root"] / "datasets" / "cohort.parquet"
        inputs = {"config": self.config_path, "evaluation_config": self.ev.config_path, "cohort": self.cohort_path,
                  "policy": self.root / self.ev.config["policy"]}
        sources = {p.relative_to(self.root).as_posix(): p for p in
                   [self.root / "src" / "aki_report" / "fairness.py", self.root / "src" / "aki_final" / "test_evaluation.py"]}
        self.prespec = freeze_prespec(self.out, self.config, inputs, sources)
        self.seed = int(self.config["seed"])
        self.n = 500

    def frame(self, splits):
        """Hourly frame with locked risks for the requested splits (validation risks via the same scoring cache)."""
        parts = []
        for s in splits:
            ev = self.ev if s == self.ev.config["split"] else LockedEvaluation(self.root, self.ev.config_path, split=s)
            parts.append(ev.load().assign(eval_split=s))
        f = pd.concat(parts, ignore_index=True)
        c = pd.read_parquet(self.cohort_path, columns=["patient_id"] + list(self.config["variables"]))
        c["patient_id"] = c["patient_id"].astype(str)
        return f.merge(c, on="patient_id", how="left", validate="many_to_one")

    def group_metrics(self, f, pp, members):
        rm = f["patient_id"].isin(members).to_numpy()
        e = rm & f["eligible_24h"].to_numpy(bool)
        y = f["aki_within_24h"].to_numpy(float)[e]
        p = f["risk_24"].to_numpy(float)[e]
        sm = snapshot_metrics(y, p)
        g = pp.loc[pp.index.isin(members)]
        al = alert_summary(g)
        return y.astype(int), p, f["patient_id"].to_numpy()[e], g, sm, al

    def run_split(self, label, splits):
        f = self.frame(splits)
        a = self.ev.alert_frame(f)
        pp = per_patient(a, alert_events(a, self.ev.threshold, self.ev.rule))
        rows, diffs = [], []
        mins = self.config["min_group"]
        for var, spec in self.config["variables"].items():
            pat = f.groupby("patient_id")[var].first()
            cache = {}
            for level in spec["levels"]:
                members = set(pat.index[pat == level])
                y, p, pid, g, sm, al = self.group_metrics(f, pp, members)
                row = {"analysis": label, "variable": var, "level": level, "patients": len(members),
                       "aki_events": int(g["event"].sum())}
                if len(members) < mins["patients"] or row["aki_events"] < mins["aki_events"]:
                    rows.append(row | {"reported": "counts only"})
                    continue
                boot = PatientBootstrap(pid, self.n, self.seed)
                ci = boot.interval(lambda idx: {"auroc": fast_auroc(y[idx], p[idx])})
                rng = np.random.default_rng(self.seed)
                draws = pd.DataFrame([alert_summary(g.iloc[rng.integers(0, len(g), len(g))]) for _ in range(self.n)])
                aq = draws[ALERT_KEYS].quantile([.025, .975])
                rows.append(row | {"reported": "full", "auroc_24h": sm["auroc"], "auroc_ci_low": ci.loc[.025, "auroc"],
                                   "auroc_ci_high": ci.loc[.975, "auroc"], "observed_over_expected": sm["observed_over_expected"],
                                   "calibration_slope": sm["calibration_slope"], "brier": sm["brier"],
                                   **{k: al[k] for k in ALERT_KEYS},
                                   **{f"{k}_ci_low": aq.loc[.025, k] for k in ALERT_KEYS},
                                   **{f"{k}_ci_high": aq.loc[.975, k] for k in ALERT_KEYS}})
                cache[level] = (y, p, boot, g)
                log(f"{label} {var}={level}: {len(members):,} patients")
            ref = spec["reference"]
            if ref not in cache:
                continue
            yr, pr, br, gr = cache[ref]
            rng_r = np.random.default_rng(self.seed + 1)
            ref_auc = np.array([fast_auroc(yr[i], pr[i]) for i in (np.concatenate([br.rows[j] for j in d]) for d in br.draws)])
            ref_det = np.array([alert_summary(gr.iloc[rng_r.integers(0, len(gr), len(gr))])["event_detection_rate"]
                                for _ in range(self.n)])
            for level, (y, p, boot, g) in cache.items():
                if level == ref:
                    continue
                auc = np.array([fast_auroc(y[i], p[i]) for i in (np.concatenate([boot.rows[j] for j in d]) for d in boot.draws)])
                rng_g = np.random.default_rng(self.seed + 2)
                det = np.array([alert_summary(g.iloc[rng_g.integers(0, len(g), len(g))])["event_detection_rate"]
                                for _ in range(self.n)])
                for metric, point, dist in [("auroc_24h", fast_auroc(y, p) - fast_auroc(yr, pr), auc - ref_auc),
                                            ("event_detection_rate", alert_summary(g)["event_detection_rate"] -
                                             alert_summary(gr)["event_detection_rate"], det - ref_det)]:
                    diffs.append({"analysis": label, "variable": var, "level": level, "reference": ref, "metric": metric,
                                  "difference": point, "ci_low": np.quantile(dist, .025), "ci_high": np.quantile(dist, .975)})
        return pd.DataFrame(rows), pd.DataFrame(diffs)

    def run(self):
        sp = self.config["splits"]
        t1, d1 = self.run_split("test", [sp["primary"]])
        t2, d2 = self.run_split("validation+test", sp["secondary"])
        self.out.mkdir(parents=True, exist_ok=True)
        pd.concat([t1, t2]).to_csv(self.out / "fairness_by_group.csv", index=False)
        pd.concat([d1, d2]).to_csv(self.out / "fairness_differences.csv", index=False)
        write_json(self.out / "run_manifest.json", {"version": self.config["version"],
                   "prespec_sha256": self.prespec["prespec_sha256"], "finished_at": datetime.now(timezone.utc).isoformat(),
                   "outputs": sorted(p.name for p in self.out.iterdir())})
        log(f"reports -> {self.out}")
