"""Validation-only development of fixes for alert-budget-v1 (configs/alert_budget_v2_dev.yaml); test is never loaded."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aki_report.budget_dev import BudgetDev

if __name__ == "__main__":
    BudgetDev(ROOT).run()
