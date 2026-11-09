"""Diagnose frozen saved-feature versus event-replay differences; never retune a policy.

Only aggregate outputs are written. The original study records, source engine, model,
policy, and LATEST.json are read-only inputs to this separate sensitivity analysis.
"""
from __future__ import annotations
import argparse
import functools
import json
import os
from pathlib import Path
import sys
import time
import types

for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:
    os.environ.setdefault(name,'2')

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from threadpoolctl import threadpool_limits
from aki_ml import features as engine
from aki_eval.data import PRED_COLUMNS
from aki_eval.policies import AlertRule,alert_events,metrics,prepare
from aki_eval.protocol import sha256,validation_roles,write_json
from aki_eval.replay import ReplayData,sample_patients


class WindowLocalIndex(engine.EventIndex):
    """Diagnostic oracle using each patient's native times and each window's own sums.

    This comparator does not define a new production feature version. It deliberately
    trades speed for arithmetic independence from other patients and unseen events.
    """
    def __init__(self,stay_idx,t_hours,values):
        super().__init__(stay_idx,t_hours,values)
        unique,starts,counts=np.unique(self.stay,return_index=True,return_counts=True)
        self.segments={int(s):(int(a),int(a+n)) for s,a,n in zip(unique,starts,counts)}

    def bounds(self,q_stay,q_t,window=None,since_hour=None,include_t=True,closed_left=False):
        qs=np.asarray(q_stay,np.int64);qt=np.broadcast_to(np.asarray(q_t,float),qs.shape)
        lo=np.zeros(len(qs),int);hi=lo.copy()
        for s in np.unique(qs):
            if int(s) not in self.segments:continue
            a,b=self.segments[int(s)];idx=np.flatnonzero(qs==s);tt=self.t[a:b]
            hi[idx]=a+np.searchsorted(tt,qt[idx],side='right' if include_t else 'left')
            if since_hour is not None:
                lower=np.broadcast_to(np.asarray(since_hour,float),qs.shape)[idx]
                lo[idx]=a+np.searchsorted(tt,lower,side='left')
            elif window is None:lo[idx]=a
            else:lo[idx]=a+np.searchsorted(tt,qt[idx]-window,side='left' if closed_left else 'right')
        return lo,np.maximum(hi,lo)

    def sum(self,lo,hi):
        return np.array([np.sum(self.v[a:b],dtype=np.float64) for a,b in zip(lo,hi)])

    def mean(self,lo,hi):
        return np.array([np.mean(self.v[a:b]) if b>a else np.nan for a,b in zip(lo,hi)])

    def std(self,lo,hi):
        return np.array([np.std(self.v[a:b],ddof=1) if b-a>=2 else np.nan for a,b in zip(lo,hi)])

    def slope(self,lo,hi,min_span_hours=1.):
        output=np.full(len(lo),np.nan)
        for i,(a,b) in enumerate(zip(lo,hi)):
            if b-a<2 or self.t[b-1]-self.t[a]<min_span_hours:continue
            t=self.t[a:b];v=self.v[a:b];dt=t-t.mean();dv=v-v.mean()
            den=np.dot(dt,dt)
            if (b-a)*den>1e-9:output[i]=np.dot(dt,dv)/den
        return output


def local_compute():
    function = types.FunctionType(engine.compute_features.__code__,
        {**engine.compute_features.__globals__,'EventIndex':WindowLocalIndex},
        name='diagnostic_window_local_features',argdefs=engine.compute_features.__defaults__,
        closure=engine.compute_features.__closure__)
    function.__kwdefaults__ = dict(engine.compute_features.__kwdefaults__ or {})
    return function


