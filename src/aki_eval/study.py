"""Complete Ke Jin workflow: integrity, validation selection, one fixed confirmation pass."""
from datetime import datetime, timezone
import functools
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from aki_ml import alerts as legacy
from . import checks as ck
from .data import StudyData
from .policies import (AlertRule,alert_events,bootstrap_comparison,choose,metrics,per_patient,
                       policy_grid,stability_grid)
from .protocol import canonical,digest,lock_choice,safe_json,sha256,write_json
from .replay import ReplayData,audit_replay,sample_patients


class KeJinStudy:
    def __init__(self, root, **kwargs):
        self.data=StudyData(root,**kwargs)
        self.out=self.data.out
        self.config=self.data.config
        self._cutoffs={}
        self.pipe=None

    def log(self,message):
        print(message,flush=True)

    def reproduce_baseline(self):
        data=self.data;frames=data.load();rows=[]
        for role,key in [("train_oof","selection_metrics_train_oof"),("validation","validation_metrics")]:
            got=metrics(frames[role],data.policy["threshold"]["value"],data.baseline)
            if any(not np.isclose(got[k],v,rtol=1e-9,atol=1e-12) for k,v in data.policy[key].items()):
                raise RuntimeError("Frozen Demo I metrics did not reproduce for "+role)
            rows.append({"data":role,**got})
        result=pd.DataFrame(rows)
        result.to_csv(self.out/"baseline_reproduction.csv",index=False)
        write_json(self.out/"data_roles.json",data.metadata())
        self.log("Part 0: frozen OOF and validation alert metrics reproduced.")
        return result

    def checks(self):
        path=self.out/"checks.csv"
        if path.exists():
            result=pd.read_csv(path)
            if result["passed"].all():
                return result
            raise RuntimeError("Preserve failed audit; fix inputs/code and start a new recorded run")
        data=self.data;data.load();snap,pred,manifest=data.snapshots,data.predictions,data.split_manifest
        val=pred[pred["prediction_set"]=="validation"]
        rows=[]
        def record(key,name,value,evidence="artifact contents"):
            rows.append({"id":key,"check":name,"passed":value[0],"detail":value[1],"evidence":evidence})
        a=ck.unique_keys(pred,["family","feature_set","horizon_h","prediction_set","patient_id","hours_since_icu"])
        b=ck.unique_keys(snap,["stay_id","snapshot_time"])
        record("A1","Unique prediction/snapshot keys",(a[0] and b[0],"predictions: "+a[1]+"; snapshots: "+b[1]))
        record("A2","Finite probabilities in [0,1]",ck.probability_bounds(pred))
        schema=__import__("pyarrow.parquet",fromlist=["ParquetFile"]).ParquetFile(data.paths["snapshots"]).schema_arrow
        version=ck.schema_and_versions(schema,data.dictionary,data.dataset_manifest,data.run_manifest,data.policy,
            data.paths["model"],config_path=data.paths["dataset_config"],
            versions=snap["dataset_version"].unique(),prediction_versions=pred["run_id"].unique())
        recorded=data.run_manifest["dataset"]["files"]
        provenance_ok=all(sha256(data.paths[key])==recorded[rel] for key,rel in [
            ("dictionary","datasets/feature_dictionary.csv"),("splits","splits/patient_split.parquet")])
        record("A3","Schemas, versions and immutable model",(version[0] and provenance_ok,
               version[1]+"; dictionary_and_split_hashes_match="+str(provenance_ok)))
        record("A4","Every eligible snapshot has each horizon",ck.missing_predictions(snap,val,self.config["horizons"]))
        record("A5","Frozen patient splits",ck.split_disjointness(manifest,snap,pred))
        record("A6","One selected stay per patient",ck.one_stay_per_patient(manifest,snap))
        record("A7","Chronology and ICU-relative hours",ck.chronology(snap))
        record("A8","Horizon labels, eligibility and alignment",ck.label_definition(snap,val,self.config["horizons"]))
        record("A9","No at/after-onset prediction snapshots",ck.pre_onset_snapshots(snap))
        # Never run candidate selection if an earlier integrity check failed.
        if not all(r["passed"] for r in rows):
            pd.DataFrame(rows).to_csv(path,index=False)
            raise RuntimeError("Artifact audit failed; inspect aggregate checks.csv")
        cfg=self.config["feature_checks"]
        pool=sample_patients(snap,cfg["patient_cap"],self.config["seed"]+10)
        sample=pool.sample(n=min(cfg["rows"],len(pool)),random_state=self.config["seed"]).reset_index(drop=True)
        self.log("A10/A11: replaying "+str(len(sample))+" sampled snapshots through frozen features.")
        context=ReplayData.load(data.paths["events"],pool,data.data_config,data.features)
        a10,a11=audit_replay(context,sample,min_rows=cfg["minimum_rows"])
        record("A10","Past-only feature parity and future injection",a10,
               "deployed features; static admission/service inputs supplied from snapshots, not independently source-timed")
        record("A11","No future urine contribution",a11,
               "urine-only truncation at every sampled origin; rates and fluid balance included")
        provenance=ck.no_test_fitting(data.run_manifest)
        train=pred[pred["prediction_set"]=="train_oof"]
        folds_ok=(train["cv_fold"].ge(0).all() and
                  train.groupby("patient_id",observed=True)["cv_fold"].nunique().eq(1).all() and
                  val["cv_fold"].eq(-1).all())
        record("A12","No test use declared and OOF patient folds",(provenance[0] and folds_ok,
               provenance[1]+"; actual_prediction_fold_roles_match="+str(bool(folds_ok))),
               "historical fitting declaration + current prediction fold invariants; full fitting cannot be re-audited")
        result=pd.DataFrame(rows)
        result.to_csv(path,index=False)
        if not result["passed"].all():
            raise RuntimeError("Temporal audit failed; inspect aggregate checks.csv")
        self.log("A1-A12: all checks passed within the recorded evidence scope.")
        return result

    def prespecification(self):
        self.data.load()
        meta=self.data.metadata()
        return pd.DataFrame([
            {"item":"prespec_sha256","value":self.data.prespec["prespec_sha256"]},
            {"item":"selection","value":"validation_selection, patient-level deterministic 70% role"},
            {"item":"confirmation","value":"remaining validation patients; fixed comparison, no tuning"},
            {"item":"candidate_count","value":str(len(self.data.rules)*len(self.data.thresholds))},
            {"item":"selection_patients","value":str(meta["patients"]["selection"])},
            {"item":"confirmation_patients","value":str(meta["patients"]["confirmation"])},
            {"item":"registration","value":"local immutable record, before grid; no Git commit/public preregistration"},
        ])

    def _model(self):
        if self.pipe is None:
            if sha256(self.data.paths["model"]) != self.data.policy["model"]["pipeline_sha256"]:
                raise RuntimeError("Model changed after prespec")
            self.pipe=joblib.load(self.data.paths["model"])
            for head in self.pipe.heads.values():
                head.predict=functools.partial(head.predict,num_threads=2)
        return self.pipe

    def cutoff_inputs(self,role):
        if role in self._cutoffs:
            return self._cutoffs[role]
        if role not in ["selection","confirmation"]:
            raise ValueError("Cutoff data must have a validation role")
        if role=="confirmation" and not (self.out/"choice.json").exists():
            raise RuntimeError("Lock a validation-selection choice before confirmation analysis")
        data=self.data;data.load();cfg=self.config["cutoff"]
        pool=data.snapshots[data.snapshots["evaluation_role"]==role]
        cap=cfg["selection_patient_cap" if role=="selection" else "confirmation_patient_cap"]
        pool=sample_patients(pool,cap,self.config["seed"]+(31 if role=="selection" else 32))
        ref=data.frames[role]
        ref=ref[ref["patient_id"].isin(pool["patient_id"].unique())].reset_index(drop=True)
        ref.attrs["horizon_h"]=self.config["alert_horizon_h"]
        keys=["patient_id","hours_since_icu"]
        queries=ref[keys].merge(pool,on=keys,how="left",validate="one_to_one")
        if queries["stay_id"].isna().any():
            raise RuntimeError("Missing replay inputs")
        context=ReplayData.load(data.paths["events"],pool,data.data_config,data.features)
        pipe=self._model();h=self.config["alert_horizon_h"]
        stored_X = queries[data.features].to_numpy(np.float32)
        with threadpool_limits(limits=2):
            stored_risk = pipe.predict(stored_X,h,"platt")
        if not np.allclose(stored_risk,ref["risk"],atol=1e-6,rtol=0):
            raise RuntimeError("Stored features do not reproduce saved risks; model/key alignment invalid")
        variants={}
        diagnostics=[]
        for lag in cfg["lags_hours"]:
            self.log(role+": computing fixed-origin input cutoff lag "+str(lag)+"h.")
            X,ok=context.matrix(queries,lag_hours=lag,clip="all")
            with threadpool_limits(limits=2):
                risk=pipe.predict(X,h,"platt").astype(float)
            risk[~ok]=np.nan
            variants[lag]=risk
            if lag == 0:
                close=np.isclose(X,stored_X,rtol=1e-5,atol=1e-4,equal_nan=True)
                paired=np.isfinite(risk)
                difference=np.abs(risk[paired]-ref["risk"].to_numpy(float)[paired])
                diagnostics.append({"role":role,"rows":len(queries),"paired_rows":int(paired.sum()),
                    "feature_values_outside_original_tolerance":int((~close).sum()),
                    "feature_rows_outside_original_tolerance":int((~close).any(axis=1).sum()),
                    "risk_rows_above_1e6_difference":int((difference>1e-6).sum()),
                    "mean_absolute_risk_difference":float(difference.mean()) if len(difference) else np.nan,
                    "max_absolute_risk_difference":float(difference.max()) if len(difference) else np.nan,
                    "stored_features_reproduce_saved_risk":True,
                    "interpretation":"export/aggregation representation sensitivity; not pure clinical time-lag effect"})
        pd.DataFrame(diagnostics).to_csv(self.out/("zero_lag_representation_"+role+".csv"),index=False)
        self._cutoffs[role]=(ref,variants)
        return ref,variants

    def cutoff_table(self, role, rules, thresholds):
        ref, variants = self.cutoff_inputs(role)
        if 0 not in variants:
            raise ValueError("Zero-lag replay control is required")
        stored = stability_grid(ref,variants,rules,thresholds).assign(reference_basis="stored_snapshot")
        replay = ref.copy()
        replay["risk"] = variants[0]
        replayed = stability_grid(replay,{lag:value for lag,value in variants.items() if lag != 0},
                                  rules,thresholds).assign(reference_basis="zero_lag_event_replay")
        return pd.concat([stored,replayed],ignore_index=True)

    def _chosen(self):
        path=self.out/"choice.json"
        if not path.exists():self.select()
        value=json.loads(path.read_text(encoding="utf-8"))
        return value["chosen"]

    def select(self):
        self.checks()
        choice_path=self.out/"choice.json"
        if choice_path.exists():
            chosen=json.loads(choice_path.read_text(encoding="utf-8"))["chosen"]
            return self._choice_table(chosen)
        data=self.data;frames=data.load();cfg=self.config
        self.log("B.2: evaluating prespecified policy/threshold grid on validation selection only.")
        grid=policy_grid(frames["selection"],data.rules,data.thresholds)
        stability=self.cutoff_table("selection",data.rules,data.thresholds)
        baseline=metrics(frames["selection"],data.policy["threshold"]["value"],data.baseline)
        chosen,ranked=choose(grid,stability,baseline,cfg["selection_rule"])
        ranked["analysis_role"]="validation_selection"
        ranked.to_csv(self.out/"policy_grid_validation_selection.csv",index=False)
        stability.to_csv(self.out/"cutoff_stability_validation_selection.csv",index=False)
        ranked.sort_values(["accepted","false_alerts_per_patient_day"],ascending=[False,True]).groupby(
            "candidate",sort=False).head(1).to_csv(self.out/"policy_candidates_validation_selection.csv",index=False)
        lock_choice(self.out,chosen,data.prespec["prespec_sha256"])
        # These required handoff files are diagnostics AFTER locking the choice, not selection evidence.
        self.log("B.2 diagnostic: computing train-OOF comparison after locking the validation choice.")
        diagnostic=policy_grid(frames["train_oof"],data.rules,data.thresholds)
        diagnostic["analysis_role"]="train_oof_diagnostic_not_selection"
        diagnostic.to_csv(self.out/"policy_grid_train_oof.csv",index=False)
        diagnostic.sort_values(["false_alerts_per_patient_day","threshold"],ascending=[True,False]).groupby(
            "candidate",sort=False).head(1).to_csv(self.out/"policy_candidates_train_oof.csv",index=False)
        self._plot_grid(ranked,baseline,"policy_tradeoff_validation_selection.png","Validation selection")
        self._plot_grid(diagnostic,metrics(frames["train_oof"],data.policy["threshold"]["value"],data.baseline),
                        "policy_tradeoff_train_oof.png","Training OOF diagnostic (not selection)")
        self.log("B.2: choice sealed; no confirmation result used for selection.")
        return self._choice_table(chosen)

    @staticmethod
    def _choice_table(chosen):
        if chosen is None:
            return pd.DataFrame([{"status":"no_feasible_candidate","action":"retain default policy; no confirmation tuning"}])
        columns=["candidate","threshold","event_detection_rate","alert_ppv","false_alerts_per_patient_day",
                 "lead_time_median_h","non_aki_patients_alerted","worst_warning_flip","min_pair_coverage"]
        return pd.DataFrame([{k:chosen[k] for k in columns}])

    def _plot_grid(self,grid,baseline,filename,title):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig,ax=plt.subplots(figsize=(8,5))
        for name,group in grid.groupby("candidate",sort=False):
            group=group.sort_values("threshold")
            ax.plot(group["false_alerts_per_patient_day"],group["event_detection_rate"],lw=1,alpha=.7,label=name)
        ax.scatter([baseline["false_alerts_per_patient_day"]],[baseline["event_detection_rate"]],
                   marker="*",s=130,c="black",label="Frozen demo-v1",zorder=5)
        chosen=self._chosen()
        if chosen:
            point=grid[(grid["candidate"]==chosen["candidate"]) & np.isclose(grid["threshold"],chosen["threshold"])]
            if len(point):
                ax.scatter(point["false_alerts_per_patient_day"],point["event_detection_rate"],s=70,
                           facecolors="none",edgecolors="black",label="Locked candidate",zorder=6)
        ax.set(xlabel="False alerts per scorable monitored patient-day",ylabel="Target events detected (fraction)",title=title)
        ax.grid(alpha=.2);ax.legend(fontsize=8,ncol=2)
        fig.tight_layout();fig.savefig(self.out/filename,dpi=160);plt.close(fig)

    def confirm(self):
        self.select()
        cached=self.out/"confirmation.json"
        if cached.exists():
            value=json.loads(cached.read_text(encoding="utf-8"))
            if value["choice_sha256"] != digest(json.loads((self.out/"choice.json").read_text(encoding="utf-8"))):
                raise RuntimeError("Confirmation belongs to a different locked choice")
            return pd.read_csv(self.out/"policy_confirmation_validation.csv")
        chosen=self._chosen();data=self.data
        if chosen is None:
            result=pd.DataFrame([{"status":"not_run_no_feasible_candidate"}])
            result.to_csv(self.out/"policy_confirmation_validation.csv",index=False)
            self._export_candidate(None,{"status":"no_feasible_candidate"})
            return result
        rule=next(r for r in data.rules if r.name==chosen["candidate"]);threshold=float(chosen["threshold"])
        frame=data.load()["confirmation"];h=self.config["alert_horizon_h"]
        baseline_pp=per_patient(frame,data.policy["threshold"]["value"],data.baseline)
        candidate_pp=per_patient(frame,threshold,rule)
        result=bootstrap_comparison(baseline_pp,candidate_pp,n=self.config["bootstrap_resamples"],seed=self.config["seed"])
        result["analysis_role"]="validation_confirmation_only"
        result.to_csv(self.out/"policy_confirmation_validation.csv",index=False)
        base_metrics=legacy.summarize(baseline_pp);candidate_metrics=legacy.summarize(candidate_pp)
        stability=pd.concat([self.cutoff_table("confirmation",[data.baseline],[data.policy["threshold"]["value"]]).assign(role="baseline"),
                             self.cutoff_table("confirmation",[rule],[threshold]).assign(role="candidate")],ignore_index=True)
        stability.to_csv(self.out/"cutoff_stability_validation_confirmation.csv",index=False)
        criteria=self.config["selection_rule"];cs=stability[stability["role"]=="candidate"]
        accepted=(
            candidate_metrics["event_detection_rate"] >= max(criteria["min_event_detection_rate"],base_metrics["event_detection_rate"]-criteria["max_detection_loss"]) and
            candidate_metrics["lead_time_median_h"] >= max(criteria["min_median_lead_h"],base_metrics["lead_time_median_h"]-criteria["max_median_lead_loss_h"]) and
            candidate_metrics["alert_ppv"] >= criteria["min_ppv"] and
            candidate_metrics["alerts_per_patient_day"] <= criteria["max_alerts_per_patient_day"] and
            candidate_metrics["non_aki_patients_alerted"] <= base_metrics["non_aki_patients_alerted"]+criteria["max_non_target_patient_alert_increase"] and
            cs["warning_flip_fraction"].max() <= criteria["max_warning_flip_fraction"] and
            cs["pair_coverage"].min() >= criteria["min_pair_coverage"] and
            cs["patient_alert_flip_fraction"].max() <= criteria["max_patient_alert_flip_fraction"])
        value={"choice_sha256":digest(json.loads((self.out/"choice.json").read_text(encoding="utf-8"))),
               "status":"confirmed_internal" if accepted else "not_confirmed_no_retuning",
               "candidate_confirmed":bool(accepted),"baseline":base_metrics,"candidate":candidate_metrics,
               "test_evaluated":False,"clinical_validation":False,"patient_bootstrap_resamples":self.config["bootstrap_resamples"]}
        write_json(cached,value,exclusive=True)
        self._export_candidate(chosen,value)
        return result

    def _export_candidate(self,chosen,confirmation):
        original = self.data.policy
        policy = {"policy_version": self.config["version"],
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "model": json.loads(canonical(original["model"])),
                  "alert_horizon_h": self.config["alert_horizon_h"],
                  "display_horizons_h": [h for h in self.config["horizons"] if h != self.config["alert_horizon_h"]],
                  "monitoring_hours": self.config["monitoring_hours"],
                  "threshold": json.loads(canonical(original["threshold"])),
                  "alert_policy": json.loads(canonical(original["alert_policy"])),
                  "baseline_policy_version": original["policy_version"],
                  "selection_metrics_validation_selection": safe_json(chosen)}
        policy["threshold"]["locked"]=False
        policy["threshold"]["policy_version"]=policy["policy_version"]
        policy["threshold"]["validation_run_id"]=self.out.name+"/validation_selection"
        policy["threshold"]["stability_assessment_id"]="cutoff-"+self.data.prespec["prespec_sha256"][:20]
        policy["threshold"]["selection_basis"]="Prespecified validation patient selection with fixed-origin cutoff constraints; separate internal patient confirmation. Not clinical/test validation."
        if chosen:
            rule=next(r for r in self.data.rules if r.name==chosen["candidate"])
            policy["threshold"]["value"]=float(chosen["threshold"])
            policy["alert_policy"]=rule.to_dict()
            policy["alert_policy"]["sustain_definition"]="k consecutive hourly scores; high duration >= k-1 h; gaps reset"
        policy["deployment_status"]="research_candidate_not_for_live_alerts"
        policy["requires_stateful_alert_adapter"]=True
        policy["requires_feature_replay_review"]=True
        policy["cutoff_interpretation"]="stored-snapshot and zero-lag replay references; exported-event numerical effects reported separately"
        policy["evaluation_grid"]="hourly; minute/event-driven operation not validated"
        policy["selection_split"]="validation_selection"
        policy["confirmation_split"]="validation_confirmation"
        policy["prespec_sha256"]=self.data.prespec["prespec_sha256"]
        policy["confirmation"]=confirmation
        write_json(self.out/"policy_v1_candidate.json",policy)

    def summary(self):
        self.confirm()
        chosen=self._chosen()
        if chosen is None:
            text="No candidate met the prespecified validation and cutoff constraints; retain the default policy. No confirmation-driven tuning was performed."
        else:
            value=json.loads((self.out/"confirmation.json").read_text(encoding="utf-8"))
            a,b=value["baseline"],value["candidate"]
            text="\n".join([
                "Model: frozen LightGBM no_dc; no retraining.",
                "Choice: "+chosen["candidate"]+"; threshold "+format(chosen["threshold"],".2f")+".",
                "Selection: deterministic validation patient role with cutoff-stability constraints.",
                "Confirmation: separate validation patients; previously viewed baseline, not a final test.",
                f"Target-event detection: {a['event_detection_rate']:.3f} -> {b['event_detection_rate']:.3f}.",
                f"False alerts/scorable patient-day: {a['false_alerts_per_patient_day']:.3f} -> {b['false_alerts_per_patient_day']:.3f}.",
                f"Non-target-event patients alerted: {a['non_aki_patients_alerted']:.3f} -> {b['non_aki_patients_alerted']:.3f}.",
                f"Median first-correct-alert lead: {a['lead_time_median_h']:.2f} -> {b['lead_time_median_h']:.2f} h.",
                "Confirmation status: "+value["status"]+"; no retuning.",
                "Deployment: threshold unlocked; stateful rules not activated in the website.",
            ])
        (self.out/"SUMMARY.md").write_text(text+"\n",encoding="utf-8")
        write_json(self.out/"run_manifest.json",{"status":"complete","configuration":self.config,
            "prespec_sha256":self.data.prespec["prespec_sha256"],"data_roles":self.data.metadata(),
            "trained_model_unchanged":sha256(self.data.paths["model"])==self.data.inputs_hash["model"],
            "original_policy_unchanged":sha256(self.data.paths["policy"])==self.data.inputs_hash["policy"],
            "source_sha256":self.data.source_hash,
            "registration":"local immutable prespec before grid; no Git commit or PR performed",
            "test_used":False,"clinical_validation":False})
        return text
