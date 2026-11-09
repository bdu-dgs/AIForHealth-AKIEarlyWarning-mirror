# Validation Record

This document records checks actually executed, the environment, results, and applicable boundaries. Run methods and the development acceptance checklist are in [DEVELOPMENT.md](DEVELOPMENT.md); field rules are in [DATA-CONTRACT.md](DATA-CONTRACT.md).

## Current validation summary

| Date | Check | Result | Coverage |
|---|---|---|---|
| 2026-09-10 | Backend automation | 19 passed | Append, revision, transactions, replay, fingerprints, file watching, pagination, volumes, photos, and request validation |
| 2026-09-10 | Frontend contract | 3 passed | Missing/unlocked thresholds, comparison operators, stale input, expired windows, model versions, and horizons |
| 2026-09-10 | Clean source installation and HTTP | Passed | Fresh-copy installation, build, single-process page/API, and static assets |
| 2026-09-10 | File events and SSE | Passed | Synthetic observation file import, revision notification, and query |
| 2026-09-12 | Backend automation | 25 passed, 2 warnings | Original 19 checks plus 6 CSV read-only preview tests |
| 2026-09-12 | Frontend tests and production build | 3 passed; build passed | Node tests, TypeScript, and Vite production assets |
| 2026-09-12 | Local HTTP and browser | Passed | Health, CSV API, dataset route, horizontal cards, switching, details, pagination, and search |
| 2026-09-12 | Git ignore and dependency rebuild | Passed | Dataset ignored; renamed dependencies rebuilt from files in the current directory |
| 2026-10-07 | Backend automation | 28 passed | Earlier checks plus locked-threshold overview preference, demo playback (frozen clock, hour-by-hour release), and model heartbeat status |
| 2026-10-07 | Frontend tests and production build | 4 passed; build passed | Alert strings in English, default locked-threshold series, TypeScript, and Vite production assets |
| 2026-10-07 | English-only interface | Passed | No Chinese characters in tracked dashboard sources or the built `dist/` output |
| 2026-10-08 | Live model end to end (demo playback, synthetic patients) | Passed | See "Live model and demo playback checks" below |
| 2026-10-10 | English-only browser controls | Passed; frontend tests 6 passed, build passed | File pickers, date-time fields, and form-validation messages no longer use browser-native widgets that follow the OS language; Data entry page checked in Chrome with `--lang=zh-CN` |
| 2026-11-04 | Backend and scoring regressions | 36 passed, 2 dependency warnings | Previous 28 checks plus 8 synthetic time-boundary regressions; no model weights loaded |
| 2026-11-04 | Frontend tests and production build | 7 passed; TypeScript and build passed | Existing UI contracts remain compatible with the runtime fixes |

The two backend warnings on 2026-09-12 were future-compatibility deprecation notices from Starlette TestClient for httpx and AnyIO BlockingPortal aliases; the tests themselves passed.

## Local handoff import — 2026-11-05

Source: `AKI_handoff_v1.rar`, recording archive code commit `a0c6906f9da626b9e5e5210eb60f9724c9ea43fd`. Current code baseline: `cb16d7d`. The package was extracted to a separate temporary folder; all 251 listed checksums passed. The 23 differing tracked files were preserved locally, and the archive's old code/configuration did not replace current runtime changes.

- Restored 5 model-family directories, stored predictions, aggregate policy reports and the 8-file validation companion dataset. Model/data artifacts are ignored or outside the repository. No GitHub upload was performed as part of this import.
- The default LightGBM pipeline and feature-order hashes match the committed `demo-v1` policy. Model containers, feature engine, alert helpers and core configs match the archive. Current metadata provides 246 deployed features and 6/12/24/48-hour heads.
- All 5 model families loaded and matched stored Platt risks on 8 validation snapshots per horizon; maximum difference was approximately `2.98e-8`. XGBoost 3.4.1 was installed in a separate ignored target directory, leaving the project environment unchanged.
- Ke Jin's setup and Part 0 reproduced the frozen train-OOF and validation alert metrics.
- Jiaju's setup and Part 0 rescored all 198,524 validation snapshots for 4,950 patients. All four stored risk arrays matched exactly; validation AUROC was 0.865 / 0.839 / 0.809 / 0.782. This reproduces the named historical experiment, not an untouched final test.
- Backend: 54 passed, with the 2 known dependency warnings. Frontend: 8 passed; TypeScript and production build passed.
- An isolated service on port 8767 used 4 fictional patients with the restored real LightGBM model. Homepage, 4 horizons, 5 contributors, input-file-triggered rescoring, freshness/fingerprint matching and earlier replay boundaries passed. Watcher errors and pending file writes were zero.
- Browser inspection confirmed the real model heartbeat, risk display and 5 contributors. The existing service and daily database were not restarted or changed.

