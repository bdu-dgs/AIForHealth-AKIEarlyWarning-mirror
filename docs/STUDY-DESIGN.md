# Study design

The prespecified design of the dataset, prediction task, temporal rules, split and evaluation. It was written before
the models were trained; the realised choices and results are in [FINAL-REPORT.md](FINAL-REPORT.md) and
[FINAL-ANALYSES.md](FINAL-ANALYSES.md). All parameters live in `configs/`.

## Core pipeline

```text
Local MIMIC-III tables
-> validated identifiers and deterministic one-stay-per-patient cohort
-> assign dataset-local patient_id/stay_id after cohort selection
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
- Identity: `subject_id` is the source patient key; `hadm_id` is the hospital admission key; `icustay_id` is the ICU-stay key. After cohort selection, create dataset-local `patient_id` and `stay_id` keys with one-to-one mappings to the selected source IDs.
- Sampling: one training snapshot per ICU hour during the eligible monitoring period.
- Prediction targets: AKI within 6, 12, 24, and 48 hours; the operational horizon is selected on validation data.
- Reporting checkpoints: 6, 12, 24, and 48 hours after ICU admission, without training separate checkpoint-specific models.
- Primary label: creatinine-based KDIGO only. Retain urine-output KDIGO 2012 as a separate alternative endpoint for robustness testing, using prespecified weight, rolling-window, missing-output, and renal-replacement-therapy rules.
- AKI timing: use creatinine-based onset for the primary task and retain urine-output onset separately; do not combine the two definitions or train a combined-label model in the current notebook set.
- Model families: Logistic Regression, Random Forest, and boosted trees (LightGBM as the prespecified primary implementation, XGBoost as a comparator), compared independently. Model settings live in `configs/ml.yaml`.
- Deployment simulation: replay timestamped raw EHR events, recompute the current feature state and risk whenever a relevant event arrives, and keep fitted weights fixed during prediction.

Each model-family pipeline contains horizon-specific output heads for 6h, 12h, 24h, and 48h. This keeps one reproducible pipeline per family while allowing each horizon to be calibrated and evaluated correctly.

## Temporal rules

For every snapshot and horizon:

```text
feature_time <= snapshot_time
label_window = (snapshot_time, snapshot_time + horizon]
```

- Stop generating snapshots at AKI onset, ICU discharge, or the configured monitoring endpoint.
- For each snapshot and horizon, require observable follow-up through the horizon or an observed AKI event; if ICU discharge or another censoring boundary occurs first, mark that horizon ineligible rather than assigning a negative label.
- Apply configured minimum observation/measurement coverage rules separately from outcome eligibility; insufficient data coverage must not be silently treated as a negative outcome.
- Post-snapshot urine may define a future outcome but must never enter the current feature vector.
- Overlapping hourly labels are expected; all snapshots from one patient must remain in one split.
- The unique-stay selection and patient split manifest are frozen before snapshots, labels, or features are generated. A later table join must never re-select a stay using outcome information.
- Imputation, scaling, feature selection, weighting, calibration, and tuning must not use test information.

## Data split and model selection

- First select exactly one eligible ICU stay per patient using a prespecified rule based only on cohort eligibility and source chronology (for example, the earliest eligible ICU stay). Do not choose a stay using AKI outcome, feature completeness, or model performance.
- After cohort selection, create one dataset-local `patient_id` per included `subject_id` and one `stay_id` per selected `icustay_id`; record the crosswalk before splitting.
- Create one reproducible row per patient in the split manifest: `patient_id`, source `subject_id`, selected `icustay_id`, and `split`.
- Generate snapshots, labels, and features only from the selected stay. Every row derived from that patient inherits the manifest assignment.
- Never randomly split snapshot rows or independently split `hadm_id`/`icustay_id` rows after snapshot generation.
- Process large MIMIC event tables in chunks or stay-level partitions and preserve identifiers, timestamps, units, and provenance before aggregation.

- Train: fit preprocessing and models; use patient-grouped internal cross-validation for hyperparameters when needed.
- Validation: compare model families, inspect calibration and predictor stability, select the monitoring window, horizon, threshold, and alert policy.
- Test: evaluate the completely locked system once.

Candidate horizons, time segments, and policies should be limited and prespecified to reduce validation overfitting. MIMIC-III dates are shifted independently by patient, so shifted calendar years must not be treated as true chronology. CareVue versus MetaVision can be evaluated as a system-era robustness analysis; external validation on another approved local database is a later extension.

## Evaluation

Snapshot-level evaluation includes AUROC, AUPRC, sensitivity, specificity, F1, Brier score, and calibration curves. Operational evaluation must also report patient/event-level detection rate, first-warning lead time, false alerts per patient-day, repeat-alert burden, and the proportion of patients alerted without future AKI.

Subgroup and robustness analyses include age, sex, ICU type, CareVue versus MetaVision, baseline-creatinine definitions, creatinine-only versus creatinine plus urine-output KDIGO 2012, feature-window choices, missingness handling, and urine-output measurement/weight sensitivity.

## Deferred extensions

The current scope does not include future creatinine or urine-output trajectory prediction, or training on a combined creatinine-plus-urine-output label. If the creatinine-only model and urine-output robustness analysis justify it, a separate future notebook may define and train a combined-label model.

