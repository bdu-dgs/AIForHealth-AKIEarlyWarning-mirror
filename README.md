# AKI early warning that stays reliable when the system changes

A course research project on MIMIC-III v1.4. It is a research prototype, not a validated clinical product; risks and
alerts are not treatment recommendations.

## The goal

We want an AKI (acute kidney injury) early-warning system that can be used in real clinical settings, and we focus on
one problem: **after an AKI model moves to a new record system, its alerts become unreliable.** Published work shows
that AKI can be predicted hours ahead (Tomašev et al. 2019), but electronic AKI alerts did not improve outcomes in a
multicentre trial (Wilson et al. 2021), and AKI models keep their ranking but lose calibration at new sites (Song et al.
2020; Davis et al. 2017). Nobody guarantees that the alerts a clinician sees stay reliable in a new setting.

We train our own model because published AKI models are not released, and because the later experiments need full
control of training (retraining on one record system, calibrating with out-of-fold training predictions).

## The project in three steps

| Step | Question | What we did | What we found |
|---|---|---|---|
| **1. Build a strong baseline** | How good can an AKI model get on this data? | One adult ICU stay per patient (earliest; at least 7 h of follow-up): **32,907 patients**. A snapshot every ICU hour 1-72 with **246 features** from the past only, and four answers: AKI within **6/12/24/48 h**. Logistic regression, random forest, LightGBM and XGBoost, one model per horizon (20 models). Improvement checks: a GRU sequence model (another way to read the data), retraining with other feature statistics, and an input ablation (fewer inputs) | LightGBM is best: test AUROC 0.866 / 0.841 / 0.817 / 0.792, observed / expected 0.98, about 0.11 above a creatinine-only model, in the range of published models. GRU + LightGBM gains only 0.009; other feature windows change AUROC by at most 0.003; the top 100 inputs lose 0.003-0.005, and without creatinine the model still reaches 0.780. **With creatinine-defined AKI and this training set, the model is near its ceiling: a strong baseline** |
| **2. Move it and see what breaks** | Does the baseline's alerting hold up on a new record system, and if not, why? | Alert rule **policy v3** (24 h risk >= 21% and 12 h risk >= 10.5%) chosen on validation for the model trained on both record systems. Then two models retrained on one system each (CareVue 2001-08, MetaVision 2008-12) and used on the other, with policy v3 applied unchanged. Which inputs cause the drift; the same check across insurance and ethnicity groups | In place, policy v3 warns before 71.7% of AKI events about 12 h ahead (40 of 41 later dialysis cases). After a move, **ranking holds** (AUROC -0.01 to -0.03) but **risks drift** (observed / expected 1.16 and 0.86), so the fixed threshold fails: false alerts exceed the budget (0.258 > 0.25) or detection falls from 75.8% to 67.4%, as the literature reports. Creatinine transfers; the drift comes from a baseline-risk change and the other laboratory inputs, and charting differs most. This is information only the new system's own data contain. Some patient groups are under-predicted and get fewer alerts |
| **3. Repair the alerts** | Can the alert burden be kept? | On patients from the new system: **recalibrate** the risks (refit Platt calibration; 500-1,000 patients are enough), let the unit **set a false-alert budget**, then **certify the threshold**: test thresholds from strict to loose and keep the most sensitive one that stays within budget with 95% confidence (Learn-then-Test). Website panel to switch budgets | Without local data the budget holds 65% of the time; with local data **98-100%**, at a cost of -3 to +2 points of detection. Group-specific corrections were unstable (too few patients per group), so group calibration is monitored instead |

**Main line.** We first build a strong baseline: with creatinine-defined AKI and without more training data, the model
is near its ceiling, because a sequence model, other feature statistics and fewer inputs give no meaningful gain. We
then move it between record systems. As the literature reports, its alerts fail, and the cause is not a loss of
ranking but probability drift that breaks the fixed threshold. The drift comes from the new system's baseline risk and
inputs, which only local data reveal. So we repair the output layer with a small local sample: we recalibrate the risks,
then choose the most sensitive threshold that stays within the false-alert budget the unit sets.

