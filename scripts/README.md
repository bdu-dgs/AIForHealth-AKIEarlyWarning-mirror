# Scripts

Command-line entry points, run from the repository root with the research environment
(`python scripts/<name>.py`). The website scripts run with the website's `.venv`. Scripts stay in one folder because
tests and the website import some of them as the `scripts` package.

| Group | Script | Purpose |
|---|---|---|
| Validation stage | `rescore_validation_feature_v2.py` | Rescore validation with the batch-independent feature engine (locked weights) |
| | `evaluate_alert_policy.py` | Run the alert-policy study (`configs/alert_policy_v3.yaml`); used by notebook 05 |
| | `summarize_alert_policy.py` | Aggregate-only report helper for the alert-policy study |
| Locked test stage | `evaluate_test.py` | The one locked test evaluation (`configs/evaluation_test_v1.yaml`) |
| | `run_retrain_robustness.py` | Retraining sensitivity analyses (feature windows, missing values) |
| Final-report analyses | `run_final_analyses.py` | Severity, literature operating point, baselines, alert trade-off, decision curves |
| | `run_eda.py` | Exploratory data analysis on the training split |
| | `run_system_shift.py` | Train on one MIMIC charting system, test on the other |
| | `run_recalibration.py` | Recalibrate the transported model on local data (needs `run_system_shift.py`) |
| | `run_fairness.py` | Performance and alerts by insurance and recorded ethnicity |
| | `run_sequence_model.py` | GRU sequence model versus LightGBM, validation only (go / no-go) |
| | `run_alert_budget.py` | Alert-budget control: group recalibration, budget-certified threshold, detection parity |
| | `run_alert_budget_dev.py` | Validation-only test of fixes for the alert-budget limitations |
| | `make_report_figures.py` | Draw every report figure from the aggregate tables |
| Integrity | `verify_sealed_hashes.py` | Check that committed code and configuration match all sealed study records |
| Website and demo | `score_dashboard.py` | Batch hourly scoring of website patients with the locked model |
| | `make_synthetic_demo_patients.py` | Build the four synthetic demo patients (used by `Start-AKI-Demo.cmd`) |
| | `make_demo_upload.py` | Create a "new lab result" file for a live upload during the demo |
| | `export_static_demo.py` | Record the demo with the real backend and model for the static site |
| | `serve_static_demo.py` | Serve `demo-static/` offline |
| Setup (Windows) | `setup-windows.ps1`, `ensure-node.ps1` | Called by `Setup-AKI.cmd`: Python environment, Node.js, frontend build |
