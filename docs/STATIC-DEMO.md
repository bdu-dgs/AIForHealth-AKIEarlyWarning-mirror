# Public static demo (Vercel)

`demo-static/` is a complete, prebuilt website that can be published on Vercel and opened from any computer. It shows
the same workbench as `Start-AKI-Demo.cmd` with four synthetic patients, but needs no backend, model or Python: every
API answer is computed in the browser from a recorded playback.

## What it contains

- The React workbench, built with `vite build --mode static` (read-only: no data entry, import, export or dataset
  preview).
- `static-demo/demo.json`: the four synthetic patients of `scripts/make_synthetic_demo_patients.py`, with every
  observation and every prediction the locked model (LightGBM `no_dc`, Platt) and alert policy v3 produced when the
  real backend replayed them hour by hour for ICU hours 0-72.
- `vercel.json`: rewrites every path to `index.html`, so patient pages can be reloaded.

No MIMIC record or derived patient-level data is included. The only model-derived content is the risks, warnings and
top-five drivers computed for the synthetic patients.

## Using the demo

- The header shows the demo clock. **+1 h** and **+6 h** advance it; **Restart** returns to ICU hour 0.
- `?hour=N` in the address opens the demo at ICU hour N (for example `/?hour=30`).
- Each visitor has an independent clock; reloading the page restarts at hour 0 (or at `?hour=`).
- Story for a presentation: Demo B (sepsis) is warned from ICU hour 18 and stays warned through hour 72 (55 hours);
  Demo C (post cardiac surgery, chronic kidney disease, creatinine stays near baseline) has a 24 h risk of at least
  21% in 17 hours, but the 12 h confirmation rule of policy v3 shows a warning in only 7 of them; Demos A and D never
  reach the threshold.

## Deploy (one-time, about 2 minutes)

From a computer with Node.js 18 or later:

```powershell
cd demo-static
npx vercel login          # sign in with the Vercel account that should own the site
npx vercel deploy --prod  # first run: accept the defaults (new project, directory ./, no build settings)
```

The command prints the public address, for example `https://aki-demo-static.vercel.app`. Later updates use the same
`npx vercel deploy --prod` from `demo-static/`.

Alternative without the CLI: on vercel.com choose *Add New -> Project*, import this GitHub repository, set **Root
Directory** to `demo-static` and **Framework Preset** to *Other*, leave the build command empty, and deploy. Every push
to `main` then redeploys the site.

## Offline backup

If the network fails during a presentation:

```powershell
python scripts/serve_static_demo.py      # then open http://127.0.0.1:4173/
```

This needs only Python (standard library) and the repository checkout.

## Rebuilding

Needed only when the model, policy, synthetic patients or website change:

```powershell
.venv\Scripts\python.exe scripts\export_static_demo.py   # ~3 min; needs the local model artifacts
cd dashboard\web
pnpm build:static                                        # writes ../../demo-static
```

`export_static_demo.py` runs the real backend (`dashboard/api`) and model worker in a temporary directory with a fixed
demo start (2026-11-02 06:00 UTC), advances the clock one hour at a time and waits for every hour to be scored, then
writes `dashboard/web/public/static-demo/demo.json`. The browser adapter `dashboard/web/lib/static-demo.ts` applies the
same visibility rules as `dashboard/api/storage.py`: the latest record revision known at the requested time, the
requested event-time window, and the demo clock as the limit of what is known.