**Limits.** Both record systems come from one hospital, so this is not a fully external validation; only creatinine
AKI is predicted (a urine-output AKI model would be separate); the small loss of ranking after a move needs local
retraining; there is no prospective or outcome evidence.

Every number: [docs/FINAL-ANALYSES.md](docs/FINAL-ANALYSES.md) (its Parts 1-3 are Steps 1-3) and
[docs/FINAL-REPORT.md](docs/FINAL-REPORT.md) (locked test evaluation). Figures: [artifacts/reports/figures/](artifacts/reports/figures/).

## Where each step lives

| Step | Notebooks (`pipeline/`) | Scripts (`scripts/`) | Docs (`docs/FINAL-ANALYSES.md`) |
|---|---|---|---|
| 1. Build the model | 01 dataset, 02 training, 04 stability, **07 results** | `run_eda.py`, `run_retrain_robustness.py`, `run_sequence_model.py`, `run_feature_ablation.py`, `run_final_analyses.py` | Part 1 (1.1 EDA, 1.2 baselines, 1.3 GRU, 1.4 ablation) |
| 2. Move it | 03 Demo I threshold, 05 policy v3 and checks, 06 locked test evaluation, **08 results (first half)** | `evaluate_alert_policy.py`, `evaluate_test.py`, `run_system_shift.py`, `run_shift_drivers.py`, `run_fairness.py` | Part 2 (2.1-2.3 policy results, 2.4 system shift, 2.4.1 drift drivers, 2.5 groups) |
| 3. Repair the alerts | **08 results (second half)** | `run_recalibration.py`, `run_alert_budget.py`, `run_alert_budget_dev.py` | Part 3 (3.1 recalibration, 3.2 alert-budget control, 3.3 limits) |
| Website | - | `score_dashboard.py`, `export_static_demo.py` | [docs/STATIC-DEMO.md](docs/STATIC-DEMO.md), [dashboard/README.md](dashboard/README.md) |

```text
MIMIC-III v1.4 (local, credentialed)
 |
 |  Step 1  Build a strong baseline
 |   01 dataset construction ---- cohort, KDIGO creatinine labels, hourly features from the past only
 |   02 model training ---------- LR, RF, LightGBM (locked), XGBoost; one model per horizon; Platt calibration
 |   04 stability analysis ------ calibration, SHAP and risk stability across ICU time (validation)
 |   07 results ----------------- baselines, calibration, improvement checks (GRU, retraining, ablation)
 |
 |  Step 2  Move it and see what breaks
 |   03 first alert threshold --- Demo I rule
 |   05 alert policy v3 --------- 12 automated checks; policy chosen and confirmed on validation, then locked
 |   06 locked test evaluation -- test split opened once
 |   08 results (first half) ---- policy results, system shift, drift drivers, patient groups
 |
 |  Step 3  Repair the alerts
 |   08 results (second half) --- local recalibration, alert-budget control, limits
 |
 website (dashboard/) and public demo (demo-static/): live scoring, alert-budget panel
```

Notebook numbers follow the order in which the work was run. [pipeline/README.md](pipeline/README.md) describes each
notebook. Every analysis after the policy was locked froze its settings and code hashes before it ran
(`prespec.json`). The test split was never used to choose anything.

## Repository map

| Folder | What it contains |
|---|---|
| [pipeline/](pipeline/) | Notebooks 01-08 (the readable story); 07 and 08 summarise all results and need no MIMIC access |
| [src/](src/) | Python packages: `aki_ml` (features, models, scoring), `aki_eval` (alert rules, checks, validation protocol), `aki_final` (locked test evaluation), `aki_robustness` (retraining sensitivity), `aki_report` (report analyses, including the alert-budget method in `budget.py`, and figures) |
| [scripts/](scripts/) | Command-line entry points grouped by step ([scripts/README.md](scripts/README.md)) |
| [configs/](configs/) | Every study setting (dataset, training, alert policy, test protocol, each analysis) |
| [artifacts/](artifacts/) | Results. Committed: locked policies, aggregate tables and figures. Local only: data-derived models, predictions and caches ([artifacts/README.md](artifacts/README.md)) |
| [dashboard/](dashboard/) | Local website: FastAPI backend, React frontend, live model worker, launcher, tests |
| [demo-static/](demo-static/) | Prebuilt public demo with four synthetic patients, deployed at https://aki-icu-demo.vercel.app |
| [docs/](docs/) | Final analyses, final report, study design, alert policy, feature engine, static demo; `history/` holds early design-stage documents |
| [tests/](tests/) | Tests of the research code on synthetic data |

