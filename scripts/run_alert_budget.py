"""Alert-budget control (configs/alert_budget_v1.yaml): group recalibration, budget-certified threshold, detection parity.

Calibrates on validation patients and evaluates on test patients (a prespecified second test look for this new alerting
layer; the locked model is unchanged). Also replays the CareVue <-> MetaVision shift with the system-shift-v1 models.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aki_report.budget import BudgetStudy

if __name__ == "__main__":
    BudgetStudy(ROOT).run()
