# Post-confirmation replay diagnosis and follow-up plan

This document owns the numerical diagnosis and the proposed response to the failed candidate. The original protocol and recorded experiment remain in [KEJIN-EVALUATION.md](KEJIN-EVALUATION.md); executed checks are indexed in [dashboard/VALIDATION.md](../dashboard/VALIDATION.md).

## Decision

The observed differences are real engineering defects in reproducibility. Within the tested controls, they do **not** reverse the original result: the fixed 23% threshold / 12h cooldown candidate remains unconfirmed under the fixed PPV >= 1/3 criterion. Its original validation-selection result also survives correction of the cutoff arithmetic.

These are post-confirmation sensitivity analyses on already viewed validation patients, not a new independent confirmation. No threshold, acceptance criterion, model weight or default website policy was changed.

## Inputs and controls

The frozen source run is `kejin-validation-policy-v2-21d980db7c292219`. All 4,950 validation patients and 198,524 hourly origins were compared using the same model, feature order, calibration, origins, outcomes and original patient roles. Saved features reproduce saved risks exactly.

The independent arithmetic comparator uses native per-patient times and float64 calculations on the actual query window. It does not use a global cumulative sum or a global centering value to calculate window statistics. It is a slow diagnostic oracle, not an installed production replacement. Its function uses a separate globals mapping; the original feature engine is not patched in memory or on disk.

Additional controls hold only the saved zero-lag creatinine ratio fixed, or reconstruct the original high-precision pre-ICU baseline where possible. Holding a saved ratio fixed is an attribution control only; no such control is used for delayed inputs, where the current creatinine value legitimately changes.

Detailed aggregate outputs are local and ignored by Git under `artifacts/handoff/kejin/numeric-diagnosis-20261106-01/`. That directory contains plans, manifests, tables and a preserved copy of the initially executed cause-control helper. The corrected tree trace has its own manifest. No patient keys, patient times, raw measurements or model trees are exported.

## Located causes

### 1. Cross-patient cumulative arithmetic and near-zero tree splits

[EventIndex](../src/aki_ml/features.py) centers values using the complete concept batch and builds prefix sums across stays. Window means, sample standard deviations and slopes use subtraction of cumulative totals. Although the bounds select the appropriate past window, changing unrelated patients or future rows can change floating-point cancellation. Casting the final features to float32 does not remove every difference.

On 512 real constant-SpO2 windows, the exact window-local standard deviation is zero. The existing arithmetic reported a small nonzero standard deviation in 49 windows with the whole validation batch, 126 with the patient's complete history, and 23 with only that patient's past. The observed spurious standard deviations were at most about 1.9e-5. This demonstrates dependence on the surrounding calculation context.

Actual model-path tracing, using float64 values and float64 thresholds as LightGBM does, was independently checked against `predict(pred_leaf=True)`: all 17,868 changed tree paths agree with changed leaf outputs on the 6,503 changed-risk rows in the full-batch control. It locates divergent paths at near-zero `spo2_std_24h` and `creatinine_slope_48h` splits. Other identified paths involve the admission-creatinine ratio, temperature means and a small number of blood-pressure statistics. Path counts in the local table are tree-path counts, not unique clinical rows.

The original hourly construction processes partitions of approximately 4,000 stays; later replay uses other batches and/or origin-wise event truncation. Thus even identical measurement values can take a different arithmetic path. Feature-level rtol=1e-5 / atol=1e-4 is insufficient as an acceptance test for downstream risk or alert equivalence.

### 2. Loss of admission-static input precision

The construction notebook calculates `creatinine_ratio_to_baseline` using an admission baseline that can retain float64 precision. The snapshot then saves the baseline feature as float32. Replay reconstructs its static inputs from that rounded snapshot column, so its denominator differs from the one used during construction.

For 3,956 patients / 160,682 origins, the original 7-day pre-ICU minimum can be reconstructed from the exported creatinine events using the original decimal-rounding and duplicate-mean rules. Every reconstructed baseline agrees with the saved baseline after conversion to float32; every reconstructed ratio matches the saved ratio exactly. This isolates the precision path for those patients.

