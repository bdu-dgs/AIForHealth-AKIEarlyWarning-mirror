# Pipeline notebooks

Run in order. Each notebook reads the outputs of the previous ones from disk (Parquet, joblib, JSON). Nothing is
passed in memory. Settings are in `configs/`. Shared code is in `src/`. Long computations are run as scripts
(see [INSTALL.md](../INSTALL.md) for the full order and runtimes).

| # | Notebook | What it does | Reads | Writes |
|---|---|---|---|---|
| 01 | `01_dataset_construction.ipynb` | Selects one adult ICU stay per patient, applies exclusions and splits patients 70/15/15 before any feature is built. Derives creatinine-KDIGO onset (urine-output KDIGO kept as a robustness endpoint) and builds hourly as-of features and multi-horizon labels with censoring. Exports the event stream for replay. | MIMIC-III CSVs, `configs/default.yaml` | `../Cleaned/` (cohort, snapshots, splits, feature dictionary, event stream, reports) |
| 02 | `02_ml_pipeline.ipynb` | Trains logistic regression, random forest, LightGBM (primary) and XGBoost with one head per horizon, patient-grouped CV, exposure and class weights, and Platt/isotonic calibration on training out-of-fold scores. Includes a LightGBM ablation without discharge-coded diagnoses (`no_dc`, the deployed model) and event-driven replay. | `../Cleaned/`, `configs/ml.yaml` | `artifacts/models/aki_ml_v1_28e0b1fb83/`, `artifacts/predictions/` |
| 03 | `03_alert_threshold_demo_i.ipynb` | Selects the monitoring window (ICU hours 1-72) and the first alert threshold (24 h risk >= 21%) on training out-of-fold predictions, confirmed on validation (Demo I). | notebook 02 outputs | `artifacts/demo_i/policy.json` |
| 04 | `04_stability_analysis.ipynb` | Checks calibration by segment, TreeSHAP stability, feature shift, risk volatility and horizon monotonicity on validation. | notebook 02 outputs, policy | `artifacts/handoff/jiaju/` |
| 05 | `05_alert_policy_v3_and_checks.ipynb` | Runs artifact and leakage checks A1-A12. Selects the alert policy on 70% of validation patients and confirms it on the other 30%: 24 h >= 21% and 12 h >= 10.5%, 12 h cooldown. The policy is then locked. | validation risks (feature engine v2), `configs/alert_policy_v3.yaml` | `artifacts/handoff/policy_v3/`, `artifacts/policy/policy_v3.json` |
| 06 | `06_test_evaluation_summary.ipynb` | Summarises the one locked test evaluation (`scripts/evaluate_test.py`) and the retraining sensitivity analyses (`scripts/run_retrain_robustness.py`): discrimination, calibration, alerts, event-driven replay, subgroups, robustness, SHAP. | `artifacts/reports/test_v1/`, `robustness_retrain_v1/` | - (read-only) |
| 07 | `07_final_report_analyses.ipynb` | Summarises the final-report analyses: severity, literature operating point, baselines, decision curves, CareVue <-> MetaVision transfer, local recalibration, fairness, EDA, with all report figures. | `artifacts/reports/*` (aggregate only) | - (read-only) |

Notebooks 06 and 07 only read committed aggregate tables, so they run without MIMIC-III access.

**Naming.** Notebooks 01 and 02 keep their original file names, because each hashes its own code cells into the
dataset and run identifiers. In older records and in the sealed code, notebook 06 is called "notebook 03";
notebooks 03-05 were `demo_i_evaluation`, `Jiaju_stability` and `KeJin_alert_policy_and_checks`. Notebooks 04 and 05
were contributed by teammates.
