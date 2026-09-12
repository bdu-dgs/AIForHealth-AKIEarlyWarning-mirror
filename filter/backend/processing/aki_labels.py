import numpy as np
import pandas as pd

def detect(events,strategy):
    """Retrospective SCr criterion times. Strictly prior values; no simultaneous-value comparison."""
    if strategy not in ('prior_7d_min','prior_7d_first'):
        raise ValueError('Choose and confirm a baseline strategy.')
    e=events.sort_values('CHARTTIME'); found=[]
    for r in e.itertuples():
        prior=e.loc[(e.CHARTTIME<r.CHARTTIME)&(e.CHARTTIME>=r.CHARTTIME-pd.Timedelta(days=7))]
        p48=prior.loc[prior.CHARTTIME>=r.CHARTTIME-pd.Timedelta(hours=48)]
        absolute=len(p48)>0 and r.VALUENUM-float(p48.VALUENUM.min())>=.3-1e-9
        baseline=(prior.VALUENUM.min() if strategy=='prior_7d_min' else prior.VALUENUM.iloc[0]) if len(prior) else np.nan
        if absolute or (pd.notna(baseline) and r.VALUENUM>=1.5*baseline-1e-9): found.append(r.CHARTTIME)
    return found

def label(events,intime,hours,c):
    t=intime+pd.Timedelta(hours=hours); end=t+pd.Timedelta(hours=48)
    onsets=detect(events,c.baseline)
    if any(intime-pd.Timedelta(days=7)<=x<=t for x in onsets): return None,'pre_existing_aki'
    if any(t<x<=end for x in onsets): return 1,'positive'
    prior=events.loc[(events.CHARTTIME<=t)&(events.CHARTTIME>=t-pd.Timedelta(days=7))]
    future=events.loc[(events.CHARTTIME>t)&(events.CHARTTIME<=end)].sort_values('CHARTTIME')
    if not len(prior): return None,'no_prior_baseline'
    if len(future)<c.negative_min_measurements: return None,'insufficient_outcome_measurements'
    points=[t]+list(future.CHARTTIME)+[end]
    if max((b-a).total_seconds()/3600 for a,b in zip(points,points[1:]))>c.negative_max_gap_hours:
        return None,'insufficient_outcome_coverage'
    return 0,'negative_under_selected_surveillance_rule'
