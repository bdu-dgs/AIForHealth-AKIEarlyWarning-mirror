# Ke Jin: artifact checks and alert-policy evaluation

This guide owns the completed Ke Jin experiment's methods and module interfaces. General website operation stays in [dashboard/README.md](../dashboard/README.md), live JSON rules in [DATA-CONTRACT.md](../dashboard/DATA-CONTRACT.md), and actual checks/results in [VALIDATION.md](../dashboard/VALIDATION.md). [The notebook](../pipeline/KeJin_alert_policy_and_checks.ipynb) is the execution entry point.

## Scope and system decisions

The LightGBM no_dc model, its calibration, original feature engine, original metric definitions and Demo I policy remain fixed. There is no retraining or test-feature/outcome loading.

The supplied scaffold chose on train OOF and confirmed on validation. The user required validation-set warning-threshold selection linked to cutoff stability. The new experiment therefore assigns validation patients deterministically to selection (70%) and confirmation (30%), using a SHA256-based assignment with seed 20261102. No patient's rows cross roles. Training OOF is a diagnostic comparison after the choice is sealed.

The full-validation baseline had already been viewed in Demo I and during restoration. Confirmation is an internal patient holdout for this candidate, not an untouched final test. Baseline reproduction and data-integrity checks may read both validation roles; candidate choice uses selection-role results only.

## Prespecification and version history

[configs/alert_policy_v1.yaml](../configs/alert_policy_v1.yaml) defines the complete finite grid and constraints before a run. The file name is retained; its explicit study version records amendments.

- v1 stopped before a candidate choice or confirmation because an additional bit-exact event-replay risk control failed.
- Stored features still reproduced saved risks exactly. In a 31,694-row selection-role diagnostic, exported-event replay had 865 rows differing by more than 1e-6 in risk; most feature differences were inside the original feature tolerance, with one larger heart-rate-standard-deviation discrepancy. This exposed a loss of downstream risk parity despite near feature-level agreement. The later [numerical diagnosis](REPLAY-NUMERIC-DIAGNOSIS.md) localizes batch-dependent cumulative arithmetic and rounded admission-static inputs; feature-tolerance agreement alone is insufficient.
- A future-event-injection diagnostic on those rows changed no risk by more than 1e-6. This does not prove universal future-data invariance.
- v2 adds a zero-lag event-replay control and two reference bases: stored snapshot and zero-lag replay. The candidate grid, threshold range and quality constraints were not tuned after those diagnostics.
- The initial v1 grid computation was aborted before its results were saved or inspected for a choice. Failed execution files and the earlier records remain local.

Before any new grid evaluation, the context writes an exclusive prespec record with configuration, input/model hashes and evaluation-code hashes. Existing records cannot be replaced with different contents. A local immutable record is not public preregistration, and no pre-results Git commit or PR is claimed. Any future adaptive revision needs a new labelled development cycle and appropriate untouched evaluation data.

## Modules

| Module in `src/aki_eval/` | Responsibility |
|---|---|
| `checks.py` | A1–A12 reusable predicates; counts-only diagnostics |
| `replay.py` | Fixed-origin feature replay with measured-data cutoffs |
| `policies.py` | Stateful alert events and hourly metric aggregation |
| `protocol.py` | Deterministic patient roles, immutable prespec and sealed choices |
| `data.py` | Version-checked local artifacts and study context |
| `study.py` | Baseline reproduction, audit, selection, confirmation and exports |

Check predicates return `(passed, detail)`. Synthetic tests include passing and deliberately broken cases. The complete synthetic workflow also checks confirmation reuse and rejection of an unlocked candidate by the current live loader. A separate report-only finalizer uses saved aggregate grids to produce point-constrained OOF representatives and an explicit confirmation acceptance table; it cannot modify the sealed choice or confirmation.

## Audit evidence

