# Dynamic AKI early warning in the ICU: final report

Course research prototype on MIMIC-III v1.4. Not a validated clinical product; risks and warnings are not treatment
recommendations. All numbers below are aggregate; patient-level data stay on the local machine.

## 1. Question and design

Can routinely charted ICU data predict creatinine-defined acute kidney injury (KDIGO) 6, 12, 24 and 48 hours ahead,
updated every hour, with an alert burden that a unit could tolerate?

| Element | Choice |
|---|---|
| Cohort | Adults, first eligible ICU stay per patient; ESKD/dialysis/transplant, pre-ICU creatinine >= 4 mg/dL and follow-up < 7 h excluded; 32,907 patients |
| Split | 70 / 15 / 15 by patient (train / validation / test), stratified by source system, fixed before any snapshot was built |
| Prediction origins | Every ICU hour 1-72 until AKI onset, discharge, death or RRT; 1.34 million origins |
| Outcome | Creatinine KDIGO within 6, 12, 24, 48 h; censored horizons are ineligible, not negative |
| Features | 246 as-of features (vitals, labs with a 1 h result delay, urine, fluids, drugs, interventions, admission data); discharge-coded diagnoses excluded because they are unknown during the stay |
| Model | LightGBM with one head per horizon, patient-exposure and class weights, hyperparameters by 5-fold patient-grouped CV on train, Platt calibration on training out-of-fold scores (run `aki_ml_v1_28e0b1fb83`, `no_dc`) |
| Alert policy v3 | Warn when the 24 h risk is >= 21% and the 12 h risk is >= 10.5%; 12 h cooldown for burden metrics ([ALERT-POLICY.md](ALERT-POLICY.md)) |
| Feature engine at use | Batch-independent `patient_window_v2`, revalidated with the locked weights ([FEATURE-ENGINE-V2.md](FEATURE-ENGINE-V2.md)) |

Development used train and validation only: model families and horizons (notebook 02), monitoring window and
threshold (Demo I), stability (Jiaju), and the alert policy (selected on 70% of validation patients, confirmed on the
other 30%). The test split was opened once, on 2026-11-10, after the full protocol (`configs/evaluation_test_v1.yaml`)
and the code had been frozen with their hashes (`artifacts/reports/test_v1/prespec.json`). Test results were not used
to change anything.

## 2. Test results (4,959 patients, 202,797 hourly origins)

### Discrimination and calibration

Platt calibration, eligible origins in ICU hours 1-72, 95% CI from 1,000 patient-cluster bootstrap resamples.

| Horizon | Origins | AKI prevalence | AUROC | AUPRC | Brier | Observed / expected | Calibration slope |
|---|---:|---:|---|---|---:|---|---|
| 6 h | 185,059 | 2.8% | 0.866 (0.856-0.876) | 0.184 (0.165-0.205) | 0.025 | 0.98 (0.92-1.04) | 1.00 (0.95-1.05) |
| 12 h | 166,998 | 5.8% | 0.841 (0.830-0.853) | 0.285 (0.260-0.311) | 0.047 | 0.98 (0.93-1.04) | 1.04 (0.99-1.09) |
| 24 h | 134,149 | 11.9% | 0.817 (0.803-0.832) | 0.419 (0.388-0.450) | 0.086 | 0.98 (0.92-1.03) | 1.06 (1.00-1.13) |
| 48 h | 95,052 | 24.4% | 0.792 (0.775-0.810) | 0.573 (0.540-0.605) | 0.145 | 0.98 (0.92-1.04) | 1.07 (0.99-1.16) |

Test discrimination matches validation (0.865 / 0.839 / 0.809 / 0.782), and calibration holds. Isotonic calibration
gives the same AUROC and Brier score. Discrimination is lowest in the first ICU hours (24 h AUROC 0.764 at hours 1-3,
0.791 at 4-6) and at hours 61-72 (0.766); it is 0.81-0.83 between hours 7 and 60.

### Alerting (policy v3 versus Demo I policy demo-v1, same risks)

