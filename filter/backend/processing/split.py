import numpy as np
import pandas as pd

def patient_split(subjects,train=.7,validation=.15,seed=42):
    if not (0<train<1 and 0<validation and train+validation<1): raise ValueError('Invalid split ratios.')
    ids=np.array(sorted(set(subjects))); np.random.default_rng(seed).shuffle(ids)
    n=len(ids); a=int(n*train); b=a+int(n*validation)
    return pd.DataFrame({'SUBJECT_ID':ids,'split':['train']*a+['validation']*(b-a)+['test']*(n-b)})
