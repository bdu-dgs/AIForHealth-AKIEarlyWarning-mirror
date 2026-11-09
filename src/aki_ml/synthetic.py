"""Deterministic fictitious research fixtures. Contains no MIMIC-derived values."""
import numpy as np
import pandas as pd

from .research_data import ResearchData


def make_research_data(seed=20260929, patients=120):
    if patients < 60:
        raise ValueError("Use at least 60 synthetic patients for grouped baseline calibration")
    rng = np.random.default_rng(seed)
    cohort, snapshots, events, split_rows = [], [], [], []
    horizons = [6, 12, 24, 48]
    for i in range(patients):
        pid, stay = f"SYNTH-{i:04d}", f"SYNTH-STAY-{i:04d}"
        split = "train" if i < int(.7 * patients) else "validation" if i < int(.9 * patients) else "test"
        kind = i % 4
        cr = float(rng.uniform(14, 45)) if kind in [0, 1] else np.nan
        uo = float(rng.uniform(8, 35)) if kind in [1, 2] else np.nan
        censor = 28.0 if i % 17 == 0 else 96.0
        cr = cr if cr < censor else np.nan
        uo = uo if uo < censor else np.nan
        cohort.append(dict(patient_id=pid, stay_id=stay, split=split, censor_hours=censor,
                           cr_aki_onset_hours=cr, uo_aki_onset_hours=uo))
        split_rows.append(dict(patient_id=pid, split=split))
        base = .7 + .24 * (kind != 3) + rng.normal(0, .12)
        for hour in range(0, 97):
            if hour < censor:
                events.append(dict(stay_id=stay, hours_since_icu=float(hour), concept="heart_rate",
                                   value=float(76 + 8 * (kind != 3) + rng.normal(0, 5)), source_itemid=0))
            if hour % 4 == 0 and hour < censor:
                value = max(.2, base + (hour * .012 if kind in [0, 1] else 0) + rng.normal(0, .08))
                events.append(dict(stay_id=stay, hours_since_icu=float(hour), concept="creatinine",
                                   value=float(value), source_itemid=0))
        for hour in range(2, 25, 2):
            if hour >= censor or (np.isfinite(cr) and hour >= cr):
                continue
            row = dict(patient_id=pid, stay_id=stay, split=split, hours_since_icu=float(hour))
            for h in horizons:
                positive = bool(hour < cr <= hour + h)
                eligible = positive or censor >= hour + h
                uo_risk = i % 7 != 0 and not uo <= hour
                uo_positive = bool(uo_risk and hour < uo <= hour + h)
                uo_eligible = uo_risk and (uo_positive or (censor >= hour + h and i % 11 != 0))
                row.update({f"eligible_{h}h": eligible, f"aki_within_{h}h": int(positive) if eligible else np.nan,
                            f"eligible_uo_{h}h": uo_eligible, f"aki_uo_within_{h}h": int(uo_positive) if uo_eligible else np.nan})
            snapshots.append(row)
    ev = pd.DataFrame(events)
    return ResearchData(pd.DataFrame(snapshots), pd.DataFrame(cohort), pd.DataFrame(split_rows),
                        ev[ev["hours_since_icu"] <= 72].copy(), ev, 96,
                        {"source": "synthetic", "dataset_version": "synthetic-extension-v1", "seed": seed,
                         "patients": patients, "clinical_performance": "not_evaluated"})
