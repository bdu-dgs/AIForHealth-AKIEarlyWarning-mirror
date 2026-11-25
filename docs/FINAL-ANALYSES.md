# Final-report analyses

**Thesis.** AKI risk prediction is already good and stable enough; what keeps it from clinical use is the reliability of
the alerts. When an early-warning system moves to a new setting or is applied to different patient groups, the model
still ranks patients correctly, but its probabilities drift, and a fixed alert threshold silently changes the alert
burden and the share of AKI events caught. We show this on MIMIC-III and give an alerting method that guarantees a
stated false-alert budget using local calibration data.

The analyses below follow that story in three parts. All of them use the locked model; the test split had already been
opened once (test-evaluation-v1), so everything here that uses test data is secondary and prespecified. Nothing here
chose or tuned the model. Each study froze its configuration and code hashes (`prespec.json`) before it ran. All
outputs are aggregate tables.

| Study | Code / settings | Output |
|---|---|---|
| Severity, literature operating point, baselines, alert trade-off, decision curves, ROC/PR/calibration points | `scripts/run_final_analyses.py`, `configs/final_analyses_v1.yaml` | `artifacts/reports/final_analyses_v1/` |
| Exploratory data analysis (training split only) | `scripts/run_eda.py` | `artifacts/reports/eda_v1/` |
| Sequence model (GRU) go / no-go, validation only | `scripts/run_sequence_model.py`, `configs/sequence_model_v1.yaml` | `artifacts/reports/sequence_model_v1/` |
| Feature ablation (top-k inputs, one group dropped), validation only | `scripts/run_feature_ablation.py`, `configs/feature_ablation_v1.yaml` | `artifacts/reports/feature_ablation_v1/` |
| CareVue <-> MetaVision transfer | `scripts/run_system_shift.py`, `configs/system_shift_v1.yaml` | `artifacts/reports/system_shift_v1/` |
| Which inputs drive the transfer drift, train / validation only | `scripts/run_shift_drivers.py`, `configs/shift_drivers_v1.yaml` | `artifacts/reports/shift_drivers_v1/` |
| Fairness by insurance and recorded ethnicity | `scripts/run_fairness.py`, `configs/fairness_structured_v1.yaml` | `artifacts/reports/fairness_structured_v1/` |
| Local recalibration of a moved model | `scripts/run_recalibration.py`, `configs/system_recalibration_v1.yaml` | `artifacts/reports/system_recalibration_v1/` |
| Alert-budget control (the method) | `scripts/run_alert_budget.py`, `configs/alert_budget_v1.yaml` | `artifacts/reports/alert_budget_v1/` |
| Fixes for its limitations, validation only | `scripts/run_alert_budget_dev.py`, `configs/alert_budget_v2_dev.yaml` | `artifacts/reports/alert_budget_v2_dev/` |
| All figures (PDF for LaTeX, PNG for slides) | `scripts/make_report_figures.py` | `artifacts/reports/figures/` |

Code for all of them is in `src/aki_report/`. The run order and runtimes are in `INSTALL.md`. Use the research
environment (`requirements-research.txt`, with xgboost), not the website's `.venv`.

# Part 1. Prediction: how good can AKI risk prediction get?

Establishes that the risk model is accurate, calibrated and close to its ceiling, so better alerting has to come from somewhere other than a better model.

## 1.1 What the data look like: exploratory analysis (training split only, `eda_v1/`, `fig_eda`, `fig_missingness`, `fig_cohort_flow`)

- **Cohort** (`table1_by_split.csv`): the three splits match closely. Median age 65, 57% male, 81% emergency
  admissions, 23% creatinine AKI (stage 1/2/3: 16.6 / 3.7 / 2.7%), 0.9% RRT, 52% urine-output AKI, 80% with a pre-ICU
  baseline creatinine.
- **AKI is early.** Median onset is 31 h after ICU admission; 41% of training AKI starts within 24 h. At-risk
  prevalence is highest in the first ~36 ICU hours and roughly halves after hour 40.
- **Creatinine rises before onset.** The median latest creatinine in patients who will develop AKI is 0.9-1.0 mg/dL
  from 48 h before onset, against 0.8 in patients who never develop AKI. This rise is the model's top input (SHAP).
