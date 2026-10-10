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

The two backend warnings on 2026-09-12 were future-compatibility deprecation notices from Starlette TestClient for httpx and AnyIO BlockingPortal aliases; the tests themselves passed.

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
- Stability experiments across different data cutoffs.
- Composite data-confidence formula.
- Actual hospital-system integration, remote multi-user access, and authentication.
- Sustained multi-hour load, GPU inference, and end-to-end latency.
- Source installation recheck on another physical computer.
- Windows EXE, ZIP package, installer, or portable package.
- Clinical effectiveness, safety, and physician workflow acceptance.

Current browser interaction checks use the local dataset preview. They cannot replace full input, model-output, failure-recovery, and clinical-use acceptance.

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
