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
| 2026-11-09 | Backend, research and scoring tests | 117 passed | After removing the research extensions; includes the new alert rules and the feature-engine revalidation guard |
| 2026-11-09 | Frontend tests and production build | 7 passed; build passed | After reverting the trajectory views |
| 2026-11-09 | Locked policy v3 on the website | Backend 118 passed; frontend 8 passed; build passed | 12 h confirmation in the threshold object (schema, scoring, frontend warning) |

The two backend warnings on 2026-09-12 were future-compatibility deprecation notices from Starlette TestClient for httpx and AnyIO BlockingPortal aliases; the tests themselves passed.

## Feature engine v2 and alert policy v3 — 2026-11-09

Run on the full local dataset (Windows, Python 3.13.5, pinned model packages); validation patients only, test split
not read.

- `scripts/rescore_validation_feature_v2.py`: float64 admission baselines rebuilt from LABEVENTS for 4,950 validation
  stays (all match the stored float32 values); 198,524 origins x 4 horizons rescored with the locked weights. Sampled
  invariance (60 patients, 2,532 origins): legacy engine 1,202 origins differ between batch and single patient; v2
  0, and 0 against events cut at each origin. AUROC/Brier unchanged to 4 decimals.
- Alert policy v3 (`validation-policy-v3-66054ed84c02e348`): Part 0 reproduced demo-v1 on the stored risks; A1-A12
  passed; zero-lag replay reproduced all selection (31,694) and confirmation (15,999) risks exactly. Selected
  `confirm12_cool12` at 0.21; not confirmed (detection loss 5.08 points, limit 5). The notebook was executed end to end
  with nbconvert; it reused the sealed records and left every output file unchanged.
- The four synthetic demo patients score identically with `policy.json` (legacy) and `policy_feature_v2.json`.
- After the owner locked v3 (`artifacts/policy/policy_v3.json`), the real model scored the four synthetic patients: warning hours with 12 h confirmation DEMO-002 55 (unchanged), DEMO-003 17 -> 7, DEMO-001/004 0.
- Jiaju's notebook re-executed into a separate directory: 7 of 12 CSV outputs byte-identical, the other 5 within
  5e-8 relative difference.

## Static demo — 2026-11-09

- `scripts/export_static_demo.py` replayed the four synthetic patients through the real backend and model worker for
  ICU hours 0-72 (3,052 observations, 1,120 predictions); warning hours DEMO-002 55, DEMO-003 7, DEMO-001/004 0, the
  same as the live demo.
- `pnpm build:static` and the normal build both pass `tsc`; the 8 frontend tests pass; the normal bundle contains no
  static-demo code.
- Headless Chrome against `vite preview --mode static`: overview at `?hour=30` and the DEMO-003 detail page at
  `?hour=40` render risks, the policy v3 status (24 h risk 21.7% shown as below threshold because the 12 h
  confirmation is not met), drivers and data freshness. A deep link reload returns the page (SPA fallback);
  `scripts/serve_static_demo.py` serves the same build offline.
- Not tested: an actual Vercel deployment (needs the owner's account).

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
- Event-driven (minute-level) alerting; alert policy v3 was selected and confirmed on the hourly grid, with sampled 1-2 h cutoff stress checks.
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

