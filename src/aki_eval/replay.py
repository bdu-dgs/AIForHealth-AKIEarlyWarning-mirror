"""Frozen-origin replay through the original feature engine; no monkeypatching its code."""
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from aki_ml.features import (EventIndex, compute_features, prepare_events, static_feature_names,
                             LEGACY_FEATURE_ENGINE, STABLE_FEATURE_ENGINE, feature_engine_version)


@dataclass
class ReplayData:
    events: pd.DataFrame
    static: pd.DataFrame
    snapshots: pd.DataFrame
    stay_lookup: dict
    config: dict
    feature_names: list
    engine_version: str = LEGACY_FEATURE_ENGINE

    @classmethod
    def load(cls, path, snapshots, config, feature_names, *, engine_version=LEGACY_FEATURE_ENGINE, admission_inputs=None):
        # Original absolute timestamps preserve precision; float32 ICU-hour exports do not.
        stays = snapshots[["stay_id", "icu_intime"]].drop_duplicates("stay_id").reset_index(drop=True)
        lookup = pd.Series(np.arange(len(stays)), index=stays["stay_id"])
        stream = pq.read_table(path, filters=[("stay_id", "in", stays["stay_id"].tolist())]).to_pandas()
        events = prepare_events(stream, stays["icu_intime"], lookup)
        columns = static_feature_names(config)
        if (snapshots.groupby("stay_id", observed=True)[columns].nunique(dropna=False) > 1).any().any():
            raise ValueError("Declared admission-static inputs vary within a stay")
        static = snapshots.groupby("stay_id", observed=True, sort=False)[columns].first().reindex(stays["stay_id"])
        version = feature_engine_version(engine_version)
        if admission_inputs is not None:
            from aki_ml.admission_inputs import read_admission_inputs
            supplied = read_admission_inputs(admission_inputs, snapshots, config).set_index("stay_id")
            static = supplied.reindex(stays["stay_id"])[columns]
        elif version == STABLE_FEATURE_ENGINE and "creatinine_ratio_to_baseline" in feature_names:
            raise ValueError("v2 replay requires float64 admission inputs; rounded snapshot baselines are insufficient")
        static.index = np.arange(len(stays))
        return cls(events, static, snapshots.copy(), lookup.to_dict(), config, list(feature_names), version)

    def query_stays(self, queries):
        q = queries["stay_id"].map(self.stay_lookup)
        if q.isna().any():
            raise ValueError("Replay references an unloaded stay")
        return q.to_numpy(int)

    def service(self, queries, lag):
        if lag == 0:
            return queries["surgical_service"].to_numpy(float), np.ones(len(queries), bool)
        values, known = np.zeros(len(queries)), np.zeros(len(queries), bool)
        for stay, indices in queries.reset_index(drop=True).groupby("stay_id", sort=False).groups.items():
            history = self.snapshots[self.snapshots["stay_id"] == stay].sort_values("hours_since_icu")
            times = history["hours_since_icu"].to_numpy(float)
            pos = np.searchsorted(times, queries.iloc[indices]["hours_since_icu"].to_numpy(float) - lag, side="right") - 1
            valid = pos >= 0
            known[indices] = valid
            values[np.asarray(indices)[valid]] = history["surgical_service"].to_numpy(float)[pos[valid]]
        return values, known

    def _covered(self, events, qs, qt):
        delay = float(self.config["features"]["lab_result_delay_hours"])
        cr = events[events["concept"] == "creatinine"]
        hr = events[(events["concept"] == "heart_rate") & (events["t"] > 0)]
        if self.engine_version == STABLE_FEATURE_ENGINE:
            from aki_ml.feature_windows import WindowIndex
            Index = WindowIndex
        else:
            Index = EventIndex
        cr_index = Index(cr["stay_idx"], cr["t"] + delay, cr["value"])
        hr_index = Index(hr["stay_idx"], hr["t"], hr["value"])
        lo, hi = cr_index.bounds(qs, qt, window=24*self.config["coverage"]["prior_creatinine_lookback_days"], closed_left=True)
        ok = hi > lo
        if self.config["coverage"]["require_prior_heart_rate"]:
            lo, hi = hr_index.bounds(qs, qt)
            ok &= hi > lo
        return ok

    def matrix(self, queries, *, lag_hours=0, clip=None, events=None):
        """clip='all': measured event time <= origin-lag; windows remain at origin.

        Lab availability still uses the original delay relative to origin. Service uses
        the last recorded hourly state at cutoff, not a future service value.
        """
        if lag_hours < 0 or clip not in [None, "all", "urine"]:
            raise ValueError("Invalid cutoff replay")
        queries = queries.reset_index(drop=True)
        ev = self.events if events is None else events
        qs, qt = self.query_stays(queries), queries["hours_since_icu"].to_numpy(float)
        service, service_known = self.service(queries, lag_hours)
        result = np.empty((len(queries), len(self.feature_names)), np.float32)
        covered = np.zeros(len(queries), bool)
        if clip is None and lag_hours == 0:
            result[:] = compute_features(ev, self.static, service, qs, qt, self.config, self.feature_names, engine_version=self.engine_version)
            covered[:] = self._covered(ev, qs, qt)
            return result, covered
        # The study has an hourly grid: grouping by origin avoids one engine call per row.
        for hour in np.unique(qt):
            idx = np.flatnonzero(qt == hour)
            if clip == "urine":
                part = ev[~((ev["concept"] == "urine_output") & (ev["t"] > hour))]
            else:
                part = ev[ev["t"] <= hour-lag_hours]
            result[idx] = compute_features(part, self.static, service[idx], qs[idx], qt[idx], self.config, self.feature_names, engine_version=self.engine_version)
            covered[idx] = self._covered(part, qs[idx], qt[idx])
        return result, covered & service_known

    def future_injection(self, queries):
        additions=[]
        for stay, group in queries.groupby("stay_id", observed=True):
            t=float(group["hours_since_icu"].max()) + 1/3600
            for concept, value, itemid in [("heart_rate",290.,211),("urine_output",4999.,40055)]:
                additions.append({"stay_idx":self.stay_lookup[stay],"t":t,"concept":concept,"value":value,"itemid":itemid})
        return pd.concat([self.events,pd.DataFrame(additions)],ignore_index=True)


def sample_patients(snapshots, cap, seed):
    ids = np.sort(snapshots["patient_id"].astype(str).unique())
    rng = np.random.default_rng(seed)
    chosen = ids if len(ids) <= cap else rng.choice(ids, cap, replace=False)
    return snapshots[snapshots["patient_id"].astype(str).isin(chosen)].reset_index(drop=True)


def audit_replay(context, sample, *, min_rows=2000):
    from .checks import features_use_past, no_post_snapshot_urine
    stored = sample[context.feature_names].to_numpy(np.float32)
    replayed, _ = context.matrix(sample, clip="all")
    injected, _ = context.matrix(sample, events=context.future_injection(sample))
    urine_cut, _ = context.matrix(sample, clip="urine")
    return (features_use_past(stored,replayed,injected,min_rows=min_rows),
            no_post_snapshot_urine(injected,urine_cut,context.feature_names))
