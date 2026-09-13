# Website Development and Backend Integration Guide

This document covers website architecture, backend integration, and change conventions. Installation and routine operation are in [README.md](README.md); normative JSON fields are in [DATA-CONTRACT.md](DATA-CONTRACT.md); test evidence is in [VALIDATION.md](VALIDATION.md).

The website currently uses one local FastAPI process for the React page, HTTP API, SSE updates, and SQLite data layer. An AKI inference implementation is not currently present.

## Documentation maintenance

Maintain each fact fully in one primary document and use links plus a short summary elsewhere:

| Information | Primary document |
|---|---|
| Research goals, candidate models, notebooks, and research artifacts | Root `README.md` |
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
- Without model output, keep an empty state; do not generate demo, rule-based, or random risk.
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
| `api/model_adapter.py` | Model-process boundary; currently defines only a Protocol |
| `api/dataset_preview.py` | Read-only MIMIC subset preview |
| `api/main.py` | HTTP, SSE, local request protection, and compiled page |
| `web/lib/api.ts` | Shared frontend types, requests, historical pagination, and alert decisions |
| `web/components/clinical/` | Patient cards, details, entry, preview, and charts |
| `web/app/page.tsx` | Page routing, overview state, and live updates |

New functionality should go into the corresponding module. Model preprocessing must not be placed in React, and the model process must not modify SQLite directly.

## Backend input channels

### Web and HTTP

The page submits `input` or `prediction` batches to `POST /api/import`. `POST /api/import-file` accepts this site's JSON/ZIP exchange files. A batch is validated in one SQLite transaction and is not partially written on failure.

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Service, revision, listener errors, and outbox status |
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

When input changes, the backend updates:

```text
<data-root>/requests/{patient_id}.json
```

The request means “pending processing” and includes patient and encounter IDs, input version, the latest fingerprint, and locations for history and input export. A model worker should:

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

The data contract defines result fields, time constraints, and optional explanations. The frontend groups by model, version, target, and actual horizon; it does not impose half-hour, one-hour, or 8/12/24-hour limits. Workers should cancel expired tasks so old queues do not delay current results.

## Historical replay

The history endpoint uses `start/end` for event-time range, `as_of` for the information-availability boundary, `kind` for observation or prediction, and `offset/limit` for pagination. Storage first selects the highest revision visible at `as_of`, then filters by event time, preventing superseded versions from reappearing. Queries and replay do not trigger model computation.

The frontend loads at most 24,000 records per history type with bounded caching. Larger ranges should use pagination, a shorter window, or export. Chart downsampling is display-only and must not be used as model input or export data.

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

Frontend:

```powershell
cd dashboard\web
pnpm test
pnpm build
```

When relevant, also check transaction rollback, idempotent revisions, file events, SSE, `as_of`, stale predictions, empty model state, actual routes, and browser interactions. Tests prove only the covered code paths; record validation conclusions in `VALIDATION.md`.

## Development order

1. Confirm approved modeling data, variable dictionary, cohort, and outcome definitions.
2. Build an independent MIMIC offline extraction and training-data workflow.
3. Compare prediction stability across data cutoffs.
4. Select and lock thresholds on the validation set and link stability results.
5. Implement a model worker that follows the existing contract.
6. Define and validate composite confidence from actual data density.
7. Measure sustained load, model latency, failure recovery, and physician workflow.
8. Evaluate a Windows package only after an explicit request.

When GitHub and local work conflict, list the exact files and differences, preserve local work, and then choose the merge strategy. Do not let an old summary overwrite current source, and do not describe plans or placeholder interfaces as implemented functionality.
