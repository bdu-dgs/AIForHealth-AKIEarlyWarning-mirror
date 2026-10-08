"""Write a "new lab results" InputBatch for a live upload during demo playback.

The file holds one fictional lab panel for a synthetic patient, drawn one hour before the current demo clock and
reported now (labs are usable by the model one hour after the draw), so uploading it on the Data entry tab makes
the model update that patient's risk immediately. Run it right before the upload: the times follow the demo clock.

Usage (website running with Start-AKI-Demo.cmd):
    python scripts/make_demo_upload.py [--patient DEMO-004] [--creatinine 2.4] [--out demo_upload.json]
"""
import argparse
import json
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

LABS = {  # metric -> (label, unit)
    "creatinine": ("Creatinine", "mg/dL"), "bun": ("BUN", "mg/dL"), "potassium": ("Potassium", "mEq/L"),
    "bicarbonate": ("Bicarbonate", "mEq/L"),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--api", default="http://127.0.0.1:8765")
    ap.add_argument("--patient", default="DEMO-004")
    ap.add_argument("--creatinine", type=float, default=2.4)
    ap.add_argument("--out", default="demo_upload.json")
    args = ap.parse_args()
    base = args.api.rstrip("/") + "/api"
    clock = json.load(urllib.request.urlopen(base + "/clock"))
    patient = json.load(urllib.request.urlopen(f"{base}/patients/{args.patient}"))
    now = datetime.fromisoformat(clock["now"].replace("Z", "+00:00"))
    drawn, z = now - timedelta(hours=1), lambda t: t.isoformat().replace("+00:00", "Z")
    values = {"creatinine": args.creatinine, "bun": 48, "potassium": 5.4, "bicarbonate": 19}
    tag = now.strftime("%Y%m%dT%H%M")
    observations = [{"record_id": f"{args.patient}-upload-{m}-{tag}", "revision": 1, "patient_id": args.patient,
                     "encounter_id": patient["encounter_id"], "metric": m, "label": LABS[m][0], "unit": LABS[m][1],
                     "value": v, "measured_at": z(drawn), "available_at": z(now)} for m, v in values.items()]
    batch = {"schema_version": 1, "kind": "input", "patients": [], "observations": observations, "actor": "demo-upload"}
    Path(args.out).write_text(json.dumps(batch, indent=1), encoding="utf-8")
    print(f"{args.out}: {len(observations)} lab results for {args.patient} drawn {z(drawn)}, reported {z(now)}")


if __name__ == "__main__":
    main()
