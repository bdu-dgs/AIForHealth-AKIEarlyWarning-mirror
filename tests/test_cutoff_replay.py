"""Synthetic tests distinguish a data cutoff from moving the forecast origin."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from aki_eval.replay import ReplayData
from aki_ml.features import static_feature_names


@pytest.fixture
def context():
    config=yaml.safe_load((Path(__file__).resolve().parents[1]/"configs/default.yaml").read_text(encoding="utf-8"))
    rows=[]
    for t,v in [(-2,50),(1,80),(3,90),(4,120)]:
        rows.append((0,t,"heart_rate",v,211))
    for t,v in [(0,1),(2,1.2),(3,1.3),(4,2)]:
        rows.append((0,t,"creatinine",v,50912))
    for t,v in [(1,20),(2,30),(4,100)]:
        rows.append((0,t,"urine_output",v,40055))
    events=pd.DataFrame(rows,columns=["stay_idx","t","concept","value","itemid"])
    static=pd.DataFrame(0.,index=[0],columns=static_feature_names(config));static["baseline_cr_admission"]=1.
    snapshots=pd.DataFrame({"stay_id":["S"]*5,"patient_id":["SYN"]*5,"hours_since_icu":range(1,6),
                            "surgical_service":[0,1,0,1,1]})
    features=["hours_since_icu","creatinine_last","creatinine_hours_since_last",
              "heart_rate_mean_6h","surgical_service","urine_ml_6h"]
    return ReplayData(events,static,snapshots,{"S":0},config,features)


def test_cutoff_keeps_origin_windows_lab_delay_and_earlier_service(context):
    q=context.snapshots.iloc[[-1]]
    X,ok=context.matrix(q,lag_hours=2,clip="all")
    assert ok[0]
    np.testing.assert_allclose(X[0],[5.,1.3,1.,85.,0.,50.],rtol=1e-5)
    # A draw measured at cutoff=3 and usable at hour 4 is known at fixed origin=5.
    assert X[0,1]==pytest.approx(1.3)


def test_future_events_cannot_change_features_and_urine_is_past_only(context):
    q=context.snapshots.iloc[[-1]]
    baseline,_=context.matrix(q)
    future,_=context.matrix(q,events=context.future_injection(q))
    np.testing.assert_allclose(baseline,future,equal_nan=True)
    clipped,_=context.matrix(q,clip="urine")
    np.testing.assert_allclose(baseline,clipped,equal_nan=True)


def test_no_prior_service_or_hr_means_unscorable_not_stable_zero(context):
    q=context.snapshots.iloc[[0]]
    _,ok=context.matrix(q,lag_hours=2,clip="all")
    assert not ok[0]
