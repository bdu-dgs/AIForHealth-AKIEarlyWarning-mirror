"""Extension regressions use only small fictitious tables and deterministic synthetic fixtures."""
import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from aki_ml.evaluation import regression_metrics, select_validation_threshold
from aki_ml.experiments import classification_experiment, save_experiment, trajectory_experiment
from aki_ml.research_data import data_config, development_data, feature_matrix, validate_data
from aki_ml.synthetic import make_research_data
from aki_ml.targets import combined_targets
from aki_ml.trajectory import trajectory_targets
from aki_ml.baselines import BaselineModel


@pytest.fixture(scope="module")
def synthetic():
    return make_research_data()


def endpoint_fixture(origins=(3, 4, 5), cr=10, uo=4, censor=30, uo_ok=True):
    s = pd.DataFrame([dict(patient_id="p", stay_id="s", split="validation", hours_since_icu=k,
                          eligible_6h=True, aki_within_6h=int(k < cr <= k+6),
                          eligible_uo_6h=uo_ok, aki_uo_within_6h=int(k < uo <= k+6) if uo_ok else np.nan)
                      for k in origins])
    c = pd.DataFrame([dict(patient_id="p", stay_id="s", cr_aki_onset_hours=cr,
                          uo_aki_onset_hours=uo, censor_hours=censor)])
    return s, c


def test_combined_recomputes_risk_set_at_earlier_urine_onset():
    s, c = endpoint_fixture()
    result = combined_targets(s, c, [6])
    assert result["combined_at_risk"].tolist() == [True, False, False]
    assert result["aki_combined_within_6h"].iloc[0] == 1
    assert result["aki_combined_within_6h"].iloc[1:].isna().all()
    assert "combined_at_risk" not in s  # Existing dataset remains unchanged.


def test_combined_unknown_is_not_negative_but_known_positive_establishes_union():
    s, c = endpoint_fixture(origins=(3,), cr=np.nan, uo=np.nan, uo_ok=False)
    assert pd.isna(combined_targets(s, c, [6])["aki_combined_within_6h"].iloc[0])
    c["cr_aki_onset_hours"] = 7
    s["aki_within_6h"] = 1
    assert combined_targets(s, c, [6])["aki_combined_within_6h"].iloc[0] == 1


def test_censor_boundary_and_identity_mismatch():
    s, c = endpoint_fixture(origins=(3,), cr=8, uo=np.nan, censor=8, uo_ok=False)
    s["eligible_6h"], s["aki_within_6h"] = False, np.nan
    result = combined_targets(s, c, [6])
    assert pd.isna(result["aki_combined_within_6h"].iloc[0])
    c["patient_id"] = "different"
    with pytest.raises(ValueError, match="mapping"):
        combined_targets(s, c, [6])


def test_trajectory_uses_observed_time_no_interpolation_and_censors():
    s, c = endpoint_fixture(origins=(3,), cr=np.nan, uo=np.nan, censor=12)
    e = pd.DataFrame(dict(stay_id=["s"]*3, concept=["creatinine"]*3,
                          hours_since_icu=[7., 9., 12.], value=[1., 3., 99.]))
    t = trajectory_targets(s, c, e, [5, 7, 9], 1, 30)
    assert t["target_value"].iloc[0] == 1  # Equal-distance tie uses earlier actual draw, never average.
    assert t["target_measured_hour"].iloc[0] == 7
    assert t["target_value"].iloc[1] == 3
    assert t["reason"].iloc[2] == "censored"
    t = trajectory_targets(s, c, e, [5], 0, 30)
    assert t["reason"].iloc[0] == "no_measurement"
    assert pd.isna(t["target_value"].iloc[0])
    t = trajectory_targets(s, c, e, [5], 1, 8)
    assert t["reason"].iloc[0] == "export_window_truncated"


def test_trajectory_does_not_use_past_draw_as_future_truth():
    s, c = endpoint_fixture(origins=(3,), cr=np.nan, uo=np.nan)
    e = pd.DataFrame(dict(stay_id=["s"], concept=["creatinine"], hours_since_icu=[3.], value=[1.]))
    t = trajectory_targets(s, c, e, [1], 3, 30)
    assert not t["eligible"].any()


