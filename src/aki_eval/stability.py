"""Validation stability analyses for the fixed AKI handoff model.

Only aggregate tables are persisted. Patient arrays remain in local memory.
"""
from itertools import combinations
import json
import warnings
import numpy as np
import pandas as pd
from scipy.special import expit
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score, average_precision_score
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HORIZONS = [6, 12, 24, 48]
SEGMENTS = [(1,3),(4,6),(7,12),(13,18),(19,24),(25,36),(37,48),(49,60),(61,72)]
LABELS = [f'{a}-{b}h' for a,b in SEGMENTS]
SEED = 20261102


def segment_of(hours):
    # Hourly tasks retain the prespecified integer-hour segments.
    h = np.asarray(hours)
    out = np.full(len(h), None, dtype=object)
    for (a,b), label in zip(SEGMENTS, LABELS):
        out[(h >= a) & (h <= b)] = label
    return out


def recalibration(y, p, weights=None):
    """Unpenalized logistic intercept and slope; no fitted model is updated.

    Newton steps with line search. Non-convergence/separation returns NaN,
    never a plausible-looking pass. Clipping matches aki_ml.logit.
    """
    y = np.asarray(y, float)
    w = np.ones(len(y)) if weights is None else np.asarray(weights, float)
    x = np.log(np.clip(p,1e-6,1-1e-6)/np.clip(1-np.asarray(p),1e-6,1-1e-6))
    if len(y)==0 or np.sum(w*y)==0 or np.sum(w*(1-y))==0:
        return np.nan, np.nan
    if np.ptp(x[w>0]) < 1e-10:
        return np.nan, np.nan
    beta = np.array([0.,1.])
    def loss(b):
        z=b[0]+b[1]*x
        return np.sum(w*(np.logaddexp(0,z)-y*z))
    for _ in range(100):
        q=expit(beta[0]+beta[1]*x)
        v=w*q*(1-q)
        g=np.array([np.sum(w*(q-y)), np.sum(w*(q-y)*x)])
        mat=np.array([[v.sum(),np.sum(v*x)],[np.sum(v*x),np.sum(v*x*x)]])
        try:
            step=np.linalg.solve(mat,g)
        except np.linalg.LinAlgError:
            return np.nan,np.nan
        old=loss(beta)
        scale=1.
        while scale>1e-8 and loss(beta-scale*step)>old+1e-9:
            scale*=.5
        beta-=scale*step
        if np.max(np.abs(scale*step)) < 1e-7:
            return tuple(beta) if np.max(np.abs(beta))<1e4 else (np.nan,np.nan)
    return np.nan,np.nan


def metrics(y,p):
    y=np.asarray(y,int); p=np.asarray(p,float)
    both=len(np.unique(y))==2
    intercept,slope=recalibration(y,p)
    return dict(rows=len(y),positives=int(y.sum()),prevalence=float(y.mean()) if len(y) else np.nan,
                auroc=roc_auc_score(y,p) if both else np.nan,
                auprc=average_precision_score(y,p) if both else np.nan,
                brier=float(np.mean((y-p)**2)) if len(y) else np.nan,
                calibration_intercept=intercept,calibration_slope=slope,
                oe=float(y.sum()/p.sum()) if p.sum()>0 else np.nan)


