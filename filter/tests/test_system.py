import hashlib
import socket
import pandas as pd
import pytest
from backend.models.config import Config,Variable
from backend.processing.cohort import build_cohort,prediction_time
from backend.processing.features import aggregate,feature_events
from backend.processing.aki_labels import label,detect
from backend.processing.leakage import check
from backend.processing.split import patient_split
from backend.processing.missing import preprocess
from backend.services.safety import safe_path,validate_paths,install_network_guard
from backend.services.pipeline import run
from scripts.synthetic import generate

T=pd.Timestamp('2100-01-01')

def config(tmp_path):
    return Config(raw_dir=str(tmp_path/'raw'),workspace_dir=str(tmp_path/'workspace'),output_dir=str(tmp_path/'out'),local_storage_confirmed=True,baseline='prior_7d_min',followup='icu_72h',exclusions='reviewed_file',methods_confirmed=True,variables=[Variable(name='HR',itemid=220045,source='CHARTEVENTS',unit='bpm'),Variable(name='Creatinine',itemid=50912,source='LABEVENTS',unit='mg/dL')])

def events(hours,values):
    return pd.DataFrame({'CHARTTIME':[T+pd.Timedelta(hours=h) for h in hours],'AVAILABLETIME':[T+pd.Timedelta(hours=h) for h in hours],'VALUENUM':values,'variable':'HR'})

@pytest.mark.parametrize('h',[8,12,24])
def test_time_windows_and_future_exclusion(h):
    e=events([0,h,h+.001],[1,2,9999])
    f,used=aggregate(e,T,h,[Variable(name='HR',itemid=1,source='CHARTEVENTS',unit='bpm')])
    assert f['HR_max']==2 and f['HR_count']==2
    assert prediction_time(T,h)==T+pd.Timedelta(hours=h)
    assert check(used,prediction_time(T,h),f)['status']=='PASS'

def test_late_recording():
    e=events([1,2],[1,999]); e.loc[1,'AVAILABLETIME']=T+pd.Timedelta(hours=10)
    assert len(feature_events(e,T,8))==1

def test_feature_stats():
    f,_=aggregate(events([0,2,4],[1,5,9]),T,8,[Variable(name='HR',itemid=1,source='CHARTEVENTS',unit='bpm')])
    assert f['HR_slope']==pytest.approx(2) and f['HR_std']==4 and f['HR_range']==8

def test_first_icu_before_adult_filter(tmp_path):
    generate(tmp_path/'raw',4)
    raw=tmp_path/'raw'; p=pd.read_csv(raw/'PATIENTS.csv'); a=pd.read_csv(raw/'ADMISSIONS.csv'); s=pd.read_csv(raw/'ICUSTAYS.csv')
    later=s.iloc[[0]].copy(); later['ICUSTAY_ID']=999999; later['INTIME']='2100-01-01 01:00:00'
    d,audit=build_cohort(p,a,pd.concat([later,s]),'icu_72h')
    assert len(d)==4 and 999999 not in set(d.ICUSTAY_ID)

@pytest.mark.parametrize('h',[8,12,24])
def test_label_boundary(tmp_path,h):
    c=config(tmp_path)
    assert label(events([0,h+48],[1,1.5]),T,h,c)[0]==1
    assert label(events([0,h+48+.001],[1,1.5]),T,h,c)[0] is None
    assert label(events([0,h],[1,1.5]),T,h,c)[1]=='pre_existing_aki'

def test_absolute_and_ratio():
    assert detect(events([0,47],[2,2.3]),'prior_7d_min')
    assert not detect(events([0,49],[2,2.3]),'prior_7d_min')
    assert detect(events([0,100],[1,1.5]),'prior_7d_first')
    assert not detect(events([0,169],[1,1.5]),'prior_7d_first')
    assert not detect(events([0,0],[1,2]),'prior_7d_min')

def test_missing_outcome_is_not_negative(tmp_path):
    assert label(events([0,4],[1,1]),T,8,config(tmp_path))[0] is None

def test_patient_split():
    a=patient_split(list(range(100))*3)
    assert len(a)==100 and a['split'].value_counts().to_dict()=={'train':70,'validation':15,'test':15}
    pd.testing.assert_frame_equal(a,patient_split(range(100)))
    assert check(pd.DataFrame(),None,[],a)['status']=='PASS'

def test_leakage_detected():
    assert check(events([9],[1]),T+pd.Timedelta(hours=8),[])['status']=='FAIL'
    assert check(pd.DataFrame(),None,['DISCHTIME'])['status']=='FAIL'
    a=pd.DataFrame({'SUBJECT_ID':[1,1],'split':['train','test']})
    assert check(pd.DataFrame(),None,[],a)['status']=='FAIL'

@pytest.mark.parametrize('strategy',['median','mean','most_frequent','unknown','drop_feature'])
def test_imputation_train_only_raw_unchanged(strategy):
    d=pd.DataFrame({'x':[1.,3.,None,999.],'split':['train','train','test','test']}); original=d.copy(deep=True)
    out,_,_=preprocess(d,['x'],strategy,True)
    pd.testing.assert_frame_equal(d,original)
    if strategy in ('median','mean'): assert out.loc[2,'x']==2
    assert 'x_missing' in out

@pytest.mark.parametrize('cloud',['OneDrive','Dropbox','Google Drive','iCloud'])
def test_cloud_blocked(tmp_path,cloud):
    with pytest.raises(ValueError): safe_path(str(tmp_path/cloud/'data'))

def test_nested_paths_blocked(tmp_path):
    c=config(tmp_path); (tmp_path/'raw').mkdir(); c.output_dir=str(tmp_path/'raw'/'out')
    with pytest.raises(ValueError): validate_paths(c)

def test_end_to_end_raw_immutable(tmp_path):
    c=config(tmp_path); generate(c.raw_dir)
    files=list((tmp_path/'raw').iterdir()); before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    report=run(c)
    assert report['leakage']['status']=='PASS'
    assert report['datasets'][8]['rows']==40
    assert report['datasets'][24]['positives']==13
    destination=next((tmp_path/'out').iterdir())
    assignment=pd.read_parquet(destination/'split_assignment.parquet')
    for h in (8,12,24):
        d=pd.read_parquet(destination/f'dataset_{h}h.parquet')
        assert d.SUBJECT_ID.tolist()==pd.read_parquet(destination/'dataset_8h.parquet').SUBJECT_ID.tolist()
        assert d.merge(assignment,on='SUBJECT_ID').eval('split_x == split_y').all()
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    second=run(c); assert second['run_id']!=report['run_id']

def test_unconfirmed_methods_blocked(tmp_path):
    c=config(tmp_path); generate(c.raw_dir); c.methods_confirmed=False
    with pytest.raises(ValueError): run(c)

def test_api_security():
    from fastapi.testclient import TestClient
    from backend.main import app,TOKEN
    client=TestClient(app)
    assert client.get('/health').status_code==200
    assert client.get('/status').status_code==403
    assert client.get('/status',headers={'x-aki-token':TOKEN}).status_code==200
    assert client.get('/status',headers={'x-aki-token':TOKEN,'origin':'https://example.com'}).status_code==403

def test_network_guard():
    install_network_guard()
    with socket.socket() as s:
        with pytest.raises(PermissionError): s.connect(('8.8.8.8',443))
