"""Exploratory data analysis tables for the final report (training split for all distributional analyses).

Output: artifacts/reports/eda_v1/ (aggregate tables only).
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aki_report.eda import EDA

if __name__ == "__main__":
    EDA(ROOT).run()
