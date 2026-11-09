"""A sealed choice/confirmation must survive interrupted derived-report generation."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
import aki_eval.study as workflow
from aki_eval.policies import AlertRule, metrics, policy_grid, prepare
from aki_eval.protocol import digest, lock_choice, sha256, write_json


def make_study(tmp_path):
    root=Path(__file__).resolve().parents[1]
    config=yaml.safe_load((root/"tests/fixtures/alert_policy_v2_legacy.yaml").read_text(encoding="utf-8"))
    config["selection_rule"].update(min_ppv=0, min_event_detection_rate=0, max_detection_loss=1,
                                    min_median_lead_h=0, max_median_lead_loss_h=100, max_alerts_per_patient_day=100)
    rows=[]
    for pid,risk,event in [("P",.8,True),("N",.1,False)]:
        for hour in range(1,7):
            rows.append({"patient_id":pid,"hours_since_icu":hour,"risk":risk,
                         "snapshot_time":pd.Timestamp("2020-01-01")+pd.Timedelta(hours=hour),
                         "creatinine_aki_onset_time":pd.Timestamp("2020-01-01")+pd.Timedelta(hours=9) if event else pd.NaT,
                         "eligible":True,"label":int(event)})
    def frame(prefix):
        f=pd.DataFrame(rows);f["patient_id"]=prefix+f["patient_id"]
        return prepare(f,24)
    frames={"selection":frame("SEL-"),"train_oof":frame("TRAIN-")}
    rules=[AlertRule("simple"),AlertRule("episode",repeat_mode="episode")]
    thresholds=[.2,.3];baseline=AlertRule("demo-v1")
    data=SimpleNamespace(load=lambda:frames, rules=rules, thresholds=thresholds,
                         policy={"threshold":{"value":.21}},baseline=baseline)
    study=object.__new__(workflow.KeJinStudy);study.data=data;study.config=config;study.out=tmp_path
    study.checks=lambda:pd.DataFrame({"passed":[True]})
    ranked=policy_grid(frames["selection"],rules,thresholds).assign(accepted=True, worst_warning_flip=0, min_pair_coverage=1)
    ranked.to_csv(tmp_path/"policy_grid_validation_selection.csv",index=False)
    choice=lock_choice(tmp_path,ranked.iloc[0].to_dict(),"a"*64)
    write_json(tmp_path/"confirmation.json",{"choice_sha256":digest(choice),"candidate_confirmed":False,
                                           "status":"not_confirmed_no_retuning"})
    pd.DataFrame({"comparison":["difference"],"estimate":[0.]}).to_csv(tmp_path/"policy_confirmation_validation.csv",index=False)
    write_json(tmp_path/"policy_v1_candidate.json",{"untouched":"synthetic existing export"})
    return study,frames


@pytest.mark.parametrize("interrupted",["missing_grid","partial_grid","missing_summary","partial_summary","partial_picture"])
def test_resume_repairs_reports_without_reselection_or_confirmation(tmp_path,monkeypatch,interrupted):
    study,frames=make_study(tmp_path)
    if interrupted!="missing_grid":
        diagnostic=policy_grid(frames["train_oof"],study.data.rules,study.data.thresholds)
        diagnostic.to_csv(tmp_path/"policy_grid_train_oof.csv",index=False)
    if interrupted=="partial_grid":
        (tmp_path/"policy_grid_train_oof.csv").write_text("candidate,threshold\nsimple,")
    if interrupted=="partial_summary":
        (tmp_path/"policy_candidates_train_oof.csv").write_text("")
    if interrupted=="partial_picture":
        (tmp_path/"policy_tradeoff_train_oof.png").write_bytes(b"\x89PNG\r\n\x1a\npartial")
    immutable={name:sha256(tmp_path/name) for name in ["choice.json","confirmation.json","policy_confirmation_validation.csv","policy_v1_candidate.json","policy_grid_validation_selection.csv"]}
    original=workflow.policy_grid
    def diagnostic_only(frame,*args,**kwargs):
        assert frame["patient_id"].str.startswith("TRAIN-").all()
        return original(frame,*args,**kwargs)
    monkeypatch.setattr(workflow,"policy_grid",diagnostic_only)
    monkeypatch.setattr(workflow,"choose",lambda *a,**k:pytest.fail("Must never reselect after choice seal"))
    study.cutoff_table=lambda *a,**k:pytest.fail("Must not reevaluate confirmation")
    comparison=study.confirm()
    assert comparison["comparison"].iloc[0]=="difference"
    assert len(pd.read_csv(tmp_path/"policy_grid_train_oof.csv"))==4
    assert study._complete_table(tmp_path/"policy_candidates_train_oof.csv",{"candidate","status"},2)
    assert study._complete_png(tmp_path/"policy_tradeoff_train_oof.png")
    assert study._complete_png(tmp_path/"policy_tradeoff_validation_selection.png")
    assert all(sha256(tmp_path/name)==value for name,value in immutable.items())
    monkeypatch.setattr(workflow,"policy_grid",lambda *a,**k:pytest.fail("Complete diagnostics must be reused"))
    study.confirm()
    assert all(sha256(tmp_path/name)==value for name,value in immutable.items())


def test_missing_sealed_selection_grid_refuses_to_retune(tmp_path,monkeypatch):
    study,_=make_study(tmp_path)
    (tmp_path/"policy_grid_validation_selection.csv").unlink()
    old=sha256(tmp_path/"choice.json")
    monkeypatch.setattr(workflow,"choose",lambda *a,**k:pytest.fail("Must not replace sealed choice"))
    with pytest.raises(RuntimeError,match="Sealed selection grid is missing"):
        study.select()
    assert sha256(tmp_path/"choice.json")==old
