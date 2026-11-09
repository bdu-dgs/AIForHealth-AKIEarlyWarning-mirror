"""Regression checks for versioned local arithmetic and high-precision admission inputs."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from aki_ml.feature_windows import WindowIndex
from aki_ml.features import (compute_features, static_feature_names, STABLE_FEATURE_ENGINE, LEGACY_FEATURE_ENGINE)
from aki_ml.admission_inputs import (reconstruct_baselines, admission_frame, write_admission_inputs,
                                    read_admission_inputs, file_hash)
from aki_eval.policies import AlertRule, alert_events, prepare
from aki_eval.replay import ReplayData
from aki_ml import scoring

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    return yaml.safe_load((ROOT/"configs/default.yaml").read_text(encoding="utf-8"))


def test_native_times_and_window_endpoints():
    index = WindowIndex([100000, 100000, 100001], [1-1e-8, 1+1e-8, 0], [2., 4., 999.])
    np.testing.assert_array_equal(index.last([100000], [1])[0], [2.])
    lo, hi = index.bounds([100000], [1+1e-8], window=2e-8)
    assert index.count(lo, hi)[0] == 1
    lo, hi = index.bounds([100000], [1+1e-8], since_hour=0, include_t=False)
    assert index.sum(lo, hi)[0] == 2
    lo, hi = index.bounds([999999], [1], window=24)
    assert index.count(lo, hi)[0] == 0
    assert np.isnan(index.std(lo, hi)[0])


def test_constant_windows_are_exact_zero_and_real_small_changes_survive():
    for value in [.1, 80.1, 95.]:
        idx = WindowIndex([0]*101, np.arange(101), np.full(101, value))
        lo, hi = idx.bounds([0], [100], window=24)
        assert idx.std(lo, hi)[0] == 0
        assert idx.slope(lo, hi)[0] == 0
    idx = WindowIndex([0, 0], [0., 1.], [1., 1.+1e-10])
    lo, hi = idx.bounds([0], [1])
    assert idx.std(lo, hi)[0] > 0
    assert idx.slope(lo, hi)[0] == pytest.approx(1e-10, rel=1e-6)


def inputs(config):
    rows = []
    for stay in [0, 1]:
        for t in [1., 2., 3., 5.]:
            rows.extend([(stay, t, "heart_rate", 80.1+stay, 211),
                         (stay, t, "spo2", 95., 646),
                         (stay, t, "temp_c", 36.6, 676),
                         (stay, t, "urine_output", 20., 40055)])
        rows.extend([(stay, -1., "creatinine", 1.1, 50912), (stay, 1., "creatinine", 1.2, 50912)])
    events = pd.DataFrame(rows, columns=["stay_idx", "t", "concept", "value", "itemid"])
    static = pd.DataFrame(0., index=[0, 1], columns=static_feature_names(config))
    static["baseline_cr_admission"] = [1.1, 1.015]
    names = ["hours_since_icu", "heart_rate_mean_24h", "heart_rate_std_24h", "heart_rate_slope_24h",
             "spo2_std_24h", "temp_c_mean_6h", "temp_c_std_24h", "temp_c_slope_24h", "urine_ml_6h",
             "creatinine_last", "creatinine_slope_48h", "creatinine_ratio_to_baseline"]
    return events, static, names


def test_v2_feature_and_probe_risk_equal_across_batches_chunks_and_future(config):
    ev, static, names = inputs(config)
    qs = np.array([0, 1, 0, 1]); qt = np.array([3.5, 3., 6., 5.])
    def calc(events, states=static, stays=qs, times=qt):
        return compute_features(events, states, np.zeros(len(stays)), stays, times, config, names,
                                engine_version=STABLE_FEATURE_ENGINE)
    original = calc(ev)
    extra = pd.DataFrame([(0, 1000., "spo2", 50., 646), (0, 1000., "heart_rate", 290., 211),
                          (99999, 1., "spo2", 50., 646), (99999, 2., "creatinine", 30., 50912)], columns=ev.columns)
    np.testing.assert_array_equal(original, calc(pd.concat([ev, extra], ignore_index=True)))
    chunks = np.concatenate([calc(ev, stays=qs[:2], times=qt[:2]), calc(ev, stays=qs[2:], times=qt[2:])])
    np.testing.assert_array_equal(original, chunks)
    for row, (stay, hour) in enumerate(zip(qs, qt)):
        one = ev[(ev["stay_idx"] == stay) & (ev["t"] <= hour)].copy()
        one["stay_idx"] = 0
        state = static.loc[[stay]].set_axis([0])
        actual = calc(one, states=state, stays=np.array([0]), times=np.array([hour]))
        np.testing.assert_array_equal(actual[0], original[row])
    # A zero split is intentionally sensitive: equal features must give equal decisions.
    assert np.all(original[:, names.index("spo2_std_24h")] == 0)


def test_v2_refuses_rounded_denominator_and_unknown_version(config):
    ev, static, names = inputs(config)
    static["baseline_cr_admission"] = static["baseline_cr_admission"].astype(np.float32)
    with pytest.raises(ValueError, match="float64"):
        compute_features(ev, static, [0], [0], [3], config, names, engine_version=STABLE_FEATURE_ENGINE)
    with pytest.raises(ValueError, match="Unknown"):
        compute_features(ev, static, [0], [0], [3], config, names, engine_version="unregistered")
    assert compute_features(ev, static, [0], [0], [3], config, names).shape == (1, len(names))


@pytest.fixture
def baseline_data(tmp_path, config):
    admit = pd.Timestamp("2020-01-01")
    rows = []
    for subject, stay, baseline in [(1, "S1", np.float32(1.1)), (2, "S2", np.float32(1.015)), (3, "S3", np.nan)]:
        row = {"patient_id": "SYN"+str(subject), "subject_id": subject, "stay_id": stay, "icu_intime": admit,
               "hours_since_icu": 3., "surgical_service": 0.}
        row.update({name: 0. for name in static_feature_names(config)})
        row["baseline_cr_admission"] = baseline; rows.append(row)
    snapshots = pd.DataFrame(rows); snapshots["baseline_cr_admission"] = snapshots["baseline_cr_admission"].astype(np.float32)
    labs = []
    for subject, hour, value in [(1, -1, 1.1), (1, 1, 9), (2, -200, 1.01), (2, -220, 1.02),
                                  (3, 1, 1), (3, -300, -10)]:
        labs.append({"SUBJECT_ID": subject, "ITEMID": 50912, "CHARTTIME": admit+pd.Timedelta(hours=hour),
                     "VALUE": str(value), "VALUENUM": value})
    raw = tmp_path/"labs.csv.gz"; pd.DataFrame(labs).to_csv(raw, index=False, compression="gzip")
    return snapshots, raw


def test_admission_reconstruction_and_roundtrip_keep_input_precision(tmp_path, baseline_data, config):
    snapshots, raw = baseline_data
    baseline, pre, history, scanned = reconstruct_baselines(raw, snapshots, config)
    assert scanned == 6
    assert baseline.loc[1] == 1.1
    assert baseline.loc[2] == pytest.approx(1.015, abs=1e-15)
    assert np.isnan(baseline.loc[3])
    frame = admission_frame(snapshots, baseline, config)
    directory = write_admission_inputs(tmp_path/"admission", frame, {"source_sha256": file_hash(raw)})
    loaded = read_admission_inputs(directory, snapshots.iloc[[1]], config)
    assert loaded["baseline_cr_admission"].dtype == np.float64
    assert loaded["baseline_cr_admission"].iloc[0] == frame.loc[1, "baseline_cr_admission"]
    assert loaded["baseline_cr_admission"].iloc[0] != float(np.float32(1.015))
    with pytest.raises(FileExistsError):
        write_admission_inputs(directory, frame, {})


@pytest.mark.parametrize("broken", ["hash", "float32", "patient", "missing_stay"])
def test_admission_reader_rejects_invalid_artifact(tmp_path, baseline_data, config, broken):
    snapshots, raw = baseline_data; baseline, *_ = reconstruct_baselines(raw, snapshots, config)
    frame = admission_frame(snapshots, baseline, config)
    directory = write_admission_inputs(tmp_path/"artifact", frame, {})
    if broken == "hash":
        with (directory/"inputs.parquet").open("ab") as target:
            target.write(b"changed")
    else:
        if broken == "float32": frame["baseline_cr_admission"] = frame["baseline_cr_admission"].astype(np.float32)
        if broken == "patient": frame.loc[0, "patient_id"] = "OTHER-SYNTHETIC"
        if broken == "missing_stay": frame = frame.iloc[1:]
        pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), directory/"inputs.parquet")
        meta = json.loads((directory/"manifest.json").read_text())
        meta["sha256"] = file_hash(directory/"inputs.parquet")
        (directory/"manifest.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        read_admission_inputs(directory, snapshots, config)


def test_v2_replay_requires_and_uses_admission_artifact(tmp_path, baseline_data, config):
    snapshots, raw = baseline_data; baseline, *_ = reconstruct_baselines(raw, snapshots, config)
    artifact = write_admission_inputs(tmp_path/"inputs", admission_frame(snapshots, baseline, config), {})
    stream = []
    for row in snapshots.itertuples():
        for concept, value, hour, item in [("creatinine", 1.2, 1, 50912), ("heart_rate", 80, 1, 211)]:
            stream.append({"stay_id": row.stay_id, "charttime": row.icu_intime+pd.Timedelta(hours=hour),
                           "concept": concept, "value": np.float32(value), "source_itemid": item})
    path = tmp_path/"events.parquet"; pd.DataFrame(stream).to_parquet(path, index=False)
    names = ["creatinine_last", "creatinine_ratio_to_baseline"]
    with pytest.raises(ValueError, match="admission inputs"):
        ReplayData.load(path, snapshots, config, names, engine_version=STABLE_FEATURE_ENGINE)
    context = ReplayData.load(path, snapshots, config, names, engine_version=STABLE_FEATURE_ENGINE, admission_inputs=artifact)
    matrix, covered = context.matrix(snapshots, clip="all")
    assert covered.all()
    expected = np.float32(float(np.float32(1.2))/1.1)
    assert matrix[0, 1] == expected


def test_rapid_rise_ignores_stale_or_poisoned_history_cache():
    frame = pd.DataFrame({"patient_id": ["SYN"]*3, "hours_since_icu": [1, 2, 3],
                          "snapshot_time": pd.date_range("2020", periods=3, freq="h"),
                          "creatinine_aki_onset_time": [pd.NaT]*3, "eligible": [True]*3, "label": [0]*3,
                          "risk": [.4, .4, .4]})
    changed = prepare(frame, 24); changed["risk"] = [.1, .16, .2]; changed["prior_min_12"] = [np.nan, .4, .4]
    rule = AlertRule(name="rise", cooldown_hours=0, rise_ratio=1.5)
    np.testing.assert_array_equal(alert_events(changed, .1, rule), [True, True, True])
    fresh = prepare(changed, 24)
    assert "prior_min_12" not in fresh
    np.testing.assert_array_equal(alert_events(changed, .1, rule), alert_events(fresh, .1, rule))


@pytest.mark.parametrize("declared,trained,valid", [(None,None,True), (STABLE_FEATURE_ENGINE,STABLE_FEATURE_ENGINE,True),
                                                   (STABLE_FEATURE_ENGINE,None,False), (None,STABLE_FEATURE_ENGINE,False)])
def test_live_model_engine_contract(tmp_path, monkeypatch, config, declared, trained, valid):
    (tmp_path/"configs").mkdir(); (tmp_path/"configs/default.yaml").write_text(yaml.safe_dump(config))
    modeldir = tmp_path/"model"; modeldir.mkdir(); path = modeldir/"pipeline.joblib"; path.write_bytes(b"synthetic")
    model = {"model_dir": str(modeldir), "pipeline_sha256": hashlib.sha256(b"synthetic").hexdigest(),
             "family": "lightgbm", "feature_set": "no_dc", "run_id": "synthetic"}
    if declared is not None: model["feature_engine_version"] = declared
    policy = {"model": model, "threshold": {"locked": True}}
    policy_path = tmp_path/"policy.json"; policy_path.write_text(json.dumps(policy))
    pipeline = SimpleNamespace(family="lightgbm", feature_set="no_dc", metadata={} if trained is None else {"feature_engine_version":trained}, feature_names=[])
    monkeypatch.setattr(scoring, "REPO_ROOT", tmp_path); monkeypatch.setattr(scoring.joblib, "load", lambda _: pipeline)
    if valid:
        loaded = scoring.LockedModel(policy_path)
        assert loaded.feature_engine_version == (trained or LEGACY_FEATURE_ENGINE)
    else:
        with pytest.raises(RuntimeError, match="different feature engines"):
            scoring.LockedModel(policy_path)


def test_v2_live_feature_path_keeps_precision_and_ignores_future(config):
    admit=datetime(2020,1,1,tzinfo=timezone.utc)
    def observation(metric,value,hour):
        return {"metric":metric,"value":value,"measured_at":(admit+timedelta(hours=hour)).isoformat()}
    obs=[observation("baseline_cr_admission",1.1,0),observation("creatinine",1.2,1),
         observation("heart_rate",80.1,1),observation("heart_rate",80.1,3),
         observation("spo2",95,1),observation("spo2",95,3)]
    names=["creatinine_last","creatinine_ratio_to_baseline","heart_rate_std_24h","spo2_std_24h"]
    class Probe:
        feature_engine_version=STABLE_FEATURE_ENGINE
        data_cfg=config
        pipe=SimpleNamespace(feature_names=names)
        policy={"monitoring_hours":[1,72],"policy_version":"synthetic-v2"}
        version="synthetic"
        def predict(self,X):
            self.inputs=X.copy()
            return {}
    model=Probe()
    scoring.score_hours(model,obs,admit,[3.5]);before=model.inputs.copy()
    future=obs+[observation("spo2",50,5),observation("heart_rate",290,5),observation("baseline_cr_admission",5,9)]
    scoring.score_hours(model,future,admit,[3.5])
    np.testing.assert_array_equal(model.inputs,before)
    assert before[0,1]==np.float32(float(np.float32(1.2))/1.1)
    assert before[0,2]==before[0,3]==0
    new_key=scoring.model_key(model);model.feature_engine_version=LEGACY_FEATURE_ENGINE
    assert scoring.model_key(model)!=new_key
