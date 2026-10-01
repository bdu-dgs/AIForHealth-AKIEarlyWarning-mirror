"""As-of feature engine on the event stream (event-driven replay and dashboard scoring).

Port of notebook 01 Part 10 (`build_partition_features`) and its Part 6 `EventIndex`, reading
`datasets/event_stream.parquet` instead of the raw MIMIC views. Every feature at query time t uses only
events available at or before t; windows are (t - W, t]; lab results become usable `lab_result_delay_hours`
after the draw. Notebook 02 verifies that this engine reproduces the stored hourly features exactly.

Two inputs are not in the event stream and are supplied by the caller:
- static admission inputs (demographics, baseline creatinine, one-hot admission fields, `_dc` comorbidities),
  constant within a stay;
- `surgical_service` (from SERVICES transfers), passed per query time.
"""
import numpy as np
import pandas as pd

KEY_SCALE, T_OFFSET = 1e5, 2e4        # key = stay * KEY_SCALE + (t + T_OFFSET); history >= -2e4 h allowed

VITALS = ["heart_rate", "sbp", "dbp", "mbp", "resp_rate", "temp_c", "spo2"]
VITAL_STATS = [("last", None), ("hours_since_last", None), ("mean", 6), ("min", 6), ("max", 6), ("mean", 24),
               ("min", 24), ("max", 24), ("std", 24), ("slope", 24), ("count", 24)]
LAB_STATS = [("last", None), ("hours_since_last", None), ("delta_prev", None), ("min", 48), ("max", 48)]
CREATININE_STATS = [("last", None), ("hours_since_last", None), ("delta_prev", None), ("min", 48), ("max", 48),
                    ("count", 48), ("slope", 48)]
STATIC_BASE = ["age_years", "age_over_89", "gender_male", "pre_icu_hours", "baseline_cr_admission",
               "baseline_cr_available", "admission_type_urgent", "admission_type_elective",
               "admission_location_group_transfer", "admission_location_group_referral", "first_careunit_sicu",
               "first_careunit_csru", "first_careunit_ccu", "first_careunit_tsicu"]


