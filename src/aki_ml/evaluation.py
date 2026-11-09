"""Shared validation-only threshold selection and fixed-origin cutoff comparisons."""
import hashlib
import json

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def classification_metrics(y, risk, threshold=None):
    y, risk = np.asarray(y, float), np.asarray(risk, float)
    if y.shape != risk.shape or np.isinf(y).any() or np.isinf(risk).any():
        raise ValueError("Invalid prediction/label arrays")
    if not np.isin(y[np.isfinite(y)], [0, 1]).all():
        raise ValueError("Labels must be binary or missing")
    if ((risk[np.isfinite(risk)] < 0) | (risk[np.isfinite(risk)] > 1)).any():
        raise ValueError("Probabilities must be in [0, 1]")
    ok = np.isfinite(y) & np.isfinite(risk)
    y, risk = y[ok].astype(int), risk[ok]
    out = {"rows": len(y), "eligible_prediction_coverage": float(ok.mean()) if len(ok) else 0,
           "prevalence": float(y.mean()) if len(y) else None}
    both = len(np.unique(y)) == 2
    out.update(auroc=float(roc_auc_score(y, risk)) if both else None,
               auprc=float(average_precision_score(y, risk)) if both else None,
               brier=float(brier_score_loss(y, risk)) if len(y) else None)
    if threshold is not None:
        hit = risk >= threshold
        tp, fp, fn, tn = (int(x.sum()) for x in [hit & (y == 1), hit & (y == 0), ~hit & (y == 1), ~hit & (y == 0)])
        out.update(sensitivity=tp / (tp + fn) if tp + fn else None,
                   specificity=tn / (tn + fp) if tn + fp else None,
                   f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
                   warning_fraction=float(hit.mean()) if len(y) else None)
    return out


def cutoff_stability(reference, variants, threshold=None):
    """Same rows, origins, and future target windows; only input cutoffs change."""
    reference = np.asarray(reference, float)
    out = {}
    for lag, values in variants.items():
        values = np.asarray(values, float)
        if values.shape != reference.shape or np.isinf(values).any():
            raise ValueError("Cutoff variants must be aligned and finite or missing")
        paired = np.isfinite(reference) & np.isfinite(values)
        delta = np.abs(reference[paired] - values[paired])
        result = {"paired_rows": int(paired.sum()), "pair_coverage": float(paired.mean()) if len(paired) else 0.0,
                  "mean_absolute_change": float(delta.mean()) if len(delta) else None,
                  "p95_absolute_change": float(np.quantile(delta, .95)) if len(delta) else None}
        if threshold is not None:
            result["warning_flip_fraction"] = float(
                ((reference[paired] >= threshold) != (values[paired] >= threshold)).mean()) if paired.any() else None
        out[str(lag)] = result
    return out


def select_validation_threshold(y, risk, variants, splits, candidates, *, min_sensitivity=.6,
                                max_flip_fraction=.25, min_pair_coverage=.8, stability_weight=.25):
    if not len(splits) or not np.all(np.asarray(splits) == "validation"):
        raise ValueError("Threshold selection accepts validation rows only")
    y, risk = np.asarray(y, float), np.asarray(risk, float)
    if len(y) != len(splits) or not variants or len(np.unique(y[np.isfinite(y)])) != 2:
        raise ValueError("Need aligned validation labels with both classes and cutoff variants")
    if len(risk) != len(y) or not np.isfinite(risk[np.isfinite(y)]).all():
        raise ValueError("Missing current-cutoff validation predictions")
    rows = []
    for threshold in candidates:
        if not 0 <= threshold <= 1:
            raise ValueError("Threshold outside [0, 1]")
        metrics = classification_metrics(y, risk, threshold)
        stability = cutoff_stability(risk, variants, threshold)
        flips = [r["warning_flip_fraction"] for r in stability.values()]
        worst = max(flips) if all(f is not None for f in flips) else None
        coverage = min(r["pair_coverage"] for r in stability.values())
        accepted = (metrics["sensitivity"] is not None and metrics["sensitivity"] >= min_sensitivity
                    and worst is not None and worst <= max_flip_fraction and coverage >= min_pair_coverage)
        rows.append({"threshold": float(threshold), **metrics, "worst_warning_flip_fraction": worst,
                     "min_pair_coverage": coverage, "accepted": accepted,
                     "selection_score": metrics["f1"] - stability_weight * (worst if worst is not None else 1)})
    eligible = [r for r in rows if r["accepted"]]
    chosen = max(eligible, key=lambda r: (r["selection_score"], r["threshold"])) if eligible else None
    report = {"status": "selected" if chosen else "no_candidate_met_constraints", "selection_split": "validation",
              "selected": chosen, "candidates": rows, "test_used": False,
              "rule": "max F1 minus stability_weight * worst warning-flip fraction; then higher threshold",
              "constraints": {"min_sensitivity": min_sensitivity, "max_flip_fraction": max_flip_fraction,
                              "min_pair_coverage": min_pair_coverage, "stability_weight": stability_weight},
              "stability": cutoff_stability(risk, variants, chosen["threshold"] if chosen else None)}
    report["assessment_id"] = "cutoff-" + digest(report)[:20]
    return report


def first_warning_metrics(patient_ids, origins, onset, risk, threshold, horizon):
    """Event detection / first-correct-warning lead. Does not infer patient-days from sparse rows."""
    ids, origins, onset, risk = map(np.asarray, [patient_ids, origins, onset, risk])
    leads, detectable = [], 0
    for pid in np.unique(ids):
        rows = ids == pid
        possible = rows & (onset > origins) & (onset <= origins + horizon)
        if possible.any():
            detectable += 1
            warned = possible & np.isfinite(risk) & (risk >= threshold)
            if warned.any():
                leads.append(float(np.max(onset[warned] - origins[warned])))
    return {"detectable_events": detectable, "detected_events": len(leads),
            "event_detection_fraction": len(leads) / detectable if detectable else None,
            "median_first_warning_lead_hours": float(np.median(leads)) if leads else None,
            "alert_burden": "not_estimated; requires a specified complete monitoring grid and cooldown"}


def regression_metrics(truth, prediction, lower=None, upper=None):
    truth, prediction = np.asarray(truth, float), np.asarray(prediction, float)
    if truth.shape != prediction.shape:
        raise ValueError("Trajectory arrays must align")
    ok = np.isfinite(truth) & np.isfinite(prediction)
    delta = prediction[ok] - truth[ok]
    out = {"evaluated_points": int(ok.sum()), "coverage": float(ok.mean()) if len(ok) else 0,
           "mae": float(np.abs(delta).mean()) if len(delta) else None,
           "rmse": float(np.sqrt(np.mean(delta ** 2))) if len(delta) else None}
    if (lower is None) != (upper is None):
        raise ValueError("Both interval bounds are required")
    if lower is not None:
        lower, upper = np.asarray(lower, float), np.asarray(upper, float)
        if lower.shape != truth.shape or upper.shape != truth.shape or (lower > upper).any():
            raise ValueError("Invalid intervals")
        bound_ok = ok & np.isfinite(lower) & np.isfinite(upper)
        out["interval_points"] = int(bound_ok.sum())
        out["interval_coverage"] = float(((truth[bound_ok] >= lower[bound_ok]) &
                                          (truth[bound_ok] <= upper[bound_ok])).mean()) if bound_ok.any() else None
    return out