- **Measurement behaviour carries signal.** Before AKI, pH, lactate and PTT were measured more recently (median pH 3.5
  h vs 5.6 h; lactate 9.1 h vs 13.8 h). This explains why removing measurement-process features costs 0.024 AUROC at
  6 h.
- **Missingness** is highest for respiratory inputs (about 47%) and laboratory summaries (about 19%). It is similar in
  CareVue and MetaVision, except urine output, which is charted more completely in MetaVision.

## 1.2 How well does the model predict? Baselines on the test split (`baseline_metrics.csv`, `baseline_auroc_differences.csv`, `fig_roc_pr`)

All notebook-02 pipelines are frozen and scored on the stored notebook-01 test features. The creatinine-only LR was
refitted with its notebook-02 recipe; its validation AUROC matched notebook 02 to four decimals at every horizon
(`creatinine_lr_refit_check.csv`).

| Model (24 h) | AUROC (95% CI) | AUROC minus locked (95% CI) | AUPRC | Brier |
|---|---|---|---:|---:|
| LightGBM no_dc (locked system) | 0.817 (0.803-0.831) | - | 0.419 | 0.086 |
| LightGBM, all features | 0.814 | -0.003 (-0.006 to 0.000) | 0.414 | 0.086 |
| XGBoost, all features | 0.814 | -0.003 (-0.007 to 0.000) | 0.414 | 0.086 |
| Random forest, all features | 0.794 | -0.023 (-0.029 to -0.017) | 0.380 | 0.089 |
| Logistic regression, all features | 0.788 | -0.029 (-0.039 to -0.021) | 0.370 | 0.090 |
| Creatinine-only LR | 0.703 | -0.114 (-0.128 to -0.099) | 0.277 | 0.097 |
| Creatinine rise from 48 h minimum (rule score) | 0.618 | -0.199 (-0.215 to -0.179) | 0.194 | - |

At the other horizons the ranking is the same (6 h AUROC 0.866 vs 0.819 LR vs 0.718 creatinine LR). The two
boosted-tree families are equivalent. Both beat the linear and bagged models, and every multivariable model beats
creatinine alone by about 0.1 AUROC, so the model adds clearly more than a creatinine-trend rule.

## 1.3 Is there headroom? A sequence model against the locked LightGBM (`sequence_model_v1/`, validation only)

This is a go / no-go study on the validation split; the test split was not loaded. A unidirectional GRU reads the same
246 hourly inputs, plus missing-value indicators, as a sequence. It is trained on 19,548 training patients, with early
stopping and Platt calibration on 3,450 held-out training patients. Three seeds are averaged. The rule fixed in advance
was: go to a test evaluation only if the GRU or the GRU + LightGBM ensemble improves 24 h AUROC by >= 0.010 with a 95%
CI above 0.

| Validation AUROC | 6 h | 12 h | 24 h | 48 h | 24 h AUPRC |
|---|---:|---:|---:|---:|---:|
| LightGBM (locked) | 0.865 | 0.839 | 0.809 | 0.782 | 0.422 |
| GRU (3 seeds) | 0.855 | 0.830 | 0.800 | 0.773 | 0.406 |
| Ensemble (mean of both) | 0.873 | 0.848 | 0.817 | 0.790 | 0.441 |
| Ensemble minus LightGBM (95% CI) | +0.008 (0.005-0.012) | +0.008 (0.005-0.012) | +0.009 (0.004-0.013) | +0.008 (0.003-0.013) | |

- **The GRU alone is about 0.009 worse than LightGBM.** It overfits after 3-5 epochs. This agrees with published ICU
  AKI benchmarks, where gradient-boosted trees match or beat recurrent networks on engineered hourly features.
- **The ensemble is consistently better at every horizon** (+0.008-0.009 AUROC; 24 h AUPRC +0.019). The sequence
  model therefore carries some complementary temporal information.
- **Decision: stop.** The gain is below the prespecified 0.010 threshold, so the ensemble was not taken to the test
  split, and the locked system stays LightGBM. In the report this is a negative result with a positive side-finding:
  feature-engineered trees are close to the ceiling for these inputs, and the remaining headroom is small.

