# AKI early warning: hand-off package (Ke Jin, Jiaju)

This folder lets you reproduce the current Demo I results and complete two tasks from notebook 03 of the
AI for Health AKI early-warning project. The model is fixed: LightGBM without discharge-coded diagnoses (`no_dc`)
from run `aki_ml_v1_28e0b1fb83`, with Platt calibration. Start with the project README (`AIForHealth-AKIEarlyWarning/README.md`).

| Owner | Notebook | Task |
|---|---|---|
| Ke Jin | `pipeline/KeJin_alert_policy_and_checks.ipynb` | Alert policy v1 (fewer false alerts) and automated artifact and leakage checks |
| Jiaju | `pipeline/Jiaju_stability.ipynb` | Calibration, predictor (TreeSHAP) and risk stability across ICU time and horizons |

Each notebook explains its task, its data roles, and what to hand back. Part 0 of each notebook reproduces the
current results; it must pass before you start new work.

## Data use: read this first

The data are derived from MIMIC-III v1.4 and are covered by the PhysioNet Credentialed Health Data License and
Data Use Agreement. You received them because you hold your own credentialed MIMIC-III access.

- Do not pass the files to anyone else. Keep them on your own encrypted computer, and delete them when the work ends.
- Never upload patient-level data, including to ChatGPT, Claude, Copilot or any other online service.
- Hand back aggregate results only: tables of metrics and figures. Never return rows, IDs or timestamps of
  individual patients.
- The test split is not included. Do not try to obtain or reconstruct it.

## What is in the package

```text
AKI_handoff_v1/
|-- HANDOFF.md                     this file
|-- HANDOFF_MANIFEST.json          source commit, subset definitions, row counts
|-- SHA256SUMS.txt                 checksums of every file
|-- AIForHealth-AKIEarlyWarning/   code at one commit (git repository snapshot)
|   |-- pipeline/                  notebooks, including the two task notebooks
|   |-- src/aki_ml/                shared model and alert code (do not edit)
|   |-- handoff/                   Dockerfile, docker-compose.yml, requirements-handoff.txt
|   `-- artifacts/
|       |-- models/aki_ml_v1_28e0b1fb83/      fitted pipelines and run manifest
|       |-- predictions/                      hourly predictions (train out-of-fold + validation) and event replay (validation)
|       `-- demo_i/                           locked Demo I policy and its validation tables
`-- Cleaned/                       dataset outputs of notebook 01, validation patients only
    |-- datasets/snapshot_dataset_validation.parquet   hourly snapshots with 258 features and labels
    |-- datasets/event_stream_validation.parquet       timestamped model inputs, for leakage checks
    |-- datasets/feature_dictionary.csv
    |-- splits/patient_split.parquet                   patient-to-split assignment (all patients, no features)
    |-- dataset_manifest.json, reports/
```

Not included: raw MIMIC-III, training features, the test split, and model training checkpoints. Notebooks 01 and 02
therefore cannot be re-run from this package. Their outputs are included, so everything downstream can be.

## Setup

1. Copy the whole `AKI_handoff_v1` folder out of OneDrive to a local disk, for example `C:\AKI_handoff_v1`. This
   avoids OneDrive placeholder files and stops your outputs from syncing back.
2. Check that the copy is complete. From the `AKI_handoff_v1` folder, with any Python 3:
   `python AIForHealth-AKIEarlyWarning/scripts/make_handoff.py --verify .`
3. Start the environment using either option below.

**Option A: Docker** (Docker Desktop with WSL 2; 16 GB RAM recommended)

```powershell
cd C:\AKI_handoff_v1\AIForHealth-AKIEarlyWarning\handoff
docker compose up --build
```

Open http://127.0.0.1:8888 and go to `pipeline/`. The first build takes a few minutes. Files you change are saved
directly in your local folder.

**Option B: no Docker** (Python 3.13)

```powershell
cd C:\AKI_handoff_v1\AIForHealth-AKIEarlyWarning
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r handoff\requirements-handoff.txt
.venv\Scripts\python -m jupyter lab
```

The notebooks stop if a pinned package version differs. The pickled model must be loaded with the versions it was
trained with.

## Reproducing the current steps

- **Part 0 of either task notebook:** recomputes the locked `demo-v1` alert metrics (Ke Jin), or rescores every
  validation snapshot with the shipped model and matches the stored risks and AUROCs (Jiaju).
- **`pipeline/demo_i_evaluation.ipynb`:** re-runs the full Demo I selection (monitoring window, policy grid,
  threshold) from the shipped predictions, in about 10 minutes. It rewrites `artifacts/demo_i/`. Do not commit the
  regenerated `policy.json`, because only its timestamp changes.

## Working rules

- Do not edit `src/aki_ml/`. Its hash is recorded with the trained models. Put new code in `src/aki_eval/` and
  tests in `tests/`.
- Write outputs to `artifacts/handoff/kejin/` or `artifacts/handoff/jiaju/` (these folders are tracked by git;
  everything else under `artifacts/` is ignored).
- Hand back through a pull request to the project repository. Include your notebook, `src/aki_eval/`, the tests,
  and your aggregate outputs. Clear any cell output that shows patient-level data before committing.
- Ask the project owner before changing a prespecified rule, a metric definition, or the selection criteria.