The seven-day handoff export alone cannot recover remote-history medians. A subsequent read-only scan of the existing local `MIMIC-III/LABEVENTS.csv.gz` recovered the complete original baseline rules: 3,956 patients use the pre-ICU seven-day minimum, 222 use the 8-365-day historical median, and 772 have no baseline. Across all 4,950 patients, there are zero baseline mismatches after the original float32 export, and all 198,524 reconstructed ratio rows match the saved feature exactly (including matching missingness). Thus this baseline precision can be restored from files already present locally; it is not a request for new data. This establishes consistency for this baseline calculation, while source/availability timing of other admission inputs remains outside this diagnosis.

### 3. A separate rapid-rise replay-cache defect

The cutoff helper replaces a prepared frame's risk column while retaining its cached `prior_min_12` values. A rapid-rise rule can therefore use a history from the original risks. A three-origin synthetic reproduction emits one event with the stale cache and three after history is recomputed.

This affects the rapid-rise candidate's cutoff diagnostics; it does not affect the selected `cool12` rule, which does not use the cache. At the original point criteria, `rapid_rise / 0.21` has a higher false-alert objective (0.277668 versus 0.238651 for `cool12 / 0.23`), so changing its stability eligibility cannot displace the already feasible, better candidate. Repair this cache before any future search.

## Effect on the recorded conclusions

### Patient-level outcome comparison

The following are confirmation-role point estimates for the fixed candidate:

| Input/computation | Alert PPV | Target-event detection | False alerts / scorable patient-day |
|---|---:|---:|---:|
| Original saved features | 33.1915% | 73.5593% | 0.245660 |
| Existing engine, full batch | 33.2265% | 73.5593% | 0.244487 |
| Existing engine, origin-wise zero-lag cutoff | 33.1910% | 73.5593% | 0.244878 |
| Independent window-local arithmetic | 33.2620% | 73.5593% | 0.244096 |

The isolated statistic-group, held-static-ratio and reconstructed-static controls also remain below 33.3333%. The worst and best tested PPV values are approximately 33.19% and 33.26%. Thus numerical differences do not explain away the failed PPV gate. The margin is small; report failure under the stated acceptance rule, without claiming general clinical ineffectiveness.

The false-alert reduction and event-detection tradeoff also remain: under each coherent representation the candidate reduces false alerts by about 42% relative to the same-representation baseline, while detection changes from 76.9492% to 73.5593%. New bootstrap intervals were not generated for these sensitivity representations; original intervals still describe the original representation.

Individual outputs are nevertheless affected. Full-batch replay differs from saved risk by >1e-6 at 6,503 origins (3.28%); origin-wise zero-lag replay differs at 7,134 (3.59%). The largest observed absolute change across the tested full-population controls is about 0.0867, or 8.67 percentage points. At the 23% threshold, origin-wise zero-lag replay changes 83 of 198,524 point-warning states. Event timing can also change through cooldown propagation. Stable aggregate decisions do not establish safe interchangeability of these computations.

### Cutoff stability and original selection

The original 800-selection / 400-confirmation patient samples, original seeds, fixed origins/windows and 0/1/2h measurement cutoffs were replayed with independent window-local arithmetic and the complete original admission-baseline precision reconstructed from local raw labs. Both saved-snapshot and zero-lag reference bases were retained. The earlier pre-ICU-only control and its source remain preserved separately; the full-static control gives the same gate/ranking results shown below.

| Role | Worst point-warning flip | Worst patient alert-presence flip | Minimum paired coverage | Original stability gates |
|---|---:|---:|---:|---|
| Selection | 4.0700% | 6.00% | 94.9643% | Pass |
| Confirmation | 3.9542% | 6.75% | 94.9997% | Pass |

The original gates are <=10%, <=10%, and >=85%, respectively. The candidate still satisfies them.

There are eight configurations satisfying the original saved-snapshot point criteria. The sealed candidate already has the best false-alert objective among all eight, even before stability exclusion. Since it remains stability-feasible, correcting cutoff arithmetic cannot change that original selection. This is a logical audit using the original point metrics, not a new search on confirmation data. Choosing policies on newly recomputed point predictions would be a separate experiment.

## Follow-up plan after non-confirmation

