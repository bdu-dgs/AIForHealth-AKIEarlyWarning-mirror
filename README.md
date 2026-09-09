# AI for Health — Dynamic AKI Early Warning

This project develops a reproducible, local-only pipeline for dynamic acute kidney injury (AKI) early warning using MIMIC-III v1.4. The final system will repeatedly estimate a patient's risk of developing AKI within the next 48 hours, apply a validation-selected alert policy, and present the result in an interpretable dashboard.

The project is a clinical decision-support research prototype. It does not recommend treatment and is not intended for clinical deployment.

## Project workflow

```text
MIMIC-III raw data (local only)
        |
        v
Cohort filtering
        |
        v
AKI label generation
        |
        v
Landmark and time-window feature extraction
        |
        v
Multimodal EHR dataset construction
        |
        v
Patient-level train / validation / test split
        |
        v
Independent model training and tuning
        |
        v
Dynamic risk prediction and trajectory generation
        |
        v
Validation-based alert policy selection
        |
        v
Final evaluation, interpretation, and robustness analysis
        |
        v
Streamlit dashboard prototype
```

## Prediction task

- Population: adult patients, first ICU stay per patient, with sufficient follow-up.
- Primary outcome: AKI onset within 48 hours after a prediction cutoff.
- Initial landmarks: 8, 12, and 24 hours after ICU admission.
- Dynamic extension: recompute risk every 1–2 hours.
- Primary label: creatinine-based KDIGO.
- Robustness label: creatinine plus urine-output KDIGO.
- Models: Logistic Regression, Random Forest, and XGBoost or LightGBM, trained and compared independently.

Patients who have already developed AKI by a prediction cutoff are excluded from the risk set at that cutoff. ESKD, dialysis, and other prespecified exclusions must be applied consistently.

## Notebook ownership

Each team member owns one primary notebook to reduce overlapping edits and Git conflicts.

| Notebook | Responsibility |
|---|---|
| `pipeline/01_dataset_construction.ipynb` | Source-table validation, cohort definition, KDIGO labels, landmark targets, feature extraction, and patient-level data splits |
| `pipeline/02_ml_pipeline.ipynb` | Preprocessing, model training, hyperparameter tuning, model persistence, and dynamic risk generation |
| `pipeline/03_evaluation_and_testing.ipynb` | Final metrics, calibration, alert-policy evaluation, temporal tests, SHAP, subgroup analysis, robustness analysis, and dashboard exports |

Each notebook is organized into numbered Parts. Every Part begins with an English Markdown cell describing its objective, rules, inputs, checks, and outputs, followed by an empty code cell for future implementation.

## Data contracts

The notebooks communicate through versioned local files rather than notebook memory. Parquet is preferred for intermediate tables.

| Producer | Suggested output | Unit of observation |
|---|---|---|
| Dataset notebook | `artifacts/datasets/cohort.parquet` | One ICU stay |
| Dataset notebook | `artifacts/datasets/landmark_dataset.parquet` | One ICU stay and prediction cutoff |
| Dataset notebook | `artifacts/splits/patient_split.parquet` | One patient |
| ML notebook | `artifacts/models/<run_id>/` | One fitted model run |
| ML notebook | `artifacts/predictions/<run_id>.parquet` | One model, ICU stay, and snapshot |
| Evaluation notebook | `artifacts/reports/<run_id>/` | Aggregated metrics and figures |
| Evaluation notebook | `artifacts/dashboard/` | Local dashboard-ready records |

Schemas, time units, feature names, label definitions, and run identifiers must remain explicit. A downstream notebook must validate its input schema before processing it.

## Leakage prevention

The central temporal rule is:

```text
feature_time <= prediction_cutoff
label_window = (prediction_cutoff, prediction_cutoff + 48 hours]
```

Additional requirements:

- Urine output after the cutoff may define a future label but must never be used as an input feature.
- Imputation, scaling, feature selection, and calibration must be fitted without test-set information.
- Train, validation, and test partitions are created at the patient (`subject_id`) level.
- Hyperparameters and alert policies are selected using the validation set.
- The test set is reserved for locked final evaluation, subgroup analysis, and robustness analysis.

## Evaluation

Model evaluation includes AUROC, AUPRC, sensitivity, specificity, F1, and calibration when appropriate. Alert-system evaluation additionally includes detection rate, false-alarm rate, and warning lead time. Interpretation includes global feature importance and patient-level SHAP drivers.

## Repository layout

```text
.
├── README.md
├── pyproject.toml
├── .gitignore
├── .env.example
├── configs/
│   └── default.yaml
├── pipeline/
│   ├── 01_dataset_construction.ipynb
│   ├── 02_ml_pipeline.ipynb
│   └── 03_evaluation_and_testing.ipynb
├── dashboard/
│   └── app.py
└── artifacts/
    └── README.md
```

Stable logic should remain in its owning notebook during the initial research phase. A small importable package should be introduced only when code must be shared by multiple notebooks or the dashboard.

## Data and privacy rules

- All MIMIC data must remain on approved local storage.
- Never upload patient-level data to OpenAI APIs or other third-party services.
- Never commit raw data, derived patient-level datasets, predictions, SHAP records, or model weights to GitHub.
- Commit only code, configuration, documentation, aggregate metrics, and verified non-identifying figures.
- Dashboard development must use local data and should use synthetic records when a shareable demo is required.

## Collaboration workflow

- Use one feature branch per task and keep pull requests scoped to one notebook or module.
- The notebook owner resolves changes to their notebook.
- Run the complete notebook from a clean kernel before merging.
- Do not commit large cell outputs or patient-level previews.
- Agree on data schemas before changing a producer notebook.
- Record the configuration, random seed, package versions, and Git commit for each experiment.

## Current status

The repository currently contains the planned layout and notebook documentation only. Dataset construction, model training, and evaluation code will be implemented incrementally after the study definitions and interfaces are confirmed.
