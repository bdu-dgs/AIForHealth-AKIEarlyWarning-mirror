# Research extension development guide

This document owns offline extension definitions, module interfaces and run procedures. The root [README](../README.md) owns overall research status; [dashboard/README.md](../dashboard/README.md) owns website operation; [DATA-CONTRACT.md](../dashboard/DATA-CONTRACT.md) owns live JSON fields. Executed checks belong in [VALIDATION.md](../dashboard/VALIDATION.md).

## Scope and evidence

Notebooks 04 and 05 are independent development experiments. Notebook 03 now has an executable small-baseline path for integrity checks, validation metrics, threshold selection and cutoff stability. Full study objectives, final test evaluation, uncertainty intervals, subgroups and clinical validation remain unfinished.

The default data are entirely synthetic. These runs do not reproduce notebook 02 or Demo I, replace their weights or 21% policy, or establish patient-level performance. Real-data execution requires authorized notebook 01 artifacts, which were unavailable during this implementation check. Original notebooks 01/02 and the committed Demo I policy remain unchanged.

The later authorized handoff restored the frozen Demo I weights and validation-only inputs locally; see [handoff/README.md](../handoff/README.md). That subset supports reproduction of stored validation predictions, but does not include the training features or complete cohort artifacts required for real-data training of the new combined-label and trajectory baselines. Their current software checks still use synthetic defaults.

## Module boundaries

| Module in `src/aki_ml/` | Responsibility |
|---|---|
| `research_data.py` | Artifact hashes, fixed patient splits, narrow input columns, development subset and existing as-of engine |
| `targets.py` | Combined observed-onset labels and at-risk snapshots |
| `training.py` | LR baseline, fold-local preprocessing and patient-grouped training OOF calibration |
| `trajectory.py` | Future measurement matching, density audit, persistence comparator and ridge heads |
| `evaluation.py` | Validation thresholds, fixed-origin cutoff comparisons, metrics and first-warning lead |
| `experiments.py` | Compose modules, record provenance and export local results |
| `target_export.py` | Bounded Parquet batches from a complete standardized lab view to separate future truth |
| `baselines.py` | Runtime risk adapter and independently versioned trajectory composition |
| `synthetic.py` | Deterministic fictitious fixtures only |

Notebooks 03/04/05 and `scripts/run_extensions.py` are thin orchestration entry points. They reuse `models.py` and `features.py`. The baseline inputs are ICU origin hour, last creatinine, prior creatinine change and last heart rate. A different feature set requires updating the whitelist, cutoff semantics, feature order and tests together. Imputation/scaling fit training rows only. These estimator settings are development defaults.

## Combined target

Configuration: [combined_aki.yaml](../configs/combined_aki.yaml).

- The earliest observed creatinine or urine-output onset is the combined event.
- Remove origins at or after either known onset or discharge/death/RRT censoring.
- Positive window: `(origin, origin + horizon]`, with the event strictly before censoring.
- A negative needs complete follow-up and both original component eligibility flags with negative labels. Insufficient urine-output records stay unknown.
- A known positive establishes the union when the other endpoint is unknown. This does not prove undocumented earlier AKI was absent.
- Derived columns are `combined_at_risk`, `combined_onset_hours`, `eligible_combined_<H>h` and `aki_combined_within_<H>h`. Inputs are not overwritten.
- Reuse the original selected cohort and available creatinine-based snapshots; this does not expand to a new all-ICU cohort.

Every row inherits the original patient split. Training and preprocessing use eligible training rows. Platt calibration uses patient-grouped out-of-fold training probabilities. Validation supplies threshold selection. The runners do not evaluate test patients.

## Trajectory target and density

Configuration: [trajectory.yaml](../configs/trajectory.yaml).

The initial configurable candidate is creatinine in mg/dL at +6/+12/+24 h with +/-2 h matching tolerance. Revisit these prototype settings after the real density audit; they do not constrain the frontend.

- Origins precede either known AKI onset and censoring.
- Future truth may follow AKI onset but must precede discharge, death or RRT.
- Match the nearest real measurement within tolerance; a tie chooses the earlier draw.
- Keep requested target time, actual measurement time, value, eligibility and failure reason. Do not interpolate truth.
- A measurement at/before origin is never future truth.
- If the matching window extends beyond the declared export endpoint, mark `export_window_truncated`, even if a nearby exported measurement exists.
- Report requested/observed counts, matching offsets, reasons and coverage by split and lead.
- Compare persistence and ridge on the same observed targets; missing truth remains in the coverage denominator.
- No uncertainty interval or composite confidence score is estimated by the baseline.

### Separate future-truth extraction

