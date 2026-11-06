"""Run the frozen Ke Jin research workflow locally; no model training or live activation."""
import argparse
import os
from pathlib import Path
import sys

for name in ["OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS"]:
    os.environ.setdefault(name,"2")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from threadpoolctl import threadpool_limits
from aki_eval.study import KeJinStudy
sys.path.insert(0,str(ROOT))
from scripts.summarize_alert_policy import finalize_reports


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root",default=None,help="authorized Cleaned companion directory")
    parser.add_argument("--output-root",default=None,help="local aggregate-output root")
    parser.add_argument("--config",default=None,help="versioned study YAML")
    parser.add_argument("--checks-only",action="store_true")
    args=parser.parse_args()
    with threadpool_limits(limits=2):
        study=KeJinStudy(ROOT,config_path=args.config,data_root=args.data_root,output_root=args.output_root)
        print(study.reproduce_baseline().to_string(index=False))
        print(study.checks().to_string(index=False))
        if not args.checks_only:
            print(study.select().to_string(index=False))
            print(study.confirm().to_string(index=False))
            print(study.summary())
            print(finalize_reports(study.out))
        print("Aggregate outputs:",study.out)


if __name__=="__main__":
    main()
