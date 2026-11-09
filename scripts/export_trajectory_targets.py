"""Export a separate, local future-creatinine target source without changing notebook 01."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aki_ml.target_export import export_trajectory_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--lab-view", required=True, help="complete standardized VIEWS['lab'] Parquet from notebook 01")
    parser.add_argument("--output", required=True, help="new local Parquet path (with .json sidecar)")
    parser.add_argument("--end-hour", type=float, required=True, help="last origin + maximum lead + matching tolerance")
    parser.add_argument("--complete-lab-view", action="store_true", help="confirm full standardized source, not truncated event_stream")
    args = parser.parse_args()
    info = export_trajectory_source(args.data_root, args.lab_view, args.output, args.end_hour,
                                    complete_lab_view=args.complete_lab_view)
    print(f"Exported {info['rows']} local target measurements; test patients excluded.")


if __name__ == "__main__":
    main()
