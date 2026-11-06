"""Build (or verify) the collaborator hand-off folder: code at HEAD, the locked model and predictions, and the
dataset outputs restricted to validation patients. Training features and the test split are never copied.

    python scripts/make_handoff.py --out ../AKI_handoff_v1     # build (needs a clean git worktree)
    python scripts/make_handoff.py --verify <handoff folder>   # recipients: check SHA256SUMS.txt
"""
import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "aki_ml_v1_28e0b1fb83"
SPLIT = "validation"
DEMO_I_FILES = ["*.json", "*.csv", "*.png"]
DEMO_I_EXCLUDE = {"patients_now.json"}          # dashboard runtime state, not an evaluation output


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, check=True).stdout


def copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def filter_parquet(src, dst, keep):
    """Stream `src` to `dst`, keeping rows where keep(record_batch) is true (bounded memory)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    f = pq.ParquetFile(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with pq.ParquetWriter(dst, f.schema_arrow, compression="zstd") as writer:
        for batch in f.iter_batches(batch_size=50_000):
            part = batch.filter(keep(batch))
            if part.num_rows:
                writer.write_table(pa.Table.from_batches([part], schema=f.schema_arrow))
                rows += part.num_rows
    return rows


def build(out):
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    import yaml

    if git("status", "--porcelain", "--untracked-files=no").strip():
        sys.exit("Commit or stash code changes first: the hand-off must match one commit.")
    if out.exists() and any(out.iterdir()):
        sys.exit(f"{out} is not empty.")
    commit = git("rev-parse", "HEAD").decode().strip()
    repo_out = out / REPO_ROOT.name
    repo_out.mkdir(parents=True)
    tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", "HEAD")), mode="r:").extractall(repo_out, filter="data")
    shutil.copy2(REPO_ROOT / "handoff" / "HANDOFF.md", out / "HANDOFF.md")

    art, art_out = REPO_ROOT / "artifacts", repo_out / "artifacts"
    for src in (art / "models" / RUN_ID).rglob("*"):
        if src.is_file() and "_heads" not in src.relative_to(art / "models" / RUN_ID).parts:
            copy(src, art_out / src.relative_to(art))
    for name in [f"{RUN_ID}.parquet", f"{RUN_ID}_event_replay.parquet"]:
        copy(art / "predictions" / name, art_out / "predictions" / name)
    for pattern in DEMO_I_FILES:
        for src in (art / "demo_i").glob(pattern):
            if src.name not in DEMO_I_EXCLUDE:
                copy(src, art_out / "demo_i" / src.name)
    sets = pq.read_table(art / "predictions" / f"{RUN_ID}.parquet", columns=["prediction_set"]).column(0).unique()
    assert set(sets.to_pylist()) == {"train_oof", "validation"}, "predictions must not contain test rows"

    cfg = yaml.safe_load((REPO_ROOT / "configs" / "default.yaml").read_text(encoding="utf-8"))
    data = (REPO_ROOT / cfg["paths"]["output_root"]).resolve()
    data_out = out / "Cleaned"
    split = pq.read_table(data / "splits" / "patient_split.parquet").to_pandas()
    stays = split.loc[split["split"] == SPLIT, "stay_id"].astype(str).tolist()
    rows = {
        "snapshot_dataset_validation": filter_parquet(data / "datasets" / "snapshot_dataset.parquet",
                                                      data_out / "datasets" / "snapshot_dataset_validation.parquet",
                                                      lambda b: pc.equal(pc.cast(b.column("split"), "string"), SPLIT)),
        "event_stream_validation": filter_parquet(data / "datasets" / "event_stream.parquet",
                                                  data_out / "datasets" / "event_stream_validation.parquet",
                                                  lambda b: pc.is_in(b.column("stay_id"), value_set=pa.array(stays))),
    }
    for rel in ["datasets/feature_dictionary.csv", "splits/patient_split.parquet", "dataset_manifest.json"]:
        copy(data / rel, data_out / rel)
    for src in (data / "reports").glob("*"):
        copy(src, data_out / "reports" / src.name)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": commit,
        "run_id": RUN_ID,
        "subset": {"patients": f"split == '{SPLIT}' only (training features and the test split are not included)",
                   "predictions": "train_oof + validation (no test rows)", "rows": rows},
        "source_sha256": {"snapshot_dataset.parquet": sha256(data / "datasets" / "snapshot_dataset.parquet"),
                          "event_stream.parquet": sha256(data / "datasets" / "event_stream.parquet")},
        "license": "MIMIC-III v1.4 derived data; recipients must hold their own PhysioNet credentialed access.",
    }
    (out / "HANDOFF_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    files = sorted(p for p in out.rglob("*") if p.is_file())
    (out / "SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.relative_to(out).as_posix()}\n" for p in files),
                                        encoding="utf-8")
    size = sum(p.stat().st_size for p in files) / 1e9
    print(f"hand-off written to {out} ({len(files)} files, {size:.2f} GB, commit {commit[:10]}); rows: {rows}")


def verify(root):
    bad = []
    lines = (root / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    for line in lines:
        digest, rel = line.split("  ", 1)
        path = root / rel
        if not path.is_file() or sha256(path) != digest:
            bad.append(rel)
    print(f"{len(lines) - len(bad)}/{len(lines)} files OK")
    if bad:
        sys.exit("missing or changed: " + ", ".join(bad[:20]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--out", type=Path)
    g.add_argument("--verify", type=Path)
    a = ap.parse_args()
    build(a.out.resolve()) if a.out else verify(a.verify.resolve())
