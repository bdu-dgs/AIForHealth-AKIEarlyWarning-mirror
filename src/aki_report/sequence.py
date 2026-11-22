"""GRU sequence model versus the locked LightGBM, on the validation split (configs/sequence_model_v1.yaml).

Go / no-go study: the test split is never loaded. Training uses training patients only (fit + internal holdout for early
stopping and Platt calibration); validation patients only compare finished models. Aggregate outputs only.
"""
from datetime import datetime, timezone
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import torch
import torch.nn as nn
import yaml

from aki_eval.data import dataset_paths
from aki_eval.protocol import freeze_prespec, write_json
from aki_final.test_evaluation import PatientBootstrap, snapshot_metrics
from aki_ml.models import PlattCalibrator, load_feature_matrix
from aki_robustness.retrain import fast_auroc

HORIZONS = [6, 12, 24, 48]


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class GRUModel(nn.Module):
    def __init__(self, n_in, proj, hidden, layers, dropout, n_out):
        super().__init__()
        self.inp = nn.Sequential(nn.Linear(n_in, proj), nn.ReLU(), nn.Dropout(dropout))
        self.gru = nn.GRU(proj, hidden, num_layers=layers, batch_first=True)
        self.drop = nn.Dropout(dropout)
        self.out = nn.Linear(hidden, n_out)

    def forward(self, x):
        h, _ = self.gru(self.inp(x))
        return self.out(self.drop(h))