1. **Preserve the negative result now.** Keep the original choice, confirmation, acceptance table and provenance unchanged. Keep the candidate unlocked and inactive; retain the current website policy. Do not lower the PPV floor, change the threshold, extend cooldown, or select an alternative using the observed confirmation outcomes.
2. **Create one versioned feature contract shared by construction and live scoring.** Use native per-patient timestamps and deterministic duplicate/tie rules. Keep required admission-static inputs at their declared precision rather than reconstructing them from rounded model-feature columns. Reuse the verified local raw labs to reconstruct and save the admission baseline at original precision; preserve the 772 genuinely missing baselines without imputation. Audit the source/availability timing of other static inputs separately.
3. **Repair and validate arithmetic before policy work.** Use the window-local calculation as the correctness reference. Require the same patient/history/query to produce the same stored feature matrix, model risks and alert events across single-patient versus batched calls, chunk sizes and irrelevant/future event additions. Cover genuine constant windows and near-zero tree splits. Any optimization must preserve these results; per-patient centering alone is insufficient.
4. **Repair derived-risk caches.** Recompute prior-risk minima for every risk variant, or remove invalidatable cached columns from the prepared-frame interface. Add a regression against direct recomputation before reusing rapid-rise stability results.
5. **Regenerate a new, explicitly versioned development run.** Do not overwrite the old snapshots or predictions. Initially keep the model fixed and quantify changed features, risk calibration, discrimination, alert burden, detection and cutoff stability. If correcting features materially changes the model's input distribution or calibration, coordinate a new model/calibration version with the model owner; do not silently change only the inference side. Benchmark the corrected path for the intended <100 patients / approximately one update per minute workload.
6. **Decide whether another policy-development cycle is warranted.** The current course result can be handed back as a documented non-confirmed candidate. If lower alarm burden is still required, agree on a new labelled protocol, develop thresholds and rules on validation development data, and combine cutoff stability with selection again. Previously inspected confirmation patients are already-used evidence. A final once-only evaluation needs available, genuinely unused test data after the entire feature/model/policy stack is frozen and responsibilities are agreed.

The immediate next implementation scope is steps 2-4: feature/input consistency and replay-cache repair, with regression checks. Model retraining, a new policy search, final-test access and activation are subsequent decisions, not work completed by this diagnosis.

A subsequent working-tree review reproduced an additional interrupted-run recovery issue in [KeJinStudy.select](../src/aki_eval/study.py): after `choice.json` is sealed, a process can stop before OOF diagnostic tables/figures are written. The cached-choice path currently returns without rebuilding missing reports; the CLI's report finalizer can then fail on the missing OOF grid. This does not affect the recorded completed run. Harden report recovery and add an interrupted-run regression without unlocking the choice or repeating confirmation. This repair is pending, alongside the feature and cache fixes.


## Reproduction and evidence

The standalone scripts read the original study as frozen inputs and write only new aggregate diagnostics:

- [diagnose_feature_replay.py](../scripts/diagnose_feature_replay.py): population-wide saved/replayed/local-arithmetic comparisons and feature-group attribution.
- [inspect_replay_numeric_causes.py](../scripts/inspect_replay_numeric_causes.py): static precision, same-window context and tree-path controls.
- [check_replay_stability_sensitivity.py](../scripts/check_replay_stability_sensitivity.py): the sealed candidate's original cutoff samples and original gate/ranking audit.
- [verify_raw_admission_baselines.py](../scripts/verify_raw_admission_baselines.py): verifies the full baseline and ratio reconstruction from the existing local raw lab file, exporting only aggregate counts.
- [test_feature_replay_diagnostic.py](../tests/test_feature_replay_diagnostic.py): six synthetic checks of hand-computable statistics, native time bounds, future/batch independence and double-precision model-split tracing.

Use the existing project environment. The main diagnostic accepts a fresh `--output` directory and refuses to replace an existing one. The follow-up investigation scripts currently target the recorded local diagnostic directory; they are investigation helpers, not part of website startup or the official selection workflow. Their aggregate results and source hashes are recorded separately.

On 2026-11-05 local time, the population diagnosis, causal controls, full raw-baseline reconstruction and full-static cutoff sensitivity scripts all completed. The six new tests passed. Original prespecification, choice, confirmation, model, source feature engine, default policy and `LATEST.json` were preserved. No website process, patient database, dependencies, commit or GitHub remote was changed.
