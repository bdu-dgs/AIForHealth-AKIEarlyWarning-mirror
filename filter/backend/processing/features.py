import numpy as np
import pandas as pd

STATS=('latest','mean','min','max','range','count','std','slope')

def feature_events(events,intime,hours):
    cutoff=intime+pd.Timedelta(hours=hours)
    return events.loc[(events.CHARTTIME>=intime)&(events.CHARTTIME<=cutoff)&(events.AVAILABLETIME<=cutoff)].copy()

def aggregate(events,intime,hours,variables):
    e=feature_events(events,intime,hours); result={}
    for name in sorted(set(v.name for v in variables)):
        z=e.loc[e.variable==name].sort_values(['CHARTTIME','AVAILABLETIME'])
        y=z.VALUENUM.to_numpy(dtype=float)
        x=(z.CHARTTIME-intime).dt.total_seconds().to_numpy()/3600
        vals={'count':len(y),'latest':y[-1] if len(y) else np.nan,'mean':float(y.mean()) if len(y) else np.nan,'min':float(y.min()) if len(y) else np.nan,'max':float(y.max()) if len(y) else np.nan,'range':float(np.ptp(y)) if len(y) else np.nan,'std':float(y.std(ddof=1)) if len(y)>1 else np.nan,'slope':float(np.polyfit(x,y,1)[0]) if len(y)>1 and np.ptp(x)>0 else np.nan}
        result.update({name+'_'+k:v for k,v in vals.items()})
    return result,e