def bootstrap_metrics(d, risk, n=200, seed=SEED):
    """Cluster bootstrap: multiplicity weights preserve all rows of each patient."""
    ids, codes=np.unique(d.patient_id.to_numpy(),return_inverse=True)
    y=d.label.to_numpy(int); p=d[risk].to_numpy(float)
    # Fixed score ordering and tie groups make weighted AUROC bootstrap inexpensive.
    _, tie=np.unique(p,return_inverse=True)
    rng=np.random.default_rng(seed); aucs=[]; slopes=[]
    for _ in range(n):
        w=np.bincount(rng.integers(len(ids),size=len(ids)),minlength=len(ids))[codes]
        pos=np.bincount(tie,weights=w*y); neg=np.bincount(tie,weights=w*(1-y))
        denom=pos.sum()*neg.sum()
        aucs.append(np.sum(pos*(np.cumsum(neg)-.5*neg))/denom if denom else np.nan)
        slopes.append(recalibration(y,p,w)[1])
    out={}
    for name, vals in [('auroc',aucs),('calibration_slope',slopes)]:
        vals=np.asarray(vals); valid=vals[np.isfinite(vals)]
        lo,hi=np.quantile(valid,[.025,.975]) if len(valid) else [np.nan,np.nan]
        out.update({name+'_ci_low':lo,name+'_ci_high':hi,name+'_bootstrap_valid':len(valid)})
    out['bootstrap_resamples']=n
    return out


def calibration(pred, oof, out, n_boot=200):
    d=pred.loc[pred.eligible].copy(); d['segment']=segment_of(d.hours_since_icu)
    rows=[]
    for (h,s),g in d.dropna(subset=['segment']).groupby(['horizon_h','segment'],sort=False):
        for method in ['raw','platt','isotonic']:
            r=dict(horizon_h=h,segment=s,calibration=method,patients=g.patient_id.nunique())
            r.update(metrics(g.label,g['risk_'+method]))
            if method!='raw':
                r.update(bootstrap_metrics(g,'risk_'+method,n_boot,SEED+int(h)+LABELS.index(s)))
            rows.append(r)
        print(f'Calibration completed: {h}h / {s}',flush=True)
    table=pd.DataFrame(rows); table.to_csv(out/'calibration_by_segment.csv',index=False)
    folds=[]
    for (h,f),g in oof.loc[oof.eligible].groupby(['horizon_h','cv_fold']):
        for method in ['raw','platt','isotonic']:
            folds.append(dict(horizon_h=h,cv_fold=f,calibration=method,**metrics(g.label,g['risk_'+method])))
    folds=pd.DataFrame(folds); folds.to_csv(out/'oof_fold_metrics.csv',index=False)
    spread=folds.groupby(['horizon_h','calibration'])[['auroc','auprc','brier','calibration_slope','oe']].agg(['min','max','std'])
    spread.columns=['_'.join(c) for c in spread.columns]
    spread.to_csv(out/'oof_fold_spread.csv')
    fig,axes=plt.subplots(1,2,figsize=(13,4.6))
    for ax,col in zip(axes,['calibration_slope','oe']):
        for h in HORIZONS:
            g=table.query("horizon_h == @h and calibration == 'platt'").set_index('segment').reindex(LABELS)
            ax.plot(LABELS,g[col],marker='o',label=f'{h}h')
            if col=='calibration_slope':
                ax.fill_between(LABELS,g[col+'_ci_low'],g[col+'_ci_high'],alpha=.1)
        ax.axhspan(.8,1.25,color='green',alpha=.1); ax.axhline(1,color='gray',lw=.8)
        ax.set_title(col.replace('_',' ').title()); ax.tick_params(axis='x',rotation=50); ax.legend()
    fig.suptitle('Validation calibration by ICU time (Platt; slope: patient bootstrap 95% CI)')
    fig.tight_layout(); fig.savefig(out/'calibration_by_segment.png',dpi=170); plt.close(fig)
    return table


