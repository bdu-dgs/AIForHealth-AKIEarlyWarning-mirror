# Feature engine v2 (batch-independent)

## Problem

The original feature engine (`legacy_prefix_v1`, `EventIndex` in `src/aki_ml/features.py`) computes window means,
standard deviations and slopes from prefix sums over all events of a concept in the call, centred on the batch mean.
Floating-point cancellation therefore depends on which other patients, and which of the patient's later rows, are in
the same call. Notebook 01 built the stored features in batches of 4,000 stays, while live scoring sees one patient
with events up to the current time. As a result, the same patient and hour could get slightly different features.
Near a tree split, that can change the risk.

A second, smaller source: notebook 01 stores the admission creatinine baseline as float32, so recomputing
`creatinine_ratio_to_baseline` from the stored column does not reproduce the float64 original.

## Fix

- `patient_window_v2` (`src/aki_ml/feature_windows.py`) computes every window statistic from that window's own
  values only, using each patient's native times. Constant windows give exactly zero standard deviation.
- Admission inputs with the float64 baseline are a separate artifact (`src/aki_ml/admission_inputs.py`), rebuilt from
  LABEVENTS with the original baseline rules and checked against the stored float32 values.
- `compute_features(..., engine_version=...)` selects the engine. The default stays `legacy_prefix_v1`, so notebook 01
  and the fitted model's training data are unchanged.

## Use with the locked weights

`scripts/rescore_validation_feature_v2.py` rescores all 198,524 validation origins with v2 and the unchanged weights and
calibrators. Results are in `artifacts/handoff/feature_v2/`:

| Check | Legacy | v2 |
|---|---:|---:|
| Sampled origins (60 patients) whose features differ between batch and single-patient computation | 1,202 of 2,532 | 0 |
| Same, between batch computation and events cut at each origin | 1,209 of 2,532 | 0 |

Validation AUROC and Brier score are unchanged to 4 decimals for 6/12/24/48 h (AUROC 0.865 / 0.839 / 0.809 / 0.782).
Between 2% and 4% of origins change risk by more than 1e-6; the largest change is 0.087.

`scoring.LockedModel` normally requires the policy's engine to match the fitted model. A different engine is accepted
only when the policy names a `feature_engine_revalidation` report for the same model hash and engine. The locked policy
`artifacts/policy/policy_v3.json` uses this route. `artifacts/demo_i/policy_feature_v2.json` is demo-v1 on v2 features,
and `artifacts/demo_i/policy.json` remains the original Demo I record.

A retrained model would use v2 throughout: notebook 01 with `engine_version="patient_window_v2"` and float64 admission
inputs, then notebook 02.
