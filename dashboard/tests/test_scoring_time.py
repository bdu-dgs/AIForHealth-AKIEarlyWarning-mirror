"""Synthetic time-boundary regressions; no fitted model or patient data is loaded."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient

from dashboard import model_worker
from dashboard.api.main import create_app
from dashboard.api.schemas import set_clock, stamp
from scripts import score_dashboard as batch
from aki_ml import scoring


ADMIT = datetime(2026, 1, 1, tzinfo=timezone.utc)
PATIENT = {'patient_id': 'time_probe', 'encounter_id': 'time_encounter',
           'name': 'Synthetic timing check', 'icu_admitted_at': stamp(ADMIT)}
CFG = yaml.safe_load((Path(__file__).resolve().parents[2] / 'configs/default.yaml').read_text(encoding='utf-8'))
FEATURES = ['hours_since_icu', 'baseline_cr_admission', 'age_years', 'creatinine_ratio_to_baseline']


def observation(record_id, metric, value, hour, revision=1, available=None):
    return {'record_id': record_id, 'revision': revision, 'patient_id': PATIENT['patient_id'],
            'encounter_id': PATIENT['encounter_id'], 'metric': metric, 'label': metric,
            'unit': '', 'value': value, 'measured_at': stamp(ADMIT + timedelta(hours=hour)),
            'available_at': stamp(ADMIT + timedelta(hours=hour if available is None else available))}


def base_observations():
    return [observation('hr', 'heart_rate', 80, .5),
            observation('cr', 'creatinine', 1, 0, available=1),
            observation('baseline', 'baseline_cr_admission', 1, 0)]


class FeatureProbe:
    """Capture real engine inputs; the test output is deliberately not a clinical model."""
    def __init__(self):
        self.data_cfg = CFG
        self.pipe = SimpleNamespace(feature_names=FEATURES)
        self.version = 'synthetic-time-test'
        self.policy = {'policy_version': 'synthetic-time-test', 'alert_horizon_h': 24, 'threshold': {}}
        self.window = (100, 101)  # These contract tests do not attach an alert threshold.
        self.inputs = None

    def predict(self, X):
        self.inputs = X.copy()
        risk = np.clip(np.nan_to_num(X[:, 1]) / 10, 0, 1)
        return {24: (risk, [[] for _ in X])}


@pytest.fixture
def service(tmp_path, monkeypatch):
    clock = [ADMIT]
    with TestClient(create_app(tmp_path / 'site', watch=False)) as client:
        set_clock(lambda: stamp(clock[0]))

        def request(base, path, payload=None, **kwargs):
            response = client.get(path) if payload is None else client.post(path, json=payload)
            assert response.status_code == 200, response.text
            return response.json()

        monkeypatch.setattr(model_worker, 'call', request)
        monkeypatch.setattr(batch, 'api', request)
        request('', '/api/import', {'kind': 'input', 'patients': [PATIENT]})
        clock[0] = ADMIT + timedelta(hours=1)
        request('', '/api/import', {'kind': 'input', 'observations': base_observations()})
        yield client, clock, request


def test_static_features_follow_each_cutoff_and_keep_query_order():
    obs = base_observations() + [observation('later', 'baseline_cr_admission', 5, 10)]
    model = FeatureProbe()
    hours, _ = scoring.score_hours(model, obs, ADMIT, [2, 12, 3])
    np.testing.assert_array_equal(hours, [2, 12, 3])
    np.testing.assert_allclose(model.inputs[:, 1], [1, 5, 1])
    np.testing.assert_allclose(model.inputs[:, 3], [1, .2, 1])


def test_missing_static_values_do_not_appear_before_measurement():
    obs = [o for o in base_observations() if o['metric'] != 'baseline_cr_admission']
    obs.append(observation('later', 'baseline_cr_admission', 5, 10))
    model = FeatureProbe()
    scoring.score_hours(model, obs, ADMIT, [2, 10])
    assert np.isnan(model.inputs[0, 1])
    assert model.inputs[1, 1] == 5


def test_unchanged_static_inputs_keep_one_vectorized_engine_call(monkeypatch):
    engine = scoring.compute_features
    calls = []

    def record(*args, **kwargs):
        calls.append(len(args[4]))
        return engine(*args, **kwargs)

    monkeypatch.setattr(scoring, 'compute_features', record)
    model = FeatureProbe()
    hours, _ = scoring.score_hours(model, base_observations(), ADMIT, range(1, 73))
    assert len(hours) == 72
    assert calls == [72]
    expected = engine(*scoring.build_inputs(base_observations(), ADMIT, [72], CFG)[:2],
                      np.zeros(72), np.zeros(72, dtype=np.int64), np.arange(1, 73), CFG, FEATURES)
    np.testing.assert_allclose(model.inputs, expected, equal_nan=True)


def test_live_scoring_preserves_subsecond_origin_and_imports_exact_fingerprint(service):
    client, clock, request = service
    clock[0] = ADMIT + timedelta(hours=2, microseconds=700000)
    request('', '/api/import', {'kind': 'input', 'observations': [
        observation('later', 'baseline_cr_admission', 2, (clock[0] - ADMIT).total_seconds() / 3600)]})
    clock[0] = ADMIT + timedelta(hours=2, microseconds=900000)
    worker = model_worker.Worker('', FeatureProbe())
    worker.sync()
    predictions = request('', '/api/patients/time_probe/history?kind=prediction')['items']
    latest = next(p for p in predictions if p['input_revision'] == 3)
    assert scoring.parse(latest['origin_time']).microsecond == 700000
    assert scoring.parse(latest['generated_at']).microsecond == 900000
    assert latest['risk'] == pytest.approx(.2)
    assert request('', '/api/patients/time_probe/current-predictions')['predictions']


def test_equal_time_revisions_keep_distinct_prediction_ids(service):
    client, clock, request = service
    clock[0] = ADMIT + timedelta(hours=2)
    for i in range(2):
        request('', '/api/import', {'kind': 'input', 'observations': [
            observation(f'extra_{i}', 'spo2', 98 - i, 2)]})
    worker = model_worker.Worker('', FeatureProbe())
    worker.scored['time_probe'] = 2
    worker.sync()
    predictions = request('', '/api/patients/time_probe/history?kind=prediction')['items']
    assert {p['input_revision'] for p in predictions} == {3, 4}
    assert len({p['record_id'] for p in predictions}) == 2
    assert worker.scored['time_probe'] == 4


def test_deferred_input_is_retried_without_another_global_change(service, monkeypatch):
    client, clock, request = service
    clock[0] = ADMIT + timedelta(hours=2, microseconds=700000)
    request('', '/api/import', {'kind': 'input', 'observations': [observation('extra', 'spo2', 98, 2)]})
    clock[0] = ADMIT + timedelta(hours=2, microseconds=900000)
    reads, ticks = [], []

    def delayed_snapshot(base, path, payload=None, **kwargs):
        result = request(base, path, payload, **kwargs)
        if path == '/api/health':
            reads.append(result['revision'])
            if len(reads) == 2:  # An earlier snapshot cannot yet include the new input revision.
                result['server_time'] = stamp(ADMIT + timedelta(hours=2, microseconds=600000))
        return result

    class EndProbe(BaseException):
        pass

    def tick(seconds):
        ticks.append(seconds)
        if len(ticks) == 3:
            raise EndProbe()

    monkeypatch.setattr(model_worker, 'call', delayed_snapshot)
    monkeypatch.setattr(model_worker.time, 'sleep', tick)
    worker = model_worker.Worker('', FeatureProbe())
    worker.scored['time_probe'] = 2
    monkeypatch.setattr(worker, 'beat', lambda **kwargs: None)
    with pytest.raises(EndProbe):
        worker.run()
    assert worker.scored['time_probe'] == 3
    assert request('', '/api/patients/time_probe/history?kind=prediction')['items']


@pytest.mark.parametrize('live', [True, False], ids=['live', 'batch'])
def test_scoring_uses_the_captured_knowledge_boundary(service, monkeypatch, live):
    client, clock, request = service
    generated_at = ADMIT + timedelta(hours=2, microseconds=900000)
    clock[0] = generated_at
    worker = model_worker.Worker('', FeatureProbe())
    patient = request('', '/api/patients')[0]
    changed = []

    def concurrent_correction(base, path, payload=None, **kwargs):
        if not changed and ('/history?' in path or '/export-plan/' in path):
            changed.append(True)
            clock[0] = ADMIT + timedelta(hours=3)
            request('', '/api/import', {'kind': 'input', 'observations': [
                observation('baseline', 'baseline_cr_admission', 9, 0, revision=2, available=3)]})
        return request(base, path, payload, **kwargs)

    monkeypatch.setattr(model_worker, 'call', concurrent_correction)
    monkeypatch.setattr(batch, 'api', concurrent_correction)
    if live:
        worker.sync()
        predictions = request('', '/api/patients/time_probe/history?kind=prediction')['items']
    else:
        predictions = batch.score_patient('', FeatureProbe(), patient, generated_at)
        request('', '/api/import', {'kind': 'prediction', 'predictions': predictions})
    assert changed and predictions
    assert all(p['risk'] == pytest.approx(.1) for p in predictions)
    assert all(scoring.parse(p['generated_at']) == generated_at for p in predictions)


def test_confirmed_threshold_carries_the_other_horizon_and_validates():
    from dashboard.api.schemas import Threshold
    threshold = {"value": 0.21, "comparison": ">=", "validation_run_id": "synthetic", "policy_version": "synthetic",
                 "monitoring_window": "ICU hours 1-72", "calibration_version": "platt", "locked": True,
                 "selection_basis": "synthetic"}
    policy = {"threshold": threshold, "alert_policy": {"confirm_horizon_h": 12, "confirm_ratio": 0.5}}
    scored = {12: (np.array([0.08, 0.2]), None), 24: (np.array([0.3, 0.3]), None)}
    first, second = (scoring.threshold_for(policy, scored, k, hour=10) for k in (0, 1))
    assert first["confirmation"] == {"horizon_h": 12, "value": 0.105, "risk": 0.08}
    assert second["confirmation"]["risk"] == 0.2 and "confirmation" not in threshold   # policy is not mutated
    Threshold.model_validate(first)
    early = scoring.threshold_for({"threshold": threshold, "alert_policy": {"early_hours": 6, "early_delta": 0.05}},
                                  scored, 0, hour=3)
    assert early["value"] == 0.26 and "confirmation" not in early
