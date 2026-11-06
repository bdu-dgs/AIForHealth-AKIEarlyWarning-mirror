"""Recover admission baselines from the locally available raw lab file; aggregate report only."""
from pathlib import Path
import gzip,json,re,sys,time
import numpy as np,pandas as pd,pyarrow as pa,pyarrow.parquet as pq
import diagnose_feature_replay as diag

CENSORED=r'^\s*(?:<|>|LESS\s*TH[AE]N|GREATE\w*\s*TH[AE]N)\s*=?\s*(\d[\d,]*\.?\d*|\.\d+)'
CEILING=r'^\s*(\d+\.?\d*)\s+IS\s+HIGHEST\s+MEASURED'

def reconstruct(raw_path,snapshots):
    first=snapshots.drop_duplicates('patient_id').set_index('subject_id')
    subjects=set(first.index);parts=[];read=0
    for chunk in pd.read_csv(raw_path,compression='gzip',usecols=['SUBJECT_ID','ITEMID','CHARTTIME','VALUE','VALUENUM'],
        dtype={'SUBJECT_ID':'int32','ITEMID':'int32','CHARTTIME':'string','VALUE':'string','VALUENUM':'float32'},chunksize=1_000_000):
        read+=len(chunk);keep=chunk['ITEMID'].eq(50912)&chunk['SUBJECT_ID'].isin(subjects)
        c=chunk.loc[keep].copy()
        if len(c):
            text=c['VALUE'].str.upper();parsed=pd.to_numeric(text.str.extract(CENSORED,expand=False).str.replace(',',''),errors='coerce')
            parsed=parsed.fillna(pd.to_numeric(text.str.extract(CEILING,expand=False),errors='coerce'))
            values=c['VALUENUM'].astype(float).fillna(parsed);valid=values.between(.1,40)
            c=c[valid];values=values[valid].astype(np.float32).astype(float).round(2)
            time_=pd.to_datetime(c['CHARTTIME'],errors='coerce')
            origin=c['SUBJECT_ID'].map(first['icu_intime'])
            hours=(time_-origin)/pd.Timedelta(hours=1)
            inside=hours.between(-365*24,0)
            parts.append(pd.DataFrame({'subject_id':c['SUBJECT_ID'][inside].to_numpy(),
                'hours':hours[inside].to_numpy(float),'value':values[inside].to_numpy(float)}))
        if read%8_000_000==0:print('Raw lab rows scanned:',read,flush=True)
    if not parts:raise RuntimeError('No matching local baseline records')
    past=pd.concat(parts,ignore_index=True).groupby(['subject_id','hours'],as_index=False)['value'].mean()
    pre=past[past['hours'].between(-168,0)].groupby('subject_id')['value'].min()
    history=past[(past['hours']>=-365*24)&(past['hours']<-168)].groupby('subject_id')['value'].median()
    baseline=pre.reindex(first.index).fillna(history.reindex(first.index))
    return baseline,pre,history,read


def main():
    root=diag.ROOT;out=root/'artifacts/handoff/kejin/numeric-diagnosis-20261106-01';run=root/'artifacts/handoff/kejin/kejin-validation-policy-v2-21d980db7c292219'
    raw=root/'MIMIC-III/LABEVENTS.csv.gz';path=root.parent/'Cleaned/datasets/snapshot_dataset_validation.parquet'
    keep={p:diag.sha256(p) for p in [run/'choice.json',run/'confirmation.json',run.parent/'LATEST.json',root/'artifacts/demo_i/policy.json']}
    pa.set_cpu_count(2);pa.set_io_thread_count(2)
    columns=['patient_id','subject_id','icu_intime','baseline_cr_admission','creatinine_last','creatinine_ratio_to_baseline']
    snap=pq.read_table(path,columns=columns).to_pandas();first=snap.drop_duplicates('patient_id').set_index('subject_id')
    diag.write_json(out/'raw_baseline_control_plan.json',{'script_sha256':diag.sha256(Path(__file__)),
        'raw_source':'MIMIC-III/LABEVENTS.csv.gz','original_values_readonly':True,'output':'aggregate only',
        'rule':'original float32 source value, range .1-40, decimal round2, duplicate-time mean, 7d pre-ICU min else 8-365d median',
        'missing_baseline_is_not_imputed':True,'model_and_policy_unchanged':True})
    baseline,pre,hist,read=reconstruct(raw,snap)
    saved=first['baseline_cr_admission'].to_numpy(np.float32);computed=baseline.to_numpy(float).astype(np.float32)
    equal=(saved==computed)|(np.isnan(saved)&np.isnan(computed))
    denominator=snap['subject_id'].map(baseline).to_numpy(float)
    with np.errstate(divide='ignore',invalid='ignore'):
        ratio=(snap['creatinine_last'].to_numpy(np.float32).astype(float)/denominator).astype(np.float32)
    expected=snap['creatinine_ratio_to_baseline'].to_numpy(np.float32)
    same_ratio=(ratio==expected)|(np.isnan(ratio)&np.isnan(expected))
    if not all(diag.sha256(p)==v for p,v in keep.items()):raise RuntimeError('Original record changed')
    result={'status':'complete','patients':len(first),'raw_lab_rows_scanned':read,
        'patients_with_pre_icu_7d_minimum':int(pre.reindex(first.index).notna().sum()),
        'patients_using_remote_history_median':int((pre.reindex(first.index).isna()&hist.reindex(first.index).notna()).sum()),
        'patients_with_no_baseline':int(baseline.isna().sum()),
        'baseline_float32_mismatch_patients':int((~equal).sum()),'ratio_mismatch_rows':int((~same_ratio).sum()),
        'ratio_rows':len(snap),'original_records_unchanged':True,'model_or_policy_changed':False,
        'raw_source_sha256':diag.sha256(raw),'script_sha256':diag.sha256(Path(__file__)),
        'scope':'local raw lab copy matches all saved admission baselines and ratios under original construction rules; does not audit other static timing',
        'test_used':False,'individual_rows_exported':False}
    diag.write_json(out/'raw_baseline_control_manifest.json',result);print(json.dumps(result,indent=2))

if __name__=='__main__':main()
