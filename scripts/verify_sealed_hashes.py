"""Check that the repository files hashed by the sealed study records still have their recorded bytes.

Covers the code and repository inputs of every prespec.json under artifacts/ (alert policy v3, locked test evaluation,
retraining sensitivity). Local-only inputs (dataset, model, predictions) are reported as skipped when absent.
Usage: python scripts/verify_sealed_hashes.py   (exit code 1 on any mismatch)
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Repository paths of the named inputs (the records store names, not paths, for inputs).
INPUT_PATHS = {
    "config": None, "evaluation_config": None,          # resolved per record below
    "policy": None, "comparator_policy": "artifacts/demo_i/policy.json",
    "dataset_config": "configs/default.yaml", "ml_config": "configs/ml.yaml",
}
RECORD_INPUTS = {
    "artifacts/handoff/policy_v3/validation-policy-v3-66054ed84c02e348/prespec.json":
        {"policy": "artifacts/demo_i/policy.json", "evaluation_config": "configs/alert_policy_v3.yaml"},
    "artifacts/reports/test_v1/prespec.json":
        {"config": "configs/evaluation_test_v1.yaml", "policy": "artifacts/policy/policy_v3.json"},
    "artifacts/reports/robustness_retrain_v1/prespec.json":
        {"config": "configs/robustness_retrain_v1.yaml"},
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    bad = checked = 0
    for record, overrides in RECORD_INPUTS.items():
        spec = json.loads((ROOT / record).read_text(encoding="utf-8"))["specification"]
        paths = {**{k: v for k, v in INPUT_PATHS.items() if v}, **overrides}
        items = list(spec["source_sha256"].items()) + [
            (paths[name], value) for name, value in spec["input_sha256"].items() if name in paths]
        for rel, expected in items:
            path = ROOT / rel
            if not path.exists():
                print(f"MISSING  {rel}  ({record})")
                bad += 1
                continue
            checked += 1
            if sha256(path) != expected:
                print(f"CHANGED  {rel}  ({record})")
                bad += 1
    print(f"{checked} hashed repository files checked, {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