def monotonicity(pred,out):
    rows=[]
    for method in ['platt','isotonic']:
        p=pred.pivot(index=['patient_id','hours_since_icu'],columns='horizon_h',values='risk_'+method)
        assert p[HORIZONS].notna().all().all(), 'Unmatched horizons'
        p['segment']=segment_of(p.index.get_level_values('hours_since_icu'))
        for s,g in [('all',p),*p.dropna(subset=['segment']).groupby('segment',sort=False)]:
            for a,b in zip(HORIZONS[:-1],HORIZONS[1:]):
                excess=g[a]-g[b]; bad=excess[excess>0]
                rows.append(dict(calibration=method,segment=s,horizon_h=a,next_horizon_h=b,
                                 rows=len(g),violations=len(bad),violation_fraction=len(bad)/len(g),
                                 excess_median=bad.median(),excess_p95=bad.quantile(.95)))
        # Fixed risk bins refer to the longer-horizon risk, not a post-hoc selected range.
        p['risk_range']=pd.cut(p[24],[-np.inf,.05,.1,.2,.4,np.inf],labels=['<.05','.05-.10','.10-.20','.20-.40','>=.40'],right=False)
        for s,g in p.groupby('risk_range',observed=True):
            for a,b in zip(HORIZONS[:-1],HORIZONS[1:]):
                excess=g[a]-g[b]; bad=excess[excess>0]
                rows.append(dict(calibration=method,segment='risk_range',risk_range=s,horizon_h=a,next_horizon_h=b,
                                 rows=len(g),violations=len(bad),violation_fraction=len(bad)/len(g),
                                 excess_median=bad.median(),excess_p95=bad.quantile(.95)))
    result=pd.DataFrame(rows); result.to_csv(out/'horizon_monotonicity.csv',index=False)
    return result


def feature_shift(X,keys,features,dictionary,out):
    segments=segment_of(keys.hours_since_icu)
    base=X[segments==LABELS[0]].astype(float)
    train=dictionary.set_index('feature').missing_fraction_train
    rows=[]
    # Standardized mean difference uses pooled row SD; missingness reported separately.
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',RuntimeWarning)
        bm=np.nanmean(base,axis=0); bv=np.nanvar(base,axis=0,ddof=1)
        for s in LABELS:
            a=X[segments==s].astype(float)
            q=np.nanquantile(a,[.25,.5,.75],axis=0)
            mean=np.nanmean(a,axis=0); var=np.nanvar(a,axis=0,ddof=1)
            sd=np.sqrt((bv+var)/2)
            smd=np.divide(mean-bm,sd,out=np.full(len(features),np.nan),where=sd>0)
            smd[(sd==0)&(mean==bm)]=0
            for j,f in enumerate(features):
                miss=np.isnan(a[:,j]).mean(); tm=train.get(f,np.nan)
                rows.append(dict(feature=f,segment=s,rows=len(a),missing_fraction=miss,
                                 missing_fraction_train=tm,missing_delta_train=miss-tm,
                                 q25=q[0,j],median=q[1,j],q75=q[2,j],iqr=q[2,j]-q[0,j],
                                 standardized_difference=smd[j],abs_standardized_difference=abs(smd[j])))
    result=pd.DataFrame(rows); result.to_csv(out/'feature_shift.csv',index=False)
    return result


def _transitions(d,time_col):
    d=d.sort_values(['patient_id',time_col],kind='stable').copy()
    assert not d.duplicated(['patient_id',time_col]).any(), 'Duplicate update times'
    grp=d.groupby('patient_id',sort=False)
    d['prev_risk']=grp.risk_platt.shift()
    d['previous_time']=grp[time_col].shift()
    if time_col=='event_time':
        d['dt']=(d[time_col]-d.previous_time).dt.total_seconds()/3600
        d['time_h']=d[time_col].astype('int64')/3.6e12
    else:
        d['dt']=d[time_col]-d.previous_time
        d['time_h']=d[time_col].astype(float)
    d['delta']=abs(d.risk_platt-d.prev_risk)
    return d


