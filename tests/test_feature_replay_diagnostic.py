"""Independent checks of the slow diagnostic oracle, without private data/models."""
from pathlib import Path
import importlib.util
import sys
import numpy as np
import pandas as pd
import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('replay_diagnostic',ROOT/'scripts/diagnose_feature_replay.py')
diag=importlib.util.module_from_spec(spec);spec.loader.exec_module(diag)


def test_window_local_statistics_match_hand_calculation():
    # Window (0, 3] has (t,value)=(1,10),(2,20),(3,30).
    index=diag.WindowLocalIndex([0]*5,[-1,0,1,2,3],[1000,99,10,20,30])
    lo,hi=index.bounds([0],[3],window=3)
    np.testing.assert_allclose(index.mean(lo,hi),[20])
    np.testing.assert_allclose(index.std(lo,hi),[10])
    np.testing.assert_allclose(index.slope(lo,hi),[10])
    np.testing.assert_allclose(index.sum(lo,hi),[60])
    np.testing.assert_allclose(index.count(lo,hi),[3])
    lo2,hi2=index.bounds([0],[3],window=3,closed_left=True)
    np.testing.assert_allclose(index.sum(lo2,hi2),[159])
    lo3,hi3=index.bounds([0],[3],include_t=False,since_hour=0)
    np.testing.assert_allclose(index.sum(lo3,hi3),[129])


def test_statistics_are_exactly_invariant_to_future_and_other_patient_rows():
    first=diag.WindowLocalIndex([0,0,0],[1,2,3],[80,80,80])
    other=diag.WindowLocalIndex([0,0,0,0,99999,99999],[1,2,3,4,1,999],[80,80,80,299,1e6,-1e6])
    for method in ['sum','mean','std','slope','min','max']:
        a,b=first.bounds([0],[3],window=3);c,d=other.bounds([0],[3],window=3)
        np.testing.assert_array_equal(getattr(first,method)(a,b),getattr(other,method)(c,d))
    assert first.std(a,b)[0]==0
    assert other.slope(c,d)[0]==0


def test_native_time_bounds_do_not_cross_patient_or_round_timestamp_boundaries():
    index=diag.WindowLocalIndex([100000,100000,100001],[1-1e-8,1+1e-8,0],[2,4,999])
    np.testing.assert_array_equal(index.last([100000],[1])[0],[2])
    lo,hi=index.bounds([100002],[1],window=24)
    assert index.count(lo,hi)[0]==0
    assert np.isnan(index.mean(lo,hi)[0])
    assert np.isnan(index.std(lo,hi)[0])
    assert np.isnan(index.slope(lo,hi)[0])


def test_full_feature_oracle_is_past_only_without_mutating_frozen_engine():
    engine=diag.engine;original=engine.EventIndex
    config=yaml.safe_load((ROOT/'configs/default.yaml').read_text())
    events=pd.DataFrame([(0,1,'heart_rate',80,211),(0,3,'heart_rate',80,211),
                         (0,2,'urine_output',30,40055),(0,0,'creatinine',1,50912)],
                        columns=['stay_idx','t','concept','value','itemid'])
    future=pd.concat([events,pd.DataFrame([(0,4,'heart_rate',299,211),(1,1,'heart_rate',10,211)],columns=events.columns)],ignore_index=True)
    static=pd.DataFrame(0.,index=[0,1],columns=engine.static_feature_names(config));static['baseline_cr_admission']=1
    names=['heart_rate_mean_24h','heart_rate_std_24h','heart_rate_slope_24h','urine_ml_6h','creatinine_last']
    calculate=diag.local_compute()
    a=calculate(events,static,[0],[0],[3],config,names)
    b=calculate(future,static,[0],[0],[3],config,names)
    np.testing.assert_array_equal(a,b)
    np.testing.assert_allclose(a,[[80,0,0,30,1]])
    assert engine.EventIndex is original

def test_tree_path_trace_preserves_double_split_thresholds():
    sys.path.insert(0,str(ROOT/'scripts'))
    from inspect_replay_numeric_causes import first_path_crossings
    low=np.float32(.1);high=np.nextafter(low,np.float32(np.inf))
    threshold=(float(low)+float(high))/2
    class Model:
        def dump_model(self,**kwargs):
            return {'tree_info':[{'tree_structure':{'split_index':0,'split_feature':0,'threshold':threshold,
                'decision_type':'<=','default_left':True,'missing_type':'NaN',
                'left_child':{'leaf_index':0},'right_child':{'leaf_index':1}}}]}
    rows=first_path_crossings(Model(),np.array([[low]],np.float32),np.array([[high]],np.float32),['synthetic'],1)
    assert rows[0]['first_divergent_tree_paths']==1


def test_tree_path_trace_handles_large_threshold_without_float32_overflow():
    sys.path.insert(0,str(ROOT/'scripts'))
    from inspect_replay_numeric_causes import first_path_crossings
    class Model:
        def dump_model(self,**kwargs):
            return {'tree_info':[{'tree_structure':{'split_index':0,'split_feature':0,'threshold':1e300,
                'decision_type':'<=','default_left':True,'missing_type':'NaN',
                'left_child':{'leaf_index':0},'right_child':{'leaf_index':1}}}]}
    with np.errstate(over='raise'):
        assert first_path_crossings(Model(),np.array([[0]],np.float32),np.array([[1]],np.float32),['synthetic'],1)==[]