class SequenceStudy:
    def __init__(self, root, config_path=None, dev=None):
        """`dev`: {"patients": n, "epochs": k} code check on a subset, written outside the record, no prespec."""
        self.dev = dev
        self.root = Path(root).resolve()
        self.config_path = Path(config_path or self.root / "configs" / "sequence_model_v1.yaml")
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.paths = dataset_paths(self.root)
        ref = self.config["reference"]
        self.run_dir = self.root / "artifacts" / "models" / ref["run_id"]
        self.ref_pipe = self.run_dir / f"{ref['family']}__{ref['feature_set']}" / "pipeline.joblib"
        self.out = self.root / self.config["outputs"]
        self.private = self.run_dir / self.config["version"]
        if dev:
            self.out, self.private = self.root / "artifacts" / "reports" / "_dev_sequence", self.root / "artifacts" / "_dev_sequence"
            self.config["training"]["max_epochs"] = int(dev["epochs"])
            self.config["training"]["seeds"] = [1]
        self.private.mkdir(parents=True, exist_ok=True)
        inputs = {"config": self.config_path, "snapshots": self.paths["snapshots"], "reference_pipeline": self.ref_pipe,
                  "reference_predictions": self.root / "artifacts" / "predictions" / f"{ref['run_id']}.parquet"}
        sources = {p.relative_to(self.root).as_posix(): p for p in [self.root / "src" / "aki_report" / "sequence.py"]}
        self.prespec = {"prespec_sha256": None} if dev else freeze_prespec(self.out, self.config, inputs, sources)
        self.seed = int(self.config["seed"])
        torch.set_num_threads(12)

    # -------------------------------------------------------------------------------------------- data
    def load(self):
        features = list(joblib.load(self.ref_pipe).feature_names)
        cols = ["patient_id", "split", "hours_since_icu"] + [f"{p}_{h}h" for h in HORIZONS for p in ("eligible", "aki_within")]
        filt = [("split", "in", ["train", "validation"])]
        if self.dev:
            ids = pq.read_table(self.paths["snapshots"], columns=["patient_id", "split"], filters=filt).to_pandas()
            keep = np.concatenate([np.sort(g["patient_id"].astype(str).unique())[:self.dev["patients"]]
                                   for _, g in ids.groupby(ids["split"].astype(str))])
            filt = filt + [("patient_id", "in", list(keep))]
        meta = pq.read_table(self.paths["snapshots"], columns=cols, filters=filt).to_pandas()
        meta["patient_id"] = meta["patient_id"].astype(str)
        meta["hours_since_icu"] = meta["hours_since_icu"].round().astype(int)
        X = load_feature_matrix(self.paths["snapshots"], features, filters=filt)
        order = np.lexsort((meta["hours_since_icu"].to_numpy(), meta["patient_id"].to_numpy()))
        meta = meta.iloc[order].reset_index(drop=True)
        X = X[order]
        self.meta, self.features = meta, features
        self.Y = np.stack([meta[f"aki_within_{h}h"].astype("Float32").to_numpy(np.float32, na_value=np.nan) for h in HORIZONS], 1)
        self.E = np.stack([meta[f"eligible_{h}h"].to_numpy(bool) for h in HORIZONS], 1) & ~np.isnan(self.Y)
        split = meta["split"].astype(str).to_numpy()
        # internal holdout of training patients (seeded, stratified by any creatinine AKI in the monitored rows)
        tp = meta.loc[split == "train"].groupby("patient_id")["aki_within_48h"].max().fillna(0)
        rng = np.random.default_rng(self.seed)
        hold = set()
        for _, g in tp.groupby(tp):
            ids = g.index.to_numpy()
            hold |= set(rng.choice(ids, int(round(.15 * len(ids))), replace=False))
        role = np.where(split == "validation", "validation", np.where(meta["patient_id"].isin(hold), "holdout", "fit"))
        self.role = role
        fit = role == "fit"
        sample = np.sort(rng.choice(np.flatnonzero(fit), min(300_000, int(fit.sum())), replace=False))
        S = X[sample].astype(np.float64)
        lo, hi = np.nanquantile(S, .005, axis=0), np.nanquantile(S, .995, axis=0)
        np.clip(S, lo, hi, out=S)
        med = np.nan_to_num(np.nanmedian(S, axis=0))
        S = np.where(np.isnan(S), med, S)
        mu, sd = S.mean(0), S.std(0)
        sd[sd < 1e-8] = 1
        miss_cols = np.flatnonzero(np.isnan(X[fit]).any(axis=0))
        log(f"rows {len(meta):,}; features {len(features)} + {len(miss_cols)} missing indicators")
        Z = np.empty((len(X), len(features) + len(miss_cols)), np.float16)
        for s in range(0, len(X), 200_000):
            B = X[s:s + 200_000].astype(np.float64)
            M = np.isnan(B)
            B = np.clip(B, lo, hi)
            B = np.where(M, med, B)
            Z[s:s + 200_000, :len(features)] = ((B - mu) / sd).astype(np.float16)
            Z[s:s + 200_000, len(features):] = M[:, miss_cols]
        del X, S
        self.Z = Z
        pid = meta["patient_id"].to_numpy()
        starts = np.r_[0, np.flatnonzero(pid[1:] != pid[:-1]) + 1, len(pid)]
        self.seqs = {r: [] for r in ["fit", "holdout", "validation"]}
        for a, b in zip(starts[:-1], starts[1:]):
            self.seqs[role[a]].append((a, b))
        log({k: len(v) for k, v in self.seqs.items()})

    def batch(self, spans):
        T = max(b - a for a, b in spans)
        x = np.zeros((len(spans), T, self.Z.shape[1]), np.float32)
        y = np.zeros((len(spans), T, len(HORIZONS)), np.float32)
        m = np.zeros((len(spans), T, len(HORIZONS)), bool)
        for i, (a, b) in enumerate(spans):
            x[i, :b - a] = self.Z[a:b]
            y[i, :b - a] = np.nan_to_num(self.Y[a:b])
            m[i, :b - a] = self.E[a:b]
        return torch.from_numpy(x), torch.from_numpy(y), torch.from_numpy(m)

    @torch.no_grad()
    def predict(self, model, spans):
        model.eval()
        out = np.full((len(self.meta), len(HORIZONS)), np.nan, np.float32)
        for s in range(0, len(spans), 256):
            chunk = spans[s:s + 256]
            x, _, _ = self.batch(chunk)
            logit = model(x).numpy()
            for i, (a, b) in enumerate(chunk):
                out[a:b] = logit[i, :b - a]
        return out

    def rows(self, role):
        return np.flatnonzero(self.role == role)

    def auroc_by_h(self, scores, rows):
        res = []
        for j in range(len(HORIZONS)):
            r = rows[self.E[rows, j]]
            res.append(fast_auroc(self.Y[r, j].astype(int), scores[r, j]))
        return res

    # -------------------------------------------------------------------------------------------- training
    def train_seed(self, seed):
        path = self.private / f"gru_seed{seed}.pt"
        cfg, tcfg = self.config["model"], self.config["training"]
        torch.manual_seed(seed)
        rng = np.random.default_rng(seed)
        model = GRUModel(self.Z.shape[1], cfg["input_projection"], cfg["hidden"], cfg["layers"], cfg["dropout"], len(HORIZONS))
        hold = self.rows("holdout")
        if path.exists():
            model.load_state_dict(torch.load(path, weights_only=True))
            log(f"seed {seed}: loaded cached network")
        else:
            opt = torch.optim.AdamW(model.parameters(), lr=tcfg["optimizer"]["lr"], weight_decay=tcfg["optimizer"]["weight_decay"])
            best, best_state, wait = -1, None, 0
            fit = self.seqs["fit"]
            for epoch in range(1, tcfg["max_epochs"] + 1):
                t0 = time.time()
                model.train()
                perm = rng.permutation(len(fit))
                total = 0.0
                for s in range(0, len(perm), tcfg["batch_patients"]):
                    x, y, m = self.batch([fit[i] for i in perm[s:s + tcfg["batch_patients"]]])
                    logit = model(x)
                    loss = nn.functional.binary_cross_entropy_with_logits(logit[m], y[m])
                    opt.zero_grad()
                    loss.backward()
                    nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    opt.step()
                    total += loss.item()
                score = float(np.mean(self.auroc_by_h(self.predict(model, self.seqs["holdout"]), hold)))
                log(f"seed {seed} epoch {epoch}: loss {total:.1f}, holdout mean AUROC {score:.4f} ({time.time() - t0:.0f} s)")
                if score > best + 1e-4:
                    best, wait = score, 0
                    best_state = {k: v.clone() for k, v in model.state_dict().items()}
                else:
                    wait += 1
                    if wait >= int(tcfg["early_stopping"]["patience"]):
                        break
            model.load_state_dict(best_state)
            torch.save(best_state, path)
        raw_hold = self.predict(model, self.seqs["holdout"])
        raw_val = self.predict(model, self.seqs["validation"])
        val = self.rows("validation")
        risk = np.full((len(self.meta), len(HORIZONS)), np.nan, np.float32)
        for j in range(len(HORIZONS)):
            r = hold[self.E[hold, j]]
            platt = PlattCalibrator().fit(1 / (1 + np.exp(-raw_hold[r, j])), self.Y[r, j])
            risk[val, j] = platt.predict(1 / (1 + np.exp(-raw_val[val, j])))
        return risk

    # -------------------------------------------------------------------------------------------- comparison
    def reference_risks(self):
        ref = self.config["reference"]
        t = pq.read_table(self.root / "artifacts" / "predictions" / f"{ref['run_id']}.parquet",
                          columns=["family", "feature_set", "split", "patient_id", "hours_since_icu", "horizon_h", "risk_platt"],
                          filters=[("family", "=", ref["family"]), ("feature_set", "=", ref["feature_set"]),
                                   ("split", "=", "validation")]).to_pandas()
        t["patient_id"] = t["patient_id"].astype(str)
        t["hours_since_icu"] = t["hours_since_icu"].round().astype(int)
        w = t.pivot_table(index=["patient_id", "hours_since_icu"], columns="horizon_h", values="risk_platt")
        val = self.rows("validation")
        keys = pd.MultiIndex.from_frame(self.meta.loc[val, ["patient_id", "hours_since_icu"]])
        out = np.full((len(self.meta), len(HORIZONS)), np.nan, np.float32)
        for j, h in enumerate(HORIZONS):
            out[val, j] = w[h].reindex(keys).to_numpy()
        return out

    def run(self):
        self.load()
        seeds = self.config["training"]["seeds"]
        risks = [self.train_seed(s) for s in seeds]
        gru = np.mean(risks, axis=0)
        lgb = self.reference_risks()
        ens = (gru + lgb) / 2
        val = self.rows("validation")
        rows, diffs = [], []
        n = int(self.config["comparison"]["paired_bootstrap"]["resamples"])
        for j, h in enumerate(HORIZONS):
            r = val[self.E[val, j] & ~np.isnan(lgb[val, j])]
            y = self.Y[r, j].astype(int)
            P = {"lightgbm_locked": lgb[r, j], "gru": gru[r, j], "ensemble": ens[r, j]}
            P |= {f"gru_seed{s}": risks[k][r, j] for k, s in enumerate(seeds)}
            for name, p in P.items():
                rows.append({"horizon_h": h, "model": name, **snapshot_metrics(y, p, slope=False)})
            boot = PatientBootstrap(self.meta["patient_id"].to_numpy()[r], n, self.seed + h)

            def stat(idx):
                yy = y[idx]
                a = {k: fast_auroc(yy, P[k][idx]) for k in ["lightgbm_locked", "gru", "ensemble"]}
                return {"gru_minus_lightgbm": a["gru"] - a["lightgbm_locked"],
                        "ensemble_minus_lightgbm": a["ensemble"] - a["lightgbm_locked"]}

            ci = boot.interval(stat)
            point = stat(np.arange(len(y)))
            for k, v in point.items():
                diffs.append({"horizon_h": h, "comparison": k, "auroc_difference": v,
                              "ci_low": ci.loc[.025, k], "ci_high": ci.loc[.975, k]})
            log(f"{h} h: " + ", ".join(f"{k} {v:+.4f}" for k, v in point.items()))
        self.out.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(self.out / "validation_metrics.csv", index=False)
        d = pd.DataFrame(diffs)
        d.to_csv(self.out / "validation_auroc_differences.csv", index=False)
        d24 = d[d["horizon_h"] == 24]
        go = bool(((d24["auroc_difference"] >= 0.010) & (d24["ci_low"] > 0)).any())
        write_json(self.out / "run_manifest.json", {"version": self.config["version"], "prespec_sha256": self.prespec["prespec_sha256"],
                   "finished_at": datetime.now(timezone.utc).isoformat(), "decision": "go" if go else "stop",
                   "patients": {k: len(v) for k, v in self.seqs.items()}, "outputs": sorted(p.name for p in self.out.iterdir())})
        log(f"decision: {'GO' if go else 'STOP'} -> {self.out}")