def coalesce_event_times(d):
    """Coalesce serialization collisions only when risk/state agree exactly.

    Replay generation groups floating availability times, then stores timestamps
    at second precision. Multiple input bundles can therefore share a stored
    instant. Sum their measurement counts; never invent an ordering or average
    distinct predictions.
    """
    key=['patient_id','event_time']
    duplicate=d.duplicated(key,keep=False)
    dup=d.loc[duplicate]
    if dup.empty:
        return d,dict(collision_rows=0,collision_instants=0,rows_coalesced=0)
    groups=dup.groupby(key,observed=True,sort=False)
    for c in ['risk_platt','eligible','hourly_snapshot_hour','hours_since_icu','label']:
        assert groups[c].nunique(dropna=False).le(1).all(), 'Conflicting simultaneous event states: '+c
    combined=groups.first().reset_index()
    combined['n_events']=groups.n_events.sum().to_numpy()
    result=pd.concat([d.loc[~duplicate],combined],ignore_index=True)
    return result,dict(collision_rows=len(dup),collision_instants=len(combined),rows_coalesced=len(d)-len(result))


def risk_volatility(pred,replay,threshold,out):
    rows=[]; flaps=[]; collisions=[]
    for mode,data,tcol in [('hourly',pred,'hours_since_icu'),('event',replay,'event_time')]:
        for h,g in data.groupby('horizon_h'):
            if mode=='event':
                g,quality=coalesce_event_times(g)
                collisions.append(dict(horizon_h=h,**quality))
            d=_transitions(g,tcol)
            # Event metrics use the linked hourly_snapshot_hour, the dataset's existing
            # discretization, avoiding a new fractional-time segment definition.
            d['segment']=segment_of(d.hourly_snapshot_hour if mode=='event' else d.hours_since_icu)
            # Report all updates as specified, plus an eligible-update sensitivity.
            prev_eligible=d.groupby('patient_id',sort=False).eligible.shift(fill_value=False)
            pair_valid=(d.dt>0)
            if mode=='hourly':
                pair_valid &= np.isclose(d.dt,1)
            for scope,valid in [('all_updates',pair_valid),('eligible_updates',pair_valid & d.eligible & prev_eligible)]:
                d['valid']=valid
                if h==24:
                    d['crossing']=valid & ((d.risk_platt>=threshold)!=(d.prev_risk>=threshold))
                    # A reversal is the next opposite crossing in the same uninterrupted run.
                    d['run']=(~valid).groupby(d.patient_id).cumsum()
                    cross=d.loc[d.crossing].copy()
                    cross['next_crossing_h']=cross.groupby(['patient_id','run'],observed=True,sort=False).time_h.shift(-1)-cross.time_h
                    run_end=d.groupby(['patient_id','run'],observed=True).time_h.transform('max')
                    cross['followup_h']=run_end.loc[cross.index].to_numpy()-cross.time_h
                    for s,part in d.dropna(subset=['segment']).groupby('segment',sort=False):
                        c=cross[cross.segment==s]; exposure=part.loc[part.valid,'dt'].sum()/24
                        r=dict(mode=mode,scope=scope,horizon_h=h,segment=s,crossings=len(c),patient_days=exposure,
                               crossings_per_patient_day=len(c)/exposure if exposure>0 else np.nan)
                        for window in [1,2,3]:
                            reversed_=c.next_crossing_h.le(window)
                            assessable=reversed_ | c.followup_h.ge(window)
                            r[f'reversal_{window}h_denominator']=int(assessable.sum())
                            r[f'reversal_{window}h_fraction']=float(reversed_.sum()/assessable.sum()) if assessable.any() else np.nan
                        flaps.append(r)
                for s,part in d.loc[valid].dropna(subset=['segment']).groupby('segment',sort=False):
                    groups=[('all',part)]
                    if mode=='event':
                        groups += [('single',part[part.n_events==1]),('multiple',part[part.n_events>1])]
                    total=part.delta.sum()
                    for kind,v in groups:
                        qs=v.delta.quantile([.5,.9,.99])
                        rows.append(dict(mode=mode,scope=scope,horizon_h=h,segment=s,measurement_group=kind,
                                         transitions=len(v),delta_median=qs.loc[.5],delta_p90=qs.loc[.9],delta_p99=qs.loc[.99],
                                         total_absolute_change=v.delta.sum(),
                                         absolute_change_share=v.delta.sum()/total if total>0 else np.nan))
            print(f'Volatility completed: {mode} / {h}h',flush=True)
    result=pd.DataFrame(rows); flaps=pd.DataFrame(flaps)
    result=result.merge(flaps,on=['mode','scope','horizon_h','segment'],how='left',validate='m:1')
    result.to_csv(out/'risk_volatility.csv',index=False)
    pd.DataFrame(collisions).to_csv(out/'event_time_collisions.csv',index=False)
    fig,axes=plt.subplots(2,2,figsize=(13,8),sharex=True)
    for h,ax in zip(HORIZONS,axes.flat):
        for mode in ['hourly','event']:
            g=result.query("horizon_h == @h and mode == @mode and measurement_group == 'all' and scope == 'all_updates'").set_index('segment').reindex(LABELS)
            for q,style in [('delta_p90','-'),('delta_p99','--')]:
                ax.plot(LABELS,g[q],style,label=mode+' '+q.replace('delta_',''))
        ax.set_title(f'{h}h horizon'); ax.set_ylabel('Absolute risk change'); ax.tick_params(axis='x',rotation=50); ax.legend(fontsize=8)
    fig.suptitle('All consecutive updates; hourly gaps excluded'); fig.tight_layout()
    fig.savefig(out/'risk_volatility.png',dpi=170); plt.close(fig)
    return result


