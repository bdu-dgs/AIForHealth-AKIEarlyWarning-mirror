"""Read-only validation of feature-engine v2 against a frozen study and fixed weights.

All derived features/predictions/admission inputs are private, versioned artifacts.
Only aggregate diagnostics are written outside the private/ subdirectory. No policy
search, retraining, recalibration, test-set evaluation, activation, or Git operation.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import os
import sys
import time

for key in ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"]:
    os.environ.setdefault(key, "2")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from threadpoolctl import threadpool_limits
import diagnose_feature_replay as diagnostic
from aki_eval.policies import AlertRule, alert_events, metrics, stability_grid
from aki_eval.protocol import sha256, write_json
from aki_eval.replay import ReplayData, sample_patients
from aki_ml.admission_inputs import (admission_frame, reconstruct_baselines, read_admission_inputs,
                                    write_admission_inputs)
from aki_ml.evaluation import classification_metrics
from aki_ml.features import STABLE_FEATURE_ENGINE


def calibration_metrics(y, risk):
    valid = np.isfinite(y) & np.isfinite(risk)
    yy, pp = y[valid], risk[valid]
    ece = 0.
    for i in range(10):
        inside = (pp >= i/10) & (pp <= (i+1)/10 if i==9 else pp < (i+1)/10)
        if inside.any():
            ece += inside.mean()*abs(yy[inside].mean()-pp[inside].mean())
    return {"mean_risk_minus_prevalence": float(pp.mean()-yy.mean()), "ece_10_equal_width": float(ece)}


def log(message):
    print(message, flush=True)


def protected_hashes(root):
    record = json.loads((root/"artifacts/feature-repair/protection-54cf16c.json").read_text(encoding="utf-8"))
    if not all(sha256(path)==value for path,value in record["protected_files"].items()):
        raise RuntimeError("An original protected artifact changed")
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, default=ROOT/"artifacts/handoff/kejin/kejin-validation-policy-v2-21d980db7c292219")
    parser.add_argument("--frozen-source", type=Path, default=ROOT/"artifacts/feature-repair/frozen-source-54cf16c")
    parser.add_argument("--output", type=Path)
    args=parser.parse_args()
    out=args.output or ROOT/"artifacts/feature-repair"/("repair-v2-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    out.mkdir(parents=True, exist_ok=False); private=out/"private"; private.mkdir()
    before=protected_hashes(ROOT); start=time.monotonic()
    code_files=sorted((ROOT/"src/aki_ml").glob("*.py"))+sorted((ROOT/"src/aki_eval").glob("*.py"))+[Path(__file__)]
    sources={p.relative_to(ROOT).as_posix():sha256(p) for p in code_files}
    write_json(out/"repair-plan.json",{"purpose":"software repair validation on previously viewed validation data",
        "source_run":args.source_run.name,"feature_engine_version":STABLE_FEATURE_ENGINE,
        "policies":"fixed original 21%/6h and sealed 23%/12h; no policy search",
        "data_scope":"validation only; original outcomes, origins and patient roles",
        "calibration_diagnostics":"Brier, mean-risk bias, 10 equal-width-bin ECE; no calibration fit",
        "determinism_patient_cap":100,"benchmark":"100 patients; features plus all four risk heads and contributions",
        "clinical_or_independent_confirmation":False,"source_sha256":sources})
    pa.set_cpu_count(2); pa.set_io_thread_count(2)
    with threadpool_limits(limits=2):
        log("Loading fixed validation data and weights; historical source verified separately.")
        cfg,policy,names,frame,queries,legacy,pipe,paths=diagnostic.load_context(args.source_run,source_root=args.frozen_source)
        old_X=queries[names].to_numpy(np.float32); h=cfg["alert_horizon_h"]
        if not np.array_equal(pipe.predict(old_X,h,"platt"),frame["risk"].to_numpy()):
            raise RuntimeError("Frozen features no longer reproduce saved risks")
        log("Reconstructing full-precision admission baselines from existing local labs.")
        baseline,pre,history,scanned=reconstruct_baselines(ROOT/"MIMIC-III/LABEVENTS.csv.gz",legacy.snapshots,legacy.config,
            progress=lambda n:log("Lab rows scanned: "+str(n)))
        static=admission_frame(legacy.snapshots,baseline,legacy.config)
        admission=write_admission_inputs(private/"admission_inputs_v2",static,
            {"raw_lab_sha256":sha256(ROOT/"MIMIC-III/LABEVENTS.csv.gz"),"snapshot_sha256":sha256(paths["snapshots"]),
             "data_config_sha256":sha256(paths["dataset_config"]),"baseline_module_sha256":sources["src/aki_ml/admission_inputs.py"],
             "other_static_inputs":"frozen snapshot fields; original availability timing not newly validated"})
        context=ReplayData.load(paths["events"],legacy.snapshots,legacy.config,names,
            engine_version=STABLE_FEATURE_ENGINE,admission_inputs=admission)
        log("Computing v2 feature matrix for all validation origins.")
        X,covered=context.matrix(queries)
        if not covered.all():
            raise RuntimeError("v2 changed original snapshot scorable coverage; investigate before proceeding")
        risk={}; quality=[]; private_predictions=queries[["patient_id","stay_id","hours_since_icu"]].copy()
        private_predictions["feature_engine_version"]=STABLE_FEATURE_ENGINE
        for lead in cfg["horizons"]:
            risk[lead]=pipe.predict(X,lead,"platt").astype(float)
            private_predictions["risk_"+str(lead)+"h"]=risk[lead]
            original=pq.read_table(paths["predictions"],filters=[("family","=",cfg["family"]),("feature_set","=",cfg["feature_set"]),
                ("prediction_set","=","validation"),("horizon_h","=",lead)],
                columns=["patient_id","hours_since_icu","risk_platt"]).to_pandas()
            original["patient_id"]=original["patient_id"].astype(str)
            aligned=queries[["patient_id","hours_since_icu"]].merge(original,on=["patient_id","hours_since_icu"],how="left",validate="one_to_one")
            previous=aligned["risk_platt"].to_numpy(float)
            if not np.array_equal(pipe.predict(old_X,lead,"platt"),previous):
                raise RuntimeError("Original stored-feature scores differ from a saved head")
            y=queries["aki_within_"+str(lead)+"h"].to_numpy(float,na_value=np.nan)
            for label,values in [("original_saved",previous),("corrected_v2_fixed_weights",risk[lead])]:
                quality.append({"horizon_h":lead,"representation":label,**classification_metrics(y,values),
                                **calibration_metrics(y,values)})
        pd.DataFrame(quality).to_csv(out/"prediction_quality.csv",index=False)
        feature_delta=diagnostic.feature_comparison(old_X,X,names,"corrected_v2")
        pd.DataFrame(feature_delta).to_csv(out/"changed_features.csv",index=False)
        write_json(out/"risk_difference.json",diagnostic.risk_comparison(frame["risk"].to_numpy(float),risk[h],"corrected_v2"))
        # New snapshots keep the old dtype/label/time definitions; feature version is explicit.
        corrected=queries.copy()
        for j,name in enumerate(names):
            corrected[name]=pd.Series(X[:,j]).astype(queries[name].dtype)
        corrected["parent_dataset_version"]=corrected["dataset_version"].astype(str)
        corrected["dataset_version"]=corrected["parent_dataset_version"]+"-feature-v2"
        corrected["feature_engine_version"]=STABLE_FEATURE_ENGINE
        corrected.to_parquet(private/"snapshot_dataset_validation_features_v2.parquet",index=False,compression="zstd")
        private_predictions.to_parquet(private/"predictions_validation_features_v2.parquet",index=False,compression="zstd")
        write_json(private/"dataset_manifest.json",{"schema":"feature-repair-validation-v2","feature_engine_version":STABLE_FEATURE_ENGINE,
            "source_snapshot_sha256":sha256(paths["snapshots"]),"original_labels_and_times_retained":True,
            "snapshot_sha256":sha256(private/"snapshot_dataset_validation_features_v2.parquet"),
            "predictions_sha256":sha256(private/"predictions_validation_features_v2.parquet"),
            "admission_inputs_manifest_sha256":sha256(admission/"manifest.json"),"rows":len(queries),
            "feature_order":names,"model_weights_sha256":sha256(paths["model"]),
            "warning":"fixed old weights on corrected inputs; research sensitivity, not a new approved model/policy"})
        del corrected,private_predictions
        log("Checking full-feature and fixed-model risk equality across contexts.")
        pool=sample_patients(legacy.snapshots,100,cfg["seed"]+91)
        mask=frame["patient_id"].isin(pool["patient_id"]).to_numpy()
        query=queries.loc[mask].reset_index(drop=True); reference=X[mask]
        small=ReplayData.load(paths["events"],pool,legacy.config,names,engine_version=STABLE_FEATURE_ENGINE,admission_inputs=admission)
        measured=[]
        def equality(label,actual):
            if not np.array_equal(actual,reference,equal_nan=True):
                raise RuntimeError("Feature context invariance failed: "+label)
            predicted=pipe.predict(actual,h,"platt")
            if not np.array_equal(predicted,risk[h][mask]):
                raise RuntimeError("Model risk invariance failed: "+label)
            measured.append({"case":label,"rows":len(actual),"features":len(names),"exact_feature_equality":True,"exact_risk_equality":True})
        equality("patient_subset_and_remapped_stay_indices",small.matrix(query)[0])
        pieces=[small.matrix(query.iloc[indices])[0] for indices in np.array_split(np.arange(len(query)),4)]
        equality("four_query_chunks",np.concatenate(pieces))
        equality("zero_lag_origin_clipping",small.matrix(query,clip="all")[0])
        future=[]
        for stay in np.unique(small.events["stay_idx"]):
            events=small.events[small.events["stay_idx"]==stay]
            for concept,group in events.groupby("concept",sort=False):
                future.append({"stay_idx":stay,"t":1000.,"concept":concept,"value":float(group["value"].iloc[0]),"itemid":int(group["itemid"].iloc[0])})
        augmented=pd.concat([small.events,pd.DataFrame(future),
            pd.DataFrame([{"stay_idx":99999999,"t":1.,"concept":"spo2","value":50.,"itemid":646}])],ignore_index=True)
        equality("future_all_concepts_and_unrelated_patient",small.matrix(query,events=augmented)[0])
        pd.DataFrame(measured).to_csv(out/"determinism_checks.csv",index=False)
        policies=[("baseline",policy["threshold"]["value"],legacy_rule(policy)),
                  ("candidate",.23,AlertRule(name="cool12",cooldown_hours=12))]
        point=[]
        for role in ["selection","confirmation"]:
            role_mask=frame["evaluation_role"].eq(role).to_numpy(); original=frame.loc[role_mask].copy(); changed=original.copy()
            changed["risk"]=risk[h][role_mask]
            for label,values in [("original_saved",original),("corrected_v2_fixed_weights",changed)]:
                for name,threshold,rule in policies:
                    point.append({"representation":label,"role":role,"policy":name,**metrics(values,threshold,rule)})
        pd.DataFrame(point).to_csv(out/"fixed_policy_metrics.csv",index=False)
        cutoff=[]
        for role,offset in [("selection",31),("confirmation",32)]:
            selected=legacy.snapshots[legacy.snapshots["evaluation_role"]==role]
            selected=sample_patients(selected,cfg["cutoff"][role+"_patient_cap"],cfg["seed"]+offset)
            take=frame["patient_id"].isin(selected["patient_id"]).to_numpy()
            ref=frame.loc[take].copy().reset_index(drop=True); ref["risk"]=risk[h][take]
            q=queries.loc[take].reset_index(drop=True)
            local=ReplayData.load(paths["events"],selected,legacy.config,names,engine_version=STABLE_FEATURE_ENGINE,admission_inputs=admission)
            variants={0:risk[h][take]}
            for lag in [1,2]:
                log(role+": corrected fixed-origin cutoff "+str(lag)+"h.")
                matrix,ok=local.matrix(q,lag_hours=lag,clip="all")
                values=pipe.predict(matrix,h,"platt").astype(float);values[~ok]=np.nan;variants[lag]=values
            for name,threshold,rule in policies:
                result=stability_grid(ref,variants,[rule],[threshold]).assign(role=role,policy=name,reference_basis="corrected_v2_zero_lag")
                cutoff.extend(result.to_dict("records"))
        pd.DataFrame(cutoff).to_csv(out/"cutoff_stability.csv",index=False)
        latest=query.sort_values("hours_since_icu").groupby("patient_id",sort=False).tail(1).reset_index(drop=True)
        timings=[]
        for _ in range(3):
            clock=time.monotonic();matrix,_=small.matrix(latest);features_s=time.monotonic()-clock
            for lead in cfg["horizons"]:
                pipe.predict(matrix,lead,"platt")
                pipe.heads[lead].predict(matrix,num_iteration=pipe.n_rounds[lead],pred_contrib=True)
            timings.append({"patients":len(latest),"feature_seconds":features_s,"features_risks_contributions_seconds":time.monotonic()-clock})
        pd.DataFrame(timings).to_csv(out/"one_cycle_timing.csv",index=False)
        if not all(sha256(path)==value for path,value in before["protected_files"].items()):
            raise RuntimeError("An original protected file changed")
        write_json(out/"repair-manifest.json",{"status":"complete","engine_version":STABLE_FEATURE_ENGINE,
            "source_run":args.source_run.name,"source_checkpoint":before["checkpoint"],"source_sha256":sources,
            "patients":int(frame["patient_id"].nunique()),"rows":len(frame),"raw_lab_rows_scanned":scanned,
            "full_precision_baseline_patients":int(static["baseline_cr_admission"].notna().sum()),
            "missing_baseline_patients":int(static["baseline_cr_admission"].isna().sum()),
            "determinism_checks_passed":len(measured),"protected_original_files_unchanged":len(before["protected_files"]),
            "weights_retrained":False,"calibrators_refit":False,"threshold_reselected":False,"confirmation_repeated":False,
            "test_used":False,"default_model_or_policy_activated":False,"elapsed_seconds":time.monotonic()-start,
            "private_artifacts":"private/ includes credentialed derived patient inputs/features/predictions; never upload",
            "timing_scope":"in-process one-cycle check only; not HTTP or sustained load"} )
        log("Repair validation complete; all original records and model files unchanged. Output: "+str(out))


def legacy_rule(policy):
    return AlertRule(name="demo-v1",**{k:policy["alert_policy"][k] for k in ["sustain_hours","cooldown_hours","rise_ratio","rise_window_hours"]})


if __name__=="__main__":
    main()
