"""Aggregate causal controls for the separate post-confirmation replay diagnosis."""
from pathlib import Path
import json
import sys
import time
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits
import diagnose_feature_replay as diag


def first_path_crossings(model,A,B,names,n_rounds):
    counts={}
    for tree in model.dump_model(num_iteration=n_rounds)['tree_info']:
        stack=[(tree['tree_structure'],np.arange(len(A)))]
        while stack:
            node,idx=stack.pop()
            if not len(idx) or 'split_index' not in node:continue
            j=node['split_feature'];threshold=float(node['threshold'])
            if node['decision_type']!='<=':raise RuntimeError('Unexpected categorical split')
            def route(v):
                # LightGBM compares the float32 input values as doubles to double thresholds.
                v=np.asarray(v,dtype=np.float64)
                missing=np.isnan(v)
                if node.get('missing_type')=='Zero':missing|=np.abs(v)<=1e-35
                return np.where(missing,node['default_left'],v<=threshold)
            a,b=A[idx,j],B[idx,j];left_a,left_b=route(a),route(b);cross=left_a!=left_b
            if cross.any():
                name=names[j];c=counts.setdefault(name,{'feature':name,'first_divergent_tree_paths':0,
                    'near_zero_threshold_paths':0,'stored_zero_paths':0,'replayed_zero_paths':0})
                c['first_divergent_tree_paths']+=int(cross.sum())
                if abs(threshold)<=1e-4:c['near_zero_threshold_paths']+=int(cross.sum())
                c['stored_zero_paths']+=int(((a==0)&cross).sum());c['replayed_zero_paths']+=int(((b==0)&cross).sum())
            same=~cross
            stack.append((node['left_child'],idx[same&left_a]));stack.append((node['right_child'],idx[same&~left_a]))
    return list(counts.values())