def shap_stability(X,keys,features,tree_shap,out,n_patients=1500,n_boot=100):
    rng=np.random.default_rng(SEED); patients=np.sort(keys.patient_id.unique())
    selected=rng.choice(patients,min(n_patients,len(patients)),replace=False)
    mask=keys.patient_id.isin(selected).to_numpy(); sk=keys.loc[mask].copy()
    sx=X[mask]; segments=segment_of(sk.hours_since_icu)
    means={}; rows=[]
    for h in HORIZONS:
        # Chunking bounds contribution memory and avoids a second full matrix.
        sh=np.empty_like(sx,dtype=np.float32)
        for a in range(0,len(sx),4000):
            sh[a:a+4000]=tree_shap(sx[a:a+4000],h)
        direction=np.sign(sh.mean(axis=0,dtype=np.float64))
        for s in LABELS:
            m=segments==s; values=sh[m]; ids=sk.loc[m,'patient_id'].to_numpy()
            if not len(values):
                continue
            _,code=np.unique(ids,return_inverse=True); n=code.max()+1
            sums=np.zeros((n,len(features)),dtype=np.float64)
            np.add.at(sums,code,np.abs(values)); counts=np.bincount(code)
            mean=np.abs(values).mean(axis=0,dtype=np.float64)
            rank=pd.Series(mean).rank(ascending=False,method='min').to_numpy(int)
            order=np.argsort(-mean,kind='stable'); top=order[:min(20,len(features))]
            freq=np.zeros(len(features))
            brng=np.random.default_rng(SEED+h+LABELS.index(s))
            for _ in range(n_boot):
                w=np.bincount(brng.integers(n,size=n),minlength=n)
                b=(w@sums)/(w@counts)
                freq[np.argsort(-b,kind='stable')[:min(20,len(features))]]+=1
            means[(h,s)]=mean
            signs=np.sign(values)
            for j,f in enumerate(features):
                rows.append(dict(horizon_h=h,segment=s,feature=f,patients=n,rows=len(values),
                                 mean_abs_shap=mean[j],rank=rank[j],is_top20=bool(j in top),
                                 top20_bootstrap_frequency=freq[j]/n_boot,bootstrap_resamples=n_boot,
                                 overall_direction=int(direction[j]),
                                 sign_consistency=float(np.mean(signs[:,j]==direction[j])) if direction[j]!=0 else np.nan,
                                 zero_shap_fraction=float(np.mean(signs[:,j]==0))))
        print(f'TreeSHAP completed: {h}h / {len(selected)} sampled patients',flush=True)
        del sh
    result=pd.DataFrame(rows)
    ranks=result.pivot(index=['horizon_h','feature'],columns='segment',values='rank').reindex(columns=LABELS)
    jumps=ranks.diff(axis=1)
    result['rank_change_previous_segment']=[jumps.loc[(r.horizon_h,r.feature),r.segment] for r in result.itertuples()]
    result['rank_jump_gt10']=result.rank_change_previous_segment.abs()>10
    result.to_csv(out/'shap_stability.csv',index=False)
    agreement=[]
    def compare(a,b,kind):
        x,y=means[a],means[b]
        tx=set(np.argsort(-x,kind='stable')[:20]); ty=set(np.argsort(-y,kind='stable')[:20])
        agreement.append(dict(comparison=kind,horizon_a=a[0],segment_a=a[1],horizon_b=b[0],segment_b=b[1],
                              spearman=spearmanr(x,y).statistic,top20_overlap=len(tx&ty),top20_jaccard=len(tx&ty)/len(tx|ty)))
    for h in HORIZONS:
        for a,b in zip(LABELS[:-1],LABELS[1:]):
            compare((h,a),(h,b),'adjacent_segments')
    for s in LABELS:
        for a,b in combinations(HORIZONS,2):
            compare((a,s),(b,s),'across_horizons')
    agreement=pd.DataFrame(agreement); agreement.to_csv(out/'shap_rank_agreement.csv',index=False)
    fig,axes=plt.subplots(1,2,figsize=(13,5))
    for h in HORIZONS:
        g=agreement.query("comparison == 'adjacent_segments' and horizon_a == @h")
        axes[0].plot(g.segment_b,g.spearman,marker='o',label=f'{h}h')
        axes[1].plot(g.segment_b,g.top20_jaccard,marker='o',label=f'{h}h')
    for ax,title in zip(axes,['Importance rank correlation','Top-20 Jaccard']):
        ax.set_title(title); ax.set_ylim(-.05,1.05); ax.tick_params(axis='x',rotation=50); ax.legend()
    fig.suptitle('Predictor stability between adjacent ICU segments'); fig.tight_layout()
    fig.savefig(out/'shap_stability.png',dpi=170); plt.close(fig)
    return result,agreement