## 1.4 Which inputs does the model need? Feature ablation (`feature_ablation_v1/`, `fig_feature_ablation`, validation only)

The test split was not scored. Inputs were ranked by mean |TreeSHAP| of the locked model on 100,000 training rows per
horizon. Each variant was retrained with the locked procedure (same folds, weights, hyperparameters, round selection
and Platt calibration) and compared with the locked model on validation patients (paired patient bootstrap, 500
draws). The rule fixed in advance: a smaller model shows "no meaningful loss" only if its AUROC is within 0.005 of the
locked model at every horizon.

| Inputs kept | Share of total importance | AUROC change 6 h | 12 h | 24 h (95% CI) | 48 h |
|---|---:|---:|---:|---|---:|
| Top 10 | 25% | -0.091 | -0.066 | -0.049 (-0.058 to -0.041) | -0.055 |
| Top 20 | 38% | -0.024 | -0.021 | -0.023 (-0.030 to -0.016) | -0.028 |
| Top 30 | 48% | -0.015 | -0.015 | -0.015 (-0.020 to -0.009) | -0.017 |
| Top 50 | 63% | -0.006 | -0.007 | -0.009 (-0.013 to -0.004) | -0.012 |
| Top 100 | 84% | -0.003 | -0.003 | -0.003 (-0.006 to 0.000) | -0.005 |

| Input group dropped | Inputs left | AUROC change 6 h | 24 h (95% CI) |
|---|---:|---:|---|
| Creatinine | 233 | -0.033 | -0.029 (-0.038 to -0.021) |
| Other laboratory values | 145 | -0.011 | -0.008 (-0.014 to -0.002) |
| Vital signs | 165 | -0.008 | -0.009 (-0.013 to -0.004) |
| Neurological and respiratory | 238 | -0.004 | -0.006 (-0.009 to -0.003) |
| Fluids, medications, interventions | 228 | -0.007 | -0.005 (-0.008 to -0.002) |
| Urine output | 238 | -0.008 | -0.003 (-0.007 to 0.000) |
| Admission and demographic data | 230 | -0.001 | -0.002 (-0.005 to 0.001) |

- **The model cannot be made much smaller without a loss.** The 100 most important inputs (41%) lose 0.003 AUROC at
  6-24 h and 0.005 at 48 h, which just misses the prespecified 0.005 rule at 48 h. With 50 inputs the loss is
  0.006-0.012; with 20 it is about 0.02-0.03. A 100-input model is a practical option for a site with fewer data feeds,
  not a new compact model.
- **The signal is spread across many inputs.** Importance shares: other laboratory values 41%, vital signs 18%,
  creatinine 17%, admission data 7%, urine output 5%. The top 10 inputs carry only 25% of the total.
- **The model is more than a creatinine trend.** Creatinine is the single most important group (-0.029 at 24 h when
  removed), but without any creatinine input the model still reaches a validation AUROC of 0.780, against 0.70 for a
  creatinine-only model. Every other group costs 0.002-0.011 when removed alone, because the remaining inputs partly
  replace it.

# Part 2. From prediction to alerts: what goes wrong?

Turns the risks into alerts replayed hour by hour, and shows the core problem: when the system moves to another charting system or is used on different patient groups, the ranking holds but the probabilities drift. A fixed threshold then silently changes how many alerts are raised and how many AKI events are caught.

## 2.1 Which AKI events does policy v3 catch? (`severity_detection.csv`, `fig_severity`)

Test split, 959 creatinine-AKI events in the monitored window. An event counts as detected when an alert comes 0-24 h
(or 0-48 h) before onset. Intervals are 95% Wilson intervals over patients.