A1 keys; A2 probability bounds; A3 schema/dtype/config/model hashes; A4 prediction coverage; A5 patient splits; A6 selected-stay mapping; A7 chronology; A8 labels/eligibility; A9 pre-onset origins; A10 sampled past-only replay plus future-event injection; A11 urine-only future truncation; A12 fitting declarations and saved OOF fold roles.

The model-input replay sample has at least 2,000 rows and uses the original rtol=1e-5, atol=1e-4 feature tolerance. It checks the 246 deployed features, not the excluded discharge-coded feature set.

Admission-static values and hourly surgical-service states are supplied by the snapshots. Their raw source timing is not independently reconstructible from the handoff. A12 checks historical manifest declarations plus actual prediction-fold invariants; it cannot independently prove historical fitting from absent training data. These limitations are carried in checks.csv.

## Alert semantics

Risk probability, current above-threshold status and a newly emitted alert are distinct quantities. Only alert events are changed by this experiment.

- Sustained risk: k consecutive hourly scores correspond to observed high duration >= k-1 h. Gaps over 1.01 h reset sustain and episodes.
- Cooldown: elapsed time since the last emitted alert, retained across episode boundaries.
- Episode mode: one alert during a continuous above-threshold stage, until risk falls below threshold or a gap resets the episode.
- Episode-or-rise mode: after cooldown, repeat only for a fresh episode or an absolute risk increase >= 0.05 since the previous alert.
- Rapid-rise comparator: current risk >= 1.5 times the minimum prior risk within the previous 12 clock hours.
- Missing risk is unscorable; it resets episode/sustain rather than becoming zero probability.

The finite study has 12 rules x 31 thresholds from 0.10 through 0.40. Multi-horizon escalation and an unvalidated confidence formula are excluded to avoid adding unsupported mechanisms. Hourly metrics must not be interpreted as event/minute-grid performance. The event engine uses elapsed time, but such deployment remains unvalidated.

## Cutoff stress and selection

Origins and target windows remain fixed. Rolling windows remain anchored at origin; only events with measured time <= origin-lag are supplied. Laboratory reporting delay still applies relative to origin. Surgical service uses the last recorded hourly state available at cutoff.

Prespecified patient samples (up to 800 selection and 400 confirmation patients) provide zero/one/two-hour replay scenarios. Tables compare against both saved snapshot risks and zero-lag replay risks. They report paired coverage, risk change, point-warning flips, alert-event flips and patient alert-presence flips.

These are end-to-end sensitivity checks. Admission-input precision and numeric aggregation can affect the replay, so all changes cannot be causally attributed to removed clinical measurements. Zero-lag representation tables expose that uncertainty; feature-replay review remains a deployment blocker.

Selection minimises false alerts per scorable monitored patient-day, with:

- Detection >= 0.70 and loss <= 0.05 relative to the frozen policy on the same selection patients.
- Median correct-alert lead >= 6 h and loss <= 2 h.
- Alert PPV >= 1/3; <= 1 alert per scorable monitored patient-day.
- No increase in non-target-event patients alerted.
- Worst point-warning flip fraction <= 0.10, worst patient alert-presence flip fraction <= 0.10, paired coverage >= 0.85.

Ties prefer fewer non-target-event patients alerted, then simpler rules, then a higher threshold. If no candidate meets every constraint, the study reports no feasible candidate and leaves the criteria unchanged.

## Confirmation and export

The chosen configuration is sealed before confirmation. Only it and the frozen baseline are compared on confirmation patients. Use 1,000 paired patient bootstrap resamples for estimates, marginal intervals and intervals for candidate-minus-baseline differences. Confirmation failures do not trigger retuning. Rerunning the same completed analysis reuses its fixed confirmation record.

Original metric definitions are preserved: exposure is scorable hourly rows / 24; unknown-outcome alerts count toward burden but not PPV; non_aki refers to no target event within the evaluated monitoring/horizon risk set, not proven lifetime absence of AKI.