def feature_comparison(reference,variant,names,label):
    rows=[]
    for j,name in enumerate(names):
        a,b=reference[:,j],variant[:,j]
        exact=(a==b)|(np.isnan(a)&np.isnan(b))
        finite=np.isfinite(a)&np.isfinite(b)
        delta=np.abs(a[finite].astype(float)-b[finite].astype(float))
        if not exact.all():
            rows.append({'variant':label,'feature':name,'rows':len(a),'different_float32_values':int((~exact).sum()),
                'outside_original_tolerance':int((~np.isclose(a,b,rtol=1e-5,atol=1e-4,equal_nan=True)).sum()),
                'missingness_changes':int((np.isnan(a)!=np.isnan(b)).sum()),
                'max_absolute_difference':float(delta.max()) if len(delta) else None,
                'mean_absolute_difference':float(delta.mean()) if len(delta) else None,
                'stored_exact_zero_to_nonzero':int(((a==0)&np.isfinite(b)&(b!=0)).sum()),
                'stored_nonzero_to_exact_zero':int((np.isfinite(a)&(a!=0)&(b==0)).sum())})
    return rows


def risk_comparison(a,b,label):
    finite=np.isfinite(a)&np.isfinite(b);d=np.abs(a[finite]-b[finite])
    out={'variant':label,'rows':len(a),'paired_rows':int(finite.sum()),
         'risk_rows_above_1e6_difference':int((d>1e-6).sum()),
         'mean_absolute_risk_difference':float(d.mean()) if len(d) else None,
         'max_absolute_risk_difference':float(d.max()) if len(d) else None}
    for t in [.21,.23]:out['warning_flips_at_'+str(t)]=int(((a[finite]>=t)!=(b[finite]>=t)).sum())
    return out


