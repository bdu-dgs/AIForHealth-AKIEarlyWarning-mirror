# Installation and reproduction

There are two separate parts:

- the **website** (local workbench and static demo), described in [dashboard/README.md](dashboard/README.md);
- the **research pipeline**, which produces every number and figure in the report.

This file covers both, from a fresh clone to the final figures. No step needs a GPU or the internet after setup.
Patient data never leave the machine.

## 1. Requirements

- Windows 10/11, macOS or Linux, x64. Results were produced on Windows 11 with an i5-13500H and 16 GB of RAM, on CPU
  only.
- Python **3.13** for the research pipeline (`requirements-research.txt`). The website runs on Python 3.11 or later.
- About **60 GB** of free disk: MIMIC-III is 40+ GB, `../Cleaned` about 0.5 GB, `artifacts/` 2-3 GB.
- **Credentialed PhysioNet access to MIMIC-III v1.4.** The data are not in this repository and must never be
  committed or uploaded.

## 2. Data layout

Download MIMIC-III v1.4 (uncompressed CSVs) to a folder next to the repository:

```text
<parent>/
|-- mimiciii/1.4/             # ADMISSIONS.csv, CHARTEVENTS.csv, LABEVENTS.csv, ... (from PhysioNet)
|-- Cleaned/                  # written by notebook 01 (path fixed in configs/default.yaml)
|-- icu_pre_admission_data/   # optional small MIMIC subset for the website's read-only dataset preview
`-- AIForHealth-AKIEarlyWarning/   # this repository
```

Other locations can be set with environment variables (see `.env.example`): `AKI_MIMIC_ROOT`, `AKI_OUTPUT_ROOT`,
`AKI_INTERIM_ROOT`.

## 3. Research environment

```powershell
cd AIForHealth-AKIEarlyWarning
python -m venv .venv-research
.\.venv-research\Scripts\activate          # macOS/Linux: source .venv-research/bin/activate
pip install -r requirements-research.txt
python -m pytest -q tests                  # 81 synthetic tests, about 30 s
```

The versions are pinned to the environment that trained the models. The pickled pipelines must be loaded with the
same scikit-learn, LightGBM and XGBoost versions. The website's `.venv` (from `Setup-AKI.cmd`) is a different
environment without xgboost or matplotlib; do not use it for the research scripts.

## 4. Reproduce the study (in order)

Runtimes are for 16 CPU threads; "-" means not recorded. Steps 1-3 build and train. Steps 4-7 are the validation-stage studies that produced
the locked alert policy. Step 8 is the one locked test evaluation. Steps 9-13 are the secondary analyses for the final
report.

| # | Step | Command | Output | Time |
|---|---|---|---|---|
| 1 | Cohort, labels, hourly features | run `pipeline/01_dataset_construction.ipynb` | `../Cleaned/` | ~25 min |
| 2 | Train LR, RF, LightGBM, XGBoost (4 horizons) | run `pipeline/02_ml_pipeline.ipynb` | `artifacts/models/<run_id>/`, `artifacts/predictions/` | ~4.5 h |
| 3 | Monitoring window and threshold (Demo I) | run `pipeline/03_alert_threshold_demo_i.ipynb` | `artifacts/demo_i/` | - |
| 4 | Batch-independent features, validation rescoring | `python scripts/rescore_validation_feature_v2.py` | `artifacts/handoff/feature_v2/` | - |
| 5 | Stability analysis | run `pipeline/04_stability_analysis.ipynb` | `artifacts/handoff/jiaju/` | - |
| 6 | Alert policy v3 (selection + confirmation) | run `pipeline/05_alert_policy_v3_and_checks.ipynb` | `artifacts/handoff/policy_v3/`, `artifacts/policy/policy_v3.json` | - |
| 7 | Retraining sensitivity analyses | `python scripts/run_retrain_robustness.py` | `artifacts/reports/robustness_retrain_v1/` | ~10 min per variant |
| 8 | Locked test evaluation (run once) | `python scripts/evaluate_test.py` | `artifacts/reports/test_v1/` | ~45 min |
| 9 | Severity, baselines, decision curves | `python scripts/run_final_analyses.py` | `artifacts/reports/final_analyses_v1/` | ~11 min |
| 10 | EDA (training split) | `python scripts/run_eda.py` | `artifacts/reports/eda_v1/` | ~2 min |
| 11 | CareVue <-> MetaVision transfer | `python scripts/run_system_shift.py` | `artifacts/reports/system_shift_v1/` | ~25 min |
| 12 | Local recalibration (needs step 11) | `python scripts/run_recalibration.py` | `artifacts/reports/system_recalibration_v1/` | ~5 min |
| 13 | Fairness by insurance and ethnicity | `python scripts/run_fairness.py` | `artifacts/reports/fairness_structured_v1/` | ~7 min |
| 13b | GRU go / no-go on validation | `python scripts/run_sequence_model.py` | `artifacts/reports/sequence_model_v1/` | ~11 min |
| 13c | Alert-budget control (group recalibration, certified threshold, parity) | `python scripts/run_alert_budget.py` | `artifacts/reports/alert_budget_v1/` | ~4 min |
| 14 | All report figures | `python scripts/make_report_figures.py` | `artifacts/reports/figures/*.pdf, *.png` | ~1 min |

`pipeline/06_test_evaluation_summary.ipynb` summarises steps 7-8 and `pipeline/07_final_report_analyses.ipynb` summarises
steps 9-14. Both only read the committed aggregate tables. Local run logs are kept in `artifacts/logs/`.

**Sealed records.** Steps 6-13 first write a `prespec.json` that hashes their configuration and code. A rerun with
changed code or configuration stops instead of overwriting; a new analysis needs a new version name. To check that the
committed code still matches every sealed record, run:

```powershell
python scripts/verify_sealed_hashes.py
```

`.gitattributes` pins the line endings of the hashed files, so the check passes on any OS.

**Without MIMIC access**, only the committed aggregate tables in `artifacts/reports/` are available. Step 14 redraws
every figure from them, and the tests in step 3 run on synthetic data.

## 5. Website

```powershell
.\Setup-AKI.cmd          # Python .venv, Node.js if needed, frontend build
.\Start-AKI-Demo.cmd     # four synthetic patients, "+1 h" clock
.\Start-AKI.cmd          # empty local workbench
```

Live scoring needs the locked model folder `artifacts/models/aki_ml_v1_28e0b1fb83/lightgbm__no_dc/`, produced by
step 2. The model is trained on MIMIC and is not in the repository; the website runs without it and shows that no model
is connected. The public read-only demo (`demo-static/`, synthetic patients) needs no model and no backend
([docs/STATIC-DEMO.md](docs/STATIC-DEMO.md)).

Website tests: `.\.venv\Scripts\python -m pip install -r dashboard\requirements-dev.txt`, then
`.\.venv\Scripts\python -m pytest -q dashboard\tests` (40 tests).
