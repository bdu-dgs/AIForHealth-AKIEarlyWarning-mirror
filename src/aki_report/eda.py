"""Exploratory data analysis for the final report (plan items 7-8): aggregate tables only.

Cohort description uses all modelling patients by split. Every distributional analysis of features, labels and
measurement behaviour uses the TRAINING split only, so nothing about the test split is explored here.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from aki_eval.data import dataset_paths

HORIZONS = [6, 12, 24, 48]
KEY_CONCEPTS = ["creatinine", "bun", "potassium", "bicarbonate", "lactate", "ph", "ptt", "hemoglobin",
                "heart_rate", "sbp", "spo2", "temp_c", "urine"]


def iqr(s):
    s = pd.Series(s).dropna()
    return f"{s.median():.1f} [{s.quantile(.25):.1f}-{s.quantile(.75):.1f}]" if len(s) else ""


class EDA:
    def __init__(self, root, out=None):
        self.root = Path(root).resolve()
        self.paths = dataset_paths(self.root)
        data = self.paths["data_root"]
        self.cohort_path = data / "datasets" / "cohort.parquet"
        self.dict = pd.read_csv(data / "datasets" / "feature_dictionary.csv")
        self.flow_path = data / "reports" / "cohort_flow.csv"
        self.out = Path(out or self.root / "artifacts" / "reports" / "eda_v1")
        self.out.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------------------------- cohort
    def table1(self):
        c = pd.read_parquet(self.cohort_path)
        c = c[c["in_modeling_table"]]
        rows = []
        for split in ["train", "validation", "test", "all"]:
            d = c if split == "all" else c[c["split"].astype(str) == split]
            n = len(d)
            pct = lambda m: f"{100 * m.sum() / n:.1f}"
            r = {"split": split, "patients": n,
                 "age_years_median_iqr": iqr(d["age_years"]),
                 "male_pct": pct(d["gender"] == "M"),
                 "emergency_admission_pct": pct(d["admission_type"] == "EMERGENCY"),
                 "icu_los_hours_median_iqr": iqr(d["los_hours"]),
                 "icu_death_pct": pct(d["icu_death"].astype(bool)),
                 "carevue_pct": pct(d["source_system"] == "carevue"),
                 "metavision_pct": pct(d["source_system"] == "metavision"),
                 "creatinine_aki_pct": pct(d["cr_aki_onset_hours"].notna()),
                 "aki_stage1_pct": pct(d["cr_aki_max_stage"] == 1),
                 "aki_stage2_pct": pct(d["cr_aki_max_stage"] == 2),
                 "aki_stage3_pct": pct(d["cr_aki_max_stage"] == 3),
                 "aki_onset_hours_median_iqr": iqr(d["cr_aki_onset_hours"]),
                 "rrt_pct": pct(d["rrt_start_hours"].notna()),
                 "urine_output_aki_pct": pct(d["uo_aki_onset_hours"].notna()),
                 "pre_icu_baseline_creatinine_pct": pct(d["baseline_cr_source"].astype(str).str.contains("pre_icu")),
                 "hourly_origins": int(d["n_snapshot_rows"].sum()),
                 "origins_per_patient_median_iqr": iqr(d["n_snapshot_rows"])}
            for unit in ["MICU", "SICU", "CSRU", "CCU", "TSICU"]:
                r[f"unit_{unit}_pct"] = pct(d["first_careunit"] == unit)
            rows.append(r)
        t = pd.DataFrame(rows)
        t.to_csv(self.out / "table1_by_split.csv", index=False)
        pd.read_csv(self.flow_path).to_csv(self.out / "cohort_flow.csv", index=False)
        sources = c["baseline_cr_source"].astype(str).value_counts().rename_axis("baseline_cr_source").reset_index(name="patients")
        sources.to_csv(self.out / "baseline_sources.csv", index=False)
        return t

    def onset_timing(self):
        c = pd.read_parquet(self.cohort_path, columns=["split", "in_modeling_table", "source_system", "cr_aki_onset_hours",
                                                       "cr_aki_onset_stage"])
        # 'both' (36 training patients) is left out: its cells would be too small to publish.
        c = c[c["in_modeling_table"] & (c["split"].astype(str) == "train") & c["cr_aki_onset_hours"].notna()
              & c["source_system"].isin(["carevue", "metavision"])]
        bins = [0, 6, 12, 24, 36, 48, 72, 96, 168, np.inf]
        labels = ["1-6", "6-12", "12-24", "24-36", "36-48", "48-72", "72-96", "96-168", ">168"]
        c["bin"] = pd.cut(c["cr_aki_onset_hours"], bins, labels=labels)
        t = c.groupby(["source_system", "bin"], observed=False).size().rename("patients").reset_index()
        t.to_csv(self.out / "aki_onset_timing_train.csv", index=False)
        return t

    # -------------------------------------------------------------------------------------------- snapshots (train)
    def _train_snapshots(self, columns):
        t = pq.read_table(self.paths["snapshots"], columns=columns, filters=[("split", "=", "train")])
        return t.to_pandas()

    def prevalence_by_hour(self):
        cols = ["patient_id", "hours_since_icu", "source_system"] + [f"{p}_{h}h" for h in HORIZONS for p in ("eligible", "aki_within")]
        s = self._train_snapshots(cols)
        s["hour"] = s["hours_since_icu"].round().astype(int)
        rows = []
        for h in HORIZONS:
            g = s.groupby("hour")
            e = s[f"eligible_{h}h"].astype(bool)
            y = s[f"aki_within_{h}h"].astype("Float64")
            d = pd.DataFrame({"hour": s["hour"], "e": e, "y": y.where(e)}).groupby("hour")
            t = pd.DataFrame({"origins": g.size(), "eligible": d["e"].sum(), "positives": d["y"].sum()})
            t["prevalence"] = t["positives"] / t["eligible"]
            t["ineligible_fraction"] = 1 - t["eligible"] / t["origins"]
            t["horizon_h"] = h
            rows.append(t.reset_index())
        t = pd.concat(rows, ignore_index=True)
        t.to_csv(self.out / "prevalence_by_hour_train.csv", index=False)
        return t

    def measurement_process(self):
        """How often key variables are measured, by charting system and by upcoming-AKI status (24 h)."""
        d = self.dict.set_index("feature")
        last = [f"{c}_last" for c in KEY_CONCEPTS if f"{c}_last" in d.index]
        since = [f"{c}_hours_since_last" for c in KEY_CONCEPTS if f"{c}_hours_since_last" in d.index]
        counts = [f for f in d.index if f.endswith("_count_48h") or f.endswith("_count_6h") or f.endswith("_count_24h")]
        counts = [f for f in counts if any(f.startswith(c + "_") for c in KEY_CONCEPTS)]
        cols = ["patient_id", "hours_since_icu", "source_system", "eligible_24h", "aki_within_24h"] + last + since + counts
        cols = list(dict.fromkeys(cols))
        s = self._train_snapshots(cols)
        rows = []
        groups = {"system=carevue": s["source_system"] == "carevue", "system=metavision": s["source_system"] == "metavision"}
        e = s["eligible_24h"].astype(bool)
        y = s["aki_within_24h"].astype("Float64").fillna(0).astype(bool)
        groups |= {"aki_within_24h=yes": e & y, "aki_within_24h=no": e & ~y}
        for concept in KEY_CONCEPTS:
            sc = f"{concept}_hours_since_last"
            lc = f"{concept}_last"
            cc = next((f for f in counts if f.startswith(concept + "_")), None)
            for name, m in groups.items():
                sub = s.loc[m]
                r = {"concept": concept, "group": name, "origins": int(m.sum())}
                if sc in s:
                    hs = sub[sc]
                    r |= {"never_measured_fraction": float(hs.isna().mean()), "hours_since_last_median": float(hs.median()),
                          "hours_since_last_p75": float(hs.quantile(.75))}
                elif lc in s:
                    r |= {"never_measured_fraction": float(sub[lc].isna().mean())}
                if cc:
                    r |= {"count_feature": cc, "count_mean": float(sub[cc].mean())}
                rows.append(r)
        t = pd.DataFrame(rows)
        t.to_csv(self.out / "measurement_process_train.csv", index=False)
        return t

    def missingness_by_group(self):
        d = self.dict[self.dict["role"] == "feature"].copy()
        feats = d["feature"].tolist()
        system = self._train_snapshots(["source_system"])["source_system"].astype(str).to_numpy()
        miss = {k: {} for k in ["carevue", "metavision", "all"]}
        for start in range(0, len(feats), 40):           # column batches keep memory low
            block = self._train_snapshots(feats[start:start + 40])
            na = block.isna()
            for k in miss:
                m = np.ones(len(na), bool) if k == "all" else system == k
                miss[k].update(na[m].mean().to_dict())
            del block, na
        d["missing_carevue"] = d["feature"].map(miss["carevue"])
        d["missing_metavision"] = d["feature"].map(miss["metavision"])
        d["missing_train"] = d["feature"].map(miss["all"])
        g = d.groupby("group").agg(features=("feature", "size"), missing_median=("missing_train", "median"),
                                   missing_mean=("missing_train", "mean"), missing_carevue_mean=("missing_carevue", "mean"),
                                   missing_metavision_mean=("missing_metavision", "mean")).reset_index()
        g.to_csv(self.out / "missingness_by_group_train.csv", index=False)
        d[["feature", "group", "statistic", "missing_train", "missing_carevue", "missing_metavision"]].to_csv(
            self.out / "missingness_by_feature_train.csv", index=False)
        return g

    def creatinine_trajectory(self):
        """Median creatinine around AKI onset (AKI patients) and over ICU time (patients without AKI), training split."""
        cols = ["patient_id", "hours_since_icu", "snapshot_time", "creatinine_aki_onset_time", "creatinine_last",
                "creatinine_ratio_to_7d_min", "creatinine_rise_from_48h_min"]
        s = self._train_snapshots(cols)
        to_onset = (s["creatinine_aki_onset_time"] - s["snapshot_time"]) / pd.Timedelta(hours=1)
        aki = s["creatinine_aki_onset_time"].notna()
        rows = []
        a = s[aki].assign(t=-np.ceil(to_onset[aki]))
        a = a[a["t"].between(-48, -1)]
        for t, d in a.groupby("t"):
            rows.append({"group": "AKI (hours before onset)", "t": int(t), "patients": d["patient_id"].nunique(),
                         **{f"{v}_{q}": float(d[v].quantile(p)) for v in ["creatinine_last", "creatinine_ratio_to_7d_min"]
                            for q, p in [("p25", .25), ("median", .5), ("p75", .75)]}})
        n = s[~aki].assign(t=s.loc[~aki, "hours_since_icu"].round())
        n = n[n["t"].between(1, 72)]
        for t, d in n.groupby("t"):
            rows.append({"group": "no AKI (hours since ICU admission)", "t": int(t), "patients": d["patient_id"].nunique(),
                         **{f"{v}_{q}": float(d[v].quantile(p)) for v in ["creatinine_last", "creatinine_ratio_to_7d_min"]
                            for q, p in [("p25", .25), ("median", .5), ("p75", .75)]}})
        t = pd.DataFrame(rows)
        t.to_csv(self.out / "creatinine_trajectory_train.csv", index=False)
        return t

    def run(self):
        for step in [self.table1, self.onset_timing, self.prevalence_by_hour, self.measurement_process,
                     self.missingness_by_group, self.creatinine_trajectory]:
            print(f"EDA: {step.__name__}", flush=True)
            step()
        print(f"EDA -> {self.out}", flush=True)
