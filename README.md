# Reliable AKI early warning in the ICU: from risk prediction to a guaranteed alert burden

A course research project on MIMIC-III v1.4. It is a research prototype, not a validated clinical product; risks and
alerts are not treatment recommendations.

## The problem

Machine-learning models can predict acute kidney injury (AKI) hours before it happens, and published systems mostly
compete on discrimination (AUROC). Yet a multicentre randomised trial of electronic AKI alerts (Wilson et al., BMJ 2021)
found that alerts did not improve outcomes, and the effect differed between hospitals. Studies of AKI models also show
that their probabilities drift when they move to another site (Song et al. 2020; Davis et al. 2017).

The weak point is therefore not the prediction but the **alert**. When a system moves to a new setting or serves
different patient groups, a fixed alert threshold can silently produce more false alarms, or miss more AKI, and nobody
is told.

## Our answer, in three parts

| Part | Question | What we did | What we found |
|---|---|---|---|
| **1. Prediction** | How good can AKI risk prediction get? | Leakage-safe hourly risk for AKI within 6/12/24/48 h on 32,907 ICU patients; LightGBM against LR, RF, XGBoost, creatinine-only baselines and a GRU sequence model | Test AUROC 0.866 / 0.841 / 0.817 / 0.792; well calibrated (observed / expected 0.98). A GRU adds nothing reliable (ensemble +0.008, below the prespecified bar), and the signal is spread over many inputs (the top 100 of 246 lose 0.003-0.005 AUROC): **the model is close to its ceiling** |
| **2. From prediction to alerts** | What goes wrong when risks become alerts? | Hour-by-hour alert replay; locked alert policy; model moved between MIMIC's two charting systems; audit by insurance and ethnicity | Policy v3 catches 71.7% of AKI events about 12 h early, and 40 of 41 that later needed dialysis, at 1.99 false alerts per true alert. But when the system moves, **ranking holds** (AUROC -0.01 to -0.03) **while probabilities drift** (observed / expected 1.16). With the threshold unchanged, false alerts exceed the budget (0.258 > 0.25) or detection drops (75.8% -> 67.4%). Creatinine evidence transfers; the drift comes from a baseline-risk change and the other laboratory inputs. Some groups are under-predicted and get fewer alerts |
| **3. Reliable alerting** | Can the alert burden be guaranteed? | **Alert-budget control:** a unit states a false-alert budget; local recalibration plus a threshold certified by Learn-then-Test testing keeps it with a stated confidence | With local data the budget holds in 98-100% of recalibrations, against 65% without local data, at a cost of -3 to +2 points of detection. Correcting small patient groups separately does not hold up (validation check), so group calibration is monitored rather than corrected |

**In one sentence:** AKI prediction is good enough; the reliability of the alerts is what fails when a system moves.
We show where it fails and give a method that guarantees the alert burden from local data.

Details and every number: [docs/FINAL-ANALYSES.md](docs/FINAL-ANALYSES.md) (organised in the same three parts) and
[docs/FINAL-REPORT.md](docs/FINAL-REPORT.md) (locked test evaluation). Figures: [artifacts/reports/figures/](artifacts/reports/figures/).

## How the work is organised

```text
MIMIC-III v1.4 (local, credentialed)
 |
 |  Part 1  Prediction
 |   01 dataset construction ---- cohort, KDIGO creatinine labels with censoring, hourly as-of features
 |   02 model training ---------- LR, RF, LightGBM (locked), XGBoost; one head per horizon; Platt calibration
 |   04 stability analysis ------ calibration, SHAP and risk stability on validation
 |   07 results: prediction ----- baselines, calibration, GRU headroom check, drivers
 |
 |  Part 2  From prediction to alerts
 |   03 first alert threshold --- monitoring window and threshold (Demo I)
 |   05 alert policy v3 --------- leakage checks; policy selected and confirmed on validation, then locked
 |   06 locked test evaluation -- test split opened once: discrimination, calibration, alerts, subgroups
 |   08 results: alerts --------- severity, trade-off, system transfer, groups  (Part 2)
 |
 |  Part 3  Reliable alerting
 |   08 results: alerts --------- local recalibration, alert-budget control, limits  (Part 3)
 |
 website (dashboard/) and public demo (demo-static/): live scoring with the locked model and alert policy
```

Notebook numbers follow the order in which the work was run. [pipeline/README.md](pipeline/README.md) describes each
notebook and the scripts behind the heavy steps. Every analysis after the policy was locked froze its settings and code
hashes before it ran (`prespec.json`). The test split was never used to choose anything.

## Repository map

| Folder | What it contains |
|---|---|
| [pipeline/](pipeline/) | Notebooks 01-08 (the readable story); 07 and 08 summarise all results and need no MIMIC access |
| [src/](src/) | Python packages: `aki_ml` (features, models, scoring), `aki_eval` (alert rules, checks, validation protocol), `aki_final` (locked test evaluation), `aki_robustness` (retraining sensitivity), `aki_report` (report analyses, including the alert-budget method in `budget.py`, and figures) |
| [scripts/](scripts/) | Command-line entry points grouped by part of the story ([scripts/README.md](scripts/README.md)) |
| [configs/](configs/) | Every study setting (dataset, training, alert policy, test protocol, each analysis) |
| [artifacts/](artifacts/) | Results. Committed: locked policies and aggregate tables and figures. Local only: data-derived models, predictions and caches ([artifacts/README.md](artifacts/README.md)) |
| [dashboard/](dashboard/) | Local website: FastAPI backend, React frontend, live model worker, launcher, tests |
| [demo-static/](demo-static/) | Prebuilt public demo with four synthetic patients (deployed on Vercel) |
| [docs/](docs/) | Final analyses, final report, study design, alert policy, feature engine, static demo; `history/` holds early design-stage documents |
| [tests/](tests/) | Tests of the research code on synthetic data |

## Quick start

- **Read the results (no data needed):** [pipeline/07_results_prediction.ipynb](pipeline/07_results_prediction.ipynb)
  and [pipeline/08_results_alert_reliability.ipynb](pipeline/08_results_alert_reliability.ipynb).
- **Run the website demo:** `Setup-AKI.cmd`, then `Start-AKI-Demo.cmd` (Windows). The demo uses four synthetic patients
  with a "+1 h" clock. Live scoring needs the locked model folder, which is not in the repository.
- **Reproduce everything:** [INSTALL.md](INSTALL.md). It needs credentialed MIMIC-III access.

## Documentation

| Document | Scope |
|---|---|
| [INSTALL.md](INSTALL.md) | Installation, data layout, environments, full reproduction order with runtimes |
| [docs/FINAL-ANALYSES.md](docs/FINAL-ANALYSES.md) | The three-part analysis: prediction, alert failure modes, alert-budget control |
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
