"""Website/worker integration with isolated synthetic inputs and temporary storage."""
import copy
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dashboard import model_worker
from dashboard.api.main import create_app
from dashboard.api.schemas import PredictionBatch, Trajectory, stamp
from dashboard.launcher import demo_batch
from dashboard.tests.test_scoring_time import ADMIT, FeatureProbe, service  # noqa: F401
from dashboard.tests.test_workbench import store, observation, prediction  # noqa: F401
from aki_ml.baselines import synthetic_demo_model


def test_worker_restarts_separately_for_model_and_policy_versions(service):
    client, clock, request = service
    clock[0] = ADMIT + timedelta(hours=2)
    first = model_worker.Worker("", FeatureProbe())
    first.sync()
    before = request("", "/api/patients/time_probe/history?kind=prediction")["items"]
    restart = model_worker.Worker("", FeatureProbe())
    assert restart.handled_revision("time_probe") == 2
    model = FeatureProbe()
    model.version += "-v2"
    second = model_worker.Worker("", model)
    assert second.handled_revision("time_probe") == 0
    second.sync()
    after = request("", "/api/patients/time_probe/history?kind=prediction")["items"]
    assert len(after) == len(before) * 2
    assert len({p["record_id"] for p in after}) == len(after)
    model.policy["policy_version"] += "-new-policy"
    assert model_worker.Worker("", model).handled_revision("time_probe") == 0


def test_synthetic_worker_requires_demo_service(service):
    _, _, _ = service
    worker = model_worker.Worker("", synthetic_demo_model())
    with pytest.raises(ValueError, match="isolated demo"):
        worker.sync()


def test_demo_risk_trajectory_refresh_and_restart(tmp_path, monkeypatch):
    model = synthetic_demo_model()
    with TestClient(create_app(tmp_path / "demo", watch=False, demo_batch=demo_batch(None), demo_start=ADMIT)) as client:
        def request(base, path, payload=None, **kwargs):
            r = client.get(path) if payload is None else client.post(path, json=payload)
            assert r.status_code == 200, r.text
            return r.json()
        monkeypatch.setattr(model_worker, "call", request)
        request("", "/api/demo/advance?hours=3", {})
        worker = model_worker.Worker("", model)
        assert worker.sync()
        worker.beat(force=True)
        patients = request("", "/api/patients")
        assert len(patients) == 4
        for patient in patients:
            assert patient["prediction"]["intended_use"] == "synthetic_demo"
            records = request("", f"/api/patients/{patient['patient_id']}/history?kind=prediction")["items"]
            assert {p["target"] for p in records} == {model.target}
            assert any(p["trajectories"] for p in records)
            PredictionBatch(predictions=[{k: v for k, v in p.items() if k != "availability_basis"} for p in records])
        counts = [len(request("", f"/api/patients/{p['patient_id']}/history?kind=prediction")["items"]) for p in patients]
        model_worker.Worker("", model).sync()
        assert counts == [len(request("", f"/api/patients/{p['patient_id']}/history?kind=prediction")["items"]) for p in patients]
        request("", "/api/demo/advance?hours=1", {})
        assert worker.sync()
        assert all(p["prediction"]["input_revision"] > old["prediction"]["input_revision"]
                   for p, old in zip(request("", "/api/patients"), patients))
        assert request("", "/api/health")["model"]["intended_use"] == "synthetic_demo"


def test_old_prediction_file_reimport_preserves_stored_history(store):
    from dashboard.api.schemas import InputBatch
    store.ingest(InputBatch(observations=[observation()]))
    record = prediction(store)
    store.ingest(PredictionBatch(predictions=[record]))
    # Simulate a pre-extension JSON record in an existing database, not a new migration.
    with store.connection() as db:
        row = db.execute("SELECT body FROM records WHERE kind='prediction'").fetchone()
        legacy = json.loads(row[0])
        for name in ["intended_use", "policy_version"]:
            legacy.pop(name, None)
        db.execute("UPDATE records SET body=? WHERE kind='prediction'", (json.dumps(legacy, sort_keys=True, separators=(',', ':')),))
    with store.connection() as db:
        before = db.execute("SELECT body FROM records WHERE kind='prediction'").fetchone()[0]
    assert store.ingest(PredictionBatch(predictions=[record]))["inserted"] == 0
    with store.connection() as db:
        assert db.execute("SELECT body FROM records WHERE kind='prediction'").fetchone()[0] == before


def test_trajectory_provenance_is_paired_and_intervals_require_bounds():
    with pytest.raises(ValidationError, match="together"):
        Trajectory(metric="creatinine", label="creatinine", points=[], model_id="ridge")
    with pytest.raises(ValidationError, match="both bounds"):
        Trajectory(metric="creatinine", label="creatinine", interval_level=.9, interval_method="validated residuals",
                   points=[dict(time=stamp(ADMIT), value=1)])
