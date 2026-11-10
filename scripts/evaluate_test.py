"""Run notebook 03's locked evaluation. Default: the test split (once). --split validation is a dry run."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aki_final.test_evaluation import LockedEvaluation


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", default=None, help="validation for a dry run; default from the config (test)")
    ap.add_argument("--steps", nargs="*", default=None, help="subset of: snapshot alerts subgroups robustness shap event_replay")
    args = ap.parse_args()
    ev = LockedEvaluation(ROOT, split=args.split)
    steps = args.steps or ["snapshot", "alerts", "subgroups", "robustness", "shap", "event_replay"]
    for name in steps:
        ev.log(f"--- {name}")
        getattr(ev, {"snapshot": "snapshot_table", "alerts": "alert_table", "subgroups": "subgroup_table",
                     "robustness": "robustness_table", "shap": "shap_table", "event_replay": "event_replay_table"}[name])()
    if args.steps is None:
        ev.run_manifest()
    print("outputs:", ev.out)


if __name__ == "__main__":
    main()