| Events (maximum KDIGO stage in the stay) | n | Detected, 24 h | Detected, 48 h | Median lead (h) |
|---|---:|---|---:|---:|
| All | 959 | 71.7% (68.8-74.5) | 76.6% | 11.6 |
| Stage 1 | 693 | 68.1% (64.5-71.5) | 73.2% | 12.0 |
| Stage 2 | 144 | 76.4% (68.8-82.6) | 79.9% | 11.3 |
| Stage 3 | 122 | 86.9% (79.8-91.8) | 92.6% | 9.5 |
| Stage 2-3 | 266 | 81.2% (76.1-85.4) | 85.7% | 10.9 |
| Went on to renal replacement therapy | 41 | 97.6% (87.4-99.6) | 97.6% | 10.7 |

The more severe the AKI, the more often it is caught: policy v3 warns before 40 of the 41 AKI episodes that later
needed dialysis.

## 2.2 Comparison with published systems (`operating_points.csv`)

Policy v3 has an alert PPV of 33.5%, which is **1.99 false alerts per true alert**. That is the operating point
Tomasev et al. (Nature 2019) reported, so the two can be put side by side:

| System | Setting | Endpoint and horizon | Result |
|---|---|---|---|
| Tomasev et al., Nature 2019 | US VA, all inpatients, 703k adults | any-stage AKI, up to 48 h ahead, 2 false : 1 true | 55.8% of all AKI episodes; 90.2% of AKI that needed dialysis |
| This project, policy v3 | MIMIC-III ICU, ICU hours 1-72 | creatinine KDIGO any stage, 24 h (48 h) lookahead, 1.99 false : 1 true | 71.7% (76.6%) of AKI events; 97.6% of AKI that needed RRT |
| Koyner et al., Crit Care Med 2018 | U Chicago, ward + ICU, gradient boosting | KDIGO stage 2 within 24 h / 48 h | AUROC 0.90 / 0.87 |
| "Continuous and early prediction of future moderate and severe AKI in critically ill patients", PLOS ONE 2023 (PMC10368244) | ICU, multi-centre, US + EU | KDIGO stage 2-3, at least 14 h ahead | AUROC 0.884 internal; 0.877 / 0.911 external |
| This project, locked model | MIMIC-III ICU | any stage (stage 1 included), 24 h / 48 h | AUROC 0.817 / 0.792 |

**This is a comparison of reported numbers, not a head-to-head test.** The populations, AKI definitions, event
windows and alert counting all differ. Our ICU-only, 72 h window gives more creatinine measurements per patient than a
general ward. Stage-1 AKI is the hardest endpoint, which is why AUROCs from stage 2-3 models are higher.

## 2.3 The alert trade-off and clinical utility (`alert_tradeoff_24h.csv`, `decision_curves.csv`)

- **Alert trade-off** (`alert_tradeoff_24h.csv`, `fig_alert_tradeoff`). Each model's 24 h risk is swept over 40
  thresholds with a 12 h cooldown. At policy v3's burden of 0.23 false alerts per patient-day (interpolated), the
  models detect: LightGBM 72.9%, XGBoost 72.1%, all-feature LR 65.6%, random forest 64.4%, creatinine-only LR 52.7%,
  creatinine rule 41.7%. Median lead time is similar across models (12-13 h).
- **Decision curves** (`decision_curves.csv`, `fig_decision_curve`). At 12 h and 24 h the model's net benefit beats
  "alert everyone" and "alert no one" at every threshold from 1% to 50%. It is the highest of all models or tied with
  XGBoost (within 0.003). At 48 h it ties "alert everyone" at the lowest thresholds. At policy v3's 21% threshold, the
  24 h net benefit is 0.036, against 0.035 for XGBoost, 0.030 for all-feature LR and 0.017 for creatinine-only LR. That
  is equivalent to finding about 36 true AKI origins per 1,000 eligible origins with no unnecessary alerts.

## 2.4 What breaks when the system moves? Ranking holds, probabilities drift (`system_shift_v1/`, `fig_system_shift`)

CareVue (2001-2008) and MetaVision (2008-2012) differ in era, software, item definitions and charting density. Two
models were retrained with the locked procedure, each on **9,810 training patients from one system** (CareVue
subsampled, so size is matched). Each was tested on both systems' test patients.