## Quick start

- **Read the results (no data needed):** [pipeline/07_results_prediction.ipynb](pipeline/07_results_prediction.ipynb)
  (Step 1) and [pipeline/08_results_alert_reliability.ipynb](pipeline/08_results_alert_reliability.ipynb) (Steps 2-3).
- **Try the website:** https://aki-icu-demo.vercel.app, or locally `Setup-AKI.cmd`, then `Start-AKI-Demo.cmd`
  (Windows). Four synthetic patients replay with a "+1 h" clock; the "Unit alert setting" panel switches the
  false-alert budget. Live local scoring needs the locked model folder, which is not in the repository.
- **Reproduce everything:** [INSTALL.md](INSTALL.md). It needs credentialed MIMIC-III access.

## Documentation

| Document | Scope |
|---|---|
| [INSTALL.md](INSTALL.md) | Installation, data layout, environments, full reproduction order with runtimes |
| [docs/FINAL-ANALYSES.md](docs/FINAL-ANALYSES.md) | All analyses in three parts (= Steps 1-3) |
| [docs/FINAL-REPORT.md](docs/FINAL-REPORT.md) | Design summary and the locked test evaluation, with subgroups, sensitivity analyses and limitations |
| [docs/STUDY-DESIGN.md](docs/STUDY-DESIGN.md) | Prespecified cohort, labels, temporal rules, split and evaluation design |
| [docs/ALERT-POLICY.md](docs/ALERT-POLICY.md) | Locked alert policy v3: rule, selection and confirmation design |
| [docs/FEATURE-ENGINE-V2.md](docs/FEATURE-ENGINE-V2.md) | Batch-independent feature engine and its revalidation |
| [docs/STATIC-DEMO.md](docs/STATIC-DEMO.md) | Public read-only demo: deployment, offline backup, rebuild |
| [dashboard/README.md](dashboard/README.md) | Website installation and daily use; [DEVELOPMENT.md](dashboard/DEVELOPMENT.md), [DATA-CONTRACT.md](dashboard/DATA-CONTRACT.md), [VALIDATION.md](dashboard/VALIDATION.md) for developers |

## Data, models and privacy

- MIMIC-III data, anything derived per patient (cohort, features, predictions) and trained model weights are **never
  committed or uploaded**. They stay in `../mimiciii`, `../Cleaned` and `artifacts/` on approved local storage.
- The repository holds code, configuration, documentation, the locked alert policies, and aggregate, non-identifying
  results and figures. The website and the public demo show synthetic patients only.
- `python scripts/verify_sealed_hashes.py` checks that the committed code and configuration still match every sealed
  study record.

## References

- Tomašev et al., continuous AKI prediction up to 48 hours, Nature 2019: https://doi.org/10.1038/s41586-019-1390-1
- Wilson et al., electronic AKI alerts, multicentre randomised trial, BMJ 2021: https://doi.org/10.1136/bmj.m4786
- Song et al., cross-site transportability of an AKI model, Nat Commun 2020: https://doi.org/10.1038/s41467-020-19551-w
- Davis et al., calibration drift in AKI models, JAMIA 2017: https://doi.org/10.1093/jamia/ocx030
- Koyner et al., machine-learning AKI prediction, Crit Care Med 2018: https://doi.org/10.1097/CCM.0000000000003123
- Angelopoulos et al., Learn then Test: risk control for predictive algorithms: https://arxiv.org/abs/2110.01052
- KDIGO 2012 AKI guideline: https://kdigo.org/wp-content/uploads/2016/10/KDIGO-2012-AKI-Guideline-English.pdf
- MIMIC-III v1.4: https://physionet.org/content/mimiciii/1.4/
