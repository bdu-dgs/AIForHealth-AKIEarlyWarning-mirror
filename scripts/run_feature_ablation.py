"""Feature ablation of the locked model on validation patients: top-k inputs and one-group-dropped variants.

Settings: configs/feature_ablation_v1.yaml (frozen with input and code hashes on the first run). The test split is not
scored. Fitted heads are cached per variant and horizon, so an interrupted run resumes. About 2 h on 16 threads.
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from aki_report.ablation import AblationStudy
from run_retrain_robustness import keep_full_speed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variants", nargs="*", default=None, help="subset of variant names (default: all)")
    args = ap.parse_args()
    keep_full_speed()
    AblationStudy(ROOT).run(args.variants)


if __name__ == "__main__":
    main()
