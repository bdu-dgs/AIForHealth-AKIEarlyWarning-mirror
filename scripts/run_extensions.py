"""Run the small extension baselines locally; never trains the original production candidate."""
import argparse
import json
import os
from pathlib import Path
import sys

# Keep this development command's numerical work bounded; no persistent system settings.
for name in ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"]:
    os.environ.setdefault(name, "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aki_ml.experiments import classification_experiment, save_experiment, trajectory_experiment
from aki_ml.research_data import load_data
from aki_ml.synthetic import make_research_data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--synthetic", action="store_true", help="fictitious data; no clinical performance claims")
    source.add_argument("--data-root", help="notebook 01 output root with verified dataset manifest")
    parser.add_argument("--trajectory-source", help="optional extended target Parquet with .json completeness/hash sidecar")
    parser.add_argument("--task", choices=["primary", "combined", "trajectory", "all"], default="all")
    parser.add_argument("--output-root", default=None, help="local artifact directory (default: ignored artifacts/extensions)")
    args = parser.parse_args()
    data = make_research_data() if args.synthetic else load_data(args.data_root, trajectory_source=args.trajectory_source)
    for task in (["primary", "combined", "trajectory"] if args.task == "all" else [args.task]):
        result = (trajectory_experiment(data) if task == "trajectory" else
                  classification_experiment(data, endpoint="creatinine" if task == "primary" else "combined"))
        path = save_experiment(result, args.output_root)
        print(json.dumps({"task": task, "artifact_directory": str(path), "source": data.provenance["source"],
                          "test_used": False}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
