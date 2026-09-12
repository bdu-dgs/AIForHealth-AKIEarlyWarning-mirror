import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from backend.services.io import find_table,chunks,metadata,dates

def extract(raw,workspace,cohort,c,progress):
    """Stream only selected ITEMIDs into 32 patient partitions, entirely outside the code tree."""
    mapping={(v.source,v.itemid):v for v in c.variables}
    subject_stays=cohort.set_index('SUBJECT_ID')
    seen=0; kept=0; parts=[]
    for source in ('CHARTEVENTS','LABEVENTS'):
        selected={i for s,i in mapping if s==source}
        if source=='LABEVENTS': selected.add(c.creatinine_itemid)
        if not selected: continue
        path=find_table(raw,source)
        cols=metadata(path)['columns']
        needed=['SUBJECT_ID','HADM_ID','ITEMID','CHARTTIME','VALUENUM','VALUEUOM']
        if not set(needed)<=set(cols): raise ValueError(source+': required measurement columns missing.')
        needed += [x for x in ('ICUSTAY_ID','STORETIME','ERROR') if x in cols]
        if c.availability=='require_storetime' and 'STORETIME' not in cols:
            raise ValueError(source+': STORETIME is unavailable; strict availability cannot be established.')
        for chunk in chunks(path,needed,c.chunk_size):
            seen+=len(chunk)
            chunk=chunk.loc[chunk.ITEMID.isin(selected)&chunk.SUBJECT_ID.isin(subject_stays.index)].copy()
            if not len(chunk): continue
            chunk=dates(chunk,['CHARTTIME'])
            if chunk.CHARTTIME.isna().any(): raise ValueError('Selected measurements have missing CHARTTIME.')
            chunk['AVAILABLETIME']=chunk.CHARTTIME
            if 'STORETIME' in chunk:
                chunk=dates(chunk,['STORETIME'])
                if c.availability=='require_storetime' and chunk.STORETIME.isna().any(): raise ValueError('STORETIME missing under strict availability policy.')
                chunk['AVAILABLETIME']=chunk[['CHARTTIME','STORETIME']].max(axis=1)
            if 'ERROR' in chunk: chunk=chunk.loc[chunk.ERROR.fillna(0)!=1]
            chunk['VALUENUM']=pd.to_numeric(chunk.VALUENUM,errors='coerce')
            chunk=chunk.loc[chunk.VALUENUM.notna()].copy()
            if not len(chunk): continue
            lookup=subject_stays.loc[chunk.SUBJECT_ID]
            start=pd.Series(lookup.INTIME.to_numpy(),index=chunk.index)
            end=start+pd.Timedelta(hours=72)
            # Lab linkage uses SUBJECT_ID + HADM_ID, never a cross-admission nearest-time join.
            keep=(chunk.HADM_ID.to_numpy()==lookup.HADM_ID.to_numpy())&(chunk.CHARTTIME>=start-pd.Timedelta(days=7))&(chunk.CHARTTIME<=end)
            if source=='CHARTEVENTS' and 'ICUSTAY_ID' in chunk:
                keep &= chunk.ICUSTAY_ID.to_numpy()==lookup.ICUSTAY_ID.to_numpy()
            chunk=chunk.loc[keep].copy()
            if not len(chunk): continue
            chunk['variable']=''; chunk['source']=source
            for item in chunk.ITEMID.unique():
                mask=chunk.ITEMID==item
                v=mapping.get((source,int(item)))
                expected=v.unit if v else 'mg/dL'
                units=chunk.loc[mask,'VALUEUOM'].fillna('').astype(str).str.strip().str.lower()
                is_cr=source=='LABEVENTS' and item==c.creatinine_itemid
                if is_cr:
                    ok=units.isin(['mg/dl','umol/l','µmol/l','μmol/l'])
                    if not ok.all(): raise ValueError('Creatinine units missing or unsupported; resolve units before processing.')
                    convert=mask & chunk.VALUEUOM.astype(str).str.lower().isin(['umol/l','µmol/l','μmol/l'])
                    chunk.loc[convert,'VALUENUM']/=88.4
                    if (chunk.loc[mask,'VALUENUM']<=0).any(): raise ValueError('Creatinine must be positive.')
                elif not units.eq(expected.strip().lower()).all():
                    raise ValueError('Selected variable units disagree with configured units; select homogeneous ITEMIDs or normalize first.')
                chunk.loc[mask,'variable']=v.name if v else '__label_creatinine'
            chunk=chunk[['SUBJECT_ID','ITEMID','CHARTTIME','AVAILABLETIME','VALUENUM','variable','source']]
            kept+=len(chunk)
            for bucket,z in chunk.groupby(chunk.SUBJECT_ID.astype('int64')%32):
                folder=workspace/f'part_{int(bucket):02d}'; folder.mkdir(exist_ok=True)
                file=folder/f'{source}_{seen}.parquet'
                z.to_parquet(file,index=False); parts.append(file)
            progress({'stage':'Extract selected measurements','rows_scanned':seen,'rows_selected':kept})
    return parts,{'rows_scanned':seen,'rows_selected':kept}

def read_patient_partition(workspace,bucket,max_rows=3000000):
    folder=workspace/f'part_{bucket:02d}'
    files=list(folder.glob('*.parquet'))
    if not files: return pd.DataFrame(columns=['SUBJECT_ID','ITEMID','CHARTTIME','AVAILABLETIME','VALUENUM','variable','source'])
    if sum(pq.ParquetFile(f).metadata.num_rows for f in files)>max_rows:
        raise ValueError('Selected partition exceeds memory safety limit. Reduce selected variables or use a larger-partition extension.')
    return pd.concat([pd.read_parquet(f) for f in files],ignore_index=True)
