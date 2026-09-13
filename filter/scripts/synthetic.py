"""Entirely invented FAKE records, created algorithmically; never derived from MIMIC."""
from pathlib import Path
import pandas as pd

def generate(folder,n=40):
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    names=['PATIENTS','ADMISSIONS','ICUSTAYS','CHARTEVENTS','LABEVENTS','D_ITEMS','D_LABITEMS','ELIGIBILITY_REVIEW']
    if any((folder/(x+'.csv')).exists() for x in names): raise ValueError('Refusing to overwrite existing files.')
    patients=[]; admissions=[]; stays=[]; charts=[]; labs=[]; review=[]
    t=pd.Timestamp('2100-01-01')
    for i in range(1,n+1):
        subject=900000+i; hadm=800000+i; stay=700000+i
        ids={'SUBJECT_ID':subject,'HADM_ID':hadm,'ICUSTAY_ID':stay}
        patients.append({'SUBJECT_ID':subject,'DOB':'2060-01-01','GENDER':'F' if i%2 else 'M'})
        admissions.append({'SUBJECT_ID':subject,'HADM_ID':hadm,'DISCHTIME':t+pd.Timedelta(hours=120)})
        stays.append(ids|{'INTIME':t,'OUTTIME':t+pd.Timedelta(hours=100)})
        review.append(ids|{'ESRD':0,'PRE_EXISTING_AKI':0,'DIALYSIS_START':None,'REVIEW_COMPLETE':1})
        for h in (0,4,8,12,24,30,48,56,60,72):
            if not (i%5==0 and h<=24): charts.append(ids|{'ITEMID':220045,'CHARTTIME':t+pd.Timedelta(hours=h),'STORETIME':t+pd.Timedelta(hours=h),'VALUENUM':60+i+h/4,'VALUEUOM':'bpm'})
            labs.append({'SUBJECT_ID':subject,'HADM_ID':hadm,'ITEMID':50912,'CHARTTIME':t+pd.Timedelta(hours=h),'VALUENUM':1.5 if h>=30 and i%3==0 else 1.,'VALUEUOM':'mg/dL'})
    tables=[patients,admissions,stays,charts,labs,[{'ITEMID':220045,'LABEL':'FAKE Heart Rate','UNITNAME':'bpm'}],[{'ITEMID':50912,'LABEL':'FAKE Creatinine','FLUID':'Blood'}],review]
    for name,rows in zip(names,tables): pd.DataFrame(rows).to_csv(folder/(name+'.csv'),index=False)
    (folder/'FAKE_SYNTHETIC_DATA.txt').write_text('Entirely fabricated software-test records. Not MIMIC patients. No real records were used.',encoding='utf-8')

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('directory'); args=p.parse_args(); generate(args.directory)