The first smoke check hit a native LightGBM access violation while calling `reset_parameter` on a loaded Booster. Successful checks used `predict(..., num_threads=2)` instead; no model file or learned parameter was rewritten.

Imported task notebooks are scaffolds. Only their first two code cells (setup and Part 0) were executed in fresh namespaces; later checks/policy-selection/stability TODOs remain unfinished. The task notebook code cells were preserved unchanged. The archive's Docker mounts were narrowed to this project and its Cleaned companion; Docker and Jupyter GUI operation were not tested.

Training feature rows, test feature rows and raw MIMIC tables are not supplied. The full-dataset manifest is provenance for the original data, not proof that the subset is complete. No original notebook 01/02 rerun, new threshold selection, model retraining, final-test evaluation or clinical validation was performed. Local reports are under `artifacts/handoff-imports/20261105-235915/`.

## Ke Jin audit and policy experiment — 2026-11-05

The executable notebook and independent `src/aki_eval/` package were completed locally. The deployed feature engine, fitted model containers, original alert helpers, learned weights, default policy and dataset config were unchanged.

- New research tests: 45 synthetic cases covering each A1–A12 pass/fail pair, event lifecycle/timing, validation-role partitioning, immutable records, full selection/confirmation/cached rerun, report constraints and unlocked-policy rejection. The main joint run passed 98 tests (44 research + 54 existing backend); the additional report-layer test passed separately, for 99 covered cases.
- The final notebook's 7 code cells executed in a fresh project Jupyter kernel; its aggregate executed copy is local and source outputs are cleared. Repeated fresh-kernel execution preserved prespec.json, choice.json and confirmation.json byte-for-byte.
- A1–A12 passed their recorded validation/OOF artifact scope: 4,549,144 prediction rows, 13,647,432 probability values, no key/split/label gaps, and 2,048 sampled feature queries across 246 deployed features. A12's fitting claim is provenance-based, not a fresh fitting audit; static/service timing is not independently available.
- The original v1 run stopped on an extra bit-exact replay-risk requirement. Diagnostic checks showed stored-feature risk equality, small exported-feature differences that can move tree branches, and no risk change from one tested future-event-injection control. A labelled v2 amendment added zero-lag representation controls and both stored/replay reference bases without tuning the rule grid or quality thresholds. Technical retry files were retained.
- Candidate: 23% threshold, 12h cooldown. Selection used 3,420 validation patients; confirmation used the other 1,530. Train OOF grids were diagnostic only and generated after the choice was sealed.
- Confirmation: false alerts/scorable patient-day 0.424 -> 0.246; event detection 76.95% -> 73.56%; median lead 12.82 -> 12.09 h. PPV 33.19% missed the fixed 1/3 floor. It is recorded as not_confirmed_no_retuning; the default website policy was not changed.
- Confirmation cutoff samples had minimum paired coverage about 95%, worst point-warning flip about 3.95%, and worst patient alert-presence flip 6.75%. Larger zero-lag samples retain numeric representation discrepancies (2/5 feature rows beyond tolerance in selection/confirmation), so the sampled A10 pass is not a universal bit-exact replay guarantee.
- The candidate JSON has threshold.locked=false and is rejected by the live loader. Neither the hourly policy nor its bootstrap result establishes minute/event-driven operation, an untouched final-test result or clinical effectiveness.

Methodology and report semantics are in [handoff/KEJIN-EVALUATION.md](../handoff/KEJIN-EVALUATION.md). Completed local aggregate outputs are under `artifacts/handoff/kejin/kejin-validation-policy-v2-21d980db7c292219/`; original model/data artifacts remain excluded from Git. No automatic commit, push or PR was performed.

## Capacity-related test evidence

Synthetic data was used to verify:

- 100 patients.
- One burst of writes from 8 concurrent submission threads.
- Pagination and exchange of 10,001 historical records.
- Recovery of 25,001 records split into volumes.
- CSV preview ID matching, isolation of separate stays for the same patient, variable units, timezone-free relative time, malformed data, missing files, read-only database behavior, and file updates.

