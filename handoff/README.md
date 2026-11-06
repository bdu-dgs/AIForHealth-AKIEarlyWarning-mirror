# Imported handoff package

This directory contains the additional team handoff material from `AKI_handoff_v1.rar`. The archive records source commit `a0c6906f9da626b9e5e5210eb60f9724c9ea43fd`; it is a snapshot, not authority to replace the current repository or change research definitions.

## Local layout

- Models and stored predictions: `artifacts/models/aki_ml_v1_28e0b1fb83/` and `artifacts/predictions/`.
- Validation-only data: the existing configured companion directory `../Cleaned/`. Keep the `_validation.parquet` names.
- Import/check reports: ignored `artifacts/handoff-imports/20261105-235915/`.
- Original package instructions: [HANDOFF.md](HANDOFF.md). Current operation and integration are defined by [dashboard/README.md](../dashboard/README.md), [DEVELOPMENT.md](../dashboard/DEVELOPMENT.md) and [DATA-CONTRACT.md](../dashboard/DATA-CONTRACT.md).

The package does not contain raw MIMIC tables, training feature rows or test feature rows. Its patient split manifest includes split assignments, but does not supply held-out test features. The original `dataset_manifest.json` describes the complete dataset; package-level SHA256 checks and `HANDOFF_MANIFEST.json` identify this partial handoff. Do not rename the subset to a full-dataset filename or make the new extension loader silently accept it as complete.

All model, prediction and derived-data artifacts remain local. The current repository ignores `artifacts/handoff/` too; the archive's statement that those outputs are tracked does not describe this checkout.

## Two team task notebooks

| Notebook | Scope | Status |
|---|---|---|
| [KeJin_alert_policy_and_checks.ipynb](../pipeline/KeJin_alert_policy_and_checks.ipynb) | Artifact/leakage checks and a lower-burden alert policy | Full notebook completed; candidate not confirmed and not activated; see [evaluation guide](KEJIN-EVALUATION.md) |
| [Jiaju_stability.ipynb](../pipeline/Jiaju_stability.ipynb) | Calibration, predictor and risk stability | Setup and Part 0 reproduced; later tasks remain TODO |

These are the concrete two-person work assignments supplied by this archive. They are separate from the combined-label/trajectory development extensions in notebooks 04/05. Demo I's train-OOF selection/validation-confirmation protocol is retained as the original experiment; no new threshold or candidate policy was selected during this import.

The later Ke Jin completion uses validation patient roles for selection and confirmation to meet the user requirement, while preserving original Demo I. It exports aggregate results and an unlocked candidate; see [KEJIN-EVALUATION.md](KEJIN-EVALUATION.md). Jiaju tasks remain at their original scaffold status.

## Running locally

The normal project environment matches the required LightGBM, sklearn, NumPy, pandas and Arrow versions. From the repository root, the restored default model can be used with fictional demo patients:

```powershell
.\Start-AKI-Demo.cmd --port 8766
```

Advance to ICU hour 3 for the built-in scenarios' first usable creatinine. An already-running Python service must be restarted to load the restored model and current code.

Notebook setup and Part 0 were executed as Python code cells, not through a Jupyter GUI. JupyterLab and matplotlib were not added during that import stage. The later Ke Jin evaluation added its optional notebook/matplotlib dependencies through `requirements-eval.txt`; see [KEJIN-EVALUATION.md](KEJIN-EVALUATION.md). The supplied `requirements-handoff.txt` describes the original isolated environment and pins pytest 8.3.4, while the main project uses pytest 9.1.1; do not install it wholesale over the current environment.

XGBoost 3.4.1 was installed only in the ignored import report directory's `optional-runtime/` for its smoke check. It was not added to the main environment, and it is not required by the default LightGBM worker.

## Docker configuration

The imported Compose configuration was adapted to mount only this repository and `../Cleaned` separately, with Cleaned read-only, instead of the entire parent folder. Docker was not started or validated during this import.

## Actual checks and limits

See [dashboard/VALIDATION.md](../dashboard/VALIDATION.md) for the aggregate evidence. Model weights, component definitions, the committed Demo I policy, existing runtime source, and daily data were preserved. The packaging helper `scripts/make_handoff.py` was imported but not run; rebuilding a full handoff still requires the original source artifacts.

For a loaded LightGBM Booster, pass `num_threads` to `predict` when limiting test resources. Resetting its parameters triggered a native access violation in the first check; the successful checks used prediction-call options instead and did not save changed model files.

## Later numerical diagnosis

[REPLAY-NUMERIC-DIAGNOSIS.md](REPLAY-NUMERIC-DIAGNOSIS.md) owns the completed post-confirmation investigation and proposed repairs. It preserves the negative candidate result and original policy, and separates numerical/input consistency defects from the later policy-development decision.

## Later numerical diagnosis

[REPLAY-NUMERIC-DIAGNOSIS.md](REPLAY-NUMERIC-DIAGNOSIS.md) owns the completed post-confirmation investigation and proposed repairs. It preserves the negative candidate result and original policy, and separates numerical/input consistency defects from the later policy-development decision.