| Test system | Horizon | Within-system model | Cross-system model | Cross minus within (95% CI) | Locked model (both systems) |
|---|---|---:|---:|---|---:|
| CareVue | 24 h | 0.792 | 0.782 | -0.010 (-0.020 to 0.001) | 0.807 |
| MetaVision | 24 h | 0.821 | 0.806 | -0.015 (-0.026 to -0.005) | 0.832 |
| CareVue | 6 h | 0.855 | 0.839 | -0.015 (-0.023 to -0.006) | 0.867 |
| MetaVision | 6 h | 0.862 | 0.833 | -0.029 (-0.038 to -0.019) | 0.866 |

- **Discrimination** transfers with a modest loss of 0.01-0.03 AUROC, largest at short horizons. Short horizons lean
  most on measurement-process features (see the retraining sensitivity analysis), and charting habits differ between
  the systems.
- **Calibration** transfers worse than discrimination. The CareVue model underestimates MetaVision risk (observed /
  expected 1.16 at 24 h). The MetaVision model overestimates CareVue risk (0.86).
- **Alerting** changes when the policy is applied unchanged. On MetaVision patients, the CareVue-trained model detects
  67.4% of events, against 75.8% for the MetaVision-trained model, at a similar false-alert rate (0.19 vs 0.21 per
  patient-day).
- **Training on both systems helps.** The locked model is 0.01-0.03 better than either single-system model on both
  test systems. Part of that comes from having more patients, part from having seen both charting styles.
- **Implication for deployment**: a model moved to a new site or EHR version should be **recalibrated, and its alert
  threshold re-checked, on local data** before use. This matches the external-validation literature, where calibration
  drifts more than AUROC.

### 2.4.1 Which inputs drive the drift? (`shift_drivers_v1/`, `fig_shift_drivers`, train / validation only)

The test split was not loaded and nothing was retrained. The two system-shift-v1 models were reused. The drift is also
present on validation patients (24 h observed / expected: CareVue model on MetaVision 1.136, MetaVision model on
CareVue 0.924; each model on its own system 0.994 and 1.004). Two analyses:

- **Input comparison.** Each input was compared between the systems on 300,000 training rows per system. Creatinine
  inputs barely differ (mean absolute standardized difference 0.04). The largest differences are in how data were
  charted: weight was missing in 15.3% of CareVue rows against 0.5% of MetaVision rows, so urine output per kg was
  missing in 19.8% against 6.6%; lactate was missing in 45% against 29%; the median time since the last pH was 6.0 h
  against 8.4 h; temperature was charted 6 times per 24 h against 5. Weighted by importance, most of the
  largest differences are in charting (timing, counts, missingness); measured values such as PTT and bicarbonate
  shift less (standardized differences 0.13 and 0.08).
- **Calibration split by input group.** The moved model's TreeSHAP contributions were summed per input group, and on
  the new system's validation patients each group (and the baseline risk) was refitted with one coefficient. Then only
  one part was corrected at a time, to see which single correction brings observed / expected back to 1.

| Moved model (24 h) | O/E before | Best single correction | O/E after it | Creatinine coefficient (95% CI) |
|---|---:|---|---:|---|
| CareVue model on MetaVision | 1.136 | baseline risk only (intercept +0.19, 95% CI 0.03 to 0.32) | 0.987 | 1.05 (0.90 to 1.24) |
| MetaVision model on CareVue | 0.924 | other laboratory values only (coefficient 0.66, 0.44 to 0.82) | 1.007 | 0.98 (0.84 to 1.13) |

- **Creatinine evidence transfers.** Its coefficient is about 1 on both systems and for both models: a creatinine rise
  means the same thing in CareVue and MetaVision.
- **Under-prediction on MetaVision is a baseline change.** No single input group explains it; one baseline shift
  (MetaVision patients have more AKI at the same inputs) brings O/E to 0.99. This is exactly what Platt recalibration
  on local patients corrects (section 3.1).
- **Over-prediction on CareVue comes from the other laboratory values.** The MetaVision model's laboratory
  contributions push CareVue risks up (mean +0.085 logit, against +0.020 on MetaVision), and weighting them down alone
  brings O/E to 1.01. The same laboratory values (not their measurement timing, which is a separate group) carry
  less AKI risk in CareVue than the MetaVision model assumes; era, case mix or laboratory methods could cause this,
  and these data cannot tell which.