These cases demonstrate behavior under the stated limited conditions. They do not represent sustained long-running performance for 100 people per minute.

## Local CSV subset check

On 2026-09-12, only aggregate checks were performed on the user-provided subset in the project. Source rows were not copied into tests or reports:

- 100 selected ICU records.
- 17 ICU records containing the currently selected variables.
- 31 heart-rate points and 23 oxygen-saturation points, for 54 displayed points total.
- No mismatches or parse failures among selected records after matching the three IDs.
- All 54 `CHARTTIME` values preceded the corresponding `INTIME`.
- Dates were de-identified 2100 dates and were displayed unchanged.

This verifies file reading and matching only. It is not validation of the MIMIC cohort, variables, labels, or model results.

## Live model and demo playback checks

On 2026-10-07 and 2026-10-08, using only the four synthetic demo patients and the locked `demo-v1` model:

- The model worker scored every demo hour from ICU hour 3 (first usable creatinine) to 72: 4 predictions per patient-hour, none skipped, including 40 "+1 h" steps sent 0.3 s apart.
- Risks from live, event-driven scoring matched the batch hourly scorer (`scripts/score_dashboard.py`) at the same hours, for example 0.361 for Demo B at hour 30 in both.
- After a "+1 h" step, new observations were visible in about 0.2 s and all four risks updated in about 1.2-3 s (longer later in the stay).
- Uploading a demo lab file (`scripts/make_demo_upload.py`) through the import endpoint raised Demo D's 24 h risk from about 4-8% to about 28-31% within seconds, with the creatinine rise as top driver.
- No worker or launcher processes remained after the service stopped.

These checks cover synthetic data on one laptop. They are not evidence of clinical accuracy; model performance is reported in the root README and the notebooks.

## Not implemented or not yet validated

- Locked test-set evaluation of the model and alert policy (notebook 03).
- Full-population/event-driven cutoff stability and the remaining Jiaju calibration/predictor studies. Ke Jin's sampled fixed-origin hourly 0/1/2h cutoff stress checks and their numerical recheck are complete; see [the diagnosis](../handoff/REPLAY-NUMERIC-DIAGNOSIS.md).
- Composite data-confidence formula.
- Actual hospital-system integration, remote multi-user access, and authentication.
- Sustained multi-hour load, GPU inference, and end-to-end latency.
- Source installation recheck on another physical computer.
- Windows EXE, ZIP package, installer, or portable package.
- Clinical effectiveness, safety, and physician workflow acceptance.

Recorded browser checks cover dataset preview and entry controls. Synthetic HTTP/worker tests cannot replace full physician-workflow, model-output, or failure-recovery acceptance.

## Runtime time-boundary verification — 2026-11-04

Environment: Windows x64, Python 3.13.5, NumPy 2.1.3, pandas 2.2.3, and Node.js 24.21.0. The source baseline was `50c299d` plus the runtime timing fixes. All new inputs and prediction outputs were synthetic; the tests use the real shared feature engine and HTTP/storage contract with a feature-capturing test model, not fitted AKI weights.

- Before the fix, 7 of the 8 new regressions failed; after the fix all 8 passed.
- Covered fractional-second origins and generation times, distinct revisions at identical times, retry of a deferred revision without another global change, and exclusion of a correction arriving after the captured knowledge boundary in both live and batch scoring.
- Covered static measurements appearing only at eligible cutoffs, missing earlier values, original query ordering, and unchanged admission-only inputs. The 72-query unchanged-static case still uses one vectorized feature-engine call and matches its original feature matrix. This is a regression guard, not a sustained-load or latency benchmark.
- The full backend suite passed with the existing Starlette/httpx and AnyIO deprecation warnings. The frontend suite and production build also passed.
- The documented standalone-filter command passed its 7 tests. All 9 tracked Markdown files passed relative-link and code-fence checks.
- An isolated synthetic demo loaded four patients and advanced three hours. The main service then started from the updated checkout: health and homepage checks passed, file watching was active without errors, and model status remained `not_configured` because weights were absent. Browser rendering was not rechecked in this run.
- This checkout lacks the matching fitted model and configured cleaned dataset. No model training, real-risk inference, cutoff-stability study, threshold re-selection, or test-set evaluation was performed. The locked policy and previously recorded research metrics were not regenerated by these tests.
- Existing stored predictions are not automatically repaired. Any recomputation of older results requires a versioned append/revision plan and separate validation; no real database was rewritten by these regressions.

