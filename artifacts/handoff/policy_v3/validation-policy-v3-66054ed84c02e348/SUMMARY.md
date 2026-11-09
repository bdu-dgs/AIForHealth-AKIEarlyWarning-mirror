Model: frozen LightGBM no_dc; no retraining. Features: patient_window_v2 (batch-independent), same weights.
Choice: confirm12_cool12; threshold 0.21.
Selection: deterministic validation patient role with cutoff-stability constraints.
Confirmation: separate validation patients; previously viewed baseline, not a final test.
Target-event detection: 0.769 -> 0.719.
False alerts/scorable patient-day: 0.424 -> 0.230.
Non-target-event patients alerted: 0.358 -> 0.311.
Median first-correct-alert lead: 12.81 -> 11.66 h.
Confirmation status: not_confirmed_no_retuning; no retuning.
Deployment: threshold unlocked; stateful rules not activated in the website.
