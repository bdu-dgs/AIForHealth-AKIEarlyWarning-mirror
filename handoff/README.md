# Collaborator hand-off

In November 2026 two team members received a validation-only package (`AKI_handoff_v1`, built by
`scripts/make_handoff.py` from commit `a0c6906`) to complete two parts of notebook 03. Package instructions are in
[HANDOFF.md](HANDOFF.md); the Docker files and pinned requirements are in this folder.

The package holds the locked model, stored predictions (train out-of-fold and validation) and validation-only
dataset files. It has no raw MIMIC tables, no training feature rows and no test split.

## Task notebooks

| Notebook | Scope | Status |
|---|---|---|
| [KeJin_alert_policy_and_checks.ipynb](../pipeline/KeJin_alert_policy_and_checks.ipynb) | A1-A12 artifact/leakage checks and a lower-burden alert policy | Checks pass. Alert policy v3 (re-run locally, see below) selected a 12 h-confirmed rule that cut false alerts by 46% but was not confirmed (detection loss 5.08 points, limit 5). [Evaluation guide](KEJIN-EVALUATION.md) |
| [Jiaju_stability.ipynb](../pipeline/Jiaju_stability.ipynb) | Calibration, predictor and risk stability | Complete; aggregate results in `artifacts/handoff/jiaju/` and [RESULTS.md](../artifacts/handoff/jiaju/RESULTS.md) |

The collaborators' own alert-policy study (v2) selected on legacy-engine risks and kept its outputs on their
computer. It was superseded by v3, which was run on the full local dataset after the feature-engine fix:

- [FEATURE-REPAIR-V2.md](FEATURE-REPAIR-V2.md): the batch-dependent feature arithmetic, the `patient_window_v2` fix and
  its revalidation with the locked weights (`scripts/rescore_validation_feature_v2.py`).
- [REPLAY-NUMERIC-DIAGNOSIS.md](REPLAY-NUMERIC-DIAGNOSIS.md): the collaborators' original diagnosis (historical).

## Aggregate outputs tracked in Git

| Folder | Content |
|---|---|
| `artifacts/handoff/jiaju/` | Jiaju's stability tables and figures |
| `artifacts/handoff/feature_v2/` | Legacy versus v2 feature-engine comparison on validation |
| `artifacts/handoff/policy_v3/` | Alert policy v3: prespec, checks, selection grid, sealed choice, confirmation, candidate policy |

Patient-level files (predictions, admission inputs, replay features) stay local and ignored.
