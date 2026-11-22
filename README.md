# Dynamic AKI early warning in the ICU (MIMIC-III)

A course research project that predicts **acute kidney injury (AKI)** in intensive-care patients 6, 12, 24 and 48 hours
ahead, updated every hour, and turns the risk into **alerts a unit could tolerate**. It includes:

- a reproducible research pipeline on MIMIC-III v1.4;
- a locally running clinical website that scores patients as new data arrive;
- a public read-only demo with synthetic patients.

It is a research prototype, not a validated clinical product. Risks and alerts are not treatment recommendations.

## Results at a glance (held-out test split, 4,959 patients, opened once)

| | |
|---|---|
| Discrimination (AUROC, 6 / 12 / 24 / 48 h) | 0.866 / 0.841 / 0.817 / 0.792; calibration observed/expected 0.98 |
| Alert policy v3 (24 h risk >= 21% and 12 h risk >= 10.5%) | 71.7% of AKI events detected, median 11.6 h before onset; 0.23 false alerts per patient-day; 1.99 false alerts per true alert |
| Severe AKI | 86.9% of stage-3 events and 40 of 41 AKI episodes that later needed dialysis were warned |
| Baselines (24 h AUROC) | XGBoost 0.814, random forest 0.794, logistic regression 0.788, creatinine-only model 0.703, creatinine-rise rule 0.618 |
| Generalisability | Moving between the two MIMIC charting systems costs 0.01-0.03 AUROC but shifts calibration; recalibrating on about 500-1,000 local patients restores it |
| Fairness | Equal AUROC across insurance and ethnicity groups; risk is underestimated for Medicaid, government-insured and Hispanic patients |

Full results: [docs/FINAL-REPORT.md](docs/FINAL-REPORT.md) (main evaluation) and
[docs/FINAL-ANALYSES.md](docs/FINAL-ANALYSES.md) (report analyses). Figures: [artifacts/reports/figures/](artifacts/reports/figures/).

## How the project flows

```text
MIMIC-III v1.4 (local, credentialed)
  |
  01 dataset construction ---- cohort (32,907 patients), KDIGO creatinine labels, hourly as-of features
  02 model training ---------- LR, RF, LightGBM (primary), XGBoost; one head per horizon; Platt calibration
  03 alert threshold --------- monitoring window and first alert rule (Demo I), on training/validation data
  04 stability analysis ------ calibration, SHAP and risk-volatility stability on validation
  05 alert policy v3 --------- artifact/leakage checks A1-A12; policy selected and confirmed on validation, then locked
  06 locked test evaluation -- test split opened once: discrimination, calibration, alerts, subgroups, sensitivity
  07 final-report analyses --- severity, baselines, decision curves, system transfer, recalibration, fairness, EDA
  |
  website (dashboard/) and public demo (demo-static/) use the locked model and policy v3
```

Each step is a notebook in [pipeline/](pipeline/); heavy steps are run as scripts. [pipeline/README.md](pipeline/README.md)
explains every notebook, its inputs and outputs. Analyses after step 5 froze their settings and code hashes before
they ran (`prespec.json`). The test split was used once and never for choosing anything.

## Repository map

| Folder | What it contains |
|---|---|
| [pipeline/](pipeline/) | Notebooks 01-07, in run order (the readable story of the project) |
| [src/](src/) | Python packages used by notebooks, scripts and the website: `aki_ml` (features, models, scoring), `aki_eval` (alert rules, checks, validation protocol), `aki_final` (locked test evaluation), `aki_robustness` (retraining sensitivity), `aki_report` (final-report analyses and figures) |
| [scripts/](scripts/) | Command-line entry points: evaluation and analysis runs, figures, scoring, demo export, setup, hash check ([scripts/README.md](scripts/README.md)) |
| [configs/](configs/) | Every study setting (dataset, training, alert policy, test protocol, each analysis) |
| [artifacts/](artifacts/) | Results. Committed: locked policies and aggregate tables and figures. Local only: data-derived models, predictions and caches ([artifacts/README.md](artifacts/README.md)) |
| [dashboard/](dashboard/) | Local website: FastAPI backend (`api/`), React frontend (`web/`), live model worker, launcher, tests |
| [demo-static/](demo-static/) | Prebuilt public demo with four synthetic patients (deployed on Vercel) |
| [docs/](docs/) | Final report, final analyses, study design, alert policy, feature engine, static demo; `history/` holds early design-stage documents |
| [tests/](tests/) | Tests of the research code on synthetic data |

