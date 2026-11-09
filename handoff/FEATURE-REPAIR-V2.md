# Feature consistency repair v2

## Status (2026-11-09): v2 in use with the locked weights

`scripts/rescore_validation_feature_v2.py` rebuilt the float64 admission baseline from the local LABEVENTS file
(every value matches the stored float32 baseline) and rescored all 198,524 validation origins with the unchanged
weights and calibrators. Report: `artifacts/handoff/feature_v2/rescore_report.json`.

- On 60 sampled patients (2,532 origins), the legacy engine's features differed for 1,202 origins between batch and
  single-patient computation; v2 features were bit-identical for whole batch, single patient and events cut at each
  origin.
- Validation AUROC and Brier score are unchanged to 4 decimals for 6/12/24/48 h; 2-4% of origins change risk by more
  than 1e-6 (largest change 0.087).
- Alert policy v3 selects and confirms on these risks. The website default policy
  `artifacts/demo_i/policy_feature_v2.json` runs demo-v1 on v2 features; `scoring.LockedModel` accepts a policy whose
  engine differs from the fitted one only with a revalidation report for the same model hash.
- The one-off diagnosis/validation scripts referenced below were bound to the collaborators' run directories and have
  been removed.

This document owns the current four software fixes, their integration contracts and repair-validation evidence. [KEJIN-EVALUATION.md](KEJIN-EVALUATION.md) owns the original alert-policy experiment; [REPLAY-NUMERIC-DIAGNOSIS.md](REPLAY-NUMERIC-DIAGNOSIS.md) preserves the pre-repair diagnosis. Website operation remains in [dashboard/README.md](../dashboard/README.md).

## Current status

The arithmetic, admission-input precision, risk-history cache and interrupted-report recovery fixes are implemented and tested. Original study files and all model weights remain unchanged. The fixed 23% / 12h candidate is still unconfirmed; no threshold search, new independent confirmation, model fitting, recalibration or policy activation was performed.

Corrected features have a new explicit contract, `patient_window_v2`. Historical fitted artifacts without an engine declaration continue to mean `legacy_prefix_v1`. This compatibility route intentionally preserves their old input distribution and recorded policy meaning. Software repair does not authorize silently replacing the feature computation of a fitted/validated model.

## Module and version contract

| Location | Responsibility |
|---|---|
| [feature_windows.py](../src/aki_ml/feature_windows.py) | Per-patient native-time bounds and window-local float64 reductions |
| [features.py](../src/aki_ml/features.py) | Shared feature definitions and explicit v1/v2 dispatch; final matrix remains float32 |
| [admission_inputs.py](../src/aki_ml/admission_inputs.py) | Original baseline reconstruction and versioned float64 admission-input artifacts |
| [replay.py](../src/aki_eval/replay.py) | Fixed-origin cutoff replay using an explicit engine/input contract |
| [scoring.py](../src/aki_ml/scoring.py) | Live/batch scoring with matching policy and fitted-model engine declarations |
| [policies.py](../src/aki_eval/policies.py) | History calculated from each scenario's current risk values |
| [study.py](../src/aki_eval/study.py) | Sealed-selection handling and recoverable derived reports |
| [reporting.py](../src/aki_eval/reporting.py) | Aggregate-only report transformations; old CLI imports remain compatible |

### Window arithmetic

v2 uses each patient's actual relative times, rather than a composite floating-point patient/time key. Sum, mean, standard deviation and slope depend only on the selected window's values. Standard deviation and slope center within that window; genuine constant windows produce exactly zero. Genuine small nonzero variation is retained; there is no blanket rounding-to-zero threshold.

Windows and clinical feature definitions are otherwise retained: (origin-W, origin], laboratory availability delay, urine rules, source-view normalization, and the existing normalized stream order for equal-time records. A caller must preserve that equal-time input order; arbitrary reordering of conflicting simultaneous measurements is a separate normalization decision.

`compute_features(..., engine_version=STABLE_FEATURE_ENGINE)` opts into v2. The omitted argument retains v1 for compatibility with existing callers/artifacts. Do not change the default and thereby change old models implicitly.

### Admission inputs

The model feature column `baseline_cr_admission` is float32. It is insufficient as the source for reconstructing `creatinine_ratio_to_baseline`.

v2 replay of that ratio requires a separate `admission_inputs_v2` artifact:

- `inputs.parquet` contains explicit patient/stay/subject keys and admission fields, with the baseline retained as float64.
- `manifest.json` records schema/engine versions, content hash, original baseline rule, and source/configuration provenance.
- The loader checks keys, row count, content hash, baseline dtype and compatibility with the frozen snapshot's exported static fields.
- Missing historical baselines remain missing. Unknown inputs are not replaced by zero.
- No silent fallback from the sidecar to a rounded snapshot denominator is allowed.

The shared `reconstruct_baselines` function reproduces the original source-view float32 normalization, decimal-safe creatinine values, duplicate-time means, pre-ICU seven-day minimum and 8-365-day median fallback. Other admission fields remain supplied by the original snapshot for this repair; their raw clinical availability timing was not independently re-audited.

Example for an authorized local artifact:

```python
from aki_ml.features import STABLE_FEATURE_ENGINE
from aki_eval.replay import ReplayData

replay = ReplayData.load(
    events_path, snapshots, data_config, feature_names,
    engine_version=STABLE_FEATURE_ENGINE,
    admission_inputs=admission_directory,
)
X, scorable = replay.matrix(queries, lag_hours=1, clip="all")
```

### Live model compatibility

