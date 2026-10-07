"""Retrospective hourly scoring of dashboard patients with the locked model (batch alternative to the live worker).

For each patient registered in the running dashboard, score every whole ICU hour 1-72 that has already passed on
the dashboard clock (hours failing the coverage rule are skipped), using all observations the dashboard holds, and
import the predictions. Scoring logic is shared with the live model worker (`src/aki_ml/scoring.py`). Predictions
already in the dashboard (same record_id) are skipped. Talks only to the dashboard on 127.0.0.1.

Usage (dashboard running):
    python scripts/score_dashboard.py [--api http://127.0.0.1:8765] [--patient DEMO-002 ...] [--input-file batch.json]
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from aki_ml.scoring import (DEFAULT_POLICY, MAX_HOUR, LockedModel, latest_revisions, parse,  # noqa: E402
                            prediction_records, score_hours, stamp)

BATCH_SIZE = 2000                  # predictions per import request (contract limit 10,000)


def api(base, path, payload=None):
    req = urllib.request.Request(base + path, method="POST" if payload is not None else "GET",
                                 data=None if payload is None else json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            if "json" not in r.headers.get("Content-Type", ""):
                raise RuntimeError(f"{path}: expected JSON, got {r.headers.get('Content-Type')}")
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:500]}") from None


def patient_observations(base, pid):
    plan = api(base, f"/api/patients/{pid}/export-plan/input")
    obs = []
    for part in range(1, plan["parts"] + 1):
        obs += api(base, f"/api/patients/{pid}/export/input?part={part}&revision={plan['revision']}")["observations"]
    return latest_revisions(obs)


def existing_ids(base, pid):
    ids, offset = set(), 0
    while offset is not None:
        page = api(base, f"/api/patients/{pid}/history?kind=prediction&limit=10000&offset={offset}")
        ids |= {p["record_id"] for p in page["items"]}
        offset = page["next_offset"]
    return ids


def score_patient(base, model, patient, generated_at):
    pid = patient["patient_id"]
    admit = parse(patient["icu_admitted_at"])
    last_hour = min(MAX_HOUR, int((generated_at - admit) / timedelta(hours=1)))
    hours, scored = score_hours(model, patient_observations(base, pid), admit, np.arange(1, last_hour + 1))
    if not scored:
        return []
    existing = existing_ids(base, pid)
    out = []
    for k, hour in enumerate(hours.astype(int)):
        origin = admit + timedelta(hours=int(hour))
        q = urllib.parse.urlencode({"as_of": stamp(generated_at), "cutoff": stamp(origin)})
        fp = api(base, f"/api/patients/{pid}/snapshot?{q}")["input_fingerprint"]
        key = f"{pid}-{model.policy['policy_version']}-t{hour:02d}-{fp[:12]}"
        out += [p for p in prediction_records(model, patient, origin, fp, patient["input_revision"], generated_at,
                                              scored, k, key) if p["record_id"] not in existing]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--api", default="http://127.0.0.1:8765")
    ap.add_argument("--policy", default=str(DEFAULT_POLICY))
    ap.add_argument("--patient", action="append", help="patient_id to score (default: all)")
    ap.add_argument("--input-file", help="InputBatch JSON to import before scoring (e.g. synthetic patients)")
    ap.add_argument("--out", help="also write the PredictionBatch JSON here")
    ap.add_argument("--dry-run", action="store_true", help="score but do not import")
    args = ap.parse_args()
    base = args.api.rstrip("/")

    if args.input_file:
        print(f"input import: {api(base, '/api/import', json.loads(Path(args.input_file).read_text(encoding='utf-8')))}")
    model = LockedModel(args.policy)
    # generated_at must not be later than the dashboard clock used for the fingerprint check.
    generated_at = parse(api(base, "/api/health")["server_time"]).replace(microsecond=0)
    patients = [p for p in api(base, "/api/patients") if not args.patient or p["patient_id"] in args.patient]
    predictions = []
    for patient in patients:
        rows = score_patient(base, model, patient, generated_at)
        above = [p for p in rows if "threshold" in p and p["risk"] >= p["threshold"]["value"]]
        print(f"{patient['patient_id']}: {len(rows)} new predictions, {len(above)} hourly 24h results above threshold")
        predictions += rows
    if args.out:
        Path(args.out).write_text(json.dumps({"schema_version": 1, "kind": "prediction", "predictions": predictions},
                                             ensure_ascii=False, indent=1), encoding="utf-8")
    if args.dry_run or not predictions:
        print("nothing imported" if not predictions else f"dry run: {len(predictions)} predictions not imported")
        return
    for s in range(0, len(predictions), BATCH_SIZE):
        res = api(base, "/api/import", {"schema_version": 1, "kind": "prediction",
                                        "predictions": predictions[s:s + BATCH_SIZE]})
        print(f"imported {res['inserted']} predictions")


if __name__ == "__main__":
    main()