def reliability(cal,vol,mono,out):
    d=cal.query("calibration == 'platt'").copy()
    for col,ok in [('auroc',d.auroc>=.75),('calibration_slope',d.calibration_slope.between(.8,1.25)),
                   ('oe',d.oe.between(.8,1.25)),('positives',d.positives>=100)]:
        d[col+'_status']=np.where(ok,'pass','flag')
    d['prespecified_all_pass']=(d.auroc>=.75)&d.calibration_slope.between(.8,1.25)&d.oe.between(.8,1.25)&(d.positives>=100)
    for mode in ['hourly','event']:
        v=vol.query("mode == @mode and measurement_group == 'all' and scope == 'all_updates'")
        keep=['horizon_h','segment','delta_median','delta_p90','delta_p99','crossings_per_patient_day','reversal_1h_fraction','reversal_2h_fraction','reversal_3h_fraction']
        d=d.merge(v[keep].rename(columns={c:mode+'_'+c for c in keep[2:]}),on=['horizon_h','segment'],how='left',validate='1:1')
    m=mono.query("calibration == 'platt' and segment != 'all' and segment != 'risk_range'")
    d=d.merge(m[['horizon_h','segment','next_horizon_h','violation_fraction','excess_p95']],on=['horizon_h','segment'],how='left',validate='1:1')
    d['volatility_status']='descriptive_no_prespecified_cutoff'
    d['monotonicity_status']=np.where(d.horizon_h==48,'not_applicable_no_longer_horizon','descriptive_no_prespecified_cutoff')
    d.to_csv(out/'reliability_summary.csv',index=False)
    return d