A future v2 model must declare `feature_engine_version: patient_window_v2` in both its fitted pipeline metadata and its policy's `model` object. The live loader rejects disagreement. Old artifacts missing both declarations retain v1. Do not edit an old weight file's metadata merely to bypass this check.

The live input builder already retains observation/static numbers as float64. The worker and batch scorer now dispatch through the model's declared engine; v2 model identities also contribute to prediction record keys. No frontend field, timing restriction or display flow was added.

The original locked default LightGBM was loaded successfully after the repair and still selects v1. New v2 feature/prediction exports are research artifacts for model-version review, not an active replacement.

## Risk history and interrupted reports

Rapid-rise rules recompute prior minima from the current scenario's patient/time/risk arrays. Prepared frames drop old `prior_min_*` columns; event calculation does not trust an injected or stale column. A regression covers the previous three-origin failure.

After a choice is sealed, resuming the study:

- Reuses the choice and confirmation, without selecting or confirming again.
- Rebuilds missing/incomplete training-OOF diagnostic grids and summaries.
- Rebuilds missing/incomplete trade-off pictures and selection summaries.
- Writes derived grids/pictures through same-directory temporary files and atomic replacement.
- Recovers a missing candidate export from an existing confirmation.
- Refuses to reconstruct a missing/corrupt sealed selection grid by retuning; restore the original scientific artifact instead.

Tests interrupt after sealing with missing/truncated grids, empty summaries and incomplete PNGs, then verify the protected choice, confirmation, confirmation table and existing export hashes remain unchanged. A second resume reuses the completed diagnostics.

## Recorded validation

Local repair run: `artifacts/feature-repair/repair-v2-20261106-01/`.

- Joint research/backend regression: **127 passed**, with 16 dependency deprecation warnings (backend and plotting libraries). The first full attempt found a diagnostic-clone keyword-default incompatibility; it was repaired before the passing run.
- All 4,950 validation patients / 198,524 hourly origins were processed. The same frozen weights and calibration were used for all four horizons.
- Reconstructed admission inputs retain 4,178 available baselines and 772 missing baselines.
- On a fixed 100-patient / 3,770-origin sample, all 246 features and the calibrated 24h risk were exactly equal across remapped patient subsets, four query chunks, zero-lag event clipping, and added future events of all available concepts plus an unrelated patient. Synthetic tests additionally cover single-patient versus batch calls and the live v2 scoring path.
- The original 800/400 patient samples were tested at fixed 0/1/2h cutoffs. Candidate worst point-warning flip was 4.0601% / 3.9542%; worst patient alert-presence flip was 6.00% / 6.75%; paired coverage stayed at least 94.96%.
- Private artifact roundtrip checks preserved all 56 non-feature parent columns, and 1,024 serialized rows reproduced all four fixed-model risk heads exactly.
- All **137 protected original artifact/data/model files** retained their hashes, including original prespec/choice/confirmation, the original latest-run pointer, weights, predictions and input data.
- The full repair script completed in about 305 seconds with bounded inference threads. It emitted two pandas export-fragmentation performance notices; the validated data and outputs were not affected.

Fixed-candidate confirmation-role sensitivity point estimates:

| Metric | Original saved features | Corrected v2, same weights |
|---|---:|---:|
| Event detection | 73.5593% | 73.5593% |
| Alert PPV | 33.1915% | 33.2620% |
| False alerts / scorable patient-day | 0.245660 | 0.244096 |
| Median correct-alert lead | 12.0928 h | 12.0756 h |

PPV remains below the original 1/3 floor. These are post-repair sensitivity results on already inspected data, not a new confirmation result. Original bootstrap intervals were not recalculated.

Prediction-quality changes were small in this population. At 24h, AUROC was 0.808886 -> 0.808948, Brier 0.090142 -> 0.090140 and ten-bin ECE 0.005508 -> 0.005602. These aggregate diagnostics do not erase the previously observed material individual-risk sensitivity near tree splits.

Three in-process cycles for 100 patients, including current features, all four risk heads and contribution calculations, took 0.461, 0.474 and 0.475 seconds. This is a one-cycle measurement on this computer's sampled histories. HTTP/file ingestion, sustained minute-by-minute operation and denser clinical streams were not load-tested.

## Private outputs and reproduction

Everything under `artifacts/feature-repair/` stays local and ignored by Git. In particular, `private/` contains credentialed patient-level admission inputs, corrected snapshots and fixed-weight predictions. It is not an aggregate report package.

The new dataset explicitly records its feature version and parent dataset; its original labels, origins and target windows are retained. It must not be substituted for old model-training artifacts under the old filenames.

The command used the existing environment:

```powershell
.\.venv\Scripts\python.exe -m pytest tests dashboard\tests -q
.\.venv\Scripts\python.exe scripts\validate_feature_repairs.py --output artifacts\feature-repair\new-run
```

The validation script requires the authorized model/data/raw-lab files and the local protection/frozen-source records from checkpoint `54cf16c`. It refuses to replace an existing output directory. `sources/` preserves the executed repaired source, and the context-loader provenance is recorded separately.

To reproduce the original Ke Jin experiment exactly, use checkpoint `54cf16c` or its frozen source snapshot and its original inputs. Current source hashes deliberately produce a different evaluation-run identity; do not replace the original prespec or confirmation. The task notebook's original code cells remain unchanged, with a Markdown link to this repair record.

## Remaining deployment decision

The four software defects have tested repaired paths. Promotion of v2 to a fitted-model/policy version remains a separate decision: review input-distribution and calibration effects, establish a coherent new feature/model/policy contract, and use the agreed unused-test protocol when the required data are available. Original clinical timing assumptions, final-test evaluation, an active stateful alert adapter and clinical effectiveness remain outside this repair.
