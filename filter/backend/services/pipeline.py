import json
import uuid
from pathlib import Path
import pandas as pd
from backend.services.safety import validate_paths
from backend.services.io import small_table,find_table,dates
from backend.services.events import extract,read_patient_partition
from backend.processing.cohort import build_cohort,count_step,IDS
from backend.processing.aki_labels import label
from backend.processing.features import aggregate
from backend.processing.leakage import check
from backend.processing.split import patient_split
from backend.processing.missing import preprocess

def run(c,progress=lambda x:None):
    raw,work,out=validate_paths(c)
    if not c.methods_confirmed or c.baseline=='unselected' or c.followup=='unselected' or c.exclusions=='unselected':
        raise ValueError('Select and confirm baseline, follow-up and reviewed exclusion strategy.')
    if not c.variables: raise ValueError('Select at least one variable.')
    # Validate chosen mappings against dictionaries before any large event scan.
    for source,table in [('CHARTEVENTS','D_ITEMS'),('LABEVENTS','D_LABITEMS')]:
        dictionary=small_table(raw,table,c.chunk_size)
        required={v.itemid for v in c.variables if v.source==source}
        if source=='LABEVENTS': required.add(c.creatinine_itemid)
        if not required<=set(dictionary.ITEMID): raise ValueError(table+': configured ITEMID not found.')
    names={}
    for v in c.variables:
        if v.name in names and names[v.name]!=v.unit.lower(): raise ValueError('Merged variable ITEMIDs must use the same unit.')
        names[v.name]=v.unit.lower()
    cohort,audit=build_cohort(small_table(raw,'PATIENTS',c.chunk_size),small_table(raw,'ADMISSIONS',c.chunk_size),small_table(raw,'ICUSTAYS',c.chunk_size),c.followup)
    review=small_table(raw,'ELIGIBILITY_REVIEW',c.chunk_size)
    required=IDS+['ESRD','PRE_EXISTING_AKI','DIALYSIS_START','REVIEW_COMPLETE']
    if not set(required)<=set(review): raise ValueError('ELIGIBILITY_REVIEW requires IDs, ESRD, PRE_EXISTING_AKI, DIALYSIS_START, REVIEW_COMPLETE.')
    review=dates(review,['DIALYSIS_START'])
    if review.ICUSTAY_ID.duplicated().any(): raise ValueError('Duplicate eligibility review rows.')
    z=cohort.merge(review[required],on=IDS,how='left',validate='one_to_one')
    if not z.REVIEW_COMPLETE.eq(1).all() or not z.ESRD.isin([0,1]).all() or not z.PRE_EXISTING_AKI.isin([0,1]).all():
        raise ValueError('Every candidate stay requires complete reviewed exclusion evidence; unknown is not eligible.')
    for title,mask in [('Reviewed ESRD / ESKD exclusion',z.ESRD.eq(0)),('Reviewed pre-existing AKI exclusion',z.PRE_EXISTING_AKI.eq(0)),('Dialysis on or before 24h (common cohort)',z.DIALYSIS_START.isna()|(z.DIALYSIS_START>z.INTIME+pd.Timedelta(hours=24)))]:
        after=z.loc[mask.reindex(z.index)]
        audit.append(count_step(title,z,after)); z=after.copy()
    cohort=z
    run_id=uuid.uuid4().hex[:12]
    workspace=work/('run_'+run_id); destination=out/('run_'+run_id)
    workspace.mkdir(); destination.mkdir()
    try:
        parts,extraction=extract(raw,workspace,cohort,c,progress)
        records={h:[] for h in (8,12,24)}; statuses=[]; failures=0
        for bucket in range(32):
            events=read_patient_partition(workspace,bucket)
            for row in cohort.loc[cohort.SUBJECT_ID.astype('int64')%32==bucket].itertuples():
                e=events.loc[events.SUBJECT_ID==row.SUBJECT_ID]
                cr=e.loc[(e.source=='LABEVENTS')&(e.ITEMID==c.creatinine_itemid)]
                for h in records:
                    value,reason=label(cr,row.INTIME,h,c)
                    statuses.append({'SUBJECT_ID':row.SUBJECT_ID,'ICUSTAY_ID':row.ICUSTAY_ID,'prediction_hours':h,'AKI_48H':value,'reason':reason})
                    f,used=aggregate(e,row.INTIME,h,c.variables)
                    result=check(used,row.INTIME+pd.Timedelta(hours=h),f.keys())
                    failures+=int(result['status']=='FAIL')
                    records[h].append({k:getattr(row,k) for k in IDS}|f|{'AKI_48H':value,'label_reason':reason})
            progress({'stage':'Features and labels','partitions_complete':bucket+1,'partitions_total':32})
        status=pd.DataFrame(statuses,columns=['SUBJECT_ID','ICUSTAY_ID','prediction_hours','AKI_48H','reason'])
        status.to_parquet(destination/'label_status.parquet',index=False)
        bad=set(status.loc[status.AKI_48H.isna(),'ICUSTAY_ID'])
        common=cohort.loc[~cohort.ICUSTAY_ID.isin(bad)]
        audit.append(count_step('Common cohort: reliable labels and no pre-prediction AKI at all three times',cohort,common))
        audit.append(count_step('Final cohort',common,common))
        assignment=patient_split(common.SUBJECT_ID,c.train,c.validation,c.seed)
        assignment.to_parquet(destination/'split_assignment.parquet',index=False)
        split_check=check(pd.DataFrame(),None,[],assignment)
        if failures or split_check['status']=='FAIL': raise ValueError('Leakage checks failed; datasets not exported.')
        datasets={}; missing={}; preprocessing={}; features=[]
        for h,rows in records.items():
            d=pd.DataFrame(rows)
            if not len(d):
                d=pd.DataFrame(columns=IDS+['AKI_48H','label_reason'])
            d=d.loc[d.ICUSTAY_ID.isin(common.ICUSTAY_ID)].merge(assignment,on='SUBJECT_ID',how='left',validate='many_to_one')
            d['AKI_48H']=d.AKI_48H.astype('Int8')
            features=[k for k in d if k not in IDS+['AKI_48H','label_reason','split']]
            d.to_parquet(destination/f'dataset_{h}h.parquet',index=False)
            if c.csv_export: d.to_csv(destination/f'dataset_{h}h.csv',index=False)
            if len(d) and (d['split']=='train').any():
                processed,missing[h],preprocessing[h]=preprocess(d,features,c.missing,c.missing_indicator)
                processed.to_parquet(destination/f'dataset_{h}h_preprocessed.parquet',index=False)
            datasets[h]={'rows':len(d),'positives':int(d.AKI_48H.sum())}
        clean_config=c.model_dump(); clean_config['raw_dir']='[local raw directory]'; clean_config['workspace_dir']='[local workspace]'; clean_config['output_dir']='[local output]'
        report={'software_version':'1.0.0','run_id':run_id,'source':'Local MIMIC-III v1.4-compatible files; no patient records in report','config':clean_config,'cohort':audit,'extraction':extraction,'datasets':datasets,'feature_list':features,'missing':missing,'preprocessing':preprocessing,'label_status_counts':status.groupby(['prediction_hours','reason']).size().rename('count').reset_index().to_dict('records'),'split_sizes':assignment['split'].value_counts().to_dict(),'leakage':{'status':'PASS','future_feature_failures':failures,'patient_split':split_check},'warnings':['Research preprocessing only; not a clinical decision tool.','Baseline and negative surveillance rules are operational research choices requiring review.','Common cohort excludes AKI before 24h and all indeterminate labels, which can induce selection bias.','Hospital follow-up does not guarantee absence of AKI; negative labels require selected SCr surveillance coverage.']}
        if c.availability=='charttime_proxy': report['warnings'].append('WARNING: LABEVENTS lacks reliable result-availability timestamps; CHARTTIME is an explicitly accepted proxy, not proof of real-time availability.')
        report['label_definition']='SCr rise >=0.3 mg/dL versus a strictly earlier value within 48h, OR >=1.5 times selected strictly prior 7-day baseline. Outcome is (prediction, prediction+48h]; features are [INTIME,prediction]. No urine output.'
        (destination/'pipeline_config.json').write_text(json.dumps(clean_config,indent=2),encoding='utf-8')
        (destination/'pipeline_report.md').write_text('# AKI Prediction Data Processing System\n\n```json\n'+json.dumps(report,indent=2)+'\n```\n',encoding='utf-8')
        progress({'stage':'Complete'})
        return report
    except Exception:
        # Incomplete run folders are retained locally for explicit user-controlled cleanup.
        (destination/'INCOMPLETE.txt').write_text('Run incomplete. Do not use this run for research. No exception details recorded.',encoding='utf-8')
        raise