Notebook 01's `event_stream.parquet` stops at the monitoring endpoint, currently hour 72. It cannot establish complete truth for all later target windows.

Use the complete standardized lab view identified by notebook 01's `VIEWS["lab"]`, not raw CSV or the truncated event export. The exporter reads bounded batches, maps the original selected stay via `subject_id` and ICU-relative time, excludes test patients, and applies censoring. It does not alter notebook 01 or raw data.

Replace the example source path with the actual complete standardized view:

```powershell
.\.venv\Scripts\python.exe scripts/export_trajectory_targets.py --data-root ..\Cleaned --lab-view artifacts\interim\ACTUAL_COMPLETE_LAB_VIEW.parquet --output artifacts\trajectory-targets\run01\target_events.parquet --end-hour 98 --complete-lab-view
```

Hour 98 is 72 + 24 + 2 for the current configuration. The flag asserts source completeness, not measurement density; never use it for a truncated source.

Parquet fields: `stay_id`, `hours_since_icu`, `concept`, `value`, `source_itemid`. The adjacent same-stem JSON records dataset version, file/source/cohort hashes, units, extraction endpoint, split scope and completeness. The loader verifies these. Existing exports are preserved; a failed export may leave a `.partial` file, so fix the failure and choose a new output path.

## Cutoff stability and threshold selection

Fix patient, prediction origin and future outcome window. Recompute inputs after withholding observations after `origin - lag`; current lag candidates are 1 and 2 hours. The existing lab-result delay still applies, while the origin-hour feature remains fixed. Only last-value/delta features are used, avoiding accidentally recentered rolling windows. This tests cutoff sensitivity, not every possible charting or revision delay.

Report probability/trajectory change and paired coverage. For each threshold report warning flips. Missing prediction coverage is not treated as stability.

Rank the finite validation threshold grid by F1 minus a configured stability penalty, subject to sensitivity, worst warning-flip and paired-coverage constraints. The selected threshold references the stability assessment ID. If no candidate qualifies, no threshold is locked. These are provisional engineering criteria.

First-correct-warning lead and detectable-event coverage are also reported. Do not infer patient-days from sparse snapshots or claim cooldown-adjusted alert burden. Full monitoring/cooldown selection, confidence intervals and the scientific protocol lock remain pending.

Demo I's historical train-OOF-selection/validation-confirmation experiment is unchanged. The new baseline path implements validation selection; it does not retrospectively change Demo I.

## Run and reproduce

After normal source setup, from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts/run_extensions.py --synthetic --task all
```

Choose `--task primary`, `combined` or `trajectory` for one path. Notebooks default to `MODE = "synthetic"` and `EXPORT = False`. To use approved local artifacts:

```powershell
.\.venv\Scripts\python.exe scripts/run_extensions.py --data-root ..\Cleaned --task combined
.\.venv\Scripts\python.exe scripts/run_extensions.py --data-root ..\Cleaned --task trajectory --trajectory-source artifacts\trajectory-targets\run01\target_events.parquet
```

Input hashes and config are checked before fitting. Patient-level test snapshots/events are not loaded for modelling. Only required baseline columns and concepts are read; the separate target exporter is opt-in.

Each export creates a new timestamp-suffixed directory under ignored `artifacts/extensions/`: model, aggregate report, manifest, and optional classification policy. Provenance includes dataset/config/source hashes, feature order, software versions, Git commit when available and model hash. Source hashes distinguish uncommitted changes at the same commit. No row-level predictions are exported by default. See [artifact inventory](../artifacts/README.md).

Load only trusted local joblib files; hash equality does not make an unknown executable serialization safe.

## Frozen-model alert-policy work

Ke Jin’s independent evaluation is implemented in [src/aki_eval](../src/aki_eval/) and documented in [handoff/KEJIN-EVALUATION.md](../handoff/KEJIN-EVALUATION.md). It audits the restored data, selects a threshold/policy on a validation patient role with cutoff controls, and confirms once on the other role. The 23%/12h candidate reduced false-alert burden but failed its confirmation PPV floor; it was not deployed. This does not complete Jiaju’s calibration/SHAP work, the new-model training extensions or final test evaluation.

## Later model replacement

Replace the estimator/training module while preserving target definitions, split inheritance, feature order and provenance. Evaluate each version on validation before policy lock. Standalone trajectories remain regression outputs; they do not invent AKI risk to satisfy a website schema. Website composition is in [DEVELOPMENT.md](../dashboard/DEVELOPMENT.md).

Before a scientific performance claim: audit actual density and availability, finalize target/censor rules, compare/calibrate candidates, lock the complete protocol and evaluate untouched test patients. Implemented software and synthetic checks are separate evidence.
