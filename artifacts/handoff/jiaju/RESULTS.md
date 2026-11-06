# Jiaju stability: results and interpretation

The fixed LightGBM no_dc model was reproduced on 4,950 validation patients and 198,524 snapshots; all four risk and AUROC assertions passed.

Whole-window validation AUROCs (6/12/24/48h): 0.865, 0.839, 0.809, 0.782.

Prespecified reliability criteria passed in 22/36 horizon-segment cells; 6h: 3/9; 12h: 7/9; 24h: 7/9; 48h: 5/9.

Platt segment AUROC ranged 0.705-0.900; calibration slope 0.771-1.246; O/E 0.519-1.364.

Adjacent-segment predictor Spearman ranged 0.992-0.999; top-20 Jaccard 0.667-1.000 (fixed 1,500-patient sample; 100 patient bootstrap draws).

Platt horizon violation rates: 6->12h: 4.38%; 12->24h: 1.65%; 24->48h: 0.83%.

At the locked 24h threshold 0.21, raw crossings were 0.666/patient-day hourly and 0.885/patient-day event-driven (all-update exposure); these are not cooldown-filtered alert counts.

Feature-distribution shifts include time, measurement availability and changing patient composition; SHAP describes associations, not causal drivers.

Flagged cells require qualification when describing reliability. This validation analysis does not reselect the monitoring window, threshold, calibration or model; policy changes need separate project-owner review.

No test data or patient-level outputs are included; uncertainty is based on patient clusters (200 calibration bootstrap draws).

## Reliability by horizon and ICU segment

| Horizon | Passed segments | Flagged segments |
|---|---|---|
| 6h | 7-12h, 13-18h, 19-24h | 1-3h, 4-6h, 25-36h, 37-48h, 49-60h, 61-72h |
| 12h | 4-6h, 7-12h, 13-18h, 19-24h, 37-48h, 49-60h, 61-72h | 1-3h, 25-36h |
| 24h | 4-6h, 7-12h, 13-18h, 19-24h, 25-36h, 37-48h, 49-60h | 1-3h, 61-72h |
| 48h | 7-12h, 13-18h, 19-24h, 25-36h, 37-48h | 1-3h, 4-6h, 49-60h, 61-72h |

Flags by criterion (a cell may have multiple flags):

- auroc: 6/36 cells.
- calibration_slope: 2/36 cells.
- oe: 8/36 cells.
- positives: 0/36 cells.

## Validation evidence

- Python 3.13.5; all model package versions match notebook pins.
- Original handoff verification: 251/251 files passed before analysis.
- Synthetic regression tests: 8 passed (calibration/reference fit, degenerate data, cluster bootstrap/ties, patient boundaries, all-row monotonicity, flapping/censoring/gaps, event serialization collisions, all-update vs eligible-update scopes).
- Entire completed notebook executed in a fresh Jupyter kernel; outputs cleared in the submission copy.
- No prespecified criteria were changed. No volatility or monotonicity cutoff was invented.
- Detailed estimator definitions and eligibility rules are in the delivery README.

## Event replay serialization checks

Same-second bundles are combined only after exact equality checks on prediction and state fields; measurement counts are summed.

| Horizon | Collision rows | Distinct collision instants | Rows coalesced |
|---|---|---|---|
| 6h | 90 | 45 | 45 |
| 12h | 90 | 45 | 45 |
| 24h | 90 | 45 | 45 |
| 48h | 90 | 45 | 45 |
