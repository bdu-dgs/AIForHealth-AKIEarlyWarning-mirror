"""Record the demo playback (synthetic patients, live locked model) for the static website build.

Runs the real backend in demo mode in a temporary data directory, steps the clock one hour at a time, and lets the
model worker score every hour before the next step, exactly as `Start-AKI-Demo.cmd` does. The result is one JSON file
with every observation, prediction, input revision and input fingerprint, each with its availability time, so the
static site can answer the same API questions in the browser for any demo-clock time.

Only the synthetic patients of scripts/make_synthetic_demo_patients.py are used; nothing is derived from MIMIC records
except the fitted model's outputs. Usage:
    python scripts/export_static_demo.py [--out dashboard/web/public/static-demo/demo.json] [--hours 72]
"""
import argparse
import json
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import uvicorn  # noqa: E402

from dashboard.api.main import create_app  # noqa: E402
from dashboard.launcher import demo_batch  # noqa: E402
from dashboard.model_worker import Worker, call  # noqa: E402

DEMO_START = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)   # fictional, fixed for a reproducible export


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "dashboard" / "web" / "public" / "static-demo" / "demo.json"))
    ap.add_argument("--hours", type=int, default=72)
    args = ap.parse_args()
    from aki_ml.scoring import DEFAULT_POLICY, LockedModel
    model = LockedModel(DEFAULT_POLICY)
    with tempfile.TemporaryDirectory(prefix="aki-static-demo-") as tmp:
        app = create_app(tmp, watch=False, demo_batch=demo_batch(None), demo_start=DEMO_START)
        port = free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                               access_log=False, loop="asyncio", http="h11", ws="none"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        while not server.started:
            time.sleep(0.1)
        worker = Worker(base, model)
        worker.beat(force=True)
        health = call(base, "/api/health")

        def score_until_done():
            for _ in range(50):
                if worker.sync():
                    return
                time.sleep(0.2)
            raise RuntimeError("model worker did not catch up")

        t0 = time.time()
        score_until_done()
        for hour in range(1, args.hours + 1):
            call(base, "/api/demo/advance?hours=1", {})
            score_until_done()
            if hour % 12 == 0:
                print(f"hour {hour}: {time.time() - t0:.0f} s", flush=True)
        health = call(base, "/api/health")
        store = app.state.store
        patients, records, revisions, fingerprints = [], {"observation": [], "prediction": []}, {}, {}
        with store.connection() as db:
            for row in db.execute("SELECT * FROM patients ORDER BY id"):
                patients.append(json.loads(row["body"]))
            for row in db.execute("SELECT kind, body FROM records ORDER BY kind, patient, event_at, id, revision"):
                records[row["kind"]].append(json.loads(row["body"]))
            for row in db.execute("SELECT patient, version, available_at FROM revisions ORDER BY patient, version"):
                revisions.setdefault(row["patient"], []).append([row["version"], row["available_at"]])
            # Input fingerprint as a step function of knowledge time: it changes only when an observation becomes
            # available (snapshot with as_of == cutoff == t, as the backend computes it).
            for p in patients:
                pid = p["patient_id"]
                times = sorted({r["available_at"] for r in records["observation"] if r["patient_id"] == pid})
                steps = [[health["clock"]["start"], store.snapshot(db, pid, health["clock"]["start"], health["clock"]["start"])]]
                for t in times:
                    fp = store.snapshot(db, pid, t, t)
                    if t <= steps[0][0]:
                        steps[0][1] = fp
                    elif fp != steps[-1][1]:
                        steps.append([t, fp])
                fingerprints[pid] = steps
        server.should_exit = True
        thread.join(timeout=10)
    out = {
        "schema": "aki-static-demo-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": "Synthetic patients only (scripts/make_synthetic_demo_patients.py); risks from the locked model.",
        "clock": {"start": health["clock"]["start"], "hours": args.hours},
        "model": health["model"],
        "patients": patients,
        "revisions": revisions,
        "fingerprints": fingerprints,
        "observations": records["observation"],
        "predictions": records["prediction"],
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"{path}: {len(patients)} patients, {len(records['observation'])} observations, "
          f"{len(records['prediction'])} predictions, {path.stat().st_size / 1e6:.1f} MB", flush=True)


if __name__ == "__main__":
    main()
