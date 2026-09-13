from pathlib import Path
import pandas as pd
import pyarrow.parquet as pq

TABLES = ['PATIENTS','ADMISSIONS','ICUSTAYS','CHARTEVENTS','LABEVENTS','D_ITEMS','D_LABITEMS']

def find_table(raw, name, optional=False):
    candidates = [p for p in Path(raw).iterdir() if p.is_file() and p.name.upper() in [name.upper()+s for s in ('.CSV','.CSV.GZ','.PARQUET')]]
    if len(candidates)>1:
        raise ValueError(f'{name}: multiple formats found; keep one source per table.')
    if not candidates:
        if optional: return None
        raise ValueError(f'{name}: file not found (.csv, .csv.gz, .parquet).')
    p=candidates[0]
    if not p.resolve().is_relative_to(Path(raw).resolve()):
        raise ValueError('Source file resolves outside Raw Data Directory.')
    return p

def chunks(path, columns=None, size=100000):
    if path.suffix.lower()=='.parquet':
        for b in pq.ParquetFile(path).iter_batches(batch_size=size,columns=columns):
            yield b.to_pandas()
    else:
        yield from pd.read_csv(path,usecols=columns,chunksize=size,low_memory=False)

def metadata(path):
    if path.suffix.lower()=='.parquet':
        f=pq.ParquetFile(path)
        return {'rows':f.metadata.num_rows,'row_count_mode':'exact metadata','columns':f.schema_arrow.names,'dtypes':{x.name:str(x.type) for x in f.schema_arrow}}
    d=pd.read_csv(path,nrows=1000)
    return {'rows':None,'row_count_mode':'not counted; schema from first 1000 rows','columns':list(d.columns),'dtypes':{k:str(v) for k,v in d.dtypes.items()}}

def inspect(path,size):
    n=0; missing={}; ids={}; ranges={}
    # Exact distinct IDs with a bounded-memory SQLite index in a caller-supplied workspace is unnecessary
    # for preview: ID counts below are explicitly capped and labeled lower bounds.
    for d in chunks(path,size=size):
        n+=len(d)
        for col in d:
            missing[col]=missing.get(col,0)+int(d[col].isna().sum())
            if col in ('SUBJECT_ID','HADM_ID','ICUSTAY_ID'):
                s=ids.setdefault(col,set())
                if len(s)<1000000: s.update(d[col].dropna().unique())
            if col.endswith('TIME') or col=='DOB':
                t=pd.to_datetime(d[col],errors='coerce').dropna()
                if len(t):
                    old=ranges.get(col,(t.min(),t.max()))
                    ranges[col]=(min(old[0],t.min()),max(old[1],t.max()))
    return {'rows':n,'missing_percentage':{k:100*v/max(n,1) for k,v in missing.items()},'unique_ids':{k:{'count':len(v),'exact':len(v)<1000000} for k,v in ids.items()},'time_ranges':{k:[str(a),str(b)] for k,(a,b) in ranges.items()}}

def small_table(raw,name,size):
    parts=[]; n=0
    for d in chunks(find_table(raw,name),size=size):
        n+=len(d)
        if n>2000000: raise ValueError(f'{name}: small-table safety limit exceeded.')
        parts.append(d)
    return pd.concat(parts,ignore_index=True) if parts else pd.DataFrame()

def dates(d,cols):
    d=d.copy()
    for col in cols:
        if col not in d: raise ValueError(f'Missing required column: {col}')
        parsed=pd.to_datetime(d[col],errors='coerce')
        if (d[col].notna() & parsed.isna()).any():
            raise ValueError(f'Unparseable time field: {col}')
        d[col]=parsed
    return d