def test_split_integrity_and_no_future_features(synthetic):
    data = development_data(synthetic)
    assert "test" not in set(data.snapshots["split"])
    query = data.snapshots.iloc[:2].copy()
    xx, _ = feature_matrix(data, query, data_config())
    changed = copy.deepcopy(data)
    future = changed.events["hours_since_icu"] > query["hours_since_icu"].max()
    changed.events.loc[future, "value"] = 99999
    actual, _ = feature_matrix(changed, query, data_config())
    np.testing.assert_allclose(actual, xx, equal_nan=True)
    shifted, _ = feature_matrix(data, query, data_config(), lag_hours=1)
    np.testing.assert_array_equal(shifted[:, 0], xx[:, 0])  # Fixed origin, not a different forecast window.
    data.snapshots.loc[0, "split"] = "test"
    with pytest.raises(ValueError, match="manifest"):
        validate_data(data)


def test_validation_threshold_links_stability_and_never_reads_test():
    args = ([0, 0, 1, 1], [.1, .3, .7, .9], {1: [.1, .6, .7, .9]})
    result = select_validation_threshold(*args, ["validation"]*4, [.2, .5, .8],
                                          min_sensitivity=.5, max_flip_fraction=0)
    assert result["selected"]["threshold"] != .5  # This threshold flips one warning under the cutoff.
    assert result["assessment_id"].startswith("cutoff-")
    assert result["test_used"] is False
    with pytest.raises(ValueError, match="validation"):
        select_validation_threshold(*args, ["test"]*4, [.5])
    rejected = select_validation_threshold(*args, ["validation"]*4, [.5], max_flip_fraction=0)
    assert rejected["selected"] is None and rejected["status"] == "no_candidate_met_constraints"


def test_training_ignores_validation_and_test_outcomes(synthetic):
    first = classification_experiment(synthetic)
    changed = copy.deepcopy(synthetic)
    # Modifying held-out outcomes cannot alter model parameters or training OOF calibrators.
    held = changed.cohort["split"] != "train"
    changed.cohort.loc[held, "cr_aki_onset_hours"] += .25
    changed.cohort.loc[held, "uo_aki_onset_hours"] += .25
    # Keep validation both classes for threshold selection while changing test data arbitrarily.
    test_ids = changed.splits.loc[changed.splits["split"] == "test", "patient_id"]
    changed.snapshots.loc[changed.snapshots["patient_id"].isin(test_ids), "aki_within_24h"] = 99
    second = classification_experiment(changed)
    for h in first.model.horizons:
        np.testing.assert_array_equal(first.model.heads[h].coef_, second.model.heads[h].coef_)
        assert first.model.calibrators[h]["platt"].slope_ == second.model.calibrators[h]["platt"].slope_
    assert not first.report["training"]["test_used"]


def test_serialized_baseline_and_trajectory_outputs(synthetic, tmp_path):
    result = classification_experiment(synthetic)
    directory = save_experiment(result, tmp_path)
    loaded = BaselineModel.from_policy(directory / "policy.json")
    model = BaselineModel(result, trajectory_experiment(synthetic))
    X = np.array([[5, 1.2, .1, 80]], dtype=float)
    a, b = loaded.predict(X), model.predict(X)
    np.testing.assert_allclose(a[24][0], b[24][0])
    assert len(b.trajectory["values"]) == 3
    assert model.trajectory_version
    assert model.policy["intended_use"] == "synthetic_demo"
    assert result.policy["threshold"]["stability_assessment_id"]
    (directory / "pipeline.joblib").write_bytes(b"not a model")
    with pytest.raises(ValueError, match="hash"):
        BaselineModel.from_policy(directory / "policy.json")


def test_interval_metrics_use_only_observed_truth():
    report = regression_metrics([1, np.nan, 3], [1, 500, 4], [0, 0, 3], [2, 1000, 5])
    assert report["evaluated_points"] == 2
    assert report["mae"] == .5 and report["interval_coverage"] == 1


