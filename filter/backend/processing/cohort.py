import pandas as pd
from backend.services.io import dates

IDS=['SUBJECT_ID','HADM_ID','ICUSTAY_ID']

def build_cohort(patients,admissions,stays,followup):
    patients=dates(patients,['DOB'])
    admissions=dates(admissions,['DISCHTIME'])
    stays=dates(stays,['INTIME','OUTTIME'])
    if stays[IDS+['INTIME']].isna().any().any() or stays.ICUSTAY_ID.duplicated().any():
        raise ValueError('ICUSTAYS requires complete, unique stay identifiers and INTIME.')
    d=stays.merge(patients[['SUBJECT_ID','DOB']],on='SUBJECT_ID',validate='many_to_one').merge(admissions[['SUBJECT_ID','HADM_ID','DISCHTIME']],on=['SUBJECT_ID','HADM_ID'],validate='many_to_one')
    if len(d)!=len(stays): raise ValueError('Some ICU stays lack matching patient/admission records.')
    if d.DOB.isna().any(): raise ValueError('Missing DOB prevents adult eligibility assessment.')
    audit=[]
    def step(name,after):
        nonlocal d
        audit.append(count_step(name,d,after))
        d=after.copy()
    step('Original cohort',d)
    # Pick the first-ever recorded stay before eligibility filtering; do not promote a later stay.
    first=set(d.sort_values(['INTIME','ICUSTAY_ID']).drop_duplicates('SUBJECT_ID').ICUSTAY_ID)
    age=(d.INTIME-d.DOB).dt.total_seconds()/(365.2425*86400)
    step('Adult (age >=18; shifted ages >89 remain eligible)',d.loc[age>=18])
    step('First ICU stay per patient',d.loc[d.ICUSTAY_ID.isin(first)])
    end=d.OUTTIME if followup=='icu_72h' else d.DISCHTIME
    step('Common follow-up through ICU admission +72h',d.loc[end>=d.INTIME+pd.Timedelta(hours=72)])
    return d,audit

def count_step(name,before,after):
    a=before.SUBJECT_ID.nunique(); b=after.SUBJECT_ID.nunique()
    return {'step':name,'patients_before':int(a),'patients_excluded':int(a-b),'patients_remaining':int(b),'icu_stays_before':len(before),'icu_stays_after':len(after)}

def prediction_time(intime,hours):
    if hours not in (8,12,24): raise ValueError('Unsupported prediction window.')
    return intime+pd.Timedelta(hours=hours)
