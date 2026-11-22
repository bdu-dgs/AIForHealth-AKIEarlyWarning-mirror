"""Recalibrating a transported model on target-system validation patients (configs/system_recalibration_v1.yaml).

Frozen with input and code hashes on the first run; outputs are aggregate tables under artifacts/reports/.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aki_report.recalibration import RecalibrationStudy

if __name__ == "__main__":
    RecalibrationStudy(ROOT).run()
