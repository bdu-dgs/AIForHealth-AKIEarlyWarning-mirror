# AI for Health - Dynamic AKI Early Warning

This project builds a reproducible, local-only dynamic acute kidney injury (AKI) early-warning pipeline using MIMIC-III v1.4. It is a research and clinician decision-support prototype, not a treatment recommendation or clinical product.

## Core pipeline

```text
Local MIMIC-III tables
-> adult, first-ICU-stay cohort and exclusions
-> timestamped KDIGO AKI onset
-> hourly prediction snapshots
-> leakage-safe multimodal features
-> patient-level train / validation / test split
-> pooled LR, RF, and boosted-tree model families
-> 6h / 12h / 24h / 48h AKI risks
-> validation-selected model, monitoring window, threshold, and alert policy
-> locked test evaluation at snapshot, patient, and AKI-event levels
-> event-driven dashboard and robustness analyses
```

## Prediction design

- Population: adults, first ICU stay per patient, sufficient follow-up, and prespecified ESKD/dialysis exclusions.
- Sampling: one training snapshot per ICU hour during the eligible monitoring period.
- Prediction targets: AKI within 6, 12, 24, and 48 hours; the operational horizon is selected on validation data.
- Reporting checkpoints: 6, 12, 24, and 48 hours after ICU admission, without training separate checkpoint-specific models.
- Primary label: creatinine-based KDIGO; robustness label: creatinine plus urine-output KDIGO.
- Model families: Logistic Regression, Random Forest, and XGBoost or LightGBM, compared independently.
- Deployment simulation: recompute features and risk whenever a relevant new EHR event arrives; the fitted weights are not updated during prediction.

Each model-family pipeline contains horizon-specific output heads for 6h, 12h, 24h, and 48h. This keeps one reproducible pipeline per family while allowing each horizon to be calibrated and evaluated correctly.

## Temporal rules

For every snapshot and horizon:

```text
feature_time <= snapshot_time
label_window = (snapshot_time, snapshot_time + horizon]
```

- Stop generating snapshots at AKI onset, ICU discharge, or the configured monitoring endpoint.
- Post-snapshot urine may define a future outcome but must never enter the current feature vector.
- Overlapping hourly labels are expected; all snapshots from one patient must remain in one split.
- Imputation, scaling, feature selection, weighting, calibration, and tuning must not use test information.

## Data split and model selection

- Train: fit preprocessing and models; use patient-grouped internal cross-validation for hyperparameters when needed.
- Validation: compare model families, inspect calibration and predictor stability, select the monitoring window, horizon, threshold, and alert policy.
- Test: evaluate the completely locked system once.

Candidate horizons, time segments, and policies should be limited and prespecified to reduce validation overfitting. MIMIC-III dates are shifted independently by patient, so shifted calendar years must not be treated as true chronology. CareVue versus MetaVision can be evaluated as a system-era robustness analysis; external validation on another approved local database is a later extension.

## Evaluation

Snapshot-level evaluation includes AUROC, AUPRC, sensitivity, specificity, F1, Brier score, and calibration curves. Operational evaluation must also report patient/event-level detection rate, first-warning lead time, false alerts per patient-day, repeat-alert burden, and the proportion of patients alerted without future AKI.

Subgroup and robustness analyses include age, sex, ICU type, CareVue versus MetaVision, baseline-creatinine definitions, creatinine-only versus creatinine plus urine-output KDIGO, feature-window choices, and missingness handling.

## Trajectory extension

After the classification MVP, add prediction of future serum creatinine and urine-output trajectories at 6, 12, 24, and 48 hours. Trajectory prediction should begin early in the ICU stay rather than only after a long history is available. Early predictions may be less accurate, so performance and calibration must be reported by `hours_since_icu`; a cold-start strategy may be added if necessary.

## Notebook ownership

| Notebook | Owner responsibility |
|---|---|
| `pipeline/01_dataset_construction.ipynb` | Source validation, cohort, KDIGO timelines, hourly snapshots, multi-horizon labels, features, and patient splits |
| `pipeline/02_ml_pipeline.ipynb` | Preprocessing, patient weighting, three pooled model-family pipelines, calibration, risk outputs, and trajectory extension |
| `pipeline/03_evaluation_and_testing.ipynb` | Leakage tests, validation decisions, locked test evaluation, patient/event metrics, SHAP, robustness, and dashboard exports |

Every numbered Part in each notebook is followed by an empty code cell for implementation. Notebooks exchange versioned local Parquet artifacts rather than in-memory variables.

## Data contracts

| Producer | Local output | Unit of observation |
|---|---|---|
| Dataset notebook | `artifacts/datasets/cohort.parquet` | One ICU stay |
| Dataset notebook | `artifacts/datasets/snapshot_dataset.parquet` | One ICU stay and hourly snapshot, with horizon-specific labels and eligibility flags |
| Dataset notebook | `artifacts/splits/patient_split.parquet` | One patient |
| ML notebook | `artifacts/models/<run_id>/` | One fitted model-family pipeline |
| ML notebook | `artifacts/predictions/<run_id>.parquet` | One model, ICU stay, snapshot, and horizon |
| Evaluation notebook | `artifacts/reports/<run_id>/` | Aggregate metrics and figures |
| Evaluation notebook | `artifacts/dashboard/` | Local dashboard-ready records |

## Repository layout

```text
.
|-- README.md
|-- pyproject.toml
|-- .gitignore
|-- .env.example
|-- configs/
|   `-- default.yaml
|-- pipeline/
|   |-- 01_dataset_construction.ipynb
|   |-- 02_ml_pipeline.ipynb
|   `-- 03_evaluation_and_testing.ipynb
|-- dashboard/
|   `-- app.py
`-- artifacts/
    `-- README.md
```

Stable shared code should be extracted into a Python package only when multiple notebooks or the dashboard genuinely need the same implementation.

## Data and privacy

- Process MIMIC data only on approved local storage.
- Never upload patient-level data to OpenAI APIs or other third parties.
- Never commit raw data, derived patient records, predictions, SHAP records, or model weights.
- Commit only code, configuration, documentation, aggregate results, and verified non-identifying figures.
- A shareable dashboard must use synthetic data.

## Collaboration rules

- One owner per notebook; use scoped branches and pull requests.
- Agree on schemas before changing a producer notebook.
- Run a notebook from a clean kernel before merging and remove patient-level or large cell outputs.
- Record configuration, random seeds, package versions, dataset version, and Git commit for every experiment.

## Method references

- Tomašev et al., continuous AKI risk prediction up to 48 hours: https://doi.org/10.1038/s41586-019-1390-1
- Koyner et al., longitudinal 24-hour and 48-hour AKI prediction: https://doi.org/10.1097/CCM.0000000000003123
- Mohamadlou et al., continuously updated 6-hour and 12-hour ICU-AKI prediction: https://pmc.ncbi.nlm.nih.gov/articles/PMC10121926/
- Kate et al., prediction triggered by AKI-relevant EHR changes: https://doi.org/10.1016/j.compbiomed.2019.103580
- de Hond et al., critical appraisal of AKI prediction models: https://pmc.ncbi.nlm.nih.gov/articles/PMC9664575/
- KDIGO 2012 AKI definition and staging: https://kdigo.org/wp-content/uploads/2016/10/KDIGO-2012-AKI-Guideline-English.pdf
- MIMIC-III v1.4 data description and date shifting: https://physionet.org/content/mimiciii/1.4/

## Current status

The repository currently contains the planned layout and notebook documentation only. Implementation will proceed incrementally after study definitions and interfaces are confirmed.
