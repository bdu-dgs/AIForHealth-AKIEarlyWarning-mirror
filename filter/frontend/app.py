import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.services.safety import install_network_guard
install_network_guard()
import httpx
import streamlit as st

st.set_page_config(page_title='AKI Prediction Data Processing System',layout='wide')
st.title('AKI Prediction Data Processing System')
st.caption('MIMIC-III v1.4 · Local research workspace · 8h / 12h / 24h → next 48h')
st.info('Fully local processing. The code folder is in OneDrive; real data, caches, and outputs must use separate local folders that are not synced. The interface shows summary information only.')
PAGES=['Data Setup','Data Inspection','Cohort Builder','Variable Selection','AKI Label Builder','Feature Engineering','Missing Data','Leakage Check','Dataset Builder','Train / Validation / Test Split','Pipeline Summary']
page=st.sidebar.radio('Navigation',PAGES)

if 'config' not in st.session_state:
    from backend.models.config import Config
    st.session_state.config=Config(raw_dir='',workspace_dir='',output_dir='').model_dump()
c=st.session_state.config

def api(path,data=None):
    try:
        with httpx.Client(base_url='http://127.0.0.1:8000',headers={'x-aki-token':os.environ.get('AKI_API_TOKEN','')},trust_env=False,timeout=3600) as client:
            r=client.post(path,json=data) if data is not None else client.get(path)
            if r.status_code>=400:
                detail=r.json().get('detail','Request failed')
                st.error(detail if isinstance(detail,str) else 'Invalid configuration; check input fields.')
                return None
            return r.json()
    except Exception:
        st.error('Local backend unavailable. Start it with start.bat or python run.py.')
        return None

def report():
    s=api('/status')
    if s:
        st.caption('Pipeline status: '+s['status'])
        if s.get('error'): st.error(s['error'])
        if s.get('progress'): st.json(s['progress'])
        return s.get('report')

if page=='Data Setup':
    st.subheader('Separate code from patient data')
    for key,title in [('raw_dir','Raw Data Directory — read only'),('workspace_dir','Processing Workspace Directory'),('output_dir','Output Directory')]:
        c[key]=st.text_input(title,value=c[key])
    c['local_storage_confirmed']=st.checkbox('I confirm all three folders are local and not synced by any cloud drive, backup, or sync tool.',value=c['local_storage_confirmed'])
    st.warning('Data paths containing OneDrive / Dropbox / Google Drive / iCloud are blocked; this checkbox cannot bypass that. Symbolic links and nested folders are also checked.')
    c['chunk_size']=st.number_input('Rows per chunk',100,500000,c['chunk_size'],1000)
    if st.button('Validate paths and scan tables',type='primary'):
        data=api('/setup',c)
        if data is not None: st.session_state.scan=data
    for table in st.session_state.get('scan',[]):
        with st.expander(table['table']+(' — found' if table['found'] else ' — missing')): st.json(table)
elif page=='Data Inspection':
    st.subheader('Aggregate inspection — no raw record preview')
    table=st.selectbox('Table',['PATIENTS','ADMISSIONS','ICUSTAYS','CHARTEVENTS','LABEVENTS','D_ITEMS','D_LABITEMS'])
    st.caption('Full inspection streams all rows and may take time. CSV row counts are exact after this explicit scan; ID counts are capped and labeled. No complete large table is loaded.')
    if st.button('Run aggregate inspection'):
        result=api('/inspect/'+table,c)
        if result: st.json(result)
