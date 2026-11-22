"""Final-report secondary analyses on the test split (severity, baselines, alert trade-off, decision curves, curves).

Settings: configs/final_analyses_v1.yaml (frozen with input and code hashes on the first full run).
`--dev DIR --patients N` checks the code on N test patients, writes to DIR and records no prespec.
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aki_report.final_analyses import FinalAnalyses


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dev", default=None, help="output directory for a code check (no prespec record)")
    ap.add_argument("--patients", type=int, default=None, help="test patients used by --dev")
    args = ap.parse_args()
    if args.patients and not args.dev:
        raise SystemExit("--patients is only for --dev runs")
    FinalAnalyses(ROOT, dev_dir=args.dev, dev_patients=args.patients).run()


if __name__ == "__main__":
    main()
