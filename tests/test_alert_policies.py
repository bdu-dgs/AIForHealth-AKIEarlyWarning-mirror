"""Synthetic timeline tests: parity, state resets, clock semantics and honest selection."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from aki_eval.policies import (AlertRule,HourlyMetrics,alert_events,bootstrap_comparison,choose,prepare,prior_minimum)
from aki_eval.protocol import freeze_prespec,lock_choice,validation_roles
from aki_ml import alerts as old


def frame(risk,hours=None):
    n=len(risk);hours=list(range(1,n+1)) if hours is None else hours
    start=pd.Timestamp("2020-01-01")
    f=pd.DataFrame({"patient_id":["SYN"]*n,"hours_since_icu":hours,"risk":risk,
                    "snapshot_time":[start+pd.Timedelta(hours=h) for h in hours],
                    "creatinine_aki_onset_time":[start+pd.Timedelta(hours=n+3)]*n,
                    "eligible":[True]*n,"label":[1]*n})
    return prepare(f,24)


@pytest.mark.parametrize("sustain,cooldown",[(1,6),(2,6),(1,12),(3,2)])
def test_switching_new_component_off_reproduces_legacy(sustain,cooldown):
    f=frame(np.random.default_rng(12).uniform(0,1,120))
    rule=AlertRule("baseline",sustain_hours=sustain,cooldown_hours=cooldown)
    np.testing.assert_array_equal(alert_events(f,.3,rule),old.policy_alerts(f,.3,old.Policy(sustain,cooldown)))


def test_episode_repeat_and_absolute_rise():
    f=frame([.4,.4,.4,.46,.46,.1,.4])
    assert np.flatnonzero(alert_events(f,.3,AlertRule("episode",cooldown_hours=2,repeat_mode="episode"))).tolist()==[0,6]
    assert np.flatnonzero(alert_events(f,.3,AlertRule("rise",cooldown_hours=2,repeat_mode="episode_or_rise"))).tolist()==[0,3,6]



def test_multi_horizon_confirmation_needs_short_horizon_agreement():
    f=frame([.4,.4,.4,.4]);f["risk_12h"]=[.1,.25,.14,.3]
    rule=AlertRule("mh",cooldown_hours=0,confirm_horizon_h=12,confirm_ratio=.5)
    assert np.flatnonzero(alert_events(f,.3,rule)).tolist()==[1,3]          # 12 h risk >= .5 x .3
    f["risk_12h"]=np.nan
    assert not alert_events(f,.3,rule).any()                                  # unknown short-horizon risk never confirms


def test_early_threshold_applies_only_inside_early_hours():
    f=frame([.32,.32,.32,.32],[5.,6.,7.,8.])
    rule=AlertRule("early",cooldown_hours=0,early_hours=6,early_delta=.05)
    assert np.flatnonzero(alert_events(f,.3,rule)).tolist()==[2,3]
    f["risk"]=[.36,.2,.2,.2]
    assert np.flatnonzero(alert_events(f,.3,rule)).tolist()==[0]


def test_new_components_switched_off_reproduce_legacy():
    f=frame(np.random.default_rng(3).uniform(0,1,96));f["risk_12h"]=1.0
    rule=AlertRule("off",cooldown_hours=6,confirm_horizon_h=12,confirm_ratio=.5)
    np.testing.assert_array_equal(alert_events(f,.3,rule),old.policy_alerts(f,.3,old.Policy(1,6)))
    assert AlertRule("early",early_hours=None).risk_columns==[] and rule.risk_columns==["risk_12h"]

def test_sustain_uses_elapsed_time_not_number_of_minute_updates():
    f=frame([.4,.4,.4],[1.,1.1,2.])
    result=alert_events(f,.3,AlertRule("sustain",sustain_hours=2,cooldown_hours=0))
    assert np.flatnonzero(result).tolist()==[2]
    gap=frame([.4,.4],[1.,3.])
    assert not alert_events(gap,.3,AlertRule("sustain",sustain_hours=2)).any()


def test_unknown_risk_resets_episode_and_does_not_invent_alerts():
    f=frame([.5,np.nan,.5])
    assert np.flatnonzero(alert_events(f,.3,AlertRule("episode",cooldown_hours=0,repeat_mode="episode"))).tolist()==[0,2]


def test_rise_window_is_clock_based_and_excludes_current():
    result=prior_minimum(np.array(["S"]*3),np.array([1.,2.,20.]),np.array([.1,.5,.8]),12)
    assert np.isnan(result[0]) and result[1]==.1 and np.isnan(result[2])


def test_event_engine_does_not_consult_future_outcomes():
    f=frame([.2,.6,.7,.1,.8])
    first=alert_events(f,.3,AlertRule("episode",repeat_mode="episode"))
    f["label"]=0;f["eligible"]=False;f["creatinine_aki_onset_time"]=pd.NaT
    np.testing.assert_array_equal(first,alert_events(f,.3,AlertRule("episode",repeat_mode="episode")))


def test_fast_metrics_match_original_metric_definitions():
    a=frame([.2,.6,.8,.1,.7]*5)
    b=a.copy();b["patient_id"]="NEG";b["creatinine_aki_onset_time"]=pd.NaT;b["label"]=0
    f=prepare(pd.concat([a,b]),24)
    flags=alert_events(f,.3,AlertRule("rule",cooldown_hours=4))
    expected=old.summarize(old.per_patient(f,flags));actual=HourlyMetrics(f).summarize(flags)
    assert expected.keys()==actual.keys()
    for key in expected:assert np.isclose(actual[key],expected[key],equal_nan=True),key


def test_patient_bootstrap_pairs_same_individuals():
    f=frame([.4,.4,.4,.4])
    pp=old.per_patient(f,alert_events(f,.3,AlertRule("r")))
    result=bootstrap_comparison(pp,pp,n=20)
    delta=result[result["comparison"]=="difference"]
    assert (delta["estimate"].dropna()==0).all()
    assert (delta["ci_low"].dropna()==0).all()


def test_validation_partition_deterministic_disjoint_and_outcome_independent():
    ids=pd.Series([f"SYN-{i}" for i in range(100)])
    a=validation_roles(ids,7,.7);b=validation_roles(ids.iloc[::-1],7,.7)
    assert a.equals(b.reindex(a.index))
    assert set(a)=={"selection","confirmation"}


def test_prespec_and_choice_cannot_be_replaced(tmp_path):
    source=tmp_path/"input";source.write_text("synthetic")
    out=tmp_path/"run"
    spec=freeze_prespec(out,{"thresholds":[.1,.2]},{"input":source},{})
    assert freeze_prespec(out,{"thresholds":[.1,.2]},{"input":source},{})==spec
    with pytest.raises(RuntimeError,match="changed"):
        freeze_prespec(out,{"thresholds":[.9]},{"input":source},{})
    lock_choice(out,{"threshold":.2},spec["prespec_sha256"])
    with pytest.raises(RuntimeError,match="replace"):
        lock_choice(out,{"threshold":.3},spec["prespec_sha256"])


def test_selection_rejects_less_burdensome_but_unstable_candidate():
    import yaml
    from aki_eval.policies import choose
    criteria=yaml.safe_load((Path(__file__).resolve().parents[1]/"configs/alert_policy_v1.yaml").read_text(encoding="utf-8"))["selection_rule"]
    common={"event_detection_rate":.75,"lead_time_median_h":12.,"alert_ppv":.4,"alerts_per_patient_day":.7,
            "non_aki_patients_alerted":.3,"repeat_mode":"cooldown","sustain_hours":1,"rise_ratio":np.nan}
    grid=pd.DataFrame([{"candidate":"unstable","threshold":.2,"false_alerts_per_patient_day":.1,**common},
                       {"candidate":"stable","threshold":.3,"false_alerts_per_patient_day":.2,**common}])
    stability=pd.DataFrame([{"candidate":"unstable","threshold":.2,"warning_flip_fraction":.01,
                             "pair_coverage":1.,"patient_alert_flip_fraction":.2},
                            {"candidate":"stable","threshold":.3,"warning_flip_fraction":.01,
                             "pair_coverage":1.,"patient_alert_flip_fraction":.02}])
    baseline={"event_detection_rate":.76,"lead_time_median_h":12.5,"non_aki_patients_alerted":.37}
    selected,table=choose(grid,stability,baseline,criteria)
    assert selected["candidate"]=="stable" and table["accepted"].tolist()==[False,True]
    stability.loc[1,"pair_coverage"]=.5
    assert choose(grid,stability,baseline,criteria)[0] is None


def test_exported_research_candidate_cannot_be_silently_deployed(tmp_path,monkeypatch):
    import hashlib,json
    from types import SimpleNamespace
    from aki_eval.study import KeJinStudy
    from aki_ml.scoring import LockedModel
    model_dir=tmp_path/"model";model_dir.mkdir()
    artifact=model_dir/"pipeline.joblib";artifact.write_bytes(b"not executable")
    original={"policy_version":"demo-v1","threshold":{"locked":True,"value":.21},
              "model":{"model_dir":str(model_dir),"pipeline_sha256":hashlib.sha256(artifact.read_bytes()).hexdigest()},
              "alert_policy":{},"validation_metrics":{"old_study":123}}
    study=object.__new__(KeJinStudy);study.out=tmp_path
    study.config={"version":"synthetic-research","alert_horizon_h":24,"horizons":[6,12,24,48],"monitoring_hours":[1,72]}
    study.data=SimpleNamespace(policy=original,prespec={"prespec_sha256":"a"*64})
    study._export_candidate(None,{"status":"no_feasible_candidate"})
    path=tmp_path/"policy_v1_candidate.json"
    exported=json.loads(path.read_text(encoding="utf-8"))
    assert "validation_metrics" not in exported and not exported["threshold"]["locked"]
    monkeypatch.setattr("aki_ml.scoring.joblib.load",lambda *_:pytest.fail("Unlocked candidate must not load weights"))
    with pytest.raises(RuntimeError,match="not locked"):
        LockedModel(path)


def test_full_synthetic_selection_confirmation_and_cached_rerun(tmp_path):
    import hashlib,json,yaml
    from types import SimpleNamespace
    from aki_eval.study import KeJinStudy
    from aki_eval.policies import AlertRule,prepare
    from aki_eval.protocol import sha256
    pos=frame([.8]*6)
    neg=pos.copy();neg["patient_id"]="NEG";neg["creatinine_aki_onset_time"]=pd.NaT;neg["label"]=0;neg["risk"]=.25
    base=prepare(pd.concat([pos,neg]),24)
    def role(prefix):
        f=base.copy();f["patient_id"]=prefix+f["patient_id"].astype(str)
        return prepare(f,24)
    frames={"selection":role("SEL-"),"confirmation":role("CONF-"),"train_oof":role("TRAIN-")}
    modeldir=tmp_path/"model";modeldir.mkdir()
    artifact=modeldir/"pipeline.joblib";artifact.write_bytes(b"non-executable synthetic placeholder")
    original={"policy_version":"demo-v1","model":{"model_dir":str(modeldir),"pipeline_sha256":sha256(artifact)},
              "threshold":{"locked":True,"value":.21},"alert_policy":{}}
    policyfile=tmp_path/"original.json";policyfile.write_text(json.dumps(original))
    cfg=yaml.safe_load((Path(__file__).resolve().parents[1]/"configs/alert_policy_v1.yaml").read_text(encoding="utf-8"))
    cfg["bootstrap_resamples"]=20
    cfg["selection_rule"].update(max_alerts_per_patient_day=10,min_ppv=.1,min_median_lead_h=0)
    rules=[AlertRule("simple"),AlertRule("episode",repeat_mode="episode")]
    out=tmp_path/"run";out.mkdir()
    data=SimpleNamespace(policy=original,rules=rules,thresholds=[.2,.3],prespec={"prespec_sha256":"a"*64},
                         baseline=AlertRule("demo-v1"),load=lambda:frames,metadata=lambda:{"synthetic":True},
                         paths={"model":artifact,"policy":policyfile},
                         inputs_hash={"model":sha256(artifact),"policy":sha256(policyfile)},source_hash={})
    study=object.__new__(KeJinStudy);study.data=data;study.out=out;study.config=cfg;study._cutoffs={};study.pipe=None
    study.checks=lambda:pd.DataFrame({"passed":[True]})
    study._plot_grid=lambda *args:None
    calls=[]
    def inputs(which):
        calls.append(which)
        f=frames[which]
        return f,{lag:f["risk"].to_numpy().copy() for lag in [0,1,2]}
    study.cutoff_inputs=inputs
    selected=study.select()
    assert selected["threshold"].iloc[0]==.3
    comparison=study.confirm()
    assert "difference" in set(comparison["comparison"])
    before=len(calls)
    pd.testing.assert_frame_equal(study.confirm(),comparison)
    assert len(calls)==before  # completed confirmation reused, no new evaluation or tuning
    text=study.summary()
    assert len(text.splitlines())==10
    assert json.loads((out/"run_manifest.json").read_text(encoding="utf-8"))["trained_model_unchanged"]
    candidate=json.loads((out/"policy_v1_candidate.json").read_text(encoding="utf-8"))
    assert candidate["threshold"]["locked"] is False
    assert "validation_metrics" not in candidate