elif page=='Cohort Builder':
    st.write('Adult age ≥18 → first recorded ICU stay per patient → common follow-up → reviewed ESRD / prior AKI / dialysis exclusions → reliable labels at all three prediction times.')
    options=['unselected','icu_72h','hospital_72h']
    c['followup']=st.selectbox('Follow-up definition',options,index=options.index(c['followup']))
    c['exclusions']=st.selectbox('Exclusion evidence',['unselected','reviewed_file'],index=['unselected','reviewed_file'].index(c['exclusions']))
    st.warning('Prepare ELIGIBILITY_REVIEW.csv or .parquet in the Raw Data Directory. Runs stop when no review evidence is present. This version does not claim to identify ESRD or dialysis reliably from the seven tables alone.')
    st.code('SUBJECT_ID,HADM_ID,ICUSTAY_ID,ESRD,PRE_EXISTING_AKI,DIALYSIS_START,REVIEW_COMPLETE')
    st.write('ESRD and PRE_EXISTING_AKI are 0/1; REVIEW_COMPLETE=1 means medical history and dialysis evidence were reviewed. DIALYSIS_START is the first known dialysis time; leave it empty only when review confirmed no dialysis. The common cohort excludes dialysis at or before 24h.')
    r=report()
    if r: st.dataframe(r['cohort'],hide_index=True)
elif page=='Variable Selection':
    st.write('Search the local dictionaries. Multiple ITEMIDs with the same name are merged into one variable; their units must match.')
    st.caption('Suggested: Heart Rate, Respiratory Rate, SpO2, Temperature, SBP, DBP, MAP, GCS; Creatinine, BUN, Sodium, Potassium, Bicarbonate, Chloride, Glucose, WBC, Hemoglobin, Platelets.')
    if st.button('Load local dictionaries'):
        d=api('/variables',c)
        if d is not None: st.session_state.dictionary=d
    query=st.text_input('Search variable label or ITEMID')
    dictionary=st.session_state.get('dictionary',[])
    matches=[x for x in dictionary if query.lower() in (x['label']+' '+str(x['ITEMID'])).lower()][:200]
    if matches:
        selected=st.selectbox('Dictionary entry',matches,format_func=lambda x:f"{x['source']} · {x['ITEMID']} · {x['label']} · {x['unit']}")
        name=st.text_input('Feature name (letters, numbers, underscores)',value='HR')
        unit=st.text_input('Expected unit (required; inspect dictionary and methods)',value=selected['unit'] if selected['unit'] not in ('nan','None') else '')
        if st.button('Add selected ITEMID'):
            import re
            if not re.fullmatch('[A-Za-z][A-Za-z0-9_]*',name) or not unit.strip(): st.error('Enter a valid feature name and explicit unit.')
            elif any(v['source']==selected['source'] and v['itemid']==selected['ITEMID'] for v in c['variables']): st.error('ITEMID already selected.')
            else: c['variables'].append({'name':name,'itemid':selected['ITEMID'],'source':selected['source'],'unit':unit}); st.rerun()
    for i,v in enumerate(list(c['variables'])):
        if not st.checkbox(f"{v['name']} | {v['source']} | {v['itemid']} | {v['unit']}",value=True,key='variable_'+str(i)):
            c['variables'].pop(i); st.rerun()
elif page=='AKI Label Builder':
    st.write('Serum creatinine only: rise ≥0.3 mg/dL within 48h, or ≥1.5 × a strictly earlier baseline within 7 days. No urine-output criterion.')
    options=['unselected','prior_7d_min','prior_7d_first']
    c['baseline']=st.selectbox('Baseline strategy — explicit research choice',options,index=options.index(c['baseline']))
    st.caption('prior_7d_min: minimum strictly earlier SCr in 7 days; prior_7d_first: earliest strictly earlier SCr in 7 days. Both are rolling operational baselines, not an established pre-illness baseline.')
    c['creatinine_itemid']=st.number_input('Serum creatinine LABEVENTS ITEMID',1,999999,c['creatinine_itemid'])
    c['negative_min_measurements']=st.number_input('Minimum outcome measurements for negative label',1,100,c['negative_min_measurements'])
    c['negative_max_gap_hours']=st.number_input('Maximum unmeasured gap in negative outcome window (hours)',1.,48.,c['negative_max_gap_hours'])
    opts=['charttime_proxy','require_storetime']
    c['availability']=st.selectbox('Measurement availability policy',opts,index=opts.index(c['availability']))
    st.warning('LABEVENTS usually has no result-available time. The CHARTTIME proxy is not the same as real-time clinical availability; strict STORETIME mode blocks processing when that field is missing. Negative-label coverage thresholds also need methodological confirmation.')
    c['methods_confirmed']=st.checkbox('I have confirmed the baseline, follow-up, exclusion evidence, negative-label coverage rules, and time-availability assumptions.',value=c['methods_confirmed'])
