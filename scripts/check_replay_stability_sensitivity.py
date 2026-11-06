"""Fixed-candidate cutoff robustness using the independent diagnostic arithmetic."""
from pathlib import Path
import json
import time
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits
import diagnose_feature_replay as diag
from aki_eval.policies import stability_grid
from verify_raw_admission_baselines import reconstruct


def main():
    root=diag.ROOT;run=root/'artifacts/handoff/kejin/kejin-validation-policy-v2-21d980db7c292219'
    out=root/'artifacts/handoff/kejin/numeric-diagnosis-20261106-01';start=time.monotonic()
    records={p:diag.sha256(p) for p in [run/'prespec.json',run/'choice.json',run/'confirmation.json',run.parent/'LATEST.json',root/'artifacts/demo_i/policy.json']}
    diag.write_json(out/'full_static_cutoff_control_plan.json',{'script_sha256':diag.sha256(Path(__file__)),
        'purpose':'sensitivity of the already sealed candidate only; not reselection',
        'sample':'original 800 selection / 400 confirmation patient samples and original seeds',
        'scenarios':[0,1,2],'origin_and_target_windows':'unchanged',
        'arithmetic':'native patient times and window-local statistics; original high-precision admission baseline reconstructed from local raw labs for all validation patients',
        'held_ratio_controls':'none for delayed inputs; denominator is constant admission input',
        'gates':'original fixed stability gates, both saved-snapshot and zero-lag references'})
    rows=[];changes=[];numeric=[]
    with threadpool_limits(limits=2):
        cfg,policy,names,frame,q,whole,pipe,paths=diag.load_context(run)
        calculate=diag.local_compute();h=cfg['alert_horizon_h'];predict=lambda X:pipe.predict(X,h,'platt').astype(float)
        full_baseline,_,_,_=reconstruct(root/'MIMIC-III/LABEVENTS.csv.gz',whole.snapshots)
        for role,offset in [('selection',31),('confirmation',32)]:
            pool=whole.snapshots[whole.snapshots['evaluation_role']==role]
            pool=diag.sample_patients(pool,cfg['cutoff'][role+'_patient_cap'],cfg['seed']+offset)
            context=diag.ReplayData.load(paths['events'],pool,whole.config,names)
            mask=frame['patient_id'].isin(pool['patient_id']).to_numpy();ref=frame.loc[mask].reset_index(drop=True)
            query=q.loc[mask].reset_index(drop=True);ev=context.events
            # Reconstruct the original input precision, including 8-365d history medians.
            mapping=pool.drop_duplicates('stay_id').set_index('stay_id')['subject_id']
            ordered_stays=pd.Series({idx:stay for stay,idx in context.stay_lookup.items()}).reindex(context.static.index)
            denominator=ordered_stays.map(mapping).map(full_baseline).to_numpy(float)
            static=context.static.copy();saved=static['baseline_cr_admission'].to_numpy(np.float32)
            reconstructed=denominator.astype(np.float32)
            if not ((saved==reconstructed)|(np.isnan(saved)&np.isnan(reconstructed))).all():
                raise RuntimeError('Raw admission baseline differs from saved inputs')
            static['baseline_cr_admission']=denominator
            qs=context.query_stays(query);qt=query['hours_since_icu'].to_numpy(float);variants={}
            for lag in [0,1,2]:
                print(role,'independent arithmetic fixed-origin lag',lag,flush=True)
                X=np.empty((len(query),len(names)),np.float32);ok=np.zeros(len(query),bool)
                service,service_known=context.service(query,lag)
                if lag==0:
                    X[:]=calculate(ev,static,service,qs,qt,context.config,names);ok=context._covered(ev,qs,qt)&service_known
                else:
                    for hour in np.unique(qt):
                        idx=np.flatnonzero(qt==hour);part=ev[ev['t']<=hour-lag]
                        X[idx]=calculate(part,static,service[idx],qs[idx],qt[idx],context.config,names)
                        ok[idx]=context._covered(part,qs[idx],qt[idx])&service_known[idx]
                risk=predict(X);risk[~ok]=np.nan;variants[lag]=risk
                changes.append({'role':role,**diag.risk_comparison(ref['risk'].to_numpy(float),risk,'native_arithmetic_lag_'+str(lag))})
            rule=diag.AlertRule(name='cool12',cooldown_hours=12)
            a=stability_grid(ref,variants,[rule],[.23]).assign(role=role,reference_basis='stored_snapshot')
            zero=ref.copy();zero['risk']=variants[0]
            b=stability_grid(zero,{k:v for k,v in variants.items() if k!=0},[rule],[.23]).assign(role=role,reference_basis='zero_lag_event_replay')
            result=pd.concat([a,b],ignore_index=True);rows.extend(result.to_dict('records'))
            pd.DataFrame(rows).to_csv(out/'full_static_cutoff_stability.csv',index=False)
            pd.DataFrame(changes).to_csv(out/'full_static_cutoff_risk_changes.csv',index=False)
            numeric.append({'role':role,'patients':ref['patient_id'].nunique(),
                'worst_warning_flip':result['warning_flip_fraction'].max(),
                'worst_patient_alert_flip':result['patient_alert_flip_fraction'].max(),
                'min_pair_coverage':result['pair_coverage'].min(),
                'passes_original_stability_gates':bool((result['warning_flip_fraction']<=.1).all() and
                    (result['patient_alert_flip_fraction']<=.1).all() and (result['pair_coverage']>=.85).all())})
        grid=pd.read_csv(run/'policy_grid_validation_selection.csv');base=grid[(grid['candidate']=='baseline')&(grid['threshold']==.21)].iloc[0]
        c=cfg['selection_rule']
        point_ok=(grid['event_detection_rate']>=max(c['min_event_detection_rate'],base['event_detection_rate']-c['max_detection_loss'])) & \
            (grid['lead_time_median_h']>=max(c['min_median_lead_h'],base['lead_time_median_h']-c['max_median_lead_loss_h'])) & \
            (grid['alert_ppv']>=c['min_ppv']) & (grid['alerts_per_patient_day']<=c['max_alerts_per_patient_day']) & \
            (grid['non_aki_patients_alerted']<=base['non_aki_patients_alerted']+c['max_non_target_patient_alert_increase'])
        feasible=grid[point_ok].sort_values('false_alerts_per_patient_day')
        best=feasible.iloc[0]
        unchanged=best['candidate']=='cool12' and best['threshold']==.23 and all(x['passes_original_stability_gates'] for x in numeric)
        if not all(diag.sha256(p)==v for p,v in records.items()):raise RuntimeError('Sealed input changed')
        diag.write_json(out/'full_static_cutoff_control_manifest.json',{'status':'complete','roles':numeric,
            'original_point_feasible_configurations':len(feasible),'original_point_best_candidate':str(best['candidate']),
            'original_point_best_threshold':float(best['threshold']),
            'original_selection_unchanged_when_only_cutoff_arithmetic_is_corrected':bool(unchanged),
            'interpretation':'uses original saved-snapshot point metrics and original fixed candidate; not a new study or retuning',
            'test_used':False,'original_records_unchanged':True,'script_sha256':diag.sha256(Path(__file__)),
            'elapsed_seconds':time.monotonic()-start})
        print('Fixed-candidate cutoff diagnosis complete.',flush=True)

if __name__=='__main__':main()
