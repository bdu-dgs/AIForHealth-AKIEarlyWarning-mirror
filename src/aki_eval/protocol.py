"""Immutable local pre-analysis record and patient-level validation roles."""
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd


def sha256(path):
    with open(path,"rb") as source:
        return hashlib.file_digest(source,"sha256").hexdigest()


def safe_json(value):
    if isinstance(value, dict):
        return {str(k):safe_json(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [safe_json(v) for v in value]
    if isinstance(value,(np.integer,np.bool_)):
        return value.item()
    if isinstance(value,(float,np.floating)):
        return float(value) if np.isfinite(value) else None
    return value


def canonical(value):
    return json.dumps(safe_json(value),sort_keys=True,ensure_ascii=False,allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def write_json(path, value, *, exclusive=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("x" if exclusive else "w",encoding="utf-8") as target:
        json.dump(safe_json(value),target,indent=2,ensure_ascii=False,allow_nan=False)


def validation_roles(patient_ids, seed, selection_fraction):
    if not 0 < selection_fraction < 1:
        raise ValueError("Invalid patient split fraction")
    def role(pid):
        key=f"aki-policy-validation/{seed}/{pid}".encode()
        value=int.from_bytes(hashlib.sha256(key).digest()[:8],"big") / 2**64
        return "selection" if value < selection_fraction else "confirmation"
    return pd.Series([role(str(pid)) for pid in patient_ids],index=getattr(patient_ids,"index",None))


def threshold_grid(spec):
    start, stop, step = (Decimal(str(spec[k])) for k in ["start","stop","step"])
    if step <= 0 or start < 0 or stop > 1 or start > stop:
        raise ValueError("Invalid threshold grid")
    out=[]
    while start <= stop:
        out.append(float(start));start += step
    return out


def freeze_prespec(directory, config, inputs, source_files):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    payload={"config":config,"input_sha256":{name:sha256(path) for name,path in inputs.items()},
             "source_sha256":{name:sha256(path) for name,path in source_files.items()},
             "commit_before_results":False,"registration":"immutable local record; not public preregistration"}
    key=digest(payload);path=directory/"prespec.json"
    if path.exists():
        previous=json.loads(path.read_text(encoding="utf-8"))
        if previous["prespec_sha256"] != key or digest(previous["specification"]) != key:
            raise RuntimeError("Prespec/input/code changed; preserve this run and use a new version/directory")
        return previous
    value={"created_at":datetime.now(timezone.utc).isoformat(),"prespec_sha256":key,"specification":payload}
    write_json(path,value,exclusive=True)
    return value


def lock_choice(directory, chosen, prespec_hash):
    value={"prespec_sha256":prespec_hash,"selected_on":"validation_selection",
           "chosen":safe_json(chosen),"locked_at":datetime.now(timezone.utc).isoformat()}
    path=Path(directory)/"choice.json"
    if path.exists():
        old=json.loads(path.read_text(encoding="utf-8"))
        if old["prespec_sha256"] != prespec_hash or canonical(old["chosen"]) != canonical(chosen):
            raise RuntimeError("Cannot replace a locked choice after confirmation")
        return old
    write_json(path,value,exclusive=True)
    return value
