# Website Development and Backend Integration Guide

This document covers website architecture, backend integration, and change conventions. Installation and routine operation are in [README.md](README.md); normative JSON fields are in [DATA-CONTRACT.md](DATA-CONTRACT.md); test evidence is in [VALIDATION.md](VALIDATION.md).

The website uses one local FastAPI process for the React page, HTTP API, SSE updates, and SQLite data layer, plus a separate model worker process (`model_worker.py`) that scores input with an explicitly selected adapter (default: locked LightGBM) (see [Model worker](#model-worker)).

## Documentation maintenance

Maintain each fact fully in one primary document and use links plus a short summary elsewhere:

| Information | Primary document |
|---|---|
| Research goals, candidate models, notebooks, and research artifacts | Root `README.md` |
| Offline extension definitions, extraction, baseline modules and run procedure | `pipeline/RESEARCH.md` |
| User installation, startup, and website operation | `dashboard/README.md` |
| Module responsibilities, integration order, API inventory, and development workflow | This document |
| JSON fields, types, time equations, revisions, and fingerprint algorithm | `DATA-CONTRACT.md` |
| When checks run, their results, and what they cannot prove | `VALIDATION.md` |
| Historical handoff differences | `HANDOFF-CHECK.md` |

When updating documentation:

- Change the schema and data contract when fields change; update this document only for integration steps.
- Append new test results to `VALIDATION.md`; do not maintain pass counts in the root README.
- Put page-operation changes in the website README; record implementation conventions here.
- Put research-candidate changes in the root README; do not present candidates as website constraints.
- Remove completed items from TODO lists and preserve historical differences in `HANDOFF-CHECK.md`.
- Use evidence-consistent wording for planned, implemented, tested, and delivered work.

## System boundaries

- React handles entry, queries, and display; model computation enters through the versioned contract.
- Store observations by append and revision; historical replay is read-only.
- Do not conflate measurement time, source availability time, local receipt time, prediction origin time, or generation time.
- Without model output, keep an empty state. An explicit `--demo --demo-model` session may use the synthetic baseline adapter; missing real weights must never trigger simulated risk automatically.
- Only thresholds with complete validation metadata and `locked=true` may produce an AKI alert.
- Keep the local real-time workflow separate from the MIMIC CSV preview.
- Do not commit local databases, MIMIC files, photos, models, patient-level artifacts, or credentials.
- Build Windows packages only after an explicit request.

## Module responsibilities

| Location | Responsibility |
|---|---|
| `api/schemas.py` | Machine validation for input, observations, predictions, thresholds, drivers, and trajectories |
| `api/storage.py` | SQLite transactions, revisions, time visibility, fingerprints, and outbox |
| `api/watcher.py` | JSON watching, stable-file checks, retries, and error summaries |
| `api/exchange.py` | JSON/ZIP import, export, and volume splitting |
| `api/model_adapter.py` | Model-process boundary (Protocol); the implemented worker is `model_worker.py` |
| `api/demo.py` | Demo playback: staged stay, stepped clock, hourly release |
| `api/dataset_preview.py` | Read-only MIMIC subset preview |
| `api/main.py` | HTTP, SSE, local request protection, service clock, model status, and compiled page |
| `launcher.py` | Starts the website and the model worker; `--demo` for demo playback, `--no-model` to skip the worker |
| `model_worker.py` | Live scoring of new input revisions with the selected adapter (`src/aki_ml/scoring.py`) |
| `web/lib/api.ts` | Shared frontend types, requests, historical pagination, and alert decisions |
| `web/components/clinical/` | Patient cards, details, entry, preview, and charts |
| `web/app/page.tsx` | Page routing, overview state, and live updates |

New functionality should go into the corresponding module. Model preprocessing must not be placed in React, and the model process must not modify SQLite directly.

## Backend input channels

### Web and HTTP

The page submits `input` or `prediction` batches to `POST /api/import`. `POST /api/import-file` accepts this site's JSON/ZIP exchange files. A batch is validated in one SQLite transaction and is not partially written on failure.

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Service, revision, listener errors, outbox status, model status, and service clock |
| `GET /api/clock` | Service clock (real time, or the demo-playback time) |
| `POST /api/demo/advance?hours=1` | Demo playback only: move the clock forward and release that hour's data |
| `POST /api/model/heartbeat` | Model worker status report |
| `GET /api/patients/{id}/revisions` | Input revisions and their arrival times (model worker trigger points) |
| `GET /api/settings` | Data and exchange directories and model status |
| `GET /api/patients` | Overview |
| `GET /api/patients/{id}/history` | History and replay |
| `GET /api/patients/{id}/snapshot` | Input fingerprint at a specified time and cutoff |
| `GET /api/patients/{id}/current-predictions` | Current model results |
| `GET /api/patients/{id}/quality` | Freshness and data-density statistics |
| `GET /api/events` | SSE revision notifications |
| `GET /api/datasets/icu-preview` | Read-only CSV preview |
| `GET /api/patients/{id}/export-plan/{kind}` | Export volume plan |
| `GET /api/patients/{id}/export/{kind}` | Download input or prediction volumes |

The running `/openapi.json` is the machine-readable source for HTTP request structures. The service listens only on the loopback address, restricts Host, and checks Origin for browser writes. Any future remote access requires authentication, authorization, TLS, auditing, and a deployment design; simply listening on `0.0.0.0` is not sufficient.

### External file writers

External programs may atomically write complete JSON files to:

```text
<data-root>/inbox/input/*.json
<data-root>/inbox/prediction/*.json
```

Producers must:

1. Write a temporary file in the same directory, then atomically rename it to `.json`.
2. Use stable `record_id` values and increment `revision` for corrections.
3. Permit idempotent retries of the same batch.
4. Keep top-level `kind` consistent with the directory.
5. Keep each file at or below 16 MB.
6. Never clean up errors by deleting the database, `accepted`, or outbox data.

The watcher wakes on file events and rescans about once per second. It processes only stable files. Failed files do not replace valid records; read error summaries from the health endpoint.

## Real-time observation integration

Keep field definitions and complete JSON examples only in [DATA-CONTRACT.md](DATA-CONTRACT.md). Integration code must also follow these rules:

- Register the patient first; patient and `encounter_id` must match.
- One `patient_id` currently maps to one encounter; a later admission needs a new unique identifier.
- Use timezone-aware ISO 8601 timestamps and normalize them to UTC in storage.
- If source `available_at` is missing, use first-receipt time so late-arriving data cannot appear in earlier replay.
- The collector and model preprocessing jointly define variable codes, units, ranges, conversions, interpolation, and missingness. The backend does not replace these rules.

Successful input writes SQLite and a durable outbox. The outbox creates an `accepted/input` archive and the latest model request. Failed file writes remain in the outbox and are retried.

## Model worker

The implemented worker is `dashboard/model_worker.py`, started by the launcher as a separate process (skip it with
`--no-model`). By default it loads the locked model named in `artifacts/demo_i/policy.json` (LightGBM `no_dc`, Platt, `demo-v1`
threshold and monitoring window) with the shared scoring code in `src/aki_ml/scoring.py`, and needs the pinned
packages in `requirements-model.txt` (installed by setup). It is event-driven: every accepted input batch creates a
patient input revision (`GET /api/patients/{id}/revisions` lists them with their arrival time), and the worker
scores each new revision at its arrival time (origin = data cutoff) from all observations measured up to then.
One service-clock time is captured for each sync and used consistently as the knowledge boundary for history
pagination, scoring inputs, and fingerprints. The field semantics and the distinction between retrospective
recomputation and prospective replay are defined in [DATA-CONTRACT.md](DATA-CONTRACT.md).
Revisions are not merged. Scorable arrivals receive results; input coverage and the model monitoring window can exclude a query. For the original Demo I policy, the window is ICU hours 1-72, the 24h output carries its threshold, and 6/12/48h are display-only. Extension policies provide their own range, horizons and threshold. The worker reports itself with `POST /api/model/heartbeat` every few seconds; `GET /api/health`
shows `model_status` (`running`, `error`, `offline`, `not_configured`). For retrospective hourly scoring of data
that is already stored, use `scripts/score_dashboard.py`.

The worker keeps fractional seconds and retries a sync when a patient's advertised revision is not yet visible
at the captured time; it does not cache that global revision as complete. Equal-time input revisions retain
separate prediction IDs. Shared scoring selects static values per query cutoff and groups equal static states
for vectorized calls to the unchanged training feature engine. `build_inputs` now returns a static frame aligned
with its query-hour array; callers should use `score_hours` rather than assume a one-row static frame.

The November 4 timing fixes did not change schemas, fitted weights, feature ordering, calibration or threshold. The later extension work adds compatible optional output fields and independent baseline policies; neither change requires a SQLite migration. Existing stored predictions are not rewritten. Acceptance uses isolated synthetic data; repair
of pre-fix results must be an explicit append/revision or new-record workflow. Model scoring tests require
`requirements-dev.txt`, which also installs the model runtime dependencies, but no model weights.

### Demo playback

`Start-AKI-Demo.cmd` (or `launcher --demo [FILE]`) starts a fresh data directory under
`%LOCALAPPDATA%\AKIWorkbench\demo-runs\` and loads an InputBatch (default: the built-in synthetic patients). Every
admission is moved to the demo start (72 h before the current hour) and observations are staged. The service clock
(`schemas.now()`) is then frozen at the demo time: `POST /api/demo/advance?hours=1` moves it forward one hour and
imports the observations that became available in that hour, which triggers the model worker as live data would.
`GET /api/clock` reports the clock; the frontend uses it instead of the browser clock (`nowMs()` in `lib/api.ts`).

### Request files

When input changes, the backend also updates:

```text
<data-root>/requests/{patient_id}.json
```

The request means “pending processing” and includes patient and encounter IDs, input version, the latest fingerprint, and locations for history and input export. These files support alternative external workers; the bundled worker polls the HTTP revision API and preserves every revision. An external worker choosing to coalesce requests can:

1. Watch or poll `requests`.
2. Merge tasks by patient and retain only the newest input version.
3. Read visible history and select `data_cutoff`, `origin_time`, and horizon.
4. Call the snapshot endpoint for the fingerprint at that cutoff.
5. Run preprocessing, inference, calibration, and explanation.
6. Recheck the request version before writing and discard superseded work.
7. Atomically write the prediction inbox or call the import API.
8. Record queue, read, preprocessing, inference, explanation, write, and page-refresh latency separately.

Do not use the latest request fingerprint for an earlier cutoff. Historical cutoffs must call:

```text
GET /api/patients/{id}/snapshot?as_of=<time available at generation>&cutoff=<data cutoff>
```

The backend recomputes the fingerprint and rejects mismatched predictions. When a new observation arrives, the page marks results that reference old input as stale.

The data contract defines result fields, time constraints, and optional explanations. The frontend groups by model, version, target, and actual horizon; it does not impose half-hour, one-hour, or 8/12/24-hour limits. When several series exist, the patient overview and the detail view's default series prefer a prediction with a locked threshold (the alerting horizon); display-only horizons remain selectable. Workers should cancel expired tasks so old queues do not delay current results.

## Historical replay

The history endpoint uses `start/end` for event-time range, `as_of` for the information-availability boundary, `kind` for observation or prediction, and `offset/limit` for pagination. Storage first selects the highest revision visible at `as_of`, then filters by event time, preventing superseded versions from reappearing. Queries and replay do not trigger model computation.

The frontend loads at most 24,000 records per history type with bounded caching. Larger ranges should use pagination, a shorter window, or export. Chart downsampling is display-only and must not be used as model input or export data.

## Extension model adapters

Offline training and target definitions belong in [pipeline/RESEARCH.md](../pipeline/RESEARCH.md). React consumes model output only.

- `scoring.load_model` loads the original LightGBM policy or the versioned `baseline_lr_v1` adapter.
- Model ID and target come from policy; existing policies without them retain the original identity.
- Monitoring limits come from model policy; the frontend does not choose training horizons or sampling density.
- Baseline relative artifact paths resolve from the policy directory when `artifact_base=policy_directory`.
- `--trajectory-model <directory>` attaches a separate ridge artifact to a compatible risk model. Feature order, configuration and hashes are checked; the composite output policy changes.
- Example: `Start-AKI.cmd --policy artifacts\extensions\CLASSIFICATION_RUN\policy.json --trajectory-model artifacts\extensions\TRAJECTORY_RUN`. Replace run directories with actual outputs. Add `--demo` for synthetic artifacts.
- Restart checkpoints match model ID/version, target and policy version. Changing the trajectory creates new output IDs while preserving earlier history.
- `--demo-model` builds a small synthetic pair in memory in an isolated demo session, with no saved trained artifact by default.
- LR contributors are standardized-feature contributions on calibrated log-odds, not TreeSHAP or causal effects.
- Legacy optional defaults are normalized during duplicate-import comparison only; no database rewrite or schema migration is needed.

The worker still has one heartbeat and one selected risk adapter. Concurrent independent workers and a model registry are not implemented.

## Research alert-event candidates

[Ke Jin’s evaluation](../handoff/KEJIN-EVALUATION.md) separates alert events from instantaneous risk and above-threshold display. Its exported policy remains unlocked, so the live loader rejects it. A cooldown/episode candidate needs an explicit stateful alert adapter and a validated input-frequency contract; copying its threshold into the current chart does not implement its alert behavior. No website default was changed by the evaluation, and feature-replay representation differences remain an open review item.

## Feature-engine versions

The shared API defaults to `legacy_prefix_v1` for old callers/artifacts. The repaired `patient_window_v2` implementation uses patient-native times and window-local arithmetic. High-precision admission baselines are separate inputs; a rounded feature column is not a replacement.

`LockedModel` requires the engine version in the policy's `model` object to match fitted-pipeline metadata. Missing declarations in old artifacts mean v1. A future v2 artifact must declare v2 in both places and pass its own model/policy validation; never edit existing weight metadata to bypass the guard. `score_hours` dispatches explicitly, and v2 record keys include the engine identity. Raw observation/static numeric precision is retained in the live input builder.

See [FEATURE-REPAIR-V2.md](../handoff/FEATURE-REPAIR-V2.md) for input artifact validation, the compatibility decision and actual evidence. No prediction JSON schema or clinician-facing configuration was added by this repair.

## Frontend conventions

Use `web/lib/api.ts` for `/api` requests. Update TypeScript types when backend schemas change. SSE sends only the global revision; it does not send patient data. The page queries again after receiving a new revision.

Keep one horizontal card per patient: identity and reminders on the left, observation curves in the middle, and prediction risk or trajectories on the right. Risk plots use red above and green below. `data_confidence` may affect visual prominence but not the probability. Distinguish missing thresholds, unlocked thresholds, changed input, and ended windows.

## CSV preview boundaries

Operation and current field mapping are in [README.md](README.md). Implementation must preserve three-ID matching, separate stays for the same patient, original timezone-free source strings, relative hours within one ICU stay, read-only behavior, and small-subset loading. Full MIMIC data requires independent offline extraction and pagination. Error responses must not echo source rows, raw values, or full local paths.

## Changing the data contract

1. Define clinical meaning, time, units, keys, revisions, and compatibility.
2. Update `api/schemas.py`.
3. Update storage, HTTP, exchange, and model-adapter layers.
4. Update `web/lib/api.ts` and consuming components.
5. Add contract tests with purely synthetic data.
6. Update [DATA-CONTRACT.md](DATA-CONTRACT.md).
7. Build the page and complete HTTP and browser checks.
8. Append actual results to [VALIDATION.md](VALIDATION.md).

The current format is `schema_version: 1`; unknown fields are rejected. Do not loosen the strict schema to hide producer/consumer mismatches.

## Development verification

Backend:

```powershell
.\.venv\Scripts\python.exe -m pytest dashboard/tests -q
```

Install developer dependencies first with `.\.venv\Scripts\python.exe -m pip install -r dashboard/requirements-dev.txt`.

Frontend:

```powershell
cd dashboard\web
pnpm test
pnpm build
```

When relevant, also check transaction rollback, idempotent revisions, file events, SSE, `as_of`, stale predictions, empty model state, actual routes, and browser interactions. Tests prove only the covered code paths; record validation conclusions in `VALIDATION.md`.

## Development order

Done (October 2026): modeling data, cohort, and outcome definitions (notebook 01); offline MIMIC extraction and training (notebook 02); a locked threshold and alert policy selected on training folds and confirmed on validation (`demo-v1`); and a model worker that follows the existing contract.

Remaining:

1. Evaluate the locked system once on the test set (notebook 03).
2. Run real-data cutoff-stability studies and lock the original-study policy. Shared baseline evaluation exists; synthetic execution is not that study.
3. Define and validate composite confidence from actual data density.
4. Measure sustained load, model latency, failure recovery, and physician workflow.
5. Evaluate a Windows package only after an explicit request.

The current `/api/settings` research flags are placeholders: `validation_threshold=not_selected` does not
describe the committed Demo I policy. Use the policy and research documents for that experiment's status until
the settings endpoint is connected to research artifacts. Those flags do not describe new baseline experiments either; read each versioned policy/report. Original-model real-data cutoff stability remains unevaluated.

When GitHub and local work conflict, list the exact files and differences, preserve local work, and then choose the merge strategy. Do not let an old summary overwrite current source, and do not describe plans or placeholder interfaces as implemented functionality.