| Metric | demo-v1 | v3 | v3 minus demo-v1 (95% CI) |
|---|---:|---:|---|
| Alerts per patient-day | 0.80 | 0.42 | -0.38 (-0.40 to -0.36) |
| False alerts per patient-day | 0.42 | 0.23 | -0.19 (-0.20 to -0.18) |
| Alert PPV | 34.0% | 33.5% | -0.5 pp (-1.2 to +0.3) |
| AKI events detected (n = 959) | 76.6% | 71.7% | -4.9 pp (-6.3 to -3.5) |
| Median first-alert lead time | 12.9 h | 11.6 h | -1.3 h (-2.2 to -0.8) |
| Patients without AKI ever alerted | 36.2% | 31.5% | -4.7 pp (-5.3 to -4.1) |
| Alerts per alerted patient | 3.0 | 1.7 | -1.3 |

Policy v3 halves the alert count and cuts false alerts by 45% at the same PPV, for about 5 points of event detection
and one hour of lead time. This is the trade-off seen and accepted on validation (detection 71.9%, false alerts 0.23
per patient-day). As a point-in-time warning on eligible origins, the 24 h rule has sensitivity 47.7%, specificity
89.9% and PPV 39.1%.

### Event-driven operation

Recomputing risk at every new input (478,698 update times; median 73 updates per patient, median risk change 0.002 per
update) gives the same performance as the hourly grid: 24 h AUROC 0.829 versus 0.817, event detection 73.4% versus
71.7%, 0.24 versus 0.23 false alerts per patient-day.

### Subgroups (24 h)

| Group | AUROC (95% CI) | Event detection |
|---|---|---:|
| Age 18-64 / 65-79 / 80+ | 0.815 / 0.816 / 0.806 | 66% / 76% / 74% |
| Male / female | 0.814 / 0.821 | 71% / 72% |
| MICU / SICU | 0.821 (0.796-0.847) / 0.822 (0.776-0.866) | 69% / 68% |
| CSRU (cardiac surgery) | 0.761 (0.737-0.787) | 81% |
| CCU (coronary care) | 0.755 (0.719-0.791) | 67% |
| TSICU (trauma surgery) | 0.799 (0.743-0.851) | 61% |
| CareVue / MetaVision era | 0.807 / 0.832 | 70% / 75% |

Discrimination is similar by age and sex. It is lower in cardiac units (CSRU, CCU), where post-operative and
haemodynamic creatinine changes are common; CSRU risk is underestimated (observed / expected 1.15) while MICU, SICU and
TSICU risks are overestimated (0.83-0.88). The MetaVision era, with denser charting, performs better than CareVue.

### Sensitivity analyses

| Analysis (test) | 24 h AUROC | Observed / expected | Event detection |
|---|---:|---:|---:|
| Primary (creatinine KDIGO) | 0.817 | 0.98 | 71.7% |
| Baseline: MDRD estimate when no pre-ICU creatinine | 0.812 | 0.99 | 66.3% |
| Baseline: minimum including later history | 0.819 | 0.99 | 72.0% |
| Urine-output KDIGO endpoint | 0.618 | 3.76 | 38.8% |

Results do not depend on the baseline-creatinine definition. The model does not predict urine-output AKI: it was not
trained for it, it underestimates that risk about fourfold, and it detects 39% of urine-output events (PPV 64%, since
many patients meet both definitions). A combined endpoint would need its own model.

### Feature-window and missing-data choices (retrained)

Each variant was retrained with the notebook-02 procedure (same folds, weights, hyperparameters, round selection and
Platt calibration) and changes one input choice (`configs/robustness_retrain_v1.yaml`, frozen before training). A
refit without changes reproduced the locked model exactly (identical round counts and validation scores). Test AUROC
differences versus the locked model, with 95% CI from 500 paired patient-bootstrap resamples:

