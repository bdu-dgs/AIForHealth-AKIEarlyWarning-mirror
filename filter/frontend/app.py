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
st.info('完全本地处理。代码目录位于 OneDrive；真实数据、缓存和输出必须使用独立的非同步本地目录。界面仅显示汇总信息。')
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
        st.error('本地后端不可用，请使用 start.bat 或 python run.py 启动。')
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
    c['local_storage_confirmed']=st.checkbox('我确认三个目录均为本地目录，未被任何云盘、备份或同步工具同步。',value=c['local_storage_confirmed'])
    st.warning('含 OneDrive / Dropbox / Google Drive / iCloud 的数据路径会被阻止；确认框不能绕过此限制。符号链接和目录嵌套也会检查。')
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
    st.warning('需要在 Raw Data Directory 中准备 ELIGIBILITY_REVIEW.csv 或 .parquet。没有审核依据时运行将停止。此版本不声称仅凭七张表自动可靠识别 ESRD 或透析。')
    st.code('SUBJECT_ID,HADM_ID,ICUSTAY_ID,ESRD,PRE_EXISTING_AKI,DIALYSIS_START,REVIEW_COMPLETE')
    st.write('ESRD、PRE_EXISTING_AKI 为 0/1；REVIEW_COMPLETE=1 表示已审核病史与透析证据。DIALYSIS_START 为首次已知透析时间；只有经审核确认没有透析时才可留空。共同 cohort 排除 24h 及之前透析。')
    r=report()
    if r: st.dataframe(r['cohort'],hide_index=True)
elif page=='Variable Selection':
    st.write('从本地字典搜索。名称相同的多个 ITEMID 合并为一个变量，单位必须一致。')
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
    st.warning('LABEVENTS 通常没有结果可获得时间。CHARTTIME proxy 不等于临床实时可用性；严格 STORETIME 模式会在缺少该字段时阻止处理。负标签覆盖阈值也需要方法学确认。')
    c['methods_confirmed']=st.checkbox('我已确认 baseline、随访、排除证据、阴性标签覆盖规则和时间可获得性假设。',value=c['methods_confirmed'])
elif page=='Feature Engineering':
    st.table([{'Prediction':f'{h}h','Feature period':f'[INTIME, INTIME+{h}h]','Outcome period':f'(INTIME+{h}h, INTIME+{h+48}h]'} for h in (8,12,24)])
    st.write('每个变量输出 latest, mean, min, max, range, count, std (sample), slope (unit/hour)。不足两个不同时间点时 slope 缺失。预测时刻的测量属于特征；结局从其后开始，避免边界重叠。')
    st.write('STORETIME 存在时使用 max(CHARTTIME, STORETIME) 判断可获得性；晚录入测量不会进入更早特征。Temperature/GCS 等多 ITEMID 请只合并单位及临床含义相同的测量。')
elif page=='Missing Data':
    options=['median','mean','most_frequent','unknown','drop_feature']
    c['missing']=st.selectbox('Preprocessing strategy',options,index=options.index(c['missing']))
    c['missing_indicator']=st.checkbox('Add missing indicators',value=c['missing_indicator'])
    st.write('仅用 train 学习填补统计；原始 dataset 保留缺失，另存 preprocessed 文件。不删除患者。train 中全缺失的数值特征保留缺失并报告。unknown 会把数值列转成类别字符串。')
    r=report()
    if r:
        for h,rows in r['missing'].items():
            st.write(h+'h'); st.dataframe(rows)
elif page=='Leakage Check':
    st.write('自动检查未来 CHARTTIME / AVAILABLETIME、结局信息字段、患者跨 split。DISCHTIME 仅用于随访筛选，绝不进入特征。')
    r=report()
    if r: st.json(r['leakage']); st.warning('\n'.join(r['warnings']))
elif page=='Train / Validation / Test Split':
    c['train']=st.number_input('Train fraction',.01,.98,c['train'],.01)
    c['validation']=st.number_input('Validation fraction',.01,.98,c['validation'],.01)
    st.write(f"Test fraction: {1-c['train']-c['validation']:.2f}")
    c['seed']=st.number_input('Random seed',0,2147483647,c['seed'])
    st.write('按 SUBJECT_ID 分配，三套数据使用同一份 assignment。小样本按比例向下取整，可能出现空 validation/test，请查看实际数量。')
    r=report()
    if r: st.json(r['split_sizes'])
elif page=='Dataset Builder':
    st.write('运行所有已配置步骤。输出写到 Output Directory 的唯一 run 子目录；原始文件永不覆盖。不可判定的标签写入 label_status.parquet；最终共同 cohort 只保留三窗口都可判定的患者。')
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
        st.info('pipeline_report.md 和 pipeline_config.json 已保存在本地 output/run_<id>。配置中的实际目录已脱敏。更改界面配置后需重新运行，报告始终对应上次完成的运行。')
    else: st.write('Complete a pipeline run to generate the Methods / Experiments summary.')
