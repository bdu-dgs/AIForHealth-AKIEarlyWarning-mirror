"""Stateful alert events on an ordered time grid, separate from instantaneous risk."""
from collections import deque
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from aki_ml import alerts as legacy


@dataclass(frozen=True)
class AlertRule:
    name: str
    sustain_hours: int = 1
    cooldown_hours: float = 6
    repeat_mode: str = "cooldown"
    repeat_delta: float = .05
    rise_ratio: float | None = None
    rise_window_hours: float = 12
    max_gap_hours: float = 1.01

    def __post_init__(self):
        if self.sustain_hours < 1 or int(self.sustain_hours) != self.sustain_hours:
            raise ValueError("sustain_hours must be a positive integer on the hourly evaluation grid")
        if self.cooldown_hours < 0 or self.rise_window_hours <= 0 or self.max_gap_hours <= 0:
            raise ValueError("Invalid timing")
        if self.repeat_mode not in ["cooldown", "episode_or_rise", "episode"]:
            raise ValueError("Unknown repeat mode")
        if self.repeat_delta < 0 or (self.rise_ratio is not None and self.rise_ratio <= 0):
            raise ValueError("Invalid rise rule")

    def to_dict(self):
        return asdict(self)


def prior_minimum(patient, hours, risks, window):
    output = np.full(len(risks), np.nan)
    queue, last = deque(), None
    for i, (pid, hour, risk) in enumerate(zip(patient, hours, risks)):
        if pid != last:
            queue.clear()
            last = pid
        while queue and queue[0][0] < hour - window:
            queue.popleft()
        if queue:
            output[i] = queue[0][1]
        if np.isfinite(risk):
            while queue and queue[-1][1] >= risk:
                queue.pop()
            queue.append((hour, risk))
    return output


def prepare(frame, horizon=24):
    if frame.duplicated(["patient_id", "hours_since_icu"]).any():
        raise ValueError("Alert evaluation needs unique patient/origin keys")
    output = legacy.prepare(frame.copy(), horizon)
    hours = output["hours_since_icu"].to_numpy(float)
    risk = output["risk"].to_numpy(float)
    if not np.isfinite(hours).all() or np.isinf(risk).any() or ((risk[np.isfinite(risk)] < 0) | (risk[np.isfinite(risk)] > 1)).any():
        raise ValueError("Invalid times or probabilities")
    # A caller may replace risk for a cutoff scenario; derived history must not be cached in mutable frames.
    output = output.drop(columns=[name for name in output if name.startswith("prior_min_")])
    return output


def alert_events(frame, threshold, rule, comparison=">="):
    """Return event flags, never a new risk probability.

    For legacy hourly k-sample sustain, required observed high duration is k-1 h.
    Gaps reset sustain/episodes. Repeat suppression persists a cooldown across episodes.
    No future label or onset is consulted by the event engine.
    """
    if not 0 <= threshold <= 1 or comparison not in [">=", ">"]:
        raise ValueError("Invalid threshold")
    pid = frame["patient_id"].to_numpy()
    hours = frame["hours_since_icu"].to_numpy(float)
    risk = frame["risk"].to_numpy(float)
    if len(frame) == 0:
        return np.zeros(0, bool)
    same_patient = np.r_[False, pid[1:] == pid[:-1]]
    if np.any(same_patient[1:] & (np.diff(hours) <= 0)):
        raise ValueError("Patient times must be strictly increasing and grouped")
    # Re-entering a previously seen patient would silently carry incorrect state.
    starts = pid[~same_patient]
    if len(set(starts)) != len(starts):
        raise ValueError("Patient rows must be grouped")
    same = same_patient & np.r_[False, np.diff(hours) <= rule.max_gap_hours]
    high = (risk >= threshold if comparison == ">=" else risk > threshold) & np.isfinite(risk)
    begin = high & ~(np.r_[False, high[:-1]] & same)
    first = np.maximum.accumulate(np.where(begin, np.arange(len(frame)), 0))
    episode = np.cumsum(begin)
    trigger = high & ((hours - hours[first]) >= rule.sustain_hours - 1 - 1e-9)
    if rule.rise_ratio is not None:
        pm = prior_minimum(pid, hours, risk, rule.rise_window_hours)
        trigger &= np.isnan(pm) | (risk >= rule.rise_ratio * pm)
    output = np.zeros(len(frame), bool)
    last_pid, last_hour, last_risk, last_episode = None, -np.inf, np.nan, -1
    for i in np.flatnonzero(trigger):
        if pid[i] != last_pid:
            last_pid, last_hour, last_risk, last_episode = pid[i], -np.inf, np.nan, -1
        if hours[i] - last_hour < rule.cooldown_hours:
            continue
        fresh_episode = episode[i] != last_episode
        if rule.repeat_mode == "episode" and not fresh_episode:
            continue
        if rule.repeat_mode == "episode_or_rise" and not fresh_episode and risk[i] < last_risk + rule.repeat_delta:
            continue
        output[i] = True
        last_hour, last_risk, last_episode = hours[i], risk[i], episode[i]
    return output


