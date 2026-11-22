"""Figures for the final report and slides (plan items 7-15), drawn only from aggregate report tables.

Inputs: artifacts/reports/{eda_v1, test_v1, final_analyses_v1, robustness_retrain_v1, system_shift_v1} and the synthetic
static-demo recording. Output: artifacts/reports/figures/<name>.pdf (vector, for LaTeX) and .png (300 dpi, slides).
A figure whose inputs do not exist yet is skipped with a message.
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# Reference palette (dataviz skill, light mode): categorical slots in fixed order, a blue ordinal ramp, neutral inks.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
BLUE = {"300": "#6da7ec", "400": "#3987e5", "500": "#256abf", "650": "#104281"}
HORIZON_COLOR = {6: BLUE["300"], 12: BLUE["400"], 24: BLUE["500"], 48: BLUE["650"]}
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e5e4e0", "#fcfcfb"
REFERENCE_GRAY = "#8a8984"

MODEL_STYLE = {   # entity -> fixed colour (never by rank); the creatinine-rise score is a grey reference
    "primary": (SERIES[0], "LightGBM (locked system)"),
    "xgboost__full": (SERIES[1], "XGBoost"),
    "lr__full": (SERIES[2], "Logistic regression (all features)"),
    "creatinine_lr": (SERIES[3], "Creatinine-only logistic regression"),
    "creatinine_rise": (REFERENCE_GRAY, "Creatinine rise score"),
}

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8, "legend.fontsize": 7,
    "xtick.labelsize": 7, "ytick.labelsize": 7, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": INK2,
    "ytick.color": INK2, "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "axes.facecolor": SURFACE, "figure.facecolor": "white",
    "lines.linewidth": 1.6, "legend.frameon": False, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "pdf.fonttype": 42,
})
WIDTH = 5.5   # NeurIPS text width in inches


class Figures:
    def __init__(self, root):
        self.root = Path(root).resolve()
        r = self.root / "artifacts" / "reports"
        self.dirs = {"eda": r / "eda_v1", "test": r / "test_v1", "final": r / "final_analyses_v1",
                     "retrain": r / "robustness_retrain_v1", "shift": r / "system_shift_v1",
                     "fair": r / "fairness_structured_v1", "recal": r / "system_recalibration_v1"}
        self.demo = self.root / "dashboard" / "web" / "public" / "static-demo" / "demo.json"
        self.out = r / "figures"
        self.out.mkdir(parents=True, exist_ok=True)
        self.made, self.skipped = [], []

    def csv(self, key, name):
        return pd.read_csv(self.dirs[key] / name)

    def save(self, fig, name):
        fig.savefig(self.out / f"{name}.pdf", bbox_inches="tight")
        fig.savefig(self.out / f"{name}.png", dpi=300, bbox_inches="tight")
        plt.close(fig)
        self.made.append(name)

    # -------------------------------------------------------------------------------------------- 15 pipeline
    def pipeline(self):
        fig, ax = plt.subplots(figsize=(WIDTH, 1.55))
        ax.set_axis_off()
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 22)
        boxes = [
            ("MIMIC-III v1.4", "32,907 patients", "split by patient"),
            ("As-of features", "246 inputs/hour", "1 h lab delay"),
            ("LightGBM", "4 horizon heads", "grouped CV"),
            ("Platt scaling", "training OOF", "calibrated risk"),
            ("Alert policy v3", "24 h >= 21% and", "12 h >= 10.5%"),
        ]
        for i, (title, line1, line2) in enumerate(boxes):
            x = 1 + i * 20
            ec, fc = (SERIES[1], "#fdebe3") if i == 4 else (SERIES[0], "#e8f1fc")
            ax.add_patch(FancyBboxPatch((x, 2), 16.5, 18, boxstyle="round,pad=0.3,rounding_size=1.5", fc=fc, ec=ec, lw=1))
            ax.text(x + 8.25, 15.5, title, ha="center", va="center", fontsize=6.0, weight="bold", color=INK)
            ax.text(x + 8.25, 10, line1, ha="center", va="center", fontsize=5.4, color=INK)
            ax.text(x + 8.25, 6, line2, ha="center", va="center", fontsize=5.4, color=INK2)
            if i < 4:
                ax.add_patch(FancyArrowPatch((x + 17.1, 11), (x + 19.9, 11), arrowstyle="-|>", mutation_scale=7,
                                             color=INK2, lw=1))
        self.save(fig, "fig_pipeline")

    # -------------------------------------------------------------------------------------------- 7 cohort flow
    def cohort_flow(self):
        flow = self.csv("eda", "cohort_flow.csv")
        fig, ax = plt.subplots(figsize=(WIDTH, 3.6))
        ax.set_axis_off()
        steps = flow.to_dict("records")
        main = [s for s in steps if not str(s["step"]).startswith("Exclude")]
        excl = [s for s in steps if str(s["step"]).startswith("Exclude")]
        y = 0.95
        ax.text(0.02, y, f"{steps[0]['step']}: {int(steps[0]['stays']):,} stays, {int(steps[0]['patients']):,} patients",
                transform=ax.transAxes, fontsize=7.5, weight="bold")
        for s in steps[1:]:
            y -= 0.068
            is_ex = str(s["step"]).startswith(("Exclude", "Modeling table"))
            label = str(s["step"]).replace("Exclude: ", "").replace("Modeling table: ", "")
            label = {"No at-risk snapshot (creatinine AKI or RRT at or before hour 1)": "no at-risk hour (AKI or RRT by hour 1)",
                     "All candidate snapshots fail the coverage rule": "no hour meets the data-coverage rule"}.get(label, label)
            if is_ex:
                ax.text(0.08, y, f"- {label}", transform=ax.transAxes, fontsize=6.8, color=INK2)
                ax.text(0.98, y, f"-{int(s['removed']):,}", transform=ax.transAxes, fontsize=6.8, color=SERIES[7], ha="right")
            else:
                prefix = "Modelling table: " if "Modeling table" in str(s["step"]) else ""
                removed = f"-{int(s['removed']):,}" if pd.notna(s.get("removed")) else ""
                ax.text(0.02, y, f"{prefix}{label}", transform=ax.transAxes, fontsize=7, color=INK)
                ax.text(0.80, y, f"{int(s['patients']):,} patients", transform=ax.transAxes, fontsize=7, ha="right")
                ax.text(0.98, y, removed, transform=ax.transAxes, fontsize=6.8, color=SERIES[7], ha="right")
        y -= 0.09
        ax.text(0.02, y, f"Final cohort: {int(steps[-1]['patients']):,} patients -> train / validation / test "
                f"70 / 15 / 15 % by patient", transform=ax.transAxes, fontsize=7.5, weight="bold", color=SERIES[0])
        del main, excl
        self.save(fig, "fig_cohort_flow")

    # -------------------------------------------------------------------------------------------- 8 EDA
    def eda(self):
        prev = self.csv("eda", "prevalence_by_hour_train.csv")
        onset = self.csv("eda", "aki_onset_timing_train.csv")
        traj = self.csv("eda", "creatinine_trajectory_train.csv")
        mp = self.csv("eda", "measurement_process_train.csv")
        fig, axes = plt.subplots(2, 2, figsize=(WIDTH, 4.4))
        ax = axes[0, 0]
        for h in [6, 12, 24, 48]:
            d = prev[prev["horizon_h"] == h]
            ax.plot(d["hour"], d["prevalence"] * 100, color=HORIZON_COLOR[h], lw=1.4)
            ax.text(73, d["prevalence"].iloc[-1] * 100, f"{h} h", color=INK2, fontsize=6.5, va="center")
        ax.set(title="a  AKI prevalence by ICU hour", xlabel="ICU hour of prediction", ylabel="% eligible origins with AKI",
               xlim=(0, 79))
        ax = axes[0, 1]
        labels = onset["bin"].drop_duplicates().tolist()
        x = np.arange(len(labels))
        for i, (sys_, c) in enumerate([("carevue", SERIES[0]), ("metavision", SERIES[1])]):
            d = onset[onset["source_system"] == sys_].set_index("bin").reindex(labels)["patients"].fillna(0)
            ax.bar(x + (i - .5) * 0.38, d / d.sum() * 100, 0.36, color=c, label={"carevue": "CareVue", "metavision": "MetaVision"}[sys_])
        ax.set_xticks(x, labels, rotation=45, ha="right")
        ax.set(title="b  Timing of creatinine AKI onset", xlabel="hours after ICU admission", ylabel="% of AKI patients")
        ax.legend(loc="upper right")
        ax = axes[1, 0]
        a = traj[traj["group"].str.startswith("AKI")].sort_values("t")
        ax.fill_between(a["t"], a["creatinine_last_p25"], a["creatinine_last_p75"], color=SERIES[1], alpha=.18, lw=0)
        ax.plot(a["t"], a["creatinine_last_median"], color=SERIES[1], label="patients who develop AKI")
        n = traj[traj["group"].str.startswith("no AKI")]
        ax.axhline(n["creatinine_last_median"].median(), color=SERIES[0], lw=1.2, ls="--",
                   label="patients without AKI (median)")
        ax.set(title="c  Creatinine before AKI onset", xlabel="hours before onset", ylabel="latest creatinine (mg/dL)")
        ax.legend(loc="upper left")
        ax = axes[1, 1]
        concepts = ["creatinine", "potassium", "lactate", "ph", "ptt", "hemoglobin"]
        yes = mp[mp["group"] == "aki_within_24h=yes"].set_index("concept").reindex(concepts)
        no = mp[mp["group"] == "aki_within_24h=no"].set_index("concept").reindex(concepts)
        yy = np.arange(len(concepts))
        ax.hlines(yy, no["hours_since_last_median"], yes["hours_since_last_median"], color=GRID, lw=2)
        ax.scatter(no["hours_since_last_median"], yy, color=SERIES[0], s=22, zorder=3, label="no AKI in next 24 h")
        ax.scatter(yes["hours_since_last_median"], yy, color=SERIES[1], s=22, zorder=3, label="AKI in next 24 h")
        ax.set_yticks(yy, [c.upper() if c in ("ptt", "ph") else c for c in concepts])
        ax.set(title="d  Time since last measurement", xlabel="median hours since latest value", xlim=(2, 18))
        ax.legend(loc="center right", fontsize=6.3)
        fig.tight_layout(h_pad=1.2)
        self.save(fig, "fig_eda")

    def missingness(self):
        g = self.csv("eda", "missingness_by_group_train.csv").sort_values("missing_mean")
        g = g[(g["group"] != "comorbidity_discharge_coded") & (g["missing_mean"] > 0.001)]
        fig, ax = plt.subplots(figsize=(WIDTH * .62, 2.3))
        y = np.arange(len(g))
        ax.barh(y + .2, g["missing_carevue_mean"] * 100, .38, color=SERIES[0], label="CareVue (2001-08)")
        ax.barh(y - .2, g["missing_metavision_mean"] * 100, .38, color=SERIES[1], label="MetaVision (2008-12)")
        ax.set_yticks(y, g["group"].str.replace("_", " "))
        ax.set(title="Missing inputs at hourly origins (training)", xlabel="mean % missing per feature")
        ax.legend(loc="lower right")
        self.save(fig, "fig_missingness")

    # -------------------------------------------------------------------------------------------- 9 ROC / PR
    def roc_pr(self):
        roc, pr = self.csv("final", "roc_points.csv"), self.csv("final", "pr_points.csv")
        snap = self.csv("test", "snapshot_metrics.csv")
        snap = snap[(snap["scope"] == "hours 1-72") & (snap["calibration"] == "platt")].set_index("horizon_h")
        fig, axes = plt.subplots(1, 3, figsize=(WIDTH, 2.1))
        ax = axes[0]
        for h in [6, 12, 24, 48]:
            d = roc[(roc["model"] == "primary") & (roc["horizon_h"] == h)]
            ax.plot(d["fpr"], d["tpr"], color=HORIZON_COLOR[h], lw=1.3, label=f"{h} h  {snap.loc[h, 'auroc']:.3f}")
        ax.plot([0, 1], [0, 1], color=MUTED, lw=.7, ls=":")
        ax.set(title="a  ROC by horizon", xlabel="false-positive rate", ylabel="true-positive rate")
        ax.legend(title="horizon  AUROC", title_fontsize=6.5, loc="lower right")
        ax = axes[1]
        for m, (c, lab) in MODEL_STYLE.items():
            d = roc[(roc["model"] == m) & (roc["horizon_h"] == 24)]
            ax.plot(d["fpr"], d["tpr"], color=c, lw=1.5 if m == "primary" else 1.1, ls="--" if m == "creatinine_rise" else "-")
        ax.plot([0, 1], [0, 1], color=MUTED, lw=.7, ls=":")
        ax.set(title="b  ROC, 24 h", xlabel="false-positive rate")
        ax = axes[2]
        for m, (c, lab) in MODEL_STYLE.items():
            d = pr[(pr["model"] == m) & (pr["horizon_h"] == 24)]
            ax.plot(d["recall"], d["precision"], color=c, lw=1.5 if m == "primary" else 1.1,
                    ls="--" if m == "creatinine_rise" else "-", label=lab)
        ax.axhline(pr[(pr["horizon_h"] == 24)]["prevalence"].iloc[0], color=MUTED, lw=.7, ls=":")
        ax.set(title="c  Precision-recall, 24 h", xlabel="recall", ylabel="precision", ylim=(0, 1))
        fig.tight_layout(w_pad=0.8)
        handles, labels = axes[2].get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.12))
        self.save(fig, "fig_roc_pr")

    # -------------------------------------------------------------------------------------------- 10 calibration
    def calibration(self):
        cal = self.csv("final", "calibration_bins.csv")
        cal = cal[cal["model"] == "primary"]
        fig, axes = plt.subplots(1, 4, figsize=(WIDTH, 1.65), sharey=False)
        snap = self.csv("test", "snapshot_metrics.csv")
        snap = snap[(snap["scope"] == "hours 1-72") & (snap["calibration"] == "platt")].set_index("horizon_h")
        for ax, h in zip(axes, [6, 12, 24, 48]):
            d = cal[cal["horizon_h"] == h]
            top = max(d["mean_predicted"].max(), d["observed_ci_high"].max()) * 1.08
            ax.plot([0, top], [0, top], color=MUTED, lw=.7, ls=":")
            ax.errorbar(d["mean_predicted"], d["observed"], yerr=[d["observed"] - d["observed_ci_low"], d["observed_ci_high"] - d["observed"]],
                        fmt="o", ms=3.2, color=HORIZON_COLOR[h], ecolor=HORIZON_COLOR[h], elinewidth=.8, capsize=0)
            ax.set(xlim=(0, top), ylim=(0, top), title=f"{h} h", xlabel="predicted")
            ax.text(0.04, 0.96, f"O/E {snap.loc[h, 'observed_over_expected']:.2f}\nslope {snap.loc[h, 'calibration_slope']:.2f}",
                    transform=ax.transAxes, va="top", fontsize=6.3, color=INK2)
        axes[0].set_ylabel("observed")
        fig.tight_layout(w_pad=0.6)
        self.save(fig, "fig_calibration")

    # -------------------------------------------------------------------------------------------- 11 alert trade-off
    def tradeoff(self):
        t = self.csv("final", "alert_tradeoff_24h.csv")
        fig, axes = plt.subplots(1, 2, figsize=(WIDTH, 2.2))
        for m, (c, lab) in MODEL_STYLE.items():
            d = t[t["model"] == m].sort_values("false_alerts_per_patient_day")
            for ax, ycol in zip(axes, ["event_detection_rate", "lead_time_median_h"]):
                yv = d[ycol] * (100 if ycol == "event_detection_rate" else 1)
                ax.plot(d["false_alerts_per_patient_day"], yv, color=c, lw=1.5 if m == "primary" else 1.0,
                        ls="--" if m == "creatinine_rise" else "-", label=lab)
        for name, marker, lab, off in [("policy_v3", "D", "policy v3", (6, -11)), ("demo_v1", "o", "demo-v1", (4, 5))]:
            p = t[t["model"] == name].iloc[0]
            for ax, ycol in zip(axes, ["event_detection_rate", "lead_time_median_h"]):
                yv = p[ycol] * (100 if ycol == "event_detection_rate" else 1)
                ax.scatter(p["false_alerts_per_patient_day"], yv, marker=marker, s=30, color=INK, zorder=5,
                           edgecolor="white", linewidth=1)
                ax.annotate(lab, (p["false_alerts_per_patient_day"], yv), xytext=off, textcoords="offset points",
                            fontsize=6.3, color=INK)
        axes[0].set(title="a  AKI events detected", xlabel="false alerts per patient-day", ylabel="% of AKI events",
                    xlim=(0, 0.85), ylim=(0, 100))
        axes[1].set(title="b  Lead time of first correct alert", xlabel="false alerts per patient-day",
                    ylabel="median hours before onset", xlim=(0, 0.85))
        fig.tight_layout(w_pad=1)
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.14))
        self.save(fig, "fig_alert_tradeoff")

    # -------------------------------------------------------------------------------------------- 5 decision curve
    def decision_curve(self):
        t = self.csv("final", "decision_curves.csv")
        fig, axes = plt.subplots(1, 3, figsize=(WIDTH, 2.0), sharey=False)
        for ax, h in zip(axes, [12, 24, 48]):
            d = t[t["horizon_h"] == h]
            for m, (c, lab) in MODEL_STYLE.items():
                if m == "creatinine_rise":
                    continue
                s = d[d["model"] == m]
                ax.plot(s["threshold"] * 100, s["net_benefit"], color=c, lw=1.5 if m == "primary" else 1.0, label=lab)
            s = d[d["model"] == "treat_all"]
            ax.plot(s["threshold"] * 100, s["net_benefit"], color=REFERENCE_GRAY, lw=1, ls="--", label="alert everyone")
            ax.axhline(0, color=INK2, lw=.8, label="alert no one")
            top = d[d["model"] == "primary"]["net_benefit"].max()
            ax.set(ylim=(-top * .15, top * 1.15), xlim=(0, 50), title=f"{h} h", xlabel="threshold probability (%)")
            if h == 24:
                ax.axvline(21, color=MUTED, lw=.7, ls=":")
                ax.text(22, top * 1.05, "policy v3", fontsize=6, color=INK2)
        axes[0].set_ylabel("net benefit")
        fig.tight_layout(w_pad=0.6)
        handles, labels = axes[1].get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.16))
        self.save(fig, "fig_decision_curve")

    # -------------------------------------------------------------------------------------------- 2 severity
    def severity(self):
        s = self.csv("final", "severity_detection.csv")
        order = ["max stage 1", "max stage 2", "max stage 3", "went on to RRT"]
        fig, ax = plt.subplots(figsize=(WIDTH * .62, 2.1))
        x = np.arange(len(order))
        for i, (pol, c, lab) in enumerate([("policy_v3", SERIES[0], "policy v3"), ("demo_v1", SERIES[1], "demo-v1")]):
            d = s[s["policy"] == pol].set_index("stratum").reindex(order)
            for j, (L, alpha) in enumerate([(24, 1.0)]):
                v = d[f"detection_rate_{L}h"] * 100
                err = [v - d[f"detection_{L}h_ci_low"] * 100, d[f"detection_{L}h_ci_high"] * 100 - v]
                ax.bar(x + (i - .5) * .38, v, .36, color=c, alpha=alpha, label=lab, yerr=err,
                       error_kw={"elinewidth": .8, "ecolor": INK2})
        ns = s[s["policy"] == "policy_v3"].set_index("stratum").reindex(order)["events"]
        ax.set_xticks(x, [f"{o.replace('max ', '')}\n(n={int(n)})" for o, n in zip(order, ns)])
        ax.set(title="AKI events detected by severity (test)", ylabel="% detected", ylim=(0, 105))
        ax.legend(loc="upper left", ncol=2)
        self.save(fig, "fig_severity")

    # -------------------------------------------------------------------------------------------- 12 SHAP
    def shap(self):
        t = self.csv("test", "shap_top20.csv")
        d = t[t["horizon_h"] == 24].sort_values("rank").head(15)[::-1]
        fig, axes = plt.subplots(1, 2, figsize=(WIDTH, 2.8), gridspec_kw={"width_ratios": [1.3, 1]})
        ax = axes[0]
        ax.barh(np.arange(len(d)), d["mean_abs_shap"], color=SERIES[0], height=.62)
        ax.set_yticks(np.arange(len(d)), d["feature"].str.replace("_", " "), fontsize=6.3)
        ax.set(title="a  Top inputs, 24 h (mean |SHAP|)", xlabel="mean |contribution| (logit)")
        ax.grid(axis="y", visible=False)
        ax = axes[1]
        top = t[t["horizon_h"] == 24].sort_values("rank").head(10)["feature"].tolist()
        R = t.pivot(index="feature", columns="horizon_h", values="rank").reindex(top)
        im = ax.imshow(R.fillna(25).to_numpy(), cmap=matplotlib.colors.LinearSegmentedColormap.from_list(
            "b", [BLUE["650"], "#cde2fb"]), vmin=1, vmax=21, aspect="auto")
        for (i, j), v in np.ndenumerate(R.to_numpy()):
            ax.text(j, i, ">20" if np.isnan(v) else int(v), ha="center", va="center", fontsize=6,
                    color="white" if (not np.isnan(v) and v <= 8) else INK)
        ax.set_xticks(range(R.shape[1]), [f"{h} h" for h in R.columns])
        ax.set_yticks(range(len(top)), [f.replace("_", " ") for f in top], fontsize=6)
        ax.set(title="b  Rank by horizon")
        ax.grid(False)
        del im
        fig.tight_layout(w_pad=0.8)
        self.save(fig, "fig_shap")

    def patient_example(self):
        d = json.loads(self.demo.read_text(encoding="utf-8"))
        pid = "DEMO-002"
        start = pd.Timestamp(d["clock"]["start"])
        rows = []
        for p in d["predictions"]:
            if p["patient_id"] != pid:
                continue
            h = int(p["record_id"].rsplit("-", 1)[1].rstrip("h"))
            rows.append({"h": h, "t": (pd.Timestamp(p["origin_time"]) - start) / pd.Timedelta(hours=1), "risk": p["risk"],
                         "drivers": p.get("drivers") or []})
        t = pd.DataFrame(rows).sort_values(["h", "t"])
        name = next(x["name"] for x in d["patients"] if x["patient_id"] == pid)
        fig, ax = plt.subplots(figsize=(WIDTH, 2.0))
        for h in [6, 12, 24, 48]:
            s = t[t["h"] == h]
            ax.plot(s["t"], s["risk"] * 100, color=HORIZON_COLOR[h], lw=1.3, label=f"{h} h risk")
        ax.axhline(21, color=SERIES[1], lw=.9, ls="--")
        ax.text(1, 22.5, "policy v3 threshold (24 h risk 21%)", fontsize=6.3, color=INK2)
        s24 = t[t["h"] == 24].set_index("t")
        s12 = t[t["h"] == 12].set_index("t")["risk"]
        hit = s24[(s24["risk"] >= .21) & (s12.reindex(s24.index) >= .105)]
        if len(hit):
            t0 = hit.index[0]
            ax.scatter([t0], [hit["risk"].iloc[0] * 100], marker="D", color=INK, s=26, zorder=5, edgecolor="white")
            top = sorted(hit["drivers"].iloc[0], key=lambda x: -abs(x["contribution"]))[:3]
            txt = "first warning, top drivers:\n" + "\n".join(f"  {x['feature'].replace('_', ' ')}" for x in top)
            ax.annotate(txt, (t0, hit["risk"].iloc[0] * 100), xytext=(2, 62), textcoords="data", fontsize=6.2,
                        color=INK, va="top", arrowprops={"arrowstyle": "-", "color": INK2, "lw": .7})
        ax.set(title=f"Synthetic patient replay: {name}", xlabel="ICU hour", ylabel="predicted AKI risk (%)", xlim=(0, 72), ylim=(0, 72))
        ax.legend(loc="upper center", ncol=4, bbox_to_anchor=(0.5, -0.22))
        self.save(fig, "fig_patient_example")

    # -------------------------------------------------------------------------------------------- 13 subgroups
    def subgroups(self):
        s = self.csv("test", "subgroups_snapshot.csv")
        names = {"age_years": "Age", "sex": "Sex", "first_careunit": "Unit", "source_system": "System"}
        s = s.iloc[::-1].reset_index(drop=True)
        fig, ax = plt.subplots(figsize=(WIDTH * .62, 3.0))
        y = np.arange(len(s))
        ax.errorbar(s["auroc_24h"], y, xerr=[s["auroc_24h"] - s["auroc_24h_ci_low"], s["auroc_24h_ci_high"] - s["auroc_24h"]],
                    fmt="o", ms=3.5, color=SERIES[0], ecolor=SERIES[0], elinewidth=1, capsize=0)
        overall = self.csv("test", "snapshot_metrics.csv")
        o = overall[(overall["scope"] == "hours 1-72") & (overall["calibration"] == "platt") & (overall["horizon_h"] == 24)]["auroc"].iloc[0]
        ax.axvline(o, color=MUTED, lw=.8, ls=":")
        nice = {"carevue": "CareVue", "metavision": "MetaVision"}
        ax.set_yticks(y, [f"{names.get(v, v)}: {nice.get(l, l)}  (n={p:,})" for v, l, p in zip(s["variable"], s["level"], s["patients"])], fontsize=6.5)
        ax.set(title="24 h AUROC by subgroup (test, 95% CI)", xlabel="AUROC")
        ax.grid(axis="y", visible=False)
        self.save(fig, "fig_subgroups")

    # -------------------------------------------------------------------------------------------- 14 retrain
    def retrain(self):
        t = self.csv("retrain", "test_differences.csv")
        t = t[(t["metric"] == "auroc_difference") & (t["variant"] != "reference_refit")]
        labels = {"windows_last_value_only": "latest values only", "windows_short_only": "6 h summaries only",
                  "windows_long_only": "no 6 h vital summaries", "missing_median_impute": "median imputation",
                  "missing_median_impute_indicators": "median imputation + indicators",
                  "missing_no_measurement_process": "no measurement-process features"}
        order = list(labels)
        fig, ax = plt.subplots(figsize=(WIDTH * .62, 2.3))
        for k, h in enumerate([6, 12, 24, 48]):
            d = t[t["horizon_h"] == h].set_index("variant").reindex(order)
            y = np.arange(len(order)) + (k - 1.5) * .18
            ax.errorbar(d["estimate"], y, xerr=[d["estimate"] - d["ci_low"], d["ci_high"] - d["estimate"]], fmt="o", ms=2.8,
                        color=HORIZON_COLOR[h], ecolor=HORIZON_COLOR[h], elinewidth=.9, label=f"{h} h")
        ax.axvline(0, color=INK2, lw=.8)
        ax.set_yticks(np.arange(len(order)), [labels[o] for o in order], fontsize=6.5)
        ax.set(title="Retrained variants: AUROC change (test)", xlabel="AUROC difference vs locked model")
        ax.set_xticks([-0.03, -0.02, -0.01, 0, 0.01])
        ax.legend(loc="lower left", ncol=2)
        ax.grid(axis="y", visible=False)
        self.save(fig, "fig_retrain_sensitivity")

    # -------------------------------------------------------------------------------------------- 6 system shift
    def system_shift(self):
        m = self.csv("shift", "snapshot_metrics.csv")
        m = m[m["split"] == "test"]
        a = self.csv("shift", "alert_metrics_policy_v3.csv")
        fig, axes = plt.subplots(1, 3, figsize=(WIDTH, 2.1))
        style = {"train_carevue": (SERIES[0], "trained on CareVue"), "train_metavision": (SERIES[1], "trained on MetaVision"),
                 "reference": (REFERENCE_GRAY, "locked (both systems)")}
        for ax, target in zip(axes[:2], ["carevue", "metavision"]):
            for i, (model, (c, lab)) in enumerate(style.items()):
                d = m[(m["model"] == model) & (m["target_system"] == target)].sort_values("horizon_h")
                ax.plot(d["horizon_h"], d["auroc"], marker="o", ms=3.5, color=c, lw=1.2, label=lab,
                        ls="--" if model == "reference" else "-")
            ax.set_xscale("log", base=2)
            ax.set_xticks([6, 12, 24, 48], ["6", "12", "24", "48"])
            ax.minorticks_off()
            ax.set(title=f"{'a' if target == 'carevue' else 'b'}  Test: {'CareVue' if target == 'carevue' else 'MetaVision'}", xlabel="horizon (h)")
        axes[0].set_ylabel("AUROC")
        lo = min(ax.get_ylim()[0] for ax in axes[:2])
        hi = max(ax.get_ylim()[1] for ax in axes[:2])
        for ax in axes[:2]:
            ax.set_ylim(lo, hi)
        ax = axes[2]
        x = np.arange(2)
        for i, (model, (c, lab)) in enumerate(style.items()):
            d = a[a["model"] == model].set_index("target_system").reindex(["carevue", "metavision"])
            ax.bar(x + (i - 1) * .27, d["event_detection_rate"] * 100, .25, color=c)
        ax.set_xticks(x, ["CareVue", "MetaVision"])
        ax.set(title="c  Events detected", xlabel="test system (v3 unchanged)", ylabel="% of AKI events", ylim=(50, 80))
        fig.tight_layout(w_pad=0.8)
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.12))
        self.save(fig, "fig_system_shift")

    # -------------------------------------------------------------------------------------------- fairness
    def fairness(self):
        t = self.csv("fair", "fairness_by_group.csv")
        t = t[(t["analysis"] == "test") & (t["reported"] == "full")]
        names = {"insurance": "Insurance", "ethnicity_group": "Ethnicity"}
        nice = lambda v: v if v.isupper() is False else v.capitalize()
        t = t.iloc[::-1].reset_index(drop=True)
        y = np.arange(len(t))
        fig, axes = plt.subplots(1, 2, figsize=(WIDTH, 2.9), sharey=True, gridspec_kw={"width_ratios": [1, 1]})
        colors = [SERIES[0] if v == "insurance" else SERIES[1] for v in t["variable"]]
        ax = axes[0]
        for yi, (_, r), c in zip(y, t.iterrows(), colors):
            ax.errorbar(r["auroc_24h"], yi, xerr=[[r["auroc_24h"] - r["auroc_ci_low"]], [r["auroc_ci_high"] - r["auroc_24h"]]],
                        fmt="o", ms=3.5, color=c, ecolor=c, elinewidth=1)
        overall = self.csv("test", "snapshot_metrics.csv")
        o = overall[(overall["scope"] == "hours 1-72") & (overall["calibration"] == "platt") & (overall["horizon_h"] == 24)]["auroc"].iloc[0]
        ax.axvline(o, color=MUTED, lw=.8, ls=":")
        ax.set_yticks(y, [f"{names[v]}: {nice(l)}  (n={p:,})" for v, l, p in zip(t["variable"], t["level"], t["patients"])], fontsize=6.5)
        ax.set(title="a  24 h AUROC (95% CI)", xlabel="AUROC")
        ax.grid(axis="y", visible=False)
        ax = axes[1]
        for yi, (_, r), c in zip(y, t.iterrows(), colors):
            v = r["event_detection_rate"] * 100
            ax.errorbar(v, yi, xerr=[[v - r["event_detection_rate_ci_low"] * 100], [r["event_detection_rate_ci_high"] * 100 - v]],
                        fmt="o", ms=3.5, color=c, ecolor=c, elinewidth=1)
        a = self.csv("test", "alert_metrics.csv")
        ov = a[(a["policy"] == "policy_v3") & (a["metric"] == "event_detection_rate")]["estimate"].iloc[0] * 100
        ax.axvline(ov, color=MUTED, lw=.8, ls=":")
        ax.set(title="b  AKI events detected, policy v3", xlabel="% of events (95% CI)")
        ax.grid(axis="y", visible=False)
        fig.tight_layout(w_pad=0.6)
        self.save(fig, "fig_fairness")

    def recalibration(self):
        m = self.csv("recal", "snapshot_metrics.csv")
        a = self.csv("recal", "alert_metrics_policy_v3.csv")
        c = self.csv("recal", "sample_size_repeats.csv")
        kinds = [("source calibration", SERIES[0], "transported, source calibration"),
                 ("recalibrated", SERIES[2], "transported, recalibrated locally"),
                 ("within-system", REFERENCE_GRAY, "trained on target system")]
        fig, axes = plt.subplots(1, 3, figsize=(WIDTH, 2.15))
        targets = ["metavision", "carevue"]
        tl = {"metavision": "MetaVision", "carevue": "CareVue"}
        x = np.arange(2)
        for i, (key, col, lab) in enumerate(kinds):
            oe = [m[(m["target_system"] == t) & (m["horizon_h"] == 24) & m["model"].str.contains(key)]["observed_over_expected"].iloc[0] for t in targets]
            det = [a[(a["target_system"] == t) & a["model"].str.contains(key)]["event_detection_rate"].iloc[0] * 100 for t in targets]
            axes[0].bar(x + (i - 1) * .27, oe, .25, color=col, label=lab)
            axes[1].bar(x + (i - 1) * .27, det, .25, color=col)
        axes[0].axhline(1, color=INK2, lw=.8)
        axes[0].set_xticks(x, [tl[t] for t in targets])
        axes[0].set(title="a  Calibration, 24 h", ylabel="observed / expected", xlabel="test system", ylim=(0.6, 1.25))
        axes[1].set_xticks(x, [tl[t] for t in targets])
        axes[1].set(title="b  Events detected (v3)", ylabel="% of AKI events", xlabel="test system", ylim=(50, 80))
        ax = axes[2]
        for t, col in [("metavision", SERIES[1]), ("carevue", SERIES[0])]:
            d = c[c["target_system"] == t].groupby("n_patients")["observed_over_expected_24h"].quantile([.1, .5, .9]).unstack()
            ax.fill_between(d.index, d[.1], d[.9], color=col, alpha=.18, lw=0)
            ax.plot(d.index, d[.5], color=col, marker="o", ms=3, label=f"target {tl[t]}")
        ax.axhline(1, color=INK2, lw=.8)
        ax.set_xscale("log")
        ax.set(title="c  Local patients needed", xlabel="validation patients used (log)", ylabel="O/E (median, 10-90%)")
        ax.legend(loc="upper right", fontsize=6)
        fig.tight_layout(w_pad=0.6)
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.12))
        self.save(fig, "fig_recalibration")

    # -------------------------------------------------------------------------------------------- run
    def run(self):
        for f in [self.pipeline, self.cohort_flow, self.eda, self.missingness, self.roc_pr, self.calibration, self.tradeoff,
                  self.decision_curve, self.severity, self.shap, self.patient_example, self.subgroups, self.retrain,
                  self.system_shift, self.fairness, self.recalibration]:
            try:
                f()
            except FileNotFoundError as e:
                self.skipped.append(f"{f.__name__}: missing {Path(e.filename).name if e.filename else e}")
        print("made:", ", ".join(self.made))
        if self.skipped:
            print("skipped:", "; ".join(self.skipped))