Outputs are aggregate-only and versioned under `artifacts/handoff/kejin/`. `LATEST.json` names the run. Important files are `checks.csv`, `prespec.json`, `choice.json`, OOF diagnostic grids, validation-selection grids, zero-lag representation tables, cutoff stability tables, `policy_confirmation_validation.csv`, `confirmation.json`, `policy_v1_candidate.json`, trade-off PNGs, `SUMMARY.md` and the run/execution manifests. OOF table names are retained for handoff compatibility, but explicitly marked diagnostic, not selection.

The exported candidate has `threshold.locked=false` and requires a stateful alert adapter plus feature-replay review. The live loader rejects the unlocked policy. It is not sufficient to copy a research threshold into the website: instantaneous threshold display does not implement cooldown/episode event logic. Default `demo-v1` is not replaced automatically.

## Reproduction

Optional dependencies are in [requirements-eval.txt](requirements-eval.txt), which preserves the model pins and current pytest version:

```powershell
.\.venv\Scripts\python.exe -m pip install -r handoff\requirements-eval.txt
.\.venv\Scripts\python.exe -m pytest tests -q
```

The thin CLI runs the same package:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_alert_policy.py
```

Run the notebook from a fresh project kernel. Its source outputs stay cleared; the local executed copy contains only aggregate output. The main package does not import matplotlib or notebook execution tools during website startup.

## Recorded v2 outcome

The completed run is `kejin-validation-policy-v2-21d980db7c292219`: 3,420 selection patients and 1,530 confirmation patients. Its source notebook's 7 code cells ran in a fresh project Jupyter kernel. A second fresh-kernel execution reused the same cached analysis; prespec, choice and confirmation hashes were unchanged. Source outputs remain cleared.

The selected rule was a 23% threshold with 12h cooldown. On confirmation:

| Metric | Frozen demo-v1 | Candidate |
|---|---:|---:|
| Target-event detection | 76.95% | 73.56% |
| False alerts / scorable patient-day | 0.424 | 0.246 |
| Alerts / scorable patient-day | 0.805 | 0.453 |
| Alert PPV | 33.78% | 33.19% |
| Median first-correct-alert lead | 12.82 h | 12.09 h |
| Non-target-event patients alerted | 35.79% | 31.98% |

Paired 95% bootstrap intervals for differences were -0.200 to -0.157 false alerts/scorable patient-day and -5.74 to -1.31 percentage points of event detection. The PPV point estimate was below the prespecified 1/3 floor, so the candidate is **not confirmed**. Other configured point/stability constraints passed. No criterion was relaxed and no post-confirmation tuning was performed.

Zero-lag controls on the larger stability samples still show numeric representation differences beyond the sampled A10 scope: 2 selection and 5 confirmation feature rows exceed the original feature tolerance; corresponding risk differences can be materially larger near tree splits. Those controls are explicitly recorded rather than concealed by the sampled check's pass. The candidate remains inactive, with feature replay and a compatible live alert-event adapter requiring further review. Final test and clinical validation remain pending.

## Post-confirmation diagnosis

The [numerical diagnosis and follow-up plan](REPLAY-NUMERIC-DIAGNOSIS.md) reports the complete-population controls, independently reconstructed admission-baseline precision, same-window arithmetic dependence and fixed-candidate cutoff recheck. The original candidate selection and PPV non-confirmation survive these controls. Individual risks/events can change, so feature reproducibility still requires repair before activation. A separate rapid-rise history-cache defect is recorded there; it does not affect the selected cooldown-only rule. No original result was retuned or replaced.

## Subsequent software repair

The original results above belong to the frozen source at checkpoint `54cf16c`. The [v2 repair contract and evidence](FEATURE-REPAIR-V2.md) document corrected window arithmetic/admission inputs, fresh risk history and resumable reports. Old model artifacts retain their legacy engine; the corrected path was validated separately with fixed weights. The original choice, confirmation and output directory were not rewritten. The current source and source hashes are intentionally different from the historical prespecification.