def per_patient(frame, threshold, rule):
    return legacy.per_patient(frame, alert_events(frame, threshold, rule))


def metrics(frame, threshold, rule):
    return legacy.summarize(per_patient(frame, threshold, rule))


def policy_grid(frame, rules, thresholds):
    rows = []
    evaluator = HourlyMetrics(frame)
    for rule in rules:
        for threshold in thresholds:
            rows.append({"candidate": rule.name, "threshold": float(threshold), **rule.to_dict(),
                         **evaluator.summarize(alert_events(frame, threshold, rule))})
    return pd.DataFrame(rows).drop(columns="name")


def stability_grid(reference, variants, rules, thresholds):
    """Variants align with reference keys and retain the same origin/window and labels."""
    pid = reference["patient_id"].to_numpy()
    hours = reference["hours_since_icu"].to_numpy(float)
    current = reference["risk"].to_numpy(float)
    code = np.cumsum(np.r_[True, pid[1:] != pid[:-1]]) - 1
    n_patients = int(code[-1] + 1) if len(code) else 0
    rows = []
    for lag, values in variants.items():
        values = np.asarray(values, float)
        if values.shape != current.shape:
            raise ValueError("Unaligned cutoff predictions")
        paired = np.isfinite(current) & np.isfinite(values)
        shifted = reference.copy()
        shifted["risk"] = values
        delta = np.abs(current[paired] - values[paired])
        for rule in rules:
            for threshold in thresholds:
                base_events = alert_events(reference, threshold, rule)
                old_events = alert_events(shifted, threshold, rule)
                flips = (current[paired] >= threshold) != (values[paired] >= threshold)
                base_patients = np.bincount(code[base_events], minlength=n_patients) > 0
                old_patients = np.bincount(code[old_events], minlength=n_patients) > 0
                rows.append({"candidate": rule.name, "threshold": float(threshold), "lag_hours": lag,
                    "paired_rows": int(paired.sum()), "pair_coverage": float(paired.mean()),
                    "mean_absolute_risk_change": float(delta.mean()) if len(delta) else np.nan,
                    "p95_absolute_risk_change": float(np.quantile(delta,.95)) if len(delta) else np.nan,
                    "warning_flip_fraction": float(flips.mean()) if len(flips) else np.nan,
                    "event_flip_fraction": float((base_events[paired] != old_events[paired]).mean()) if paired.any() else np.nan,
                    "patient_alert_flip_fraction": float((base_patients != old_patients).mean()) if n_patients else np.nan})
    return pd.DataFrame(rows)


def choose(grid, stability, baseline_metrics, criteria):
    if grid.empty or stability.empty:
        raise ValueError("Selection needs validation grid and cutoff evidence")
    aggregate = stability.groupby(["candidate","threshold"],as_index=False).agg(
        worst_warning_flip=("warning_flip_fraction","max"), min_pair_coverage=("pair_coverage","min"),
        worst_patient_alert_flip=("patient_alert_flip_fraction","max"))
    result = grid.merge(aggregate,on=["candidate","threshold"],validate="one_to_one")
    detection_min = max(criteria["min_event_detection_rate"],
                        baseline_metrics["event_detection_rate"] - criteria["max_detection_loss"])
    lead_min = max(criteria["min_median_lead_h"],
                   baseline_metrics["lead_time_median_h"] - criteria["max_median_lead_loss_h"])
    result["accepted"] = ((result["event_detection_rate"] >= detection_min) &
        (result["lead_time_median_h"] >= lead_min) & (result["alert_ppv"] >= criteria["min_ppv"]) &
        (result["alerts_per_patient_day"] <= criteria["max_alerts_per_patient_day"]) &
        (result["non_aki_patients_alerted"] <= baseline_metrics["non_aki_patients_alerted"] + criteria["max_non_target_patient_alert_increase"]) &
        (result["worst_warning_flip"] <= criteria["max_warning_flip_fraction"]) &
        (result["min_pair_coverage"] >= criteria["min_pair_coverage"]) &
        (result["worst_patient_alert_flip"] <= criteria["max_patient_alert_flip_fraction"]))
    eligible = result[result["accepted"]]
    if eligible.empty:
        return None, result
    mode_complexity = {"cooldown":0,"episode":1,"episode_or_rise":2}
    eligible = eligible.assign(complexity=eligible["repeat_mode"].map(mode_complexity) +
                               (eligible["sustain_hours"]-1) + eligible["rise_ratio"].notna().astype(int))
    selected = eligible.sort_values(["false_alerts_per_patient_day","non_aki_patients_alerted",
                                    "complexity","threshold","candidate"],
                                   ascending=[True,True,True,False,True]).iloc[0]
    return selected.to_dict(), result


