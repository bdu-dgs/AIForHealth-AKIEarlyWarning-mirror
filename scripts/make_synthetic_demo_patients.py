"""Generate fictional ICU patients as a dashboard InputBatch (Local Data Contract v1).

Nothing here is derived from MIMIC records: every trajectory is a hand-written clinical scenario plus seeded
noise. Metric codes and units match the model's event-stream concepts (configs/default.yaml), so the same
file can be fed to the AKI model, not just displayed. Lab results are available one hour after the draw,
matching the model's lab_result_delay_hours.

Usage: python scripts/make_synthetic_demo_patients.py <output.json> [--end-at-now]

--end-at-now moves every admission so that ICU hour 72 ends a few minutes before the current time. Use it for a
live dashboard demo: with the fixed default dates every prediction window has already ended, so the dashboard
shows "Prediction window ended" instead of an alert status. A dashboard data directory accepts one admission time per
patient, so import a shifted file into a fresh `--data-dir`. Without the flag the output is identical on every run.
"""
import json
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

HOURS = 72
SEED = 20261006

LABELS = {  # metric -> (display label, unit)
    "heart_rate": ("Heart rate", "bpm"), "sbp": ("Systolic BP", "mmHg"), "dbp": ("Diastolic BP", "mmHg"),
    "mbp": ("Mean arterial pressure", "mmHg"), "resp_rate": ("Respiratory rate", "/min"),
    "spo2": ("SpO2", "%"), "temp_c": ("Temperature", "degC"), "glucose_poc": ("Glucose (point of care)", "mg/dL"),
    "gcs_eye": ("GCS eye", "points"), "gcs_verbal": ("GCS verbal", "points"), "gcs_motor": ("GCS motor", "points"),
    "fio2": ("FiO2", "fraction"), "mech_vent": ("Mechanical ventilation charted", "flag"),
    "urine_output": ("Urine output", "mL"), "fluid_in_ml": ("IV fluid input", "mL"),
    "vasopressor": ("Vasopressor infusion", "flag"), "loop_diuretic": ("Loop diuretic bolus", "flag"),
    "creatinine": ("Creatinine", "mg/dL"), "bun": ("BUN", "mg/dL"), "sodium": ("Sodium", "mEq/L"),
    "potassium": ("Potassium", "mEq/L"), "chloride": ("Chloride", "mEq/L"), "bicarbonate": ("Bicarbonate", "mEq/L"),
    "anion_gap": ("Anion gap", "mEq/L"), "glucose": ("Glucose (lab)", "mg/dL"), "hemoglobin": ("Hemoglobin", "g/dL"),
    "platelets": ("Platelets", "K/uL"), "wbc": ("WBC", "K/uL"), "lactate": ("Lactate", "mmol/L"),
    "weight_admit_kg": ("Admission weight", "kg"), "height_cm": ("Height", "cm"),
    "rx_vancomycin_iv": ("Vancomycin IV order (active hours)", "h"),
    "rx_piperacillin_tazobactam": ("Piperacillin-tazobactam order (active hours)", "h"),
    "rx_loop_diuretic": ("Loop diuretic order (active hours)", "h"),
    # Static admission inputs, recorded once at ICU admission.
    "age_years": ("Age", "years"), "gender_male": ("Male sex", "flag"), "pre_icu_hours": ("Hospital stay before ICU", "h"),
    "baseline_cr_admission": ("Baseline creatinine (pre-ICU)", "mg/dL"),
    "admission_type_elective": ("Elective admission", "flag"), "admission_type_urgent": ("Urgent admission", "flag"),
    "first_careunit_csru": ("Cardiac surgery ICU", "flag"), "first_careunit_sicu": ("Surgical ICU", "flag"),
    "surgical_service": ("Surgical service", "flag"),
}


def ramp(points, hours):
    """Piecewise-linear trajectory through (hour, value) points."""
    xs, ys = zip(*points)
    return np.interp(hours, xs, ys)