| Variant | Inputs | 6 h | 12 h | 24 h | 48 h |
|---|---:|---|---|---|---|
| Vitals and labs: latest values only, no window summaries | 141 | -0.003 (-0.006 to +0.001) | -0.002 | -0.001 | +0.000 |
| Vitals and labs: 6 h summaries only (no 24 h / 48 h) | 162 | -0.002 | -0.001 | +0.003 (-0.001 to +0.006) | +0.001 |
| Vitals: 24 h summaries only (no 6 h) | 225 | -0.000 | -0.000 | -0.001 | -0.001 |
| Median imputation instead of native missing handling | 246 | -0.001 | -0.002 | -0.000 (-0.003 to +0.003) | +0.001 |
| Median imputation plus missing indicators | ~380 | +0.001 | -0.002 | -0.000 | -0.001 |
| No measurement-process features (time since last value, counts) | 208 | **-0.024 (-0.029 to -0.018)** | **-0.008** | **-0.004 (-0.007 to -0.001)** | -0.003 |

Window lengths and the handling of missing values do not matter: every difference is within 0.003 and every
confidence interval includes zero, with unchanged Brier scores and calibration. Removing the measurement-process
features (how recently and how often each variable was measured) costs 0.024 AUROC at 6 h and 0.004 at 24 h. The model
therefore uses clinicians' measurement behaviour as a signal, most at short horizons; it still discriminates well
without it (24 h AUROC 0.813), but its short-horizon performance partly depends on local testing practice, which may
differ elsewhere.

### Most influential inputs (TreeSHAP, 24 h, 1,500 test patients)

Creatinine rise from the 48 h minimum, latest creatinine, ICU hours elapsed, maximum PTT in 48 h, age, time since the
last pH, creatinine relative to the 7-day minimum, bicarbonate, systolic blood-pressure trend and recent urine output
per kg. The model is mainly a creatinine-trajectory model, refined by haemodynamics, acid-base status and urine output.
These are associations, not causes.

## 3. Website

The local workbench (`Start-AKI.cmd`, `Start-AKI-Demo.cmd`) scores each patient whenever new data arrive and shows the
four horizon risks, the policy v3 warning and the top five drivers. A public read-only version with four synthetic
patients, replayed hour by hour with the same model and policy, is in `demo-static/` and deploys to Vercel without a
backend ([STATIC-DEMO.md](STATIC-DEMO.md)).

## 4. Limitations

- Single centre (Beth Israel Deaconess, 2001-2012) with shifted dates; no external or prospective validation.
- The validation set was used for several decisions (window, threshold, policy); only the test split is untouched.
  The policy missed its prespecified detection-loss limit by 0.08 points and was adopted by an owner decision.
- Creatinine-only labels; urine-output AKI is not predicted. Creatinine sampling depends on clinician suspicion, which
  the model can learn (measurement-process features).
- Lower discrimination in cardiac surgery and coronary care units and in the first six ICU hours.
- The model was trained on the legacy feature engine and is used with the batch-independent engine; validation showed
  no AUROC change, but a retrained model should use one engine throughout.
- Alert metrics come from retrospective replay; clinical response to alerts is not modelled.

## 5. Reproducibility

| Step | Entry point | Output |
|---|---|---|
| Dataset | `pipeline/01_dataset_construction.ipynb`, `configs/default.yaml` | `../Cleaned` (local) |
| Models | `pipeline/02_ml_pipeline.ipynb`, `configs/ml.yaml` | `artifacts/models/aki_ml_v1_28e0b1fb83` (local) |
| Window and threshold | `pipeline/demo_i_evaluation.ipynb` | `artifacts/demo_i/policy.json` |
| Stability | `pipeline/Jiaju_stability.ipynb` | `artifacts/handoff/jiaju/` |
| Alert policy and checks A1-A12 | `pipeline/KeJin_alert_policy_and_checks.ipynb`, `configs/alert_policy_v3.yaml` | `artifacts/policy/policy_v3.json` |
| Locked test evaluation | `scripts/evaluate_test.py`, `configs/evaluation_test_v1.yaml` | `artifacts/reports/test_v1/` |
| Retraining sensitivity | `scripts/run_retrain_robustness.py`, `configs/robustness_retrain_v1.yaml` | `artifacts/reports/robustness_retrain_v1/` |
| Summary of all of the above | `pipeline/03_evaluation_and_testing.ipynb` | - |
| Static demo | `scripts/export_static_demo.py`, `pnpm build:static` | `demo-static/` |
