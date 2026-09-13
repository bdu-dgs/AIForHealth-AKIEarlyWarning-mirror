# AI for Health - Dynamic AKI Early Warning

This project develops a reproducible, local-only dynamic acute kidney injury (AKI) early-warning research pipeline and an ICU-facing course-project website. It is a research and decision-support prototype, not a treatment recommendation or validated clinical product.

The sections below describe the planned research pipeline. They are study-design targets rather than completed model results and do not restrict the website to fixed sampling intervals or prediction horizons.

## Documentation

| Document | Scope |
|---|---|
| This README | Research objective, candidate study design, notebook ownership, privacy, and repository-wide status |
| [dashboard/README.md](dashboard/README.md) | Website installation, startup, daily use, and local CSV preview |
| [dashboard/DEVELOPMENT.md](dashboard/DEVELOPMENT.md) | Website architecture, backend/model integration, and development workflow |
| [dashboard/DATA-CONTRACT.md](dashboard/DATA-CONTRACT.md) | Normative live JSON field and time contract |
| [dashboard/VALIDATION.md](dashboard/VALIDATION.md) | Tests actually run and their limits |
| [dashboard/HANDOFF-CHECK.md](dashboard/HANDOFF-CHECK.md) | Historical comparison between the handoff summary and repository |

## Core pipeline

```text
Local MIMIC-III tables
-> validated identifiers and deterministic one-stay-per-patient cohort
-> patient-level development split manifest (before snapshots)
-> timestamped KDIGO AKI onset
-> hourly prediction snapshots
-> leakage-safe multimodal features
-> fixed train / validation / test assignment inherited by every snapshot
-> pooled LR, RF, and boosted-tree model families
-> 6h / 12h / 24h / 48h AKI risks
-> validation-selected model, monitoring window, threshold, and alert policy
-> locked test evaluation at snapshot, patient, and AKI-event levels
-> event-driven dashboard and robustness analyses
```

## Prediction design

- Population: adults, one deterministically selected eligible ICU stay per patient, sufficient follow-up, and prespecified ESKD/dialysis exclusions.
- Identity: `subject_id` is the source patient key; `hadm_id` is the hospital admission key; `icustay_id` is the ICU-stay key. A local `patient_id` surrogate may be added for modeling/export, but it must map one-to-one to `subject_id` within a dataset version.
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
- The unique-stay selection and patient split manifest are frozen before snapshots, labels, or features are generated. A later table join must never re-select a stay using outcome information.
- Imputation, scaling, feature selection, weighting, calibration, and tuning must not use test information.

## Data split and model selection

- First select exactly one eligible ICU stay per patient using a prespecified rule based only on cohort eligibility and source chronology (for example, the earliest eligible ICU stay). Do not choose a stay using AKI outcome, feature completeness, or model performance.
- Create one reproducible row per patient in the split manifest: `patient_id`, source `subject_id`, selected `icustay_id`, and `split`.
- Generate snapshots, labels, and features only from the selected stay. Every row derived from that patient inherits the manifest assignment.
- Never randomly split snapshot rows or independently split `hadm_id`/`icustay_id` rows after snapshot generation.

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
| `pipeline/01_dataset_construction.ipynb` | Source validation, ID crosswalk, one-stay-per-patient cohort, pre-snapshot patient splits, KDIGO timelines, hourly snapshots, multi-horizon labels, and features |
| `pipeline/02_ml_pipeline.ipynb` | Preprocessing, patient weighting, three pooled model-family pipelines, calibration, risk outputs, and trajectory extension |
| `pipeline/03_evaluation_and_testing.ipynb` | Leakage tests, validation decisions, locked test evaluation, patient/event metrics, SHAP, robustness, and dashboard exports |

Every numbered Part in each notebook is followed by an empty code cell for implementation. Notebooks exchange versioned local Parquet artifacts rather than in-memory variables.

## Planned research artifact contracts

| Producer | Local output | Unit of observation |
|---|---|---|
| Dataset notebook | `artifacts/datasets/cohort.parquet` | One selected ICU stay per patient, with source IDs and dataset-local patient ID |
| Dataset notebook | `artifacts/datasets/snapshot_dataset.parquet` | One ICU stay and hourly snapshot, with horizon-specific labels and eligibility flags |
| Dataset notebook | `artifacts/splits/patient_split.parquet` | One patient and its selected ICU stay, assigned before snapshots |
| ML notebook | `artifacts/models/<run_id>/` | One fitted model-family pipeline |
| ML notebook | `artifacts/predictions/<run_id>.parquet` | One model, ICU stay, snapshot, and horizon |
| Evaluation notebook | `artifacts/reports/<run_id>/` | Aggregate metrics and figures |
| Evaluation notebook | `artifacts/dashboard/` | Local dashboard-ready records |

These planned Parquet and experiment artifacts are separate from the website's live JSON exchange contract. The live contract is defined only in [dashboard/DATA-CONTRACT.md](dashboard/DATA-CONTRACT.md).

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
|   |-- app.py                 # preserved original placeholder
|   |-- README.md              # website operation
|   |-- DEVELOPMENT.md         # developer and backend integration guide
|   |-- DATA-CONTRACT.md       # live JSON contract
|   |-- api/                   # FastAPI, SQLite, file ingestion
|   |-- web/                   # React and TypeScript frontend
|   `-- tests/                 # synthetic backend tests
|-- filter/                     # separately added cohort/filter prototype
|-- Setup-AKI.cmd
|-- Start-AKI.cmd
|-- scripts/
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

The research notebooks still contain structure and documentation rather than an implemented AKI model. No model training, cutoff-stability result, validation-selected threshold, calibration result, or clinical validation is currently available.

The local working tree contains a runnable website with patient registration, append-only observations and revisions, file exchange, historical replay, local SQLite storage, SSE refresh, and a read-only MIMIC CSV subset preview. The website keeps predictions empty until a model is connected through the documented contract. Current test evidence and limitations are recorded in [dashboard/VALIDATION.md](dashboard/VALIDATION.md).

Local website changes do not become available from GitHub until they are explicitly committed and pushed. Windows EXE, installer, and portable runtime packaging have not been built.