# Each scenario: static inputs, piecewise-linear trends for vitals/labs/urine, and drug exposures.
SCENARIOS = [
    {"id": "DEMO-001", "name": "Demo A (stable, low risk)", "bed": "Demo-01", "admit": "2026-09-14T06:00:00Z",
     "note": "Synthetic patient. Medical ICU, community-acquired pneumonia, stable haemodynamics, normal renal function.",
     "static": {"age_years": 58, "gender_male": 1, "pre_icu_hours": 6, "baseline_cr_admission": 0.9,
                "weight_admit_kg": 78, "height_cm": 176},
     "trend": {"heart_rate": [(0, 92), (72, 80)], "mbp": [(0, 82), (72, 85)], "resp_rate": [(0, 22), (72, 17)],
               "spo2": [(0, 94), (72, 97)], "temp_c": [(0, 38.1), (36, 37.2), (72, 36.9)],
               "creatinine": [(0, 0.9), (72, 0.85)], "bun": [(0, 16), (72, 14)], "lactate": [(0, 1.4), (72, 1.0)],
               "urine_ml_h": [(0, 85), (72, 90)], "wbc": [(0, 14), (72, 9)]},
     "vasopressor": [], "mech_vent": [], "rx": {}, "diuretic_boluses": []},
    {"id": "DEMO-002", "name": "Demo B (sepsis, develops AKI)", "bed": "Demo-02", "admit": "2026-09-15T22:00:00Z",
     "note": "Synthetic patient. Septic shock from abdominal source; vasopressors, vancomycin + piperacillin-tazobactam; "
             "creatinine rises and urine output falls over the second day.",
     "static": {"age_years": 71, "gender_male": 0, "pre_icu_hours": 14, "baseline_cr_admission": 1.0,
                "weight_admit_kg": 68, "height_cm": 162, "admission_type_urgent": 1, "surgical_service": 1,
                "first_careunit_sicu": 1},
     "trend": {"heart_rate": [(0, 118), (24, 112), (48, 105), (72, 98)], "mbp": [(0, 61), (12, 64), (36, 66), (72, 70)],
               "resp_rate": [(0, 26), (72, 22)], "spo2": [(0, 93), (72, 95)],
               "temp_c": [(0, 38.9), (24, 38.4), (72, 37.6)],
               "creatinine": [(0, 1.05), (12, 1.15), (24, 1.3), (36, 1.55), (48, 1.9), (72, 2.3)],
               "bun": [(0, 24), (72, 46)], "lactate": [(0, 4.1), (24, 3.2), (72, 2.2)],
               "urine_ml_h": [(0, 45), (18, 35), (30, 22), (48, 14), (72, 12)], "wbc": [(0, 21), (72, 17)]},
     "vasopressor": [(0, 54)], "mech_vent": [(2, 72)],
     "rx": {"rx_vancomycin_iv": [0, 24, 48], "rx_piperacillin_tazobactam": [0, 24, 48]}, "diuretic_boluses": [40]},
    {"id": "DEMO-003", "name": "Demo C (post cardiac surgery, CKD)", "bed": "Demo-03", "admit": "2026-09-17T13:00:00Z",
     "note": "Synthetic patient. Elective CABG with chronic kidney disease (baseline creatinine 1.6); short ventilation, "
             "diuretics on day 2; creatinine stays close to baseline.",
     "static": {"age_years": 66, "gender_male": 1, "pre_icu_hours": 30, "baseline_cr_admission": 1.6,
                "weight_admit_kg": 92, "height_cm": 180, "admission_type_elective": 1, "surgical_service": 1,
                "first_careunit_csru": 1},
     "trend": {"heart_rate": [(0, 88), (72, 82)], "mbp": [(0, 72), (8, 70), (72, 78)], "resp_rate": [(0, 16), (72, 18)],
               "spo2": [(0, 98), (72, 96)], "temp_c": [(0, 36.0), (8, 37.3), (72, 37.0)],
               "creatinine": [(0, 1.6), (24, 1.75), (48, 1.7), (72, 1.65)], "bun": [(0, 30), (72, 34)],
               "lactate": [(0, 2.4), (12, 1.5), (72, 1.1)], "urine_ml_h": [(0, 120), (12, 60), (36, 70), (72, 85)],
               "wbc": [(0, 12), (72, 11)]},
     "vasopressor": [(0, 10)], "mech_vent": [(0, 8)], "rx": {"rx_loop_diuretic": [24, 48]}, "diuretic_boluses": [26, 50]},
    {"id": "DEMO-004", "name": "Demo D (transient hypotension)", "bed": "Demo-04", "admit": "2026-09-19T03:00:00Z",
     "note": "Synthetic patient. GI bleed with a hypotensive episode around hours 18-28 treated with fluids; creatinine "
             "bumps slightly then recovers.",
     "static": {"age_years": 63, "gender_male": 1, "pre_icu_hours": 3, "baseline_cr_admission": 1.1,
                "weight_admit_kg": 84, "height_cm": 174, "admission_type_urgent": 1},
     "trend": {"heart_rate": [(0, 98), (18, 104), (22, 122), (30, 100), (72, 86)],
               "mbp": [(0, 76), (18, 70), (22, 58), (28, 66), (36, 76), (72, 80)], "resp_rate": [(0, 18), (72, 16)],
               "spo2": [(0, 97), (72, 97)], "temp_c": [(0, 36.8), (72, 36.9)],
               "creatinine": [(0, 1.1), (24, 1.15), (36, 1.27), (48, 1.24), (72, 1.15)], "bun": [(0, 34), (36, 40), (72, 28)],
               "lactate": [(0, 1.8), (22, 3.0), (36, 1.6), (72, 1.1)],
               "urine_ml_h": [(0, 70), (18, 50), (24, 28), (32, 55), (72, 80)], "wbc": [(0, 10), (72, 9)]},
     "vasopressor": [], "mech_vent": [], "rx": {}, "diuretic_boluses": []},
]