def test_independent_trajectory_artifact_changes_policy_and_preserves_risk(synthetic, tmp_path):
    from aki_ml.baselines import WithTrajectory
    first = classification_experiment(synthetic)
    trajectory = trajectory_experiment(synthetic)
    directory = save_experiment(trajectory, tmp_path)
    plain = BaselineModel(first)
    attached = WithTrajectory(plain, directory)
    assert attached.policy["policy_version"] != plain.policy["policy_version"]
    assert attached.policy["threshold"]["value"] == plain.policy["threshold"]["value"]
    X = np.array([[4., 1., 0., 80.]])
    np.testing.assert_allclose(attached.predict(X)[24][0], plain.predict(X)[24][0])
    assert attached.predict(X).trajectory["model_version"] == trajectory.metadata["run_id"]


def test_target_export_is_chunked_excludes_test_and_extends_past_monitoring(tmp_path):
    import json
    from aki_ml.research_data import sha256
    from aki_ml.target_export import export_trajectory_source
    root = tmp_path / "dataset"
    (root / "datasets").mkdir(parents=True)
    c = pd.DataFrame(dict(patient_id=["a", "b"], stay_id=["s1", "s2"], subject_id=[10, 20],
        intime=pd.to_datetime(["2020-01-01", "2020-01-01"]), censor_hours=[90., 90.], split=["train", "test"]))
    cp = root / "datasets/cohort.parquet"
    c.to_parquet(cp)
    (root / "dataset_manifest.json").write_text(json.dumps({"dataset_version": "synthetic-v1",
        "files": {"datasets/cohort.parquet": {"sha256": sha256(cp)}}}))
    source = tmp_path / "lab-view.parquet"
    pd.DataFrame(dict(subject_id=[10, 10, 10, 20, 99], concept=["creatinine"]*5, itemid=[50912]*5,
        charttime=pd.to_datetime(["2020-01-04 02:00", "2020-01-04 03:00", "2020-01-04 22:00", "2020-01-04 02:00", "2020-01-04 02:00"]),
        value=[1., 2., 3., 4., 5.])).to_parquet(source)
    output = tmp_path / "targets.parquet"
    with pytest.raises(ValueError, match="complete"):
        export_trajectory_source(root, source, output, 96)
    info = export_trajectory_source(root, source, output, 96, complete_lab_view=True, batch_rows=1)
    result = pd.read_parquet(output)
    assert result["hours_since_icu"].tolist() == [74., 75.]
    assert result["stay_id"].tolist() == ["s1", "s1"]
    assert info["sha256"] == sha256(output) and info["export_end_hour"] == 96
    with pytest.raises(FileExistsError):
        export_trajectory_source(root, source, output, 96, complete_lab_view=True)


def test_real_artifact_interface_with_synthetic_files_and_hash_failure(synthetic, tmp_path):
    import json
    from aki_ml.research_data import ROOT, load_data, sha256
    root = tmp_path / "notebook01"
    (root / "datasets").mkdir(parents=True)
    (root / "splits").mkdir()
    snapshots = synthetic.snapshots.copy()
    X, _ = feature_matrix(synthetic, snapshots, data_config())
    from aki_ml.research_data import BASELINE_FEATURES
    for j, name in enumerate(BASELINE_FEATURES):
        snapshots[name] = X[:, j]
    artifacts = {
        "datasets/cohort.parquet": synthetic.cohort,
        "datasets/snapshot_dataset.parquet": snapshots,
        "splits/patient_split.parquet": synthetic.splits,
        "datasets/event_stream.parquet": synthetic.events,
    }
    for rel, frame in artifacts.items():
        frame.to_parquet(root / rel)
    manifest = {"dataset_version": "synthetic-contract-v1", "config": data_config(),
                "config_sha256": sha256(ROOT / "configs/default.yaml"),
                "files": {rel: {"sha256": sha256(root / rel)} for rel in artifacts}}
    (root / "dataset_manifest.json").write_text(json.dumps(manifest))
    loaded = load_data(root)
    assert set(loaded.snapshots["split"]) == {"train", "validation"}
    assert not set(loaded.events["stay_id"]) & set(synthetic.cohort.loc[synthetic.cohort["split"] == "test", "stay_id"])
    assert loaded.target_export_end_hour == 72
    # The default source cannot silently claim complete truth past its declared endpoint.
    result = trajectory_experiment(loaded)
    assert result.report["test_evaluation"] == "not_run"
    (root / "datasets/event_stream.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_data(root)