def load_context(run, *, source_root=None):
    record=json.loads((run/'prespec.json').read_text(encoding='utf-8'))
    spec=record['specification'];cfg=spec['config'];policy_path=ROOT/'artifacts/demo_i/policy.json'
    policy=json.loads(policy_path.read_text(encoding='utf-8'))
    data_root=ROOT.parent/'Cleaned'
    paths={'snapshots':data_root/'datasets/snapshot_dataset_validation.parquet',
           'events':data_root/'datasets/event_stream_validation.parquet',
           'predictions':ROOT/'artifacts/predictions'/f"{cfg['model_run_id']}.parquet",
           'model':ROOT/policy['model']['model_dir']/'pipeline.joblib',
           'feature_order':ROOT/policy['model']['model_dir']/'feature_order.json',
           'policy':policy_path,'dataset_config':ROOT/'configs/default.yaml'}
    for name,path in paths.items():
        if sha256(path)!=spec['input_sha256'][name]:raise RuntimeError('Frozen input changed: '+name)
    for name,value in spec['source_sha256'].items():
        if sha256(Path(source_root or ROOT)/name)!=value:raise RuntimeError('Frozen analysis source changed: '+name)
    for name in ['numpy','pandas','pyarrow','scikit-learn','lightgbm','scipy','joblib']:
        from importlib.metadata import version
        trained=json.loads((ROOT/'artifacts/models'/cfg['model_run_id']/'run_manifest.json').read_text())['software'][name]
        if version(name)!=trained:raise RuntimeError('Model runtime version changed: '+name)
    names=json.loads(paths['feature_order'].read_text());names=names if isinstance(names,list) else names.get('feature_names',names.get('features'))
    snap=pq.read_table(paths['snapshots'],filters=[('split','=','validation')]).to_pandas()
    snap['patient_id']=snap['patient_id'].astype(str);snap['stay_id']=snap['stay_id'].astype(str)
    snap['evaluation_role']=validation_roles(snap['patient_id'],cfg['seed'],cfg['validation_selection_fraction'])
    pred=pq.read_table(paths['predictions'],filters=[('family','=',cfg['family']),('feature_set','=',cfg['feature_set']),
        ('prediction_set','=','validation'),('horizon_h','=',cfg['alert_horizon_h'])],columns=PRED_COLUMNS).to_pandas()
    pred['patient_id']=pred['patient_id'].astype(str);pred['stay_id']=pred['stay_id'].astype(str);pred['risk']=pred['risk_platt']
    role=snap.drop_duplicates('patient_id').set_index('patient_id')['evaluation_role'];pred['evaluation_role']=pred['patient_id'].map(role)
    lo,hi=cfg['monitoring_hours'];frame=prepare(pred[pred['hours_since_icu'].between(lo,hi)],cfg['alert_horizon_h'])
    queries=frame[['patient_id','hours_since_icu']].merge(snap,on=['patient_id','hours_since_icu'],how='left',validate='one_to_one')
    if queries['stay_id'].isna().any():raise RuntimeError('Unmatched replay inputs')
    data_cfg=yaml.safe_load(paths['dataset_config'].read_text())
    context=ReplayData.load(paths['events'],snap,data_cfg,names)
    pipe=joblib.load(paths['model'])
    for head in pipe.heads.values():head.predict=functools.partial(head.predict,num_threads=2)
    return cfg,policy,names,frame,queries,context,pipe,paths


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,default=ROOT/'artifacts/handoff/kejin/kejin-validation-policy-v2-21d980db7c292219')
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    run=args.run.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    keep={p:sha256(p) for p in [run/'prespec.json',run/'choice.json',run/'confirmation.json',ROOT/'artifacts/demo_i/policy.json',run.parent/'LATEST.json']}
    plan={'purpose':'post-confirmation numeric sensitivity diagnosis; no policy retuning or new validation claim',
          'source_run':run.name,'diagnostic_script_sha256':sha256(Path(__file__)),
          'fixed_policies':[{'threshold':.21,'cooldown_hours':6},{'threshold':.23,'cooldown_hours':12}],
          'comparators':['saved_snapshot','all_validation_batch_no_clip','all_validation_batch_zero_lag','window_local_arithmetic'],
          'data':'validation only; no test data; original patient roles unchanged',
          'local_oracle':'native patient times and window-local float64 statistics; frozen model, diagnostic only',
          'privacy':'aggregate output only','selection_or_confirmation_records_writable':False}
    write_json(out/'diagnostic_plan.json',plan)
    start=time.monotonic();print('Loading frozen validation inputs.',flush=True)
    pa.set_cpu_count(2);pa.set_io_thread_count(2)
    with threadpool_limits(limits=2):
        cfg,policy,names,frame,q,context,pipe,paths=load_context(run)
        X=q[names].to_numpy(np.float32);reference=frame['risk'].to_numpy(float)
        predict=lambda matrix:pipe.predict(matrix,cfg['alert_horizon_h'],'platt').astype(float)
        if not np.array_equal(predict(X),reference):raise RuntimeError('Stored features fail exact prediction reproduction')
        rules=[('baseline',.21,AlertRule(name='demo-v1',sustain_hours=1,cooldown_hours=6)),
               ('candidate',.23,AlertRule(name='cool12',sustain_hours=1,cooldown_hours=12))]
        aggregate=[];risks=[];features=[];events=[];ablations=[]
        def record(label,matrix,risk,covered):
            features.extend(feature_comparison(X,matrix,names,label))
            risks.append(risk_comparison(reference,risk,label))
            for role in ['selection','confirmation']:
                mask=frame['evaluation_role'].eq(role).to_numpy();ref=frame.loc[mask].reset_index(drop=True)
                variant=ref.copy();variant['risk']=risk[mask]
                for rule_name,threshold,rule in rules:
                    m=metrics(variant,threshold,rule);aggregate.append({'variant':label,'role':role,'policy':rule_name,**m})
                    original=alert_events(ref,threshold,rule);shifted=alert_events(variant,threshold,rule)
                    code=pd.factorize(ref['patient_id'],sort=False)[0];changed=original!=shifted
                    events.append({'variant':label,'role':role,'policy':rule_name,'patients':ref['patient_id'].nunique(),
                        'rows':len(ref),'unscorable_rows':int((~covered[mask]).sum()),
                        'event_flags_changed':int(changed.sum()),'patients_with_event_time_changes':int(len(np.unique(code[changed]))),
                        'warning_status_changed':int(((ref['risk'].to_numpy()>=threshold)!=(risk[mask]>=threshold)).sum())})
            pd.DataFrame(aggregate).to_csv(out/'fixed_policy_metrics.csv',index=False)
            pd.DataFrame(risks).to_csv(out/'risk_differences.csv',index=False)
            pd.DataFrame(features).to_csv(out/'feature_differences.csv',index=False)
            pd.DataFrame(events).to_csv(out/'alert_event_differences.csv',index=False)
            print(label,'risk differences',risks[-1]['risk_rows_above_1e6_difference'],'elapsed_s',round(time.monotonic()-start,1),flush=True)
        record('saved_snapshot',X,reference,np.ones(len(q),bool))
        for label,clip in [('all_validation_batch_no_clip',None),('all_validation_batch_zero_lag','all')]:
            print('Computing '+label,flush=True);Y,ok=context.matrix(q,clip=clip);r=predict(Y);r[~ok]=np.nan
            record(label,Y,r,ok)
            if clip=='all':
                changed=np.isfinite(r)&(np.abs(r-reference)>1e-6)
                if changed.any():
                    base=X[changed].copy();base_r=reference[changed];target_r=r[changed]
                    groups={'all_std':[i for i,n in enumerate(names) if '_std_' in n],
                            'all_slope':[i for i,n in enumerate(names) if '_slope_' in n],
                            'all_mean':[i for i,n in enumerate(names) if '_mean_' in n],
                            'all_other':[i for i,n in enumerate(names) if not any(k in n for k in ['_std_','_slope_','_mean_'])]}
                    for group,cols in groups.items():
                        hybrid=base.copy();hybrid[:,cols]=Y[np.ix_(changed,cols)];hr=predict(hybrid)
                        ablations.append({'group':group,'diagnostic_rows':len(base),'changed_risks':int((np.abs(hr-base_r)>1e-6).sum()),
                            'reproduces_full_replay_risk':int((np.abs(hr-target_r)<=1e-6).sum()),
                            'max_risk_difference_from_saved':float(np.abs(hr-base_r).max())})
                    for j,n in enumerate(names):
                        if np.any(~np.isclose(base[:,j],Y[changed,j],rtol=0,atol=0,equal_nan=True)):
                            hybrid=base.copy();hybrid[:,j]=Y[changed,j];hr=predict(hybrid)
                            ablations.append({'group':n,'diagnostic_rows':len(base),'changed_risks':int((np.abs(hr-base_r)>1e-6).sum()),
                                'reproduces_full_replay_risk':int((np.abs(hr-target_r)<=1e-6).sum()),
                                'max_risk_difference_from_saved':float(np.abs(hr-base_r).max())})
                    pd.DataFrame(ablations).to_csv(out/'feature_risk_ablations.csv',index=False)
            del Y,r
        print('Computing independent window-local arithmetic comparator.',flush=True)
        qs=context.query_stays(q);qt=q['hours_since_icu'].to_numpy(float);service,_=context.service(q,0)
        Y=local_compute()(context.events,context.static,service,qs,qt,context.config,names)
        r=predict(Y);ok=np.ones(len(q),bool);record('window_local_arithmetic',Y,r,ok)
        for role,offset in [('selection',31),('confirmation',32)]:
            pool=context.snapshots[context.snapshots['evaluation_role']==role]
            cap=cfg['cutoff'][role+'_patient_cap'];pool=sample_patients(pool,cap,cfg['seed']+offset)
            local=ReplayData.load(paths['events'],pool,context.config,names)
            mask=frame['patient_id'].isin(pool['patient_id']).to_numpy();query=q.loc[mask].reset_index(drop=True)
            print('Reproducing original '+role+' zero-lag sample.',flush=True)
            Z,covered=local.matrix(query,clip='all');rz=predict(Z);rz[~covered]=np.nan
            base=reference[mask];info=risk_comparison(base,rz,'original_'+role+'_sample_zero_lag')
            info['role']=role;write_json(out/('original_'+role+'_sample_reproduction.json'),info)
        preserved=all(sha256(p)==value for p,value in keep.items())
        if not preserved:raise RuntimeError('An original sealed record changed')
        write_json(out/'diagnostic_manifest.json',{'status':'complete','elapsed_seconds':time.monotonic()-start,
            'original_records_unchanged':preserved,'source_run':run.name,'patients':frame['patient_id'].nunique(),
            'rows':len(frame),'model_sha256':sha256(paths['model']),'test_data_used':False,
            'candidate_retuned':False,'new_policy_activated':False,'diagnostic_script_sha256':sha256(Path(__file__))})
        print('Diagnostic complete; original records unchanged.',flush=True)

if __name__=='__main__':main()
