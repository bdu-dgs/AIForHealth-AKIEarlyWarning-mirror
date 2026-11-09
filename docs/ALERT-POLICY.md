# Alert policy v3

The locked alert policy is `artifacts/policy/policy_v3.json`, used by the website and scoring scripts. It was selected
and confirmed on validation patients with `pipeline/KeJin_alert_policy_and_checks.ipynb` and
`configs/alert_policy_v3.yaml`. The aggregate records of that run are in
`artifacts/handoff/policy_v3/validation-policy-v3-66054ed84c02e348/`.

## Rule

A 24 h AKI warning is shown when, at the same hourly origin inside ICU hours 1-72:

- the 24 h risk is at least 21%, and
- the 12 h risk is at least 10.5% (0.5 x the threshold).

The second condition requires that at least half of the 24 h risk falls in the first 12 h, which is the share expected
under a uniform hazard. Risks come from the Demo I model (LightGBM `no_dc`, Platt calibration), with features computed by
the batch-independent engine ([FEATURE-ENGINE-V2.md](FEATURE-ENGINE-V2.md)).

The 12 h cooldown counts toward alert-burden metrics only. The website shows the stateless warning status for every
prediction, the same as for Demo I.

## Study design

- **Data:** validation patients only. A deterministic hash split assigns 70% (3,420 patients) to selection and 30%
  (1,530) to confirmation. The test split is never read.
- **Candidates:** 16 rules x 31 thresholds (0.10-0.40). The rules vary sustain (1 or 2 h), cooldown (6, 12 or 24 h),
  episode-based repeat suppression, repeat on a 0.05 risk rise, a rapid-rise comparator (risk >= 1.5 x the 12 h minimum),
  12 h confirmation, and a stricter early threshold (+0.05 at ICU hours 1-6).
- **Selection:** minimise false alerts per patient-day, subject to:
  - detection >= 70% and no more than 5 points below demo-v1;
  - median lead >= 6 h and no more than 2 h below demo-v1;
  - PPV >= 1/3 and at most 1 alert per patient-day;
  - no increase in non-AKI patients alerted;
  - cutoff stability when inputs are withheld 1-2 h: no more than 10% warning flips and 10% patient alert flips, with
    at least 85% of rows paired.
- **Confirmation:** the sealed choice versus demo-v1 on the same risks, with 1,000 paired patient bootstraps.
- **Integrity:** the prespecification (config, input hashes, code hashes) is written before the grid, and the choice is
  sealed before confirmation. Rerunning the notebook reuses these records and refuses changed inputs or code.
- **Checks A1-A12** (`src/aki_eval/checks.py`): unique keys, probability bounds, schemas and hashes, prediction coverage,
  split disjointness, one stay per patient, chronology, label windows, no origin at or after onset, past-only feature
  replay with future-event injection, no future urine, and no test use in fitting. All passed.

## Result

| Metric | demo-v1 | v3 | Difference (95% CI) |
|---|---:|---:|---:|
| False alerts / patient-day | 0.424 | 0.230 | -0.194 (-0.219 to -0.171) |
| Alerts / patient-day | 0.804 | 0.423 | -0.381 |
| Alert PPV | 33.7% | 33.9% | +0.2 pp (-0.9 to +1.3) |
| AKI event detection | 76.9% | 71.9% | -5.1 pp (-7.7 to -2.7) |
| Median first-correct-alert lead | 12.8 h | 11.7 h | -1.1 h |
| Non-AKI patients alerted | 35.8% | 31.1% | -4.7 pp |

Every criterion passed except the detection-loss limit (observed 5.08 points; 212 instead of 213 of 295 events). The
project owner accepted this trade-off on 2026-11-09 and locked the policy (`owner_decision` in the policy file).

## Limits

- The whole validation set was viewed in Demo I, and the confirmation patients were also used in an earlier
  collaborator study (v2, legacy-engine risks). The confirmation is an internal holdout, not an untouched test.
- Metrics are on the hourly grid. Event-driven (minute-level) alerting is not validated.
- This is a research threshold, not a clinical one. The locked test evaluation (notebook 03) is still pending.
