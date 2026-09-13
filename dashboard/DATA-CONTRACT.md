# Local Data Contract v1

This document defines normative fields, constraints, and compatibility rules for real-time input and model output. Backend integration is described in [DEVELOPMENT.md](DEVELOPMENT.md), and page operations in [README.md](README.md). Machine validation is defined by `api/schemas.py` and the running `/openapi.json`.

## General rules

- Top-level objects must contain `schema_version: 1` and `kind`.
- Schemas are strict: unknown fields are rejected, strings are trimmed, and NaN or infinite values are rejected.
- IDs are 1–80 characters and may contain only letters, digits, underscores, and hyphens.
- Times must be timezone-aware ISO 8601 values, such as `2026-09-10T10:30:00+08:00` or `Z`; storage normalizes them to UTC.
- A JSON batch contains at most 10,000 observations or predictions. An input batch also contains at most 1,000 patients.
- The same `record_id + revision` must always represent the same content; increment `revision` for corrections.
- If `available_at` is absent, the backend uses the local first-received time and records `availability_basis=local_received`.

## InputBatch

```json
{
  "schema_version": 1,
  "kind": "input",
  "patients": [],
  "observations": [],
  "actor": "local-user"
}
```

`actor` is used for local auditing and is removed before writing an exchange archive.

### Patient

| Field | Type | Rule |
|---|---|---|
| `patient_id` | ID | Current database patient primary key |
| `encounter_id` | ID | Hospital encounter; must remain consistent in later records |
| `name` | Text | 1–120 characters |
| `icu_admitted_at` | Zoned time | Must not be later than local receipt time |
| `bed` | Text | Optional, at most 40 characters |
| `note` | Text | Optional, at most 500 characters |

An existing `patient_id` with different information rejects the entire batch. One `patient_id` currently maps to one `encounter_id`.

### Observation

| Field | Type | Rule |
|---|---|---|
| `record_id` | ID | Stable record ID from the source system |
| `revision` | Integer | Starts at 1 |
| `patient_id` | ID | Must already be registered |
| `encounter_id` | ID | Must match the patient record |
| `metric` | Text | Stable variable code |
| `label` | Text | Display name |
| `unit` | Text | At most 40 characters; may be empty |
| `value` | Number | Must be finite |
| `measured_at` | Zoned time | Actual measurement time |
| `available_at` | Zoned time or null | Actual source availability time, when known |

Times must satisfy:

```text
icu_admitted_at ≤ measured_at ≤ available_at ≤ local receipt time
```

If `available_at` is absent, the backend fills it with receipt time. Do not put entry time in `measured_at`. Clinical ranges, unit conversion, sampling intervals, interpolation, and missingness rules must be defined jointly by the collector and model preprocessing.

## PredictionBatch

```json
{
  "schema_version": 1,
  "kind": "prediction",
  "predictions": []
}
```

Required prediction fields:

| Category | Fields |
|---|---|
| Record | `record_id`, `revision` |
| Association | `patient_id`, `encounter_id`, `input_revision`, `input_fingerprint` |
| Model | `model_id`, `model_version`, `target` |
| Time | `origin_time`, `data_cutoff`, `horizon_end`, `generated_at` |
| Output | `risk` |

`available_at` is optional. Constraints:

- `input_revision ≥ 0`.
- `input_fingerprint` is a 64-character lowercase hexadecimal SHA-256 value.
- `0 ≤ risk ≤ 1`.
- `data_cutoff ≤ origin_time < horizon_end`.
- `generated_at ≥ origin_time`.
- Storage also requires `generated_at ≤ available_at ≤ local receipt time`; missing `available_at` means receipt time.
- Patient and encounter IDs must match.
- The fingerprint must represent all input available at generation time with `measured_at ≤ data_cutoff`.

`input_revision` supports task merging and tracing; `input_fingerprint` defines consistency across computers and historical cutoffs. The backend snapshot endpoint generates fingerprints; integrations should not copy its normalization algorithm.

### Optional fields

#### data_confidence

`data_confidence` ranges from 0 to 1 and requires a nonempty `confidence_definition`. It is a model- or quality-module-defined data-confidence value, not prediction accuracy, and does not change `risk`.

#### drivers

At most 100 items. Each item contains `feature`, `label`, and `contribution`, plus optional `value` and `unit`. Contribution scales are not currently standardized across models.

#### trajectories

At most 30 series, with at most 10,000 points per series. A series contains `metric`, `label`, and `unit`; each point contains `time` and `value`, with optional `lower` and `upper`. Points must be time-ordered and lie between `origin_time` and `horizon_end`; if present, `lower ≤ value ≤ upper`. The frontend currently draws trajectory lines but not interval bands.

#### stability_assessment_id

References a versioned stability assessment across different data cutoffs. The current system stores the field but does not generate stability conclusions.

#### threshold

| Field | Rule |
|---|---|
| `value` | 0–1 |
| `comparison` | `>=` or `>` |
| `validation_run_id` | Validation experiment identifier |
| `policy_version` | Alert-policy version |
| `monitoring_window` | Applicable monitoring window |
| `calibration_version` | Calibration version |
| `locked` | Whether the threshold is locked |
| `selection_basis` | Selection rationale, at most 2,000 characters |
| `stability_assessment_id` | Optional stability-assessment reference |

Thresholds are specific to the model, version, target, horizon, and calibration policy; they are not global constants. Without a threshold or with `locked=false`, the frontend must not derive an AKI alert. Schema validation cannot prove that a validation conclusion is scientifically valid.

## Input snapshot fingerprints

The fingerprint input has the form:

```json
{"patient_id":"...","observations":[...]}
```

Use the highest revision of each `record_id` available at `generated/as_of`, keep measurements with `measured_at ≤ cutoff`, sort by `record_id`, remove internal `availability_basis`, and compute SHA-256 over compact UTF-8 JSON with sorted keys.

The backend owns normalization. Use:

```text
GET /api/patients/{id}/snapshot?as_of=<time available at generation>&cutoff=<data cutoff>
```

Prediction import recomputes and verifies the fingerprint. After input changes, old predictions may remain in history but are marked stale in current results.

## Compatibility

- v1 rejects unknown fields.
- New optional fields must preserve readability of existing v1 files.
- Removing or renaming fields, changing units, or changing time meaning normally requires a new `schema_version` and migration plan.
- JSON/ZIP exchange, model scheduling, HTTP pagination, and UI behavior belong in the development guide rather than this contract.
