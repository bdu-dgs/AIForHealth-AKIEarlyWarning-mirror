"""Plan item 6: train on one MIMIC-III charting system (CareVue / MetaVision), test on the other.

Settings: configs/system_shift_v1.yaml (frozen with input and code hashes on the first run). Fitted heads are cached
per variant and horizon under artifacts/models/<run_id>/system-shift-v1/, so an interrupted run resumes.
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from aki_report.system_shift import SystemShiftStudy
from run_retrain_robustness import keep_full_speed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variants", nargs="*", default=None)
    args = ap.parse_args()
    keep_full_speed()
    SystemShiftStudy(ROOT).run(args.variants)


if __name__ == "__main__":
    main()