## Quick start

- **Read the results (no data needed):** open [pipeline/07_final_report_analyses.ipynb](pipeline/07_final_report_analyses.ipynb)
  or the two report documents above.
- **Run the website demo:** `Setup-AKI.cmd`, then `Start-AKI-Demo.cmd` (Windows). The demo uses four synthetic patients
  and a "+1 h" clock. Live scoring needs the locked model folder, which is not in the repository (see below).
- **Reproduce everything:** follow [INSTALL.md](INSTALL.md). It needs credentialed MIMIC-III access.

## Documentation

| Document | Scope |
|---|---|
| [INSTALL.md](INSTALL.md) | Installation, data layout, environments, full reproduction order with runtimes |
| [docs/FINAL-REPORT.md](docs/FINAL-REPORT.md) | Design summary, locked test results, subgroups, sensitivity analyses, limitations |
| [docs/FINAL-ANALYSES.md](docs/FINAL-ANALYSES.md) | Report analyses: severity, literature comparison, baselines, decision curves, transfer, recalibration, fairness, EDA |
| [docs/STUDY-DESIGN.md](docs/STUDY-DESIGN.md) | Prespecified cohort, labels, temporal rules, split and evaluation design |
| [docs/ALERT-POLICY.md](docs/ALERT-POLICY.md) | Locked alert policy v3: rule, selection and confirmation design |
| [docs/FEATURE-ENGINE-V2.md](docs/FEATURE-ENGINE-V2.md) | Batch-independent feature engine and its revalidation |
| [docs/STATIC-DEMO.md](docs/STATIC-DEMO.md) | Public read-only demo: deployment, offline backup, rebuild |
| [dashboard/README.md](dashboard/README.md) | Website installation and daily use |
| [dashboard/DEVELOPMENT.md](dashboard/DEVELOPMENT.md), [DATA-CONTRACT.md](dashboard/DATA-CONTRACT.md), [VALIDATION.md](dashboard/VALIDATION.md) | Website architecture, live JSON contract, executed tests |

## Data, models and privacy

- MIMIC-III data, anything derived per patient (cohort, features, predictions) and the trained model weights are
  **never committed or uploaded**. They live in `../mimiciii`, `../Cleaned` and `artifacts/` on approved local storage.
- The repository holds code, configuration, documentation, the locked alert policies, and aggregate, non-identifying
  results and figures.
- The website and the public demo show synthetic patients only.
- `python scripts/verify_sealed_hashes.py` checks that the committed code and configuration still match every sealed
  study record.

## Method references

- Tomašev et al., continuous AKI prediction up to 48 hours (Nature 2019): https://doi.org/10.1038/s41586-019-1390-1
- Koyner et al., machine-learning AKI prediction, 24 h and 48 h: https://doi.org/10.1097/CCM.0000000000003123
- Mohamadlou et al., continuously updated 6 h and 12 h ICU-AKI prediction: https://pmc.ncbi.nlm.nih.gov/articles/PMC10121926/
- Kate et al., prediction triggered by AKI-relevant EHR changes: https://doi.org/10.1016/j.compbiomed.2019.103580
- de Hond et al., critical appraisal of AKI prediction models: https://pmc.ncbi.nlm.nih.gov/articles/PMC9664575/
- KDIGO 2012 AKI definition and staging: https://kdigo.org/wp-content/uploads/2016/10/KDIGO-2012-AKI-Guideline-English.pdf
- MIMIC-III v1.4: https://physionet.org/content/mimiciii/1.4/