## Evidence boundaries

- A successful build does not mean every page flow is complete.
- HTTP 200 does not mean that data semantics are correct.
- Local throughput testing does not mean long-term load capacity.
- A readable file does not mean that the research cohort and labels are correct.
- A page displaying risk fields does not mean that a model is integrated or effective.
- All automated tests use synthetic data; patient-level raw data, credentials, and secrets are not written to this file.

## Automatic Node.js setup verification — 2026-09-16

- Windows x64: removed Node from the test process PATH, installed official Node.js 24.21.0 into an isolated temporary directory, verified its SHA256, and ran node/npx successfully. The test did not modify persistent user PATH.
- Repeated the helper with an empty Node PATH to verify reuse of its installed directory, then with Node on PATH to verify reuse without another download.
- Ran the complete setup script under Windows PowerShell with the normal machine/user PATH. Existing Node was reused; dependency installation, TypeScript checks, and Vite build passed.
- `Setup-AKI.cmd` now forwards arguments and preserves the setup exit code. Python still requires a separate installation; this is source setup, not Windows application packaging.
- ARM64 automatic download is implemented but not tested on ARM64 hardware. Network failure and checksum rejection paths were not fault-injected in this run.

## Startup link verification — 2026-09-16

- Executed Start-AKI.cmd against the running AKI service: printed the full URL, recognized the service using health and OpenAPI title, and invoked Windows default-browser handling successfully (exit 0). Browser rendering was not rechecked in this run.
- An isolated fresh launch with --no-browser, a custom port, and temporary data became healthy and printed the matching URL. The test server was stopped afterward.
- Python compilation and git diff --check passed. Unsupported terminal link-click behavior is outside the launcher's control; copying the URL remains available.

## Post-confirmation numerical diagnosis — 2026-11-05

[REPLAY-NUMERIC-DIAGNOSIS.md](../handoff/REPLAY-NUMERIC-DIAGNOSIS.md) owns the cause analysis and proposed implementation plan. This section records evidence only.

- All 4,950 validation patients / 198,524 hourly origins were compared with the fixed LightGBM and fixed baseline/candidate. Saved features exactly reproduced saved risks.
- Full-batch and origin-wise zero-lag replay changed risk by >1e-6 at 6,503 and 7,134 origins respectively. The largest observed full-population risk change across controls was approximately 8.67 percentage points. These differences can change warning/event timing.
- Independent native-time/window-local arithmetic, exact zero-lag static-ratio controls and reconstructible pre-ICU baseline precision were evaluated. The seven-day export first reproduced baseline precision for 3,956 patients / 160,682 rows. A subsequent read-only local raw-lab scan reconstructed every admission baseline: 3,956 pre-ICU minima, 222 remote-history medians and 772 missing baselines; all 198,524 resulting ratio rows matched the saved feature, including missingness.
- Model-path tracing locates near-zero standard-deviation/slope split sensitivity. The trace was checked using float64 threshold comparisons and independently matched all 17,868 changed leaf outputs in the full-batch changed-risk sample; six new synthetic oracle/trace tests passed.
- Across the fixed-candidate confirmation controls, detection stayed 73.5593%, false alerts/scorable patient-day ranged approximately 0.2441-0.2457, and PPV remained approximately 33.19%-33.26%, below 1/3. The original confirmation status was preserved.
- Native arithmetic and complete raw-reconstructed admission-baseline precision were also applied to the original 800/400 cutoff samples at 0/1/2h. Worst point-warning flip was 4.0700% / 3.9542%, worst patient alert-presence flip was 6.00% / 6.75%, and paired coverage remained at least 94.96%. Both roles pass the original stability gates. The original candidate remains best under the original saved-snapshot point metrics.
- A synthetic reproduction found a stale prior-minimum risk cache in rapid-rise cutoff diagnostics. It affects that rule's diagnostic table, not the selected cooldown-only candidate. Repair is proposed before another search; it was not silently changed in the sealed study.
- Primary diagnosis and cause/cutoff controls completed and wrote separate aggregate-only plans/manifests. The original prespec, choice, confirmation, model, default policy, analysis sources and `LATEST.json` remained unchanged.
- The original bootstrap intervals were not recomputed for the sensitivity representations. No new independent confirmation, training, final-test evaluation, clinical validation, website activation, dependency change, commit or push occurred.

