"""The diagnostic representative must obey point constraints, not just minimise false alerts."""
from pathlib import Path
import sys
import pandas as pd
import yaml

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.summarize_alert_policy import diagnostic_representatives


def test_diagnostic_summary_rejects_low_false_alarm_but_poor_detection():
    root=Path(__file__).resolve().parents[1]
    c=yaml.safe_load((root/"tests/fixtures/alert_policy_v2_legacy.yaml").read_text(encoding="utf-8"))["selection_rule"]
    base={"event_detection_rate":.76,"lead_time_median_h":12.5,"non_aki_patients_alerted":.37}
    values={"candidate":"rule","alert_ppv":.4,"alerts_per_patient_day":.6,
            "lead_time_median_h":12.,"non_aki_patients_alerted":.3,"analysis_role":"train_oof_diagnostic_not_selection"}
    grid=pd.DataFrame([{"threshold":.2,"event_detection_rate":.75,"false_alerts_per_patient_day":.3,**values},
                       {"threshold":.4,"event_detection_rate":.5,"false_alerts_per_patient_day":.1,**values}])
    result=diagnostic_representatives(grid,base,c)
    assert result["threshold"].iloc[0]==.2
    bad=diagnostic_representatives(grid.iloc[[1]],base,c)
    assert bad["status"].iloc[0]=="no_point_feasible_operating_point"