- **Limits.** The per-group coefficients are not exactly 1 even on the model's own system (for small groups the 95%
  CIs are wide), because TreeSHAP groups are correlated. Only the large, consistent effects above are interpreted.
- **Implication**: when moving the model, check the laboratory inputs and the charting (timing, missingness, weight
  for urine per kg) first; creatinine inputs carry the same evidence across systems.

## 2.5 What differs between patient groups? Fairness by insurance and recorded ethnicity (`fairness_structured_v1/`, `fig_fairness`)

These are recorded variables of the index admission, used as structured proxies for social position. They replace the
note-based social-risk idea, which was dropped because the external NLP model was not reliable on these notes. Groups
with fewer than 100 patients or 20 events are reported as counts only (self-pay: 70 test patients, 8 events). Results
are shown for the test split, and for the validation and test splits pooled (secondary).

| Group (test) | Patients | AUROC 24 h (95% CI) | O/E | Events detected | False alerts / patient-day |
|---|---:|---|---:|---:|---:|
| Medicare | 2,581 | 0.818 (0.800-0.835) | 0.98 | 75.9% | 0.28 |
| Private | 1,751 | 0.807 (0.777-0.836) | 0.91 | 66.5% | 0.18 |
| Medicaid | 394 | 0.813 (0.761-0.865) | 1.16 | 64.3% | 0.15 |
| Government | 163 | 0.831 (0.725-0.917) | 1.45 | 62.9% | 0.12 |
| White | 3,536 | 0.820 (0.802-0.837) | 0.93 | 72.7% | 0.23 |
| Black | 345 | 0.793 (0.721-0.860) | 1.07 | 72.7% | 0.17 |
| Hispanic | 160 | 0.828 (0.758-0.892) | 1.18 | 53.6% | 0.16 |
| Asian | 125 | 0.852 (0.786-0.910) | 1.06 | 69.6% | 0.17 |
| Other | 125 | 0.817 (0.744-0.878) | 1.38 | 73.0% | 0.28 |
| Unknown / not recorded | 668 | 0.801 (0.766-0.834) | 1.05 | 70.8% | 0.27 |

- **Discrimination is equal across groups.** Every AUROC difference from the reference group (Private, White) has a
  95% CI that includes zero, on test and on validation and test pooled.
- **Calibration is not equal.** Risk is underestimated in Medicaid, government-insured, Hispanic and "other" patients
  (O/E 1.16-1.45 on test, 1.07-1.25 pooled). A fixed threshold therefore alerts less often for them: fewer false
  alerts, but also fewer detected events.
- **Detection gaps.** Medicare patients are detected more often than privately insured patients: +9.3 points
  (2.6-15.1) on test and +11.0 (6.3-16.0) pooled. This is largely an age effect, since Medicare patients are older and
  at higher risk. Hispanic patients are detected less often than White patients: -19.1 points (-37.6 to -0.4) on test,
  but -11.8 (-24.5 to +1.7) pooled. That is one of 16 comparisons and not robust, so treat it as a signal to monitor,
  not a finding.
- **Implication**: equal AUROC does not guarantee equal alerting. Group-level calibration should be monitored after
  deployment, and the same local recalibration as in section 3.1 is the remedy if it drifts.

# Part 3. Reliable alerting: can the alert burden be guaranteed?

Repairs the problem at the alerting step: local recalibration, then a threshold certified to meet a stated false-alert budget, with its data requirements and its limits.

## 3.1 Does local recalibration repair a moved model? (`system_recalibration_v1/`, `fig_recalibration`)

This follow-up to section 2.4 retrains nothing. It keeps the cross-system model and only refits its Platt calibration on
the **target system's validation patients**. It is then judged on the target system's test patients, with policy v3
unchanged.

