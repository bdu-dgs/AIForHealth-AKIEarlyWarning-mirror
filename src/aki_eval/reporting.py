"""Aggregate-only report finalization; never reads patient rows or changes the chosen policy."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def point_constraints(grid, baseline, criteria):
    return ((grid["event_detection_rate"] >= max(criteria["min_event_detection_rate"],
              baseline["event_detection_rate"]-criteria["max_detection_loss"])) &
            (grid["lead_time_median_h"] >= max(criteria["min_median_lead_h"],
              baseline["lead_time_median_h"]-criteria["max_median_lead_loss_h"])) &
            (grid["alert_ppv"] >= criteria["min_ppv"]) &
            (grid["alerts_per_patient_day"] <= criteria["max_alerts_per_patient_day"]) &
            (grid["non_aki_patients_alerted"] <= baseline["non_aki_patients_alerted"]+
              criteria["max_non_target_patient_alert_increase"]))


def diagnostic_representatives(grid, baseline, criteria):
    data=grid.copy()
    data["accepted_point_constraints"]=point_constraints(data,baseline,criteria)
    rows=[]
    for name,group in data.groupby("candidate",sort=False):
        ok=group[group["accepted_point_constraints"]]
        if ok.empty:
            rows.append({"candidate":name,"status":"no_point_feasible_operating_point",
                         "analysis_role":"train_oof_diagnostic_not_selection"})
        else:
            row=ok.sort_values(["false_alerts_per_patient_day","non_aki_patients_alerted","threshold"],
                               ascending=[True,True,False]).iloc[0].to_dict()
            row["status"]="point_feasible_diagnostic"
            rows.append(row)
    result=pd.DataFrame(rows)
    result["cutoff_evidence"]="not available for train OOF; not selection or confirmation"
    return result


def _hash(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source,"sha256").hexdigest()


def finalize_reports(directory):
    out=Path(directory)
    immutable={name:_hash(out/name) for name in ["prespec.json","choice.json"]+
               (["confirmation.json"] if (out/"confirmation.json").exists() else [])}
    spec=json.loads((out/"prespec.json").read_text(encoding="utf-8"))["specification"]["config"]
    criteria=spec["selection_rule"]
    if spec.get("training_oof_role")!="none":
        baseline_table=pd.read_csv(out/"baseline_reproduction.csv")
        baseline_oof=baseline_table[baseline_table["data"]=="train_oof"].iloc[0].to_dict()
        grid=pd.read_csv(out/"policy_grid_train_oof.csv")
        diagnostic_representatives(grid,baseline_oof,criteria).to_csv(out/"policy_candidates_train_oof.csv",index=False)
    if (out/"confirmation.json").exists():
        confirmation=json.loads((out/"confirmation.json").read_text(encoding="utf-8"))
        a,b=confirmation["baseline"],confirmation["candidate"]
        stability=pd.read_csv(out/"cutoff_stability_validation_confirmation.csv")
        stability=stability[stability["role"]=="candidate"]
        definitions=[
            ("event_detection_rate",b["event_detection_rate"],">=",max(criteria["min_event_detection_rate"],a["event_detection_rate"]-criteria["max_detection_loss"])),
            ("lead_time_median_h",b["lead_time_median_h"],">=",max(criteria["min_median_lead_h"],a["lead_time_median_h"]-criteria["max_median_lead_loss_h"])),
            ("alert_ppv",b["alert_ppv"],">=",criteria["min_ppv"]),
            ("alerts_per_patient_day",b["alerts_per_patient_day"],"<=",criteria["max_alerts_per_patient_day"]),
            ("non_target_event_patients_alerted",b["non_aki_patients_alerted"],"<=",a["non_aki_patients_alerted"]+criteria["max_non_target_patient_alert_increase"]),
            ("worst_point_warning_flip",stability["warning_flip_fraction"].max(),"<=",criteria["max_warning_flip_fraction"]),
            ("worst_patient_alert_flip",stability["patient_alert_flip_fraction"].max(),"<=",criteria["max_patient_alert_flip_fraction"]),
            ("minimum_paired_coverage",stability["pair_coverage"].min(),">=",criteria["min_pair_coverage"]),
        ]
        rows=[{"criterion":name,"observed":float(value),"comparison":comparison,"limit":float(limit),
               "passed":bool(value>=limit if comparison==">=" else value<=limit)}
              for name,value,comparison,limit in definitions]
        table=pd.DataFrame(rows)
        assert bool(table["passed"].all()) == confirmation["candidate_confirmed"]
        table.to_csv(out/"confirmation_acceptance_checks.csv",index=False)
        failed=table.loc[~table["passed"],"criterion"].tolist()
    else:
        failed=["no_feasible_candidate"]
    assert all(_hash(out/name)==value for name,value in immutable.items())
    record={"role":"report-only; no candidate selection or confirmation re-evaluation",
            "reporter_sha256":_hash(__file__),"immutable_analysis_files_unchanged":True,
            "failed_confirmation_criteria":failed}
    (out/"reporting_manifest.json").write_text(json.dumps(record,indent=2),encoding="utf-8")
    return record
