import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer

def preprocess(d,features,strategy,indicator):
    result=d.copy(deep=True)
    training=result['split'].eq('train')
    if not training.any(): raise ValueError('No training patients; cannot fit preprocessing.')
    missing=[{'feature':k,'missing_count':int(d[k].isna().sum()),'missing_percentage':float(d[k].isna().mean()*100)} for k in features]
    if indicator:
        for k in features: result[k+'_missing']=result[k].isna().astype('int8')
    if strategy=='drop_feature':
        drop=[k for k in features if result.loc[training,k].isna().any()]
        result=result.drop(columns=drop)
        return result,missing,{'fit_split':'train','dropped':drop}
    if strategy=='unknown':
        for k in features: result[k]=result[k].astype(object).where(result[k].notna(),'unknown')
        # Convert entire feature to string for consistent Parquet schema.
        for k in features: result[k]=result[k].astype(str)
        return result,missing,{'fit_split':'train','constant':'unknown','warning':'Numeric features become categorical strings.'}
    valid=[k for k in features if result.loc[training,k].notna().any()]
    empty=[k for k in features if k not in valid]
    if valid:
        imputer=SimpleImputer(strategy=strategy)
        imputer.fit(result.loc[training,valid])
        result[valid]=imputer.transform(result[valid])
    return result,missing,{'fit_split':'train','unresolved_all_missing_training_features':empty}