| Test system | Model | O/E 24 h | Slope 24 h | Events detected | False alerts / patient-day |
|---|---|---:|---:|---:|---:|
| MetaVision | CareVue model, source calibration | 1.16 | 1.13 | 67.4% | 0.19 |
| MetaVision | CareVue model, recalibrated on 2,111 MetaVision patients | 1.01 | 1.16 | 71.2% | 0.22 |
| MetaVision | MetaVision model (within system) | 1.01 | 1.09 | 75.8% | 0.21 |
| CareVue | MetaVision model, source calibration | 0.86 | 0.98 | 67.0% | 0.26 |
| CareVue | MetaVision model, recalibrated on 2,831 CareVue patients | 0.93 | 1.02 | 64.9% | 0.24 |
| CareVue | CareVue model (within system) | 0.93 | 1.01 | 64.9% | 0.24 |

- **Recalibration fixes calibration and the alert burden.** After recalibration, O/E and the alert numbers match a
  model trained on the target system. AUROC cannot change, because recalibration is monotone. On MetaVision it recovers
  about half of the lost detection (67.4% -> 71.2%, within-system 75.8%). The rest is a discrimination gap that only
  local retraining closes.
- **How much local data is needed** (`sample_size_summary.csv`, 20 random draws per size). With 50-100 patients the
  recalibrated O/E varies widely (10th-90th percentile 0.64-1.60), and detection swings between 39% and 84%. With 500
  patients it narrows to about 0.82-1.18; with 1,000 patients to about 0.88-1.05. Practical rule: about **500-1,000 local
  ICU patients** before trusting the recalibrated risks and threshold.

## 3.2 Alert-budget control: a guaranteed alert burden (`alert_budget_v1/`, `fig_alert_budget`)

This is the method contribution. It adds a layer on top of the unchanged risk model that turns a stated **false-alert
budget** (here 0.25 false alerts per patient-day) into an alert threshold, in three steps fitted on calibration
patients:

1. **Group recalibration.** Logistic recalibration per horizon with insurance and ethnicity offsets.
2. **Certified threshold.** Learn-then-Test fixed-sequence testing over a 0.5%-step threshold grid with a
   patient-clustered test. It returns the most sensitive threshold whose false-alert rate is provably within budget
   (delta = 0.05).
3. **Detection parity.** Per-ethnicity thresholds that reach a common detection rate under the same certified budget.

The rule shape stays policy v3 (24 h and 12 h confirmation, 12 h cooldown). Calibration is on validation patients and
evaluation on test patients: a prespecified second test look for this new layer, with the model unchanged. The
coverage column refits the threshold on 500 bootstrap redraws of the calibration patients and reports how often the
test false-alert rate stays within budget (target >= 95%).

| Setting | Method | Threshold | False alerts / pt-day (test) | Within 0.25? | Events detected | PPV | Coverage |
|---|---|---:|---:|---|---:|---:|---:|
| Same system | M0 fixed (policy v3) | 0.21 | 0.227 | yes | 71.7% | 33.5% | - |
| Same system | M1 certified threshold | 0.205 | 0.234 | yes | 72.1% | 33.0% | 99.2% |
| Same system | M2 + group recalibration | 0.21 | 0.231 | yes | 72.1% | 33.3% | 100% |
| Same system | M3 + detection parity | per group | 0.224 | yes | 72.6% | 34.2% | 99.8% |
| MetaVision model -> CareVue | M0 fixed | 0.21 | **0.258** | **no** | 67.0% | 29.7% | - |
| MetaVision model -> CareVue | M1, no local data (threshold certified on the source system) | 0.215 | 0.250 | borderline | 66.1% | 30.1% | **64.6%** |
| MetaVision model -> CareVue | M1, local data (2,831 patients) | 0.225 | 0.233 | yes | 63.8% | 30.9% | 97.6% |
| CareVue model -> MetaVision | M0 fixed | 0.21 | 0.190 | yes | 67.4% | 36.5% | - |
| CareVue model -> MetaVision | M2, local data (2,111 patients) | 0.225 | 0.202 | yes | 69.4% | 35.8% | 100% |

- **The budget is met when the method sees local data, and fails when it does not.** On the same system the
  guarantee holds in 99-100% of redraws. Moved to CareVue, the fixed threshold breaks the budget (0.258). A threshold
  certified on the source system keeps it only 65% of the time and overshoots larger budgets (0.305 for 0.30, 0.423 for
  0.40). Certified on local CareVue patients, it keeps every budget from 0.15 to 0.40 and holds in 97.6% of redraws
  (`budget_sweep.csv`). The price is about 3 points of detection: the guarantee makes the threshold more cautious.