def build(scenarios, rng):
    patients, observations = [], []

    def add(pid, enc, metric, value, t_measured, t_available=None):
        label, unit = LABELS[metric]
        n = sum(1 for o in observations if o["patient_id"] == pid and o["metric"] == metric)
        obs = {"record_id": f"{pid}-{metric}-{n + 1:04d}", "revision": 1, "patient_id": pid, "encounter_id": enc,
               "metric": metric, "label": label, "unit": unit, "value": round(float(value), 3),
               "measured_at": t_measured.isoformat().replace("+00:00", "Z")}
        if t_available is not None:
            obs["available_at"] = t_available.isoformat().replace("+00:00", "Z")
        observations.append(obs)

    for s in scenarios:
        pid, enc = s["id"], f"{s['id']}-ENC1"
        admit = datetime.fromisoformat(s["admit"].replace("Z", "+00:00"))
        patients.append({"patient_id": pid, "encounter_id": enc, "name": s["name"],
                         "icu_admitted_at": s["admit"], "bed": s["bed"], "note": s["note"]})
        at = lambda h: admit + timedelta(hours=float(h))
        for metric, value in s["static"].items():
            add(pid, enc, metric, value, admit)
        tr = s["trend"]
        # Vitals hourly (charted a few minutes after the hour), temperature and GCS every 4 h.
        for h in range(1, HOURS + 1):
            t = at(h - 0.1 + rng.uniform(0, 0.08))
            hr = ramp(tr["heart_rate"], h) + rng.normal(0, 3)
            mbp = ramp(tr["mbp"], h) + rng.normal(0, 2.5)
            add(pid, enc, "heart_rate", hr, t)
            add(pid, enc, "mbp", mbp, t)
            add(pid, enc, "sbp", mbp + 30 + rng.normal(0, 4), t)
            add(pid, enc, "dbp", mbp - 15 + rng.normal(0, 3), t)
            add(pid, enc, "resp_rate", ramp(tr["resp_rate"], h) + rng.normal(0, 1.5), t)
            add(pid, enc, "spo2", min(100, ramp(tr["spo2"], h) + rng.normal(0, 0.8)), t)
            # Urine charted hourly as the volume since the previous charting.
            add(pid, enc, "urine_output", max(0, ramp(tr["urine_ml_h"], h) + rng.normal(0, 6)), at(h))
            if h % 4 == 0:
                add(pid, enc, "temp_c", ramp(tr["temp_c"], h) + rng.normal(0, 0.15), t)
                vent = any(a <= h < b for a, b in s["mech_vent"])
                add(pid, enc, "gcs_eye", 3 if vent else 4, t)
                add(pid, enc, "gcs_motor", 6, t)
                if not vent:
                    add(pid, enc, "gcs_verbal", 5, t)
            if h % 2 == 0:
                add(pid, enc, "fluid_in_ml", max(0, 150 + 120 * (ramp(tr["mbp"], h) < 65) + rng.normal(0, 20)), t)
            if h % 6 == 3:
                add(pid, enc, "glucose_poc", 135 + rng.normal(0, 18), t)
        for a, b in s["vasopressor"]:
            for h in range(a + 1, b + 1):
                add(pid, enc, "vasopressor", 1, at(h - 0.05))
        for a, b in s["mech_vent"]:
            for h in range(a + 1, b + 1):
                add(pid, enc, "mech_vent", 1, at(h - 0.05))
                add(pid, enc, "fio2", 0.4, at(h - 0.05))
        for h in s["diuretic_boluses"]:
            add(pid, enc, "loop_diuretic", 1, at(h))
        for metric, starts in s["rx"].items():
            for h in starts:
                add(pid, enc, metric, 24, at(h))
        # Labs every 12 h from hour 2 (lactate every 6 h); results available 1 h after the draw.
        for h in range(2, HOURS + 1, 6):
            draw = at(h)
            add(pid, enc, "lactate", max(0.5, ramp(tr["lactate"], h) + rng.normal(0, 0.2)), draw, draw + timedelta(hours=1))
            if (h - 2) % 12:
                continue
            cr = ramp(tr["creatinine"], h) + rng.normal(0, 0.03)
            na, k, cl, hco3 = 139 + rng.normal(0, 2), 4.1 + rng.normal(0, 0.25), 104 + rng.normal(0, 2), 23 + rng.normal(0, 1.5)
            labs = {"creatinine": round(cr, 1), "bun": ramp(tr["bun"], h) + rng.normal(0, 1.5), "sodium": na,
                    "potassium": k, "chloride": cl, "bicarbonate": hco3, "anion_gap": na - cl - hco3,
                    "glucose": 140 + rng.normal(0, 20), "hemoglobin": 10.5 + rng.normal(0, 0.5),
                    "platelets": 210 + rng.normal(0, 25), "wbc": ramp(tr["wbc"], h) + rng.normal(0, 0.8)}
            for metric, value in labs.items():
                add(pid, enc, metric, value, draw, draw + timedelta(hours=1))
    return {"schema_version": 1, "kind": "input", "patients": patients, "observations": observations,
            "actor": "synthetic-demo-generator"}


def end_at_now(scenarios, margin_minutes=10):
    """Same scenarios with admission at (now - margin - 72 h), rounded down to the minute."""
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=margin_minutes)
    admit = (end - timedelta(hours=HOURS)).isoformat().replace("+00:00", "Z")
    return [{**s, "admit": admit} for s in scenarios]


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--end-at-now"]
    out = args[0] if args else "demo_synthetic_patients.json"
    scenarios = end_at_now(SCENARIOS) if "--end-at-now" in sys.argv[1:] else SCENARIOS
    batch = build(scenarios, np.random.default_rng(SEED))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(batch, f, ensure_ascii=False, indent=1)
    print(f"{len(batch['patients'])} patients, {len(batch['observations'])} observations -> {out}")