class EventIndex:
    """Events of one concept sorted by (stay, time) with vectorised window statistics (verbatim from notebook 01)."""

    def __init__(self, stay_idx, t_hours, values):
        stay_idx, t_hours, values = (np.asarray(stay_idx, np.int64), np.asarray(t_hours, np.float64),
                                     np.asarray(values, np.float64))
        ok = np.isfinite(t_hours) & np.isfinite(values)
        order = np.lexsort((t_hours[ok], stay_idx[ok]))
        self.stay, self.t, self.v = stay_idx[ok][order], t_hours[ok][order], values[ok][order]
        self.key = self.stay * KEY_SCALE + self.t + T_OFFSET
        self.center = float(self.v.mean()) if len(self.v) else 0.0
        vc = self.v - self.center
        zero = np.zeros(1)
        self.c0 = np.concatenate([zero, np.cumsum(self.v)])
        self.c1, self.c2 = np.concatenate([zero, np.cumsum(vc)]), np.concatenate([zero, np.cumsum(vc * vc)])
        self.ct, self.ctt = np.concatenate([zero, np.cumsum(self.t)]), np.concatenate([zero, np.cumsum(self.t ** 2)])
        self.ctv = np.concatenate([zero, np.cumsum(self.t * vc)])
        self.v_sentinel = np.append(self.v, np.nan)

    def _key(self, q_stay, q_t):
        return np.asarray(q_stay, np.int64) * KEY_SCALE + T_OFFSET + np.asarray(q_t, np.float64)

    def bounds(self, q_stay, q_t, window=None, since_hour=None, include_t=True, closed_left=False):
        qk = self._key(q_stay, q_t)
        hi = np.searchsorted(self.key, qk, side="right" if include_t else "left")
        if since_hour is not None:
            lo = np.searchsorted(self.key, self._key(q_stay, since_hour), side="left")
        elif window is None:
            lo = np.searchsorted(self.key, np.asarray(q_stay, np.int64) * KEY_SCALE, side="left")
        else:
            lo = np.searchsorted(self.key, qk - window, side="left" if closed_left else "right")
        return lo, np.maximum(hi, lo)

    def count(self, lo, hi):
        return (hi - lo).astype(np.float64)

    def sum(self, lo, hi):
        return self.c0[hi] - self.c0[lo]

    def mean(self, lo, hi):
        n = (hi - lo).astype(np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(n > 0, (self.c1[hi] - self.c1[lo]) / n + self.center, np.nan)

    def std(self, lo, hi):
        n = (hi - lo).astype(np.float64)
        s1, s2 = self.c1[hi] - self.c1[lo], self.c2[hi] - self.c2[lo]
        with np.errstate(invalid="ignore", divide="ignore"):
            var = (s2 - s1 * s1 / n) / (n - 1)
        return np.where(n >= 2, np.sqrt(np.clip(var, 0, None)), np.nan)

    def slope(self, lo, hi, min_span_hours=1.0):
        n = (hi - lo).astype(np.float64)
        st, stt = self.ct[hi] - self.ct[lo], self.ctt[hi] - self.ctt[lo]
        sv, stv = self.c1[hi] - self.c1[lo], self.ctv[hi] - self.ctv[lo]
        den = n * stt - st * st
        t_sent = np.append(self.t, np.nan)
        span = np.where(n >= 2, t_sent[np.maximum(hi - 1, 0)] - t_sent[np.minimum(lo, len(self.t))], 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where((n >= 2) & (span >= min_span_hours) & (np.abs(den) > 1e-9), (n * stv - st * sv) / den, np.nan)

    def _reduce(self, ufunc, lo, hi):
        if len(lo) == 0:
            return np.array([], dtype=np.float64)
        idx = np.empty(2 * len(lo), dtype=np.int64)
        idx[0::2], idx[1::2] = lo, hi
        out = ufunc.reduceat(self.v_sentinel, idx)[0::2].astype(np.float64)
        return np.where(hi > lo, out, np.nan)

    def min(self, lo, hi):
        return self._reduce(np.minimum, lo, hi)

    def max(self, lo, hi):
        return self._reduce(np.maximum, lo, hi)

    def last(self, q_stay, q_t, back=1):
        lo, hi = self.bounds(q_stay, q_t)
        pos = hi - back
        ok = pos >= lo
        safe = np.where(ok, pos, 0)
        v = self.v[safe] if len(self.v) else np.zeros(len(pos))
        t = self.t[safe] if len(self.t) else np.zeros(len(pos))
        return np.where(ok, v, np.nan), np.where(ok, t, np.nan)


def static_feature_names(data_cfg):
    return STATIC_BASE + list(data_cfg["sources"]["comorbidities_icd9_prefix"])


def prepare_events(stream, intime_of_stay, stay_idx_of):
    """Event-stream rows -> stay_idx, t (hours since ICU INTIME, float64 from timestamps), concept, value, itemid."""
    ev = pd.DataFrame({"stay_idx": stay_idx_of.reindex(stream["stay_id"]).to_numpy(np.int64),
                       "concept": stream["concept"].to_numpy(), "value": stream["value"].to_numpy(),
                       "itemid": stream["source_itemid"].to_numpy()})
    intime = intime_of_stay.to_numpy()[ev["stay_idx"].to_numpy()]
    ev["t"] = (stream["charttime"].to_numpy().astype("datetime64[ns]") - intime) / np.timedelta64(1, "h")
    return ev


def availability_times(ev, data_cfg):
    """(stay_idx, t) at which each event becomes usable by the features (labs after the reporting delay)."""
    feat = data_cfg["features"]
    lab_concepts = set(data_cfg["sources"]["labs"])
    t = ev["t"].to_numpy() + np.where(ev["concept"].isin(lab_concepts).to_numpy(), float(feat["lab_result_delay_hours"]), 0.0)
    return pd.DataFrame({"stay_idx": ev["stay_idx"].to_numpy(), "t": t})


def compute_features(ev, static, surgical_service, q_stay, q_t, data_cfg, feature_names):
    """Feature matrix (float32, `feature_names` order) at query times q_t (hours since ICU) for stays q_stay.

    ev: prepare_events output for (at least) the queried stays; static: DataFrame indexed by stay_idx holding
    static_feature_names(); surgical_service: array aligned with the queries.
    """
    src, feat, cr_rules = data_cfg["sources"], data_cfg["features"], data_cfg["labels"]["creatinine_kdigo"]
    qs, qt = np.asarray(q_stay, np.int64), np.asarray(q_t, np.float64)
    F = {"hours_since_icu": qt}

    def ei_of(frame, t_min=None):
        e = frame if t_min is None else frame[frame["t"] > t_min]
        return EventIndex(e["stay_idx"], e["t"], e["value"])

    def window_stats(ei, concept, stats, prefix=None):
        prefix = prefix or concept
        for stat, w in stats:
            if stat == "last":
                F[f"{prefix}_last"] = ei.last(qs, qt)[0]
            elif stat == "hours_since_last":
                F[f"{prefix}_hours_since_last"] = qt - ei.last(qs, qt)[1]
            elif stat == "delta_prev":
                F[f"{prefix}_delta_prev"] = ei.last(qs, qt)[0] - ei.last(qs, qt, back=2)[0]
            else:
                lo, hi = ei.bounds(qs, qt, window=w)
                F[f"{prefix}_{stat}_{w}h"] = getattr(ei, stat)(lo, hi)

    by_concept = {c: g for c, g in ev.groupby("concept", sort=False)}
    empty = ev.iloc[:0]

    def concept(c):
        return by_concept.get(c, empty)

    def concepts(cs):
        parts = [by_concept[c] for c in cs if c in by_concept]
        # Original stream order decides ties at equal times (as in notebook 01), so restore it after concatenating.
        return pd.concat(parts).sort_index(kind="stable") if parts else empty

    def any_in(frame, window=None):
        lo, hi = EventIndex(frame["stay_idx"], frame["t"], np.ones(len(frame))).bounds(qs, qt, window=window)
        return hi > lo

    for v in VITALS:
        window_stats(ei_of(concept(v)), v, VITAL_STATS)
    window_stats(ei_of(concept("glucose_poc")), "glucose_poc",
                 [("last", None), ("hours_since_last", None), ("min", 24), ("max", 24)])
    window_stats(ei_of(concept("fio2")), "fio2", [("last", None), ("max", 24)])
    bp = concepts(["sbp", "dbp", "mbp"])
    F["arterial_line_24h"] = any_in(bp[bp["itemid"].isin(set(src["arterial_line_itemids"]))], 24)

    gcs = concepts(["gcs_eye", "gcs_verbal", "gcs_motor", "gcs_verbal_ett"])
    gw = gcs.pivot_table(index=["stay_idx", "t"], columns="concept", values="value", aggfunc="max") if len(gcs) else pd.DataFrame()
    for c in ["gcs_eye", "gcs_verbal", "gcs_motor", "gcs_verbal_ett"]:
        gw[c] = gw[c] if c in gw else np.nan
    gw = gw.reset_index() if len(gw) else pd.DataFrame(columns=["stay_idx", "t", "gcs_eye", "gcs_verbal", "gcs_motor", "gcs_verbal_ett"])
    ett = gw["gcs_verbal_ett"].eq(1)
    total = gw["gcs_eye"] + gw["gcs_motor"] + gw["gcs_verbal"].where(~ett, 1.0)
    for name, vals, mask in [("gcs_total", total, total.notna()), ("gcs_eye", gw["gcs_eye"], gw["gcs_eye"].notna()),
                             ("gcs_motor", gw["gcs_motor"], gw["gcs_motor"].notna()),
                             ("gcs_verbal", gw["gcs_verbal"], gw["gcs_verbal"].notna() & ~ett)]:
        ei = EventIndex(gw.loc[mask, "stay_idx"], gw.loc[mask, "t"], vals[mask])
        window_stats(ei, name, [("last", None), ("min", 24)] if name == "gcs_total" else [("last", None)])
    F["gcs_verbal_intubated_24h"] = any_in(gw[ett], 24)

    wt = concepts(["weight_admit_kg", "weight_daily_kg", "weight_order_kg"])
    weight = EventIndex(wt["stay_idx"], wt["t"], wt["value"]).last(qs, qt)[0]
    height = ei_of(concept("height_cm")).last(qs, qt)[0]
    F["weight_kg"], F["height_cm"] = weight, height
    F["bmi"] = weight / (height / 100) ** 2

    u = concept("urine_output")
    u = u[u["t"] > 0].sort_values("value").drop_duplicates(["stay_idx", "t", "itemid"], keep="last")
    u = u.groupby(["stay_idx", "t"], as_index=False)["value"].sum()
    uei = EventIndex(u["stay_idx"], u["t"], u["value"])
    uo_sum = {}
    for w in feat["urine_windows_hours"]:
        lo, hi = uei.bounds(qs, qt, window=w)
        uo_sum[w] = np.where(hi > lo, uei.sum(lo, hi), np.nan)
        F[f"urine_ml_{w}h"] = uo_sum[w]
    for w in [6, 24]:
        with np.errstate(invalid="ignore", divide="ignore"):
            F[f"urine_ml_kg_h_{w}h"] = uo_sum[w] / np.minimum(w, qt) / weight
    F["urine_hours_since_last"] = qt - uei.last(qs, qt)[1]
    lo, hi = uei.bounds(qs, qt, since_hour=0.0)
    F["urine_ml_since_icu"] = np.where(hi > lo, uei.sum(lo, hi), np.nan)
    F["gu_irrigation"] = any_in(concept("gu_irrigant"))

    fei = ei_of(concept("fluid_in_ml"), t_min=0)
    lo, hi = fei.bounds(qs, qt, window=24)
    fin24 = fei.sum(lo, hi)
    F["fluid_in_ml_24h"] = fin24
    lo, hi = fei.bounds(qs, qt, since_hour=0.0)
    F["fluid_in_ml_since_icu"] = fei.sum(lo, hi)
    F["fluid_balance_ml_24h"] = fin24 - uo_sum[24]
    exposure_windows = {**{c: feat["infusion_windows_hours"] for c in src["medications"]["infusions"]},
                        **{c: feat["bolus_windows_hours"] for c in src["medications"]["boluses"]},
                        "mech_vent": [feat["ventilation_window_hours"]]}
    for c, windows in exposure_windows.items():
        ei = ei_of(concept(c))
        for w in windows:
            lo, hi = ei.bounds(qs, qt, window=w)
            F[f"{c}_{w}h"] = hi > lo

    delay = float(feat["lab_result_delay_hours"])
    labs_list = [c for c in src["labs"] if c != "creatinine"]
    labs = concepts(labs_list)
    labs = labs.groupby(["stay_idx", "t", "concept"], as_index=False)["value"].mean()
    labs["t"] += delay
    lab_groups = {c: g for c, g in labs.groupby("concept", sort=False)}
    for lab in labs_list:
        window_stats(ei_of(lab_groups.get(lab, labs.iloc[:0])), lab, LAB_STATS)

    cr = concept("creatinine")
    cr = cr.assign(value=cr["value"].astype("float64").round(2)).groupby(["stay_idx", "t"], as_index=False)["value"].mean()
    cr = cr[cr["t"] >= -24 * feat["lab_lookback_days"]]
    cei = EventIndex(cr["stay_idx"], cr["t"] + delay, cr["value"])
    window_stats(cei, "creatinine", CREATININE_STATS)
    lo, hi = cei.bounds(qs, qt, window=24 * cr_rules["rel_window_days"])
    cr_min7 = cei.min(lo, hi)
    F["creatinine_min_7d"] = cr_min7
    cr_last = F["creatinine_last"].astype(np.float32).astype(float)          # notebook 01 stored float32 first
    min48 = F["creatinine_min_48h"].astype(np.float32)
    with np.errstate(invalid="ignore", divide="ignore"):
        F["creatinine_rise_from_48h_min"] = cr_last - min48
        F["creatinine_ratio_to_7d_min"] = cr_last / cr_min7
        F["creatinine_ratio_to_baseline"] = cr_last / static.loc[qs, "baseline_cr_admission"].to_numpy()
        F["bun_creatinine_ratio"] = F["bun_last"].astype(np.float32) / cr_last

    F["surgical_service"] = np.nan_to_num(np.asarray(surgical_service, np.float64), nan=0.0)
    rx = concepts([c for c in by_concept if c.startswith("rx_")])
    if len(rx):
        days = np.floor((rx["value"].to_numpy(np.float64) / 24).clip(min=0)).astype(int) + 1
        rep = rx.loc[rx.index.repeat(days)]
        offs = np.concatenate([np.arange(d) for d in days])
        rx = rep.assign(t=rep["t"].to_numpy() + 24 * offs)
    for c in src["prescriptions"]:
        r = rx[rx["concept"] == f"rx_{c}"] if len(rx) else rx
        F[f"rx_{c}_24h"] = any_in(r, 24)
    for col in static_feature_names(data_cfg):
        F[col] = static.loc[qs, col].to_numpy()

    missing = [f for f in feature_names if f not in F]
    assert not missing, f"engine does not produce {missing[:5]}"
    X = np.empty((len(qs), len(feature_names)), dtype=np.float32)
    for j, f in enumerate(feature_names):
        X[:, j] = np.asarray(F[f], dtype=np.float64)
    return X
