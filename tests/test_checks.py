"""Every artifact check gets a passing and deliberately broken synthetic case."""
import hashlib
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from aki_eval import checks as c


@pytest.fixture
def tables():
    start=pd.Timestamp("2020-01-01")
    snapshots=pd.DataFrame({
        "patient_id":["SYN-A","SYN-B"],"stay_id":["SA","SB"],"icustay_id":[1,2],"split":["validation"]*2,
        "icu_intime":[start]*2,"snapshot_time":[start+pd.Timedelta(hours=1)]*2,
        "censoring_time":[start+pd.Timedelta(hours=21)]*2,"hours_since_icu":[1.,1.],
        "followup_hours":[20.,20.],"creatinine_aki_onset_time":[start+pd.Timedelta(hours=4),pd.NaT],
        "eligible_6h":[True,True],"aki_within_6h":[1.,0.]})
    pred=snapshots[["patient_id","hours_since_icu","split"]].copy()
    pred["horizon_h"]=6;pred["eligible"]=True;pred["label"]=[1.,0.]
    pred["risk_raw"]=[.8,.2];pred["risk_platt"]=[.7,.3];pred["risk_isotonic"]=[.9,.1]
    manifest=snapshots[["patient_id","stay_id","icustay_id","split"]].copy()
    return snapshots,pred,manifest


@pytest.mark.parametrize("broken",[False,True])
def test_a1_unique_keys(tables,broken):
    s,_,_=tables
    if broken:s=pd.concat([s,s.iloc[:1]])
    assert c.unique_keys(s,["patient_id","hours_since_icu"])[0] is not broken


@pytest.mark.parametrize("broken",[False,True])
def test_a2_probabilities(tables,broken):
    _,p,_=tables
    if broken:p.loc[0,"risk_raw"]=np.inf
    assert c.probability_bounds(p)[0] is not broken


@pytest.mark.parametrize("broken",[False,True])
def test_a3_schema_versions_and_model_hash(tmp_path,broken):
    model=tmp_path/"model";model.write_bytes(b"synthetic non-executable artifact")
    dm={"dataset_version":"v1","config_sha256":"same","columns":{"features":["f"]}}
    run={"run_id":"r1","dataset":{"version":"v1","config_sha256":"same"}}
    policy={"model":{"run_id":"r1","pipeline_sha256":hashlib.sha256(model.read_bytes()).hexdigest()}}
    schema=pa.schema([("f",pa.int64() if broken else pa.float32())])
    dictionary=pd.DataFrame({"feature":["f"],"dtype":["float32"]})
    assert c.schema_and_versions(schema,dictionary,dm,run,policy,model,versions=["v1"],prediction_versions=["r1"])[0] is not broken


@pytest.mark.parametrize("broken",[False,True])
def test_a4_missing_predictions(tables,broken):
    s,p,_=tables
    if broken:p=p.iloc[1:]
    assert c.missing_predictions(s,p,[6])[0] is not broken


@pytest.mark.parametrize("broken",[False,True])
def test_a5_split_assignment(tables,broken):
    s,p,m=tables
    if broken:p.loc[0,"split"]="test"
    assert c.split_disjointness(m,s,p)[0] is not broken


@pytest.mark.parametrize("broken",[False,True])
def test_a6_selected_stay_mapping(tables,broken):
    s,_,m=tables
    if broken:s.loc[0,"icustay_id"]=99
    assert c.one_stay_per_patient(m,s)[0] is not broken


@pytest.mark.parametrize("broken",[False,True])
def test_a7_chronology(tables,broken):
    s,_,_=tables
    if broken:s.loc[0,"censoring_time"]=s.loc[0,"snapshot_time"]
    assert c.chronology(s)[0] is not broken


@pytest.mark.parametrize("broken",[False,True])
def test_a8_label_definition(tables,broken):
    s,p,_=tables
    if broken:p.loc[0,"label"]=0
    assert c.label_definition(s,p,[6])[0] is not broken


def test_label_end_inclusive_and_ineligible_not_negative(tables):
    s,p,_=tables
    s.loc[0,"creatinine_aki_onset_time"]=s.loc[0,"snapshot_time"]+pd.Timedelta(hours=6)
    s.loc[1,"followup_hours"]=2
    s.loc[1,"eligible_6h"]=False;s.loc[1,"aki_within_6h"]=np.nan
    p.loc[1,"eligible"]=False;p.loc[1,"label"]=np.nan
    assert c.label_definition(s,p,[6])[0]
    p.loc[1,"label"]=0
    assert not c.label_definition(s,p,[6])[0]


@pytest.mark.parametrize("broken",[False,True])
def test_a9_pre_onset(tables,broken):
    s,_,_=tables
    if broken:s.loc[0,"creatinine_aki_onset_time"]=s.loc[0,"snapshot_time"]
    assert c.pre_onset_snapshots(s)[0] is not broken


@pytest.mark.parametrize("broken",[False,True])
def test_a10_future_guard(broken):
    stored=np.array([[1.,np.nan],[2.,3.]])
    future=stored.copy()
    if broken:future[0,0]=99
    assert c.features_use_past(stored,stored,future,min_rows=2)[0] is not broken


@pytest.mark.parametrize("broken",[False,True])
def test_a11_urine_guard(broken):
    a=np.array([[1.,2.],[3.,4.]])
    b=a.copy()
    if broken:b[1,0]=5
    assert c.no_post_snapshot_urine(a,b,["urine_ml_6h","heart_rate_last"])[0] is not broken


@pytest.mark.parametrize("broken",[False,True])
def test_a12_provenance_declaration(broken):
    run={"test_split":"never loaded in notebook 02","calibration":{"fit_on":"validation" if broken else "train_oof"},
         "checks":{"part3_loaded_data":{"no test rows loaded":True},
                   "part4_folds_and_features":{"validation rows never enter CV folds":True}}}
    result=c.no_test_fitting(run)
    assert result[0] is not broken
    assert "manifest_declaration_only" in result[1]
