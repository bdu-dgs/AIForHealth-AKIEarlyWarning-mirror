"""Live model worker: scores every new input revision of every patient and imports the predictions.

Event-driven: each accepted input batch creates a patient input revision at the time it arrived (service clock,
which is the demo clock during playback). For every revision not yet scored, the worker predicts at that time
(origin = data cutoff = arrival time) from all observations measured up to then, using the locked model in
`artifacts/demo_i/policy.json` and the shared scoring code in `src/aki_ml/scoring.py`. Times outside ICU hours
1-72 or before the first usable creatinine get no prediction. Revisions are never skipped, so stepping the demo
clock several hours at once still gives one prediction per hour.

Started by the launcher; runs only against the local service on 127.0.0.1:
    python -m dashboard.model_worker --api http://127.0.0.1:8765
"""
import argparse
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

HEARTBEAT_SECONDS = 5
POLL_SECONDS = 0.3
GIVE_UP_SECONDS = 60          # exit when the website has been unreachable this long (e.g. its window was closed)


def call(base, path, payload=None, timeout=120):
    req = urllib.request.Request(base + path, method="POST" if payload is not None else "GET",
                                 data=None if payload is None else json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}") from None


class Worker:
    def __init__(self, base, model):
        from aki_ml import scoring
        self.base, self.model, self.sc = base, model, scoring
        self.scored = {}              # patient_id -> highest input revision already handled
        self.status, self.detail, self.last_beat = "ready", "", 0.0

    def beat(self, force=False):
        if force or time.monotonic() - self.last_beat >= HEARTBEAT_SECONDS:
            call(self.base, "/api/model/heartbeat", {
                "status": self.status, "model_id": self.sc.MODEL_ID, "model_version": self.model.version,
                "policy_version": self.model.policy["policy_version"], "detail": self.detail[:500]}, timeout=10)
            self.last_beat = time.monotonic()

    def handled_revision(self, pid):
        """Highest input revision with a stored prediction (restart-safe starting point)."""
        best, offset = 0, 0
        while offset is not None:
            page = call(self.base, f"/api/patients/{pid}/history?kind=prediction&limit=10000&offset={offset}")
            best = max([best] + [p["input_revision"] for p in page["items"] if p["model_id"] == self.sc.MODEL_ID])
            offset = page["next_offset"]
        return best

    def observations(self, pid):
        """Latest revision of every observation known now (history endpoint; faster than the export)."""
        obs, offset = [], 0
        while offset is not None:
            page = call(self.base, f"/api/patients/{pid}/history?kind=observation&limit=10000&offset={offset}")
            obs += page["items"]
            offset = page["next_offset"]
        return obs

    def sync(self):
        """Score every patient's new revisions, then import all results in one batch (one page refresh)."""
        records, done, t0 = [], {}, time.monotonic()
        generated_at = self.sc.parse(call(self.base, "/api/health")["server_time"]).replace(microsecond=0)
        jobs = []
        for patient in call(self.base, "/api/patients"):
            pid = patient["patient_id"]
            if pid not in self.scored:
                self.scored[pid] = self.handled_revision(pid)
            if patient["input_revision"] <= self.scored[pid]:
                continue
            # Revisions that arrived after generated_at (e.g. a demo step during this sync) wait for the next sync.
            todo = [r for r in call(self.base, f"/api/patients/{pid}/revisions")
                    if r["input_revision"] > self.scored[pid] and self.sc.parse(r["available_at"]) <= generated_at]
            if todo:
                jobs.append((patient, todo))
                done[pid] = max(r["input_revision"] for r in todo)
        with ThreadPoolExecutor(max_workers=4) as pool:     # patients are independent
            for out in pool.map(lambda job: self.score(*job, generated_at), jobs):
                records += out
        for s in range(0, len(records), 2000):
            call(self.base, "/api/import", {"schema_version": 1, "kind": "prediction", "predictions": records[s:s + 2000]})
        self.scored.update(done)
        if done:
            print(f"scored {len(done)} patient(s), {len(records)} predictions in {time.monotonic() - t0:.1f} s", flush=True)

    def score(self, patient, revisions, generated_at):
        sc, pid = self.sc, patient["patient_id"]
        admit = sc.parse(patient["icu_admitted_at"])
        origins = [sc.parse(r["available_at"]).replace(microsecond=0) for r in revisions]
        hours = [(o - admit) / timedelta(hours=1) for o in origins]
        obs = self.observations(pid)
        kept, scored = sc.score_hours(self.model, obs, admit, hours)
        records = []
        for k, hour in enumerate(kept):
            i = hours.index(hour)
            origin, version = origins[i], revisions[i]["input_revision"]
            q = urllib.parse.urlencode({"as_of": sc.stamp(generated_at), "cutoff": sc.stamp(origin)})
            fp = call(self.base, f"/api/patients/{pid}/snapshot?{q}")["input_fingerprint"]
            key = f"{pid}-{self.model.policy['policy_version']}-r{version}"
            records += sc.prediction_records(self.model, patient, origin, fp, version, generated_at, scored, k, key)
        return records

    def run(self):
        last_revision, reachable = None, time.monotonic()
        while True:
            try:
                revision = call(self.base, "/api/health", timeout=10)["revision"]
                reachable = time.monotonic()
                if revision != last_revision:
                    self.status = "scoring"
                    self.beat(force=True)
                    self.sync()
                    last_revision, self.status, self.detail = revision, "ready", ""
                self.beat()
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                if time.monotonic() - reachable > GIVE_UP_SECONDS:
                    print("Website unreachable; model worker stopping.", flush=True)
                    return
                time.sleep(2)                 # service starting or briefly unavailable
                continue
            except Exception as error:        # keep serving other patients; report in the header
                traceback.print_exc()
                self.status, self.detail = "error", f"{type(error).__name__}: {error}"
                try:
                    self.beat(force=True)
                except Exception:
                    pass
                last_revision = None
                time.sleep(5)
            time.sleep(POLL_SECONDS)


def main():
    ap = argparse.ArgumentParser(description="AKI live model worker")
    ap.add_argument("--api", default="http://127.0.0.1:8765")
    ap.add_argument("--policy", default=None, help="policy.json (default artifacts/demo_i/policy.json)")
    args = ap.parse_args()
    try:
        from aki_ml.scoring import DEFAULT_POLICY, LockedModel
        model = LockedModel(args.policy or DEFAULT_POLICY)
    except Exception as error:
        print(f"Model worker not started: {type(error).__name__}: {error}", flush=True)
        return 1
    print(f"Model worker: {model.version} / {model.policy['policy_version']} -> {args.api}", flush=True)
    Worker(args.api.rstrip("/"), model).run()


if __name__ == "__main__":
    sys.exit(main())