elif page=='Feature Engineering':
    st.table([{'Prediction':f'{h}h','Feature period':f'[INTIME, INTIME+{h}h]','Outcome period':f'(INTIME+{h}h, INTIME+{h+48}h]'} for h in (8,12,24)])
    st.write('Each variable outputs latest, mean, min, max, range, count, std (sample), and slope (unit/hour). Slope is missing with fewer than two distinct time points. Measurements at the prediction time belong to the features; the outcome starts after it, so the boundaries never overlap.')
    st.write('When STORETIME exists, availability uses max(CHARTTIME, STORETIME); late-entered measurements never enter earlier features. For multi-ITEMID variables such as Temperature/GCS, merge only measurements with the same unit and clinical meaning.')
elif page=='Missing Data':
    options=['median','mean','most_frequent','unknown','drop_feature']
    c['missing']=st.selectbox('Preprocessing strategy',options,index=options.index(c['missing']))
    c['missing_indicator']=st.checkbox('Add missing indicators',value=c['missing_indicator'])
    st.write('Imputation statistics are learned on train only; the raw dataset keeps missing values and a separate preprocessed file is saved. No patients are dropped. Numeric features fully missing in train stay missing and are reported. unknown converts numeric columns into categorical strings.')
    r=report()
    if r:
        for h,rows in r['missing'].items():
            st.write(h+'h'); st.dataframe(rows)
elif page=='Leakage Check':
    st.write('Automatically checks for future CHARTTIME / AVAILABLETIME, outcome-information fields, and patients crossing splits. DISCHTIME is used only for follow-up screening and never enters the features.')
    r=report()
    if r: st.json(r['leakage']); st.warning('\n'.join(r['warnings']))
elif page=='Train / Validation / Test Split':
    c['train']=st.number_input('Train fraction',.01,.98,c['train'],.01)
    c['validation']=st.number_input('Validation fraction',.01,.98,c['validation'],.01)
    st.write(f"Test fraction: {1-c['train']-c['validation']:.2f}")
    c['seed']=st.number_input('Random seed',0,2147483647,c['seed'])
    st.write('Assignment is by SUBJECT_ID, and all three datasets share the same assignment. Small samples are split by rounding down, so validation/test may be empty; check the actual counts.')
    r=report()
    if r: st.json(r['split_sizes'])
elif page=='Dataset Builder':
    st.write('Runs all configured steps. Outputs go to a unique run subfolder of the Output Directory; raw files are never overwritten. Undeterminable labels are written to label_status.parquet; the final common cohort keeps only patients determinable in all three windows.')
    c['csv_export']=st.checkbox('Also export CSV',value=c['csv_export'])
    if st.button('Run complete local pipeline',type='primary'): api('/pipeline',c)
    st.button('Refresh progress')
    r=report()
    if r: st.json(r['datasets']); st.success('Run complete. Reports and datasets saved to the configured local output directory.')
elif page=='Pipeline Summary':
    st.button('Refresh summary')
    r=report()
    if r:
        st.json(r)
        st.info('pipeline_report.md and pipeline_config.json are saved locally in output/run_<id>. Actual folder paths in the config are redacted. Re-run after changing the interface configuration; the report always reflects the last completed run.')
    else: st.write('Complete a pipeline run to generate the Methods / Experiments summary.')
