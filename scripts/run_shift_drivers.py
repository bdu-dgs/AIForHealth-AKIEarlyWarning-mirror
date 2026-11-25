"""Which inputs make a moved model over- or under-estimate risk? (CareVue <-> MetaVision, follow-up to system-shift-v1)

Settings: configs/shift_drivers_v1.yaml (frozen with input and code hashes on the first run). Training and validation
patients only; the test split is not loaded. Needs the local system-shift-v1 heads. About 20 min.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from aki_report.shift_drivers import ShiftDrivers
from run_retrain_robustness import keep_full_speed

if __name__ == "__main__":
    keep_full_speed()
    ShiftDrivers(ROOT).run()
