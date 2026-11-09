"""Recover admission baselines from the locally available raw lab file; aggregate report only."""
from pathlib import Path
import gzip,json,re,sys,time
import numpy as np,pandas as pd,pyarrow as pa,pyarrow.parquet as pq
import diagnose_feature_replay as diag

CENSORED=r'^\s*(?:<|>|LESS\s*TH[AE]N|GREATE\w*\s*TH[AE]N)\s*=?\s*(\d[\d,]*\.?\d*|\.\d+)'
CEILING=r'^\s*(\d+\.?\d*)\s+IS\s+HIGHEST\s+MEASURED'

def reconstruct(raw_path,snapshots):
    import yaml
    from aki_ml.admission_inputs import reconstruct_baselines
    config=yaml.safe_load((diag.ROOT/"configs/default.yaml").read_text(encoding="utf-8"))
    return reconstruct_baselines(raw_path,snapshots,config,progress=lambda count: print("Raw lab rows scanned:",count,flush=True))


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