## Working-tree review after conversation compaction — 2026-11-05

- The combined `tests` and `dashboard/tests` suite passed: 105 tests, with the two existing Starlette/httpx and AnyIO deprecation warnings. This check uses synthetic data; it does not rerun model fitting or patient-level confirmation.
- Frozen evaluation-source hashes, original prespec/choice/confirmation byte hashes, the source notebook's executed code hash, and its cleared outputs were rechecked. The candidate remains unconfirmed and unlocked. Both task notebooks contain no saved outputs.
- The current change set contains 6 modified tracked files and 31 new source/configuration/documentation/test files. Expanded Git inventory and representative ignore checks keep model weights, local aggregate result files and raw MIMIC files excluded. No private-format artifact, symbolic link or common credential-token pattern was found in the candidate source set. This is a bounded check, not a universal secret-detection guarantee.
- Cleaned a duplicated diagnosis paragraph and one encoding-corrupted check label in the evaluation guide, and clarified the root README's remaining-work status.
- A new synthetic interrupted-run reproduction showed that the cached-choice branch can leave missing OOF reports unrecovered. The completed real run already has those files; no original choice/confirmation was changed. The recovery defect is pending and documented with the other repairs.
- This review did not stage, commit or push files, and did not modify the production feature engine or default policy.

## Four-defect repair validation — 2026-11-05

Implementation/version semantics are owned by [FEATURE-REPAIR-V2.md](../handoff/FEATURE-REPAIR-V2.md).

- Final combined suite: **127 passed**, 16 backend/plotting dependency deprecation warnings. The old diagnostic function-clone compatibility failure was fixed before this passing run. New cases cover v2 window/batch/future invariance, nonzero small changes, native time bounds, float64 admission inputs and corrupt artifacts, live engine-contract mismatch, live v2 input precision, stale risk histories, and missing/partial report recovery.
- Full real-data repair run: 4,950 validation patients, 198,524 origins, 246 deployed features and four fixed model heads. Original labels, origins and target windows were retained in new versioned private artifacts. No model fitting, calibrator refit, threshold search, new confirmation or test-set use occurred.
- The 100-patient / 3,770-origin real-data context checks passed exact feature and 24h-risk equality for remapped cohorts, query chunks, zero-lag clipping and future/unrelated additions. Single-patient and live v2 paths also have synthetic regression coverage.
- Export roundtrip preserved all 56 non-feature parent columns and exact four-head predictions on 1,024 serialized rows. Full-precision admission inputs preserved 4,178 available and 772 missing baselines. v2 ratio replay now requires a verified float64 input artifact instead of recovering the denominator from a float32 feature.
- The fixed candidate's confirmation-role sensitivity PPV was 33.2620%, detection 73.5593%, false-alert rate 0.244096/scorable patient-day and median lead 12.0756h. PPV still misses the original 1/3 floor; the original confirmation was not replaced.
- Candidate cutoff checks on the original 800/400 samples remained inside the old stability limits: maximum point-warning flip 4.0601% / 3.9542%, patient alert-presence flip 6.00% / 6.75%, minimum paired coverage about 94.96% / 95.00%.
- Four-horizon AUROC/Brier/calibration diagnostics were exported without fitting a new calibrator. At 24h, AUROC 0.808886 -> 0.808948, Brier 0.090142 -> 0.090140 and 10-bin ECE 0.005508 -> 0.005602.
- Three in-process 100-patient cycles (features, four risk heads and contributions) took about 0.461-0.475s. No HTTP/sustained-load or denser-minute-stream benchmark is claimed.
- Missing/truncated OOF grids, summaries and PNGs were regenerated in synthetic interrupted-run cases while choice/confirmation/export hashes stayed unchanged. A missing sealed selection grid raises a restoration error rather than rerunning selection.
- All **137 protected original files** matched their pre-repair hashes. The default locked model loaded successfully and retained `legacy_prefix_v1`. The new `patient_window_v2` route requires matching fitted-model/policy declarations and was not activated as a replacement.
- Local results: `artifacts/feature-repair/repair-v2-20261106-01/`. Its `private/` data are sensitive derived artifacts; only aggregate evidence is described here. No existing service/database was changed, and no commit or push was performed in this repair turn.