- **Group recalibration closes the largest detection gap at the same burden (not confirmed on validation; see 3.3).** Hispanic patients were under-predicted
  (O/E 1.18 -> 1.00 after recalibration). Their detection rises from 53.6% (fixed) to 67.9% (step 1) and 75.0% (step 3),
  while total false alerts fall slightly (0.227 -> 0.224) and overall detection rises (71.7% -> 72.6%). Among the three
  groups with >= 50 events (White, Unknown, Black) detection spans 70.8-72.7% fixed and 71.6-76.4% with step 3.
- **Limits.** Groups with fewer than 30 test events (Asian 23, Hispanic 28, Other 37) are noisy: Asian detection fell
  (69.6% -> 60.9%) and Other rose to 91.9%. Exact parity is not achieved at this sample size. The insurance offsets
  learned on validation did not carry over to test (Medicaid O/E 1.16 -> 1.18). Recalibration with local data on the
  CareVue shift reached 90.4% coverage, below the 95% target. Within the same system, the certified threshold
  (0.205-0.21) confirms that policy v3 was already close to the most sensitive threshold for this budget, so the gain
  there is the guarantee, not more detection.

## 3.3 Can the limitations be fixed? Validation-only check (`alert_budget_v2_dev/`)

Three fixes were tested on validation patients only; the test split was not loaded. Validation was split at random into
a fitting half and an evaluation half, 20 times, and results are averaged. The adoption rule was fixed in advance.

| Fix | Idea | Result on the evaluation halves | Adopted? |
|---|---|---|---|
| A. Learn group offsets from 22,998 training patients instead of ~2,500 validation patients | more data, steadier offsets | Mean calibration error across groups (mean of abs(log O/E)): no recalibration 0.12, offsets from half of validation 0.24, offsets from training 0.17 | No: better than the v1 method, but worse than not recalibrating at all |
| B. Learn per-group thresholds from training patients | more AKI events per group | Detection range across ethnicity groups: single threshold 16 points, v1 parity method 31, training-based parity 20; worst-group detection 62%, 54%, 64% | No: the range is not smaller than with one threshold |
| C. Recalibrate on one half of the local patients and certify the threshold on the other | no double use of local data | Coverage after system change: MetaVision 86% -> 94%, CareVue 90% -> 90%; detection -2 to -3 points | No: still below 95% |

**What this means.**

- In validation, the groups are already well calibrated (Medicaid O/E 1.04, Government 1.01, White 1.02). The
  under-prediction seen on test (Medicaid 1.16, Hispanic 1.18) is not a stable feature of the model.
- Learning group offsets or group thresholds from a few hundred patients mostly adds noise. The Hispanic detection gain
  seen on test in section 3.2 (54% -> 75%) is therefore **not confirmed**, and should be reported as unconfirmed.
- The robust part of the method is step 2, the budget-certified threshold fitted on enough local patients. Its
  coverage falls when the local sample shrinks (86-90% with 60% of the local validation patients, against 98% with all
  of them in section 3.2).
- For fairness, the defensible recommendation is to **monitor** group calibration and alert rates after deployment.
  Correcting them needs thousands of patients per group.

None of the fixes meets the adoption rule, so there is no further test evaluation.

## Figures (`artifacts/reports/figures/`)

`fig_pipeline` (system overview), `fig_cohort_flow`, `fig_eda`, `fig_missingness`, `fig_roc_pr`, `fig_calibration`,
`fig_alert_tradeoff`, `fig_decision_curve`, `fig_severity`, `fig_shap`, `fig_patient_example` (synthetic demo
patient), `fig_subgroups`, `fig_retrain_sensitivity`, `fig_feature_ablation`, `fig_system_shift`, `fig_shift_drivers`,
`fig_recalibration`, `fig_fairness`, `fig_alert_budget`. Each is a PDF (vector, for LaTeX) and a
300 dpi PNG.
