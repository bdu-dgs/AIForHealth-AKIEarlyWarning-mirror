import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from aki_eval.stability import recalibration, bootstrap_metrics, segment_of, _transitions


def test_recalibration_matches_reference_with_cluster_weights():
    rng=np.random.default_rng(42)
    x=rng.normal(size=800); p=1/(1+np.exp(-x))
    y=rng.binomial(1,1/(1+np.exp(-(.3+1.2*x))))
    w=rng.integers(0,4,size=len(y))
    ref=LogisticRegression(penalty=None,tol=1e-10,max_iter=1000).fit(x[:,None],y,sample_weight=w)
    intercept,slope=recalibration(y,p,w)
    np.testing.assert_allclose([intercept,slope],[ref.intercept_[0],ref.coef_[0,0]],atol=1e-5)


def test_one_class_and_constant_predictions_are_not_reported_as_calibrated():
    assert np.isnan(recalibration([0,0],[.1,.2])[1])
    assert np.isnan(recalibration([0,1],[.2,.2])[1])


def test_bootstrap_matches_explicit_patient_resampling_with_ties():
    d=pd.DataFrame({'patient_id':['a','a','b','b','c','c'],
                    'label':[0,1,0,1,0,1],'risk_platt':[.1,.5,.2,.5,.5,.9]})
    n=30; seed=19
    r=bootstrap_metrics(d,'risk_platt',n,seed)
    rng=np.random.default_rng(seed); auc=[]
    for _ in range(n):
        chosen=rng.integers(3,size=3)
        sample=pd.concat([d[d.patient_id==['a','b','c'][i]] for i in chosen])
        auc.append(roc_auc_score(sample.label,sample.risk_platt))
    np.testing.assert_allclose([r['auroc_ci_low'],r['auroc_ci_high']],np.quantile(auc,[.025,.975]))


def test_segments_and_transitions_do_not_cross_patients():
    assert list(segment_of(pd.Series([1,3,4,6,7,72,73])))==['1-3h','1-3h','4-6h','4-6h','7-12h','61-72h',None]
    d=pd.DataFrame({'patient_id':['b','a','a','b'],'hours_since_icu':[2,1,2,1],
                    'risk_platt':[.9,.1,.3,.8]})
    r=_transitions(d,'hours_since_icu')
    np.testing.assert_allclose(r.delta.dropna().to_numpy(),[.2,.1])
    assert r.prev_risk.isna().sum()==2


def test_monotonicity_includes_ineligible_rows(tmp_path):
    from aki_eval.stability import monotonicity
    records=[]
    for pid,risks in [('a',[.3,.2,.4,.5]),('b',[.1,.2,.3,.4])]:
        for h,p in zip([6,12,24,48],risks):
            records.append(dict(patient_id=pid,hours_since_icu=1,horizon_h=h,
                                eligible=False,risk_platt=p,risk_isotonic=p))
    result=monotonicity(pd.DataFrame(records),tmp_path)
    r=result.query("calibration == 'platt' and segment == 'all' and horizon_h == 6").iloc[0]
    assert r.rows==2 and r.violation_fraction==.5


def test_flapping_censoring_and_hourly_gaps(tmp_path):
    from aki_eval.stability import risk_volatility
    hourly=pd.DataFrame(dict(patient_id=['a']*5,horizon_h=[24]*5,hours_since_icu=[1,2,3,4,6],
                             eligible=[True]*5,risk_platt=[.1,.3,.1,.3,.1]))
    replay=hourly.copy()
    replay['event_time']=pd.Timestamp('2020-01-01')+pd.to_timedelta(replay.hours_since_icu,unit='h')
    replay['hourly_snapshot_hour']=replay.hours_since_icu
    replay['n_events']=1
    r=risk_volatility(hourly,replay,.21,tmp_path)
    s=r.query("mode == 'hourly' and segment == '1-3h' and scope == 'eligible_updates'").iloc[0]
    assert s.transitions==2 and s.crossings==2
    assert s.reversal_1h_fraction==1
    later=r.query("mode == 'hourly' and segment == '4-6h' and scope == 'eligible_updates'").iloc[0]
    assert later.transitions==1 and later.crossings==1
    assert later.reversal_1h_denominator==0


def test_event_collisions_combine_counts_without_inventing_risk_order():
    from aki_eval.stability import coalesce_event_times
    import pytest
    d=pd.DataFrame(dict(patient_id=['a','a'],event_time=[pd.Timestamp('2020-01-01')]*2,
                        risk_platt=[.2,.2],eligible=[True,True],label=[0,0],
                        hourly_snapshot_hour=[1,1],hours_since_icu=[1.,1.],n_events=[1,3]))
    r,quality=coalesce_event_times(d)
    assert len(r)==1 and r.n_events.iloc[0]==4
    assert quality['rows_coalesced']==1
    d.loc[1,'risk_platt']=.3
    with pytest.raises(AssertionError,match='Conflicting'):
        coalesce_event_times(d)


def test_all_update_scope_includes_ineligible_transitions(tmp_path):
    from aki_eval.stability import risk_volatility
    d=pd.DataFrame(dict(patient_id=['a']*3,horizon_h=[24]*3,hours_since_icu=[1,2,3],
                        eligible=[True,True,False],risk_platt=[.1,.3,.1]))
    replay=d.copy()
    replay['event_time']=pd.Timestamp('2020-01-01')+pd.to_timedelta(replay.hours_since_icu,unit='h')
    replay['hourly_snapshot_hour']=replay.hours_since_icu; replay['n_events']=1
    r=risk_volatility(d,replay,.21,tmp_path)
    a=r.query("mode == 'hourly' and scope == 'all_updates'").iloc[0]
    e=r.query("mode == 'hourly' and scope == 'eligible_updates'").iloc[0]
    assert a.transitions==2 and e.transitions==1