def bootstrap_comparison(baseline, candidate, n=1000, seed=20261102):
    if not baseline.index.equals(candidate.index) or n < 1:
        raise ValueError("Paired patient bootstrap requires the same ordered patients")
    rng = np.random.default_rng(seed)
    draws = {"baseline":[], "candidate":[], "difference":[]}
    for _ in range(n):
        idx = rng.integers(0,len(baseline),len(baseline))
        a, b = legacy.summarize(baseline.iloc[idx]), legacy.summarize(candidate.iloc[idx])
        draws["baseline"].append(a);draws["candidate"].append(b)
        draws["difference"].append({k:b[k]-a[k] for k in a})
    rows=[]
    for label, values in draws.items():
        point = legacy.summarize(baseline) if label=="baseline" else legacy.summarize(candidate)
        if label=="difference":
            a,b=legacy.summarize(baseline),legacy.summarize(candidate)
            point={k:b[k]-a[k] for k in a}
        ci=pd.DataFrame(values).quantile([.025,.975])
        for key,value in point.items():
            rows.append({"comparison":label,"metric":key,"estimate":value,
                         "ci_low":ci.loc[.025,key],"ci_high":ci.loc[.975,key],
                         "bootstrap_unit":"patient","bootstrap_resamples":n})
    return pd.DataFrame(rows)

class HourlyMetrics:
    """Cache only outcome/exposure arrays; event rules never consult these labels."""
    def __init__(self, frame):
        patient = frame["patient_id"].to_numpy()
        self.code = np.cumsum(np.r_[True, patient[1:] != patient[:-1]]) - 1
        self.count = int(self.code[-1] + 1) if len(frame) else 0
        self.days = len(frame) / 24.0
        to_onset = frame["hours_to_onset"].to_numpy(float)
        self.possible = (to_onset > 0) & (to_onset <= frame.attrs["horizon_h"])
        self.negative = frame["eligible"].to_numpy(bool) & (frame["label"].to_numpy(float) == 0)
        self.to_onset = to_onset
        self.events = np.bincount(self.code[self.possible], minlength=self.count) > 0

    def summarize(self, flags):
        if self.days == 0:
            raise ValueError("No monitored exposure")
        true = flags & self.possible
        false = flags & self.negative
        alerts = np.bincount(self.code[flags], minlength=self.count)
        detected = self.events & (np.bincount(self.code[true], minlength=self.count) > 0)
        lead = np.full(self.count, -np.inf)
        np.maximum.at(lead, self.code[true], self.to_onset[true])
        lead = lead[detected]
        a,t,f = int(flags.sum()),int(true.sum()),int(false.sum())
        return {"patient_days":self.days,"alerts_per_patient_day":a/self.days,
                "false_alerts_per_patient_day":f/self.days,"unlabelled_alerts_per_patient_day":(a-t-f)/self.days,
                "alert_ppv":t/(t+f) if t+f else np.nan,"aki_events":int(self.events.sum()),
                "event_detection_rate":float(detected.sum()/self.events.sum()) if self.events.any() else np.nan,
                "lead_time_median_h":float(np.median(lead)) if len(lead) else np.nan,
                "lead_time_mean_h":float(np.mean(lead)) if len(lead) else np.nan,
                "non_aki_patients_alerted":float((alerts[~self.events]>0).mean()) if (~self.events).any() else np.nan}
