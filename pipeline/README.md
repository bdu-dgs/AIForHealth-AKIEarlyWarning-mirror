# Pipeline notebooks

The notebooks follow the project's three steps: **build a strong baseline; move it to another record system and see
what breaks and why; repair the alerts with local data.** Steps 1-3 are Parts 1-3 in `docs/FINAL-ANALYSES.md`. The
notebooks are numbered in the order the work was run.
Each reads the previous outputs from disk (Parquet, joblib, JSON); nothing is passed in memory. Settings are in
`configs/`, shared code in `src/`, and long computations run as scripts (see [INSTALL.md](../INSTALL.md)).

## Step 1. Build a strong baseline: how good can an AKI model get on this data?

| # | Notebook | What it does | Writes |
|---|---|---|---|
| 01 | `01_dataset_construction.ipynb` | Selects one adult ICU stay per patient, applies exclusions and splits patients 70/15/15 before any feature is built. Derives creatinine-KDIGO onset (urine-output KDIGO kept as a robustness endpoint). Builds hourly as-of features and multi-horizon labels; censored horizons are marked ineligible, not negative. Exports the event stream for replay. | `../Cleaned/` |
| 02 | `02_ml_pipeline.ipynb` | Trains LR, RF, LightGBM and XGBoost with one head per horizon (6/12/24/48 h), patient-grouped CV, exposure and class weights, and Platt calibration on training out-of-fold scores. The locked model is LightGBM without discharge-coded diagnoses (`no_dc`). | `artifacts/models/aki_ml_v1_28e0b1fb83/`, `artifacts/predictions/` |
| 04 | `04_stability_analysis.ipynb` | Calibration by ICU-time segment, TreeSHAP stability, feature shift, risk volatility and horizon monotonicity on validation. | `artifacts/handoff/jiaju/` |
| 07 | `07_results_prediction.ipynb` | **Results, Step 1** (read-only): cohort and EDA, test discrimination and calibration against baselines, the GRU headroom check, the feature ablation (top-k inputs, one group dropped), prediction drivers and robustness. | - |

## Step 2. Move it and see what breaks, and why

| # | Notebook | What it does | Writes |
|---|---|---|---|
| 03 | `03_alert_threshold_demo_i.ipynb` | First alert design: monitoring window (ICU hours 1-72) and threshold (24 h risk >= 21%), selected on training out-of-fold predictions and confirmed on validation. | `artifacts/demo_i/policy.json` |
| 05 | `05_alert_policy_v3_and_checks.ipynb` | Artifact and leakage checks A1-A12. Alert policy selected on 70% of validation patients and confirmed on the other 30% (24 h >= 21% and 12 h >= 10.5%, 12 h cooldown), then locked. | `artifacts/handoff/policy_v3/`, `artifacts/policy/policy_v3.json` |
| 06 | `06_test_evaluation_summary.ipynb` | Summarises the one locked test evaluation and the retraining sensitivity analyses: discrimination, calibration, alerts, event-driven replay, subgroups, SHAP (read-only). | - |
| 08 | `08_results_alert_reliability.ipynb` | **Results, Step 2** (read-only): what the locked policy achieves (severity, literature operating point, trade-off, decision curves), and how alerts fail when the model moves to the other record system (ranking holds, probabilities drift), which inputs cause the drift, and the same pattern across patient groups. | - |

## Step 3. Repair the alerts: can the alert burden be kept?

| # | Notebook | What it does |
|---|---|---|
| 08 | `08_results_alert_reliability.ipynb` | **Results, Step 3** (same notebook, second half): local recalibration, the alert-budget control method (`src/aki_report/budget.py`) with its coverage and budget sweep, and the validation-only check of group-specific fixes. |

Notebooks 06-08 only read committed aggregate tables, so they run without MIMIC-III access.

**Naming.** Notebooks 01 and 02 keep their original file names, because each hashes its own code cells into the
dataset and run identifiers. In older records and in the sealed code, notebook 06 is called "notebook 03";
notebooks 03-05 were `demo_i_evaluation`, `Jiaju_stability` and `KeJin_alert_policy_and_checks`. Notebooks 04 and 05
were contributed by teammates.