def main():
    root=diag.ROOT;run=root/'artifacts/handoff/kejin/kejin-validation-policy-v2-21d980db7c292219'
    out=root/'artifacts/handoff/kejin/numeric-diagnosis-20261106-01';start=time.monotonic()
    keep={p:diag.sha256(p) for p in [run/'prespec.json',run/'choice.json',run/'confirmation.json',run.parent/'LATEST.json',root/'artifacts/demo_i/policy.json']}
    diag.write_json(out/'cause_control_plan.json',{'script_sha256':diag.sha256(Path(__file__)),
        'fixed_policies':'original baseline and sealed candidate; no selection or retuning',
        'comparators':['single feature-group substitution','preserved stored static ratio','reconstructed pre-ICU baseline precision','native window-local statistics'],
        'pre_icu_baseline_rule':'exact original round2/group mean/7d pre-ICU minimum; only if float32 value matches saved admission baseline',
        'unknown_history_baseline':'not reconstructed; report scope; held-ratio control uses known saved zero-lag feature only',
        'results':'aggregate only; exploratory post-confirmation sensitivity analysis'})
    with threadpool_limits(limits=2):
        cfg,policy,names,frame,q,context,pipe,paths=diag.load_context(run)
        X=q[names].to_numpy(np.float32);ref=frame['risk'].to_numpy(float);h=cfg['alert_horizon_h'];j=names.index('creatinine_ratio_to_baseline')
        predict=lambda matrix:pipe.predict(matrix,h,'platt').astype(float)
        rows=[];risk_rows=[]
        def record(label,Y):
            risks=predict(Y);risk_rows.append(diag.risk_comparison(ref,risks,label))
            for role in ['selection','confirmation']:
                f=frame[frame['evaluation_role']==role].copy();f['risk']=risks[frame['evaluation_role'].eq(role).to_numpy()]
                for name,t,rule in [('baseline',.21,diag.AlertRule(name='demo-v1',cooldown_hours=6)),('candidate',.23,diag.AlertRule(name='cool12',cooldown_hours=12))]:
                    rows.append({'variant':label,'role':role,'policy':name,**diag.metrics(f,t,rule)})
            pd.DataFrame(rows).to_csv(out/'cause_control_policy_metrics.csv',index=False)
            pd.DataFrame(risk_rows).to_csv(out/'cause_control_risk_differences.csv',index=False)
            print(label,'confirmation candidate PPV',rows[-1]['alert_ppv'],flush=True)
        ev=context.events
        cr=ev[ev['concept']=='creatinine'].copy();cr['value']=cr['value'].astype(float).round(2)
        cr=cr.groupby(['stay_idx','t'],as_index=False)['value'].mean()
        pre=cr[cr['t'].between(-168,0)].groupby('stay_idx')['value'].min()
        qs=context.query_stays(q);den=pre.reindex(qs).to_numpy(float);saved=q['baseline_cr_admission'].to_numpy(np.float32)
        restored=np.isfinite(den)&(den.astype(np.float32)==saved)
        with np.errstate(invalid='ignore',divide='ignore'):
            ratio=(X[:,names.index('creatinine_last')].astype(float)/den).astype(np.float32)
        same_ratio=(ratio==X[:,j])|(np.isnan(ratio)&np.isnan(X[:,j]))
        diag.write_json(out/'static_precision_attribution.json',{
            'patients':frame['patient_id'].nunique(),'pre_icu_baseline_reconstructed_patients':int(q.loc[restored,'patient_id'].nunique()),
            'pre_icu_baseline_reconstructed_rows':int(restored.sum()),
            'pre_icu_baseline_not_matching_stored_float32_rows':int((np.isfinite(den)&~restored).sum()),
            'reconstructed_ratio_matches_saved_rows':int((same_ratio&restored).sum()),
            'scope':'only exported 7d pre-ICU minimum; remote-history median remains unavailable'})
        print('Computing unchanged full-batch replay.',flush=True)
        Y,_=context.matrix(q)
        changed=np.abs(predict(Y)-ref)>1e-6
        pd.DataFrame(first_path_crossings(pipe.heads[h],X[changed],Y[changed],names,pipe.n_rounds[h])).to_csv(out/'tree_path_crossings.csv',index=False)
        for group,cols in {'only_std':[i for i,n in enumerate(names) if '_std_' in n],
                           'only_slope':[i for i,n in enumerate(names) if '_slope_' in n],
                           'only_mean':[i for i,n in enumerate(names) if '_mean_' in n],
                           'only_static_ratio':[j]}.items():
            hybrid=X.copy();hybrid[:,cols]=Y[:,cols];record(group,hybrid)
        saved_ratio=Y.copy();saved_ratio[:,j]=X[:,j];record('batch_replay_saved_static_ratio_control',saved_ratio);del saved_ratio
        Y[restored,j]=ratio[restored];record('batch_replay_pre_icu_static_precision_restored',Y);del Y
        # Same real-patient input window; change only unrelated/future rows in the index.
        spo=ev[ev['concept']=='spo2'];batch=diag.engine.EventIndex(spo['stay_idx'],spo['t'],spo['value'])
        native=diag.WindowLocalIndex(spo['stay_idx'],spo['t'],spo['value']);qt=q['hours_since_icu'].to_numpy(float)
        l,r=native.bounds(qs,qt,window=24);truth=native.std(l,r)
        selected=np.flatnonzero((truth==0)&(r-l>=2))
        selected=np.random.default_rng(20261102).choice(selected,min(512,len(selected)),replace=False)
        controls=[];all_std=[];solo_std=[];past_std=[]
        for i in selected:
            b_lo,b_hi=batch.bounds([qs[i]],[qt[i]],window=24);all_std.append(batch.std(b_lo,b_hi)[0])
            solo=spo[spo['stay_idx']==qs[i]];past=solo[solo['t']<=qt[i]]
            for part,values in [(solo,solo_std),(past,past_std)]:
                index=diag.engine.EventIndex(part['stay_idx'],part['t'],part['value'])
                lo,hi=index.bounds([qs[i]],[qt[i]],window=24);values.append(index.std(lo,hi)[0])
        for label,values in [('all_validation',all_std),('same_patient_full_history',solo_std),('same_patient_past_only',past_std)]:
            values=np.asarray(values);controls.append({'variant':label,'constant_real_windows':len(selected),
                'reported_nonzero_std':int((values!=0).sum()),'max_spurious_std':float(np.max(np.abs(values))) if len(values) else None})
        pd.DataFrame(controls).to_csv(out/'same_window_context_dependence.csv',index=False)
        print('Computing window-local arithmetic with static-precision controls.',flush=True)
        service,_=context.service(q,0)
        Y=diag.local_compute()(ev,context.static,service,qs,qt,context.config,names)
        Z=Y.copy();Z[:,j]=X[:,j];record('window_local_saved_static_ratio_control',Z);del Z
        Y[restored,j]=ratio[restored];record('window_local_pre_icu_static_precision_restored',Y)
        if not all(diag.sha256(p)==v for p,v in keep.items()):raise RuntimeError('Original record modified')
        diag.write_json(out/'cause_control_manifest.json',{'status':'complete','elapsed_seconds':time.monotonic()-start,
            'script_sha256':diag.sha256(Path(__file__)),'original_records_unchanged':True,'test_used':False,
            'model_retrained':False,'criteria_relaxed':False,'candidate_reselected':False})
        print('Cause controls complete; all original records unchanged.',flush=True)

if __name__=='__main__':main()
