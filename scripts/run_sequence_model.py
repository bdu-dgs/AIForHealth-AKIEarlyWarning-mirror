"""GRU sequence model versus the locked LightGBM on the validation split (configs/sequence_model_v1.yaml).

Go / no-go study; the test split is never loaded. `--dev N` checks the code on N patients per split (no prespec).
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from aki_report.sequence import SequenceStudy
from run_retrain_robustness import keep_full_speed

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dev", type=int, default=None)
    a = ap.parse_args()
    keep_full_speed()
    SequenceStudy(ROOT, dev={"patients": a.dev, "epochs": 2} if a.dev else None).run()
