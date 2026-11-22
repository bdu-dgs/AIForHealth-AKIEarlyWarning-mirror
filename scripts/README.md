# Scripts

Command-line entry points, run from the repository root with the research environment (`python scripts/<name>.py`).
Website scripts use the website's `.venv`. Scripts stay in one folder because tests and the website import some of
them as the `scripts` package. They are grouped below by the part of the project they serve.

| Part | Script | Purpose |
|---|---|---|
| 1. Prediction | `run_retrain_robustness.py` | Retraining sensitivity analyses (feature windows, missing values) |
| | `run_eda.py` | Exploratory data analysis on the training split |
| | `run_sequence_model.py` | GRU sequence model against LightGBM, validation only (go / no-go) |
| 1-2. Locked evaluation | `evaluate_test.py` | The one locked test evaluation of model and alert policy (`configs/evaluation_test_v1.yaml`) |
| | `run_final_analyses.py` | Baselines, severity, literature operating point, alert trade-off, decision curves |
| 2. From prediction to alerts | `rescore_validation_feature_v2.py` | Rescore validation with the batch-independent feature engine (locked weights) |
| | `evaluate_alert_policy.py`, `summarize_alert_policy.py` | Alert-policy study used by notebook 05, and its aggregate report |
| | `run_system_shift.py` | Train on one MIMIC charting system, test on the other: ranking holds, probabilities drift |
| | `run_fairness.py` | Performance and alerts by insurance and recorded ethnicity |
| 3. Reliable alerting | `run_recalibration.py` | Local recalibration of a moved model (needs `run_system_shift.py`) |
| | `run_alert_budget.py` | **Alert-budget control**: group recalibration, budget-certified threshold, detection parity |
| | `run_alert_budget_dev.py` | Validation-only check of fixes for the method's limitations |
| Figures and integrity | `make_report_figures.py` | Draw every report figure from the aggregate tables |
| | `verify_sealed_hashes.py` | Check that committed code and configuration match all sealed study records |
| Website and demo | `score_dashboard.py` | Batch hourly scoring of website patients with the locked model |
| | `make_synthetic_demo_patients.py` | Build the four synthetic demo patients (used by `Start-AKI-Demo.cmd`) |
| | `make_demo_upload.py` | Create a "new lab result" file for a live upload during the demo |
| | `export_static_demo.py`, `serve_static_demo.py` | Record the demo for the static site; serve `demo-static/` offline |
| | `setup-windows.ps1`, `ensure-node.ps1` | Called by `Setup-AKI.cmd`: Python environment, Node.js, frontend build |
