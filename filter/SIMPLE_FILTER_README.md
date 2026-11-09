# Local Filter Implementation Notes

Installation, daily use, input limits, cleaning semantics, data handling, and troubleshooting are maintained in [README.md](README.md). This file covers implementation boundaries and development entry points only.

## Modules

| File | Responsibility |
|---|---|
| `simple_app/server.py` | Local HTTP API, in-memory session, parsing, filtering, and cleaning |
| `simple_app/index.html`, `app.js`, `style.css` | English-only browser interface |
| `simple_app/test_filter.py` | Synthetic cleaning and local HTTP regressions |
| `start_filter.bat` | Windows launcher for this standalone tool |

This standard-library application is independent of the older `backend/`, `frontend/`, `run.py`, and `start.bat` application. It does not invoke the dashboard model worker, create model-ready features automatically, or apply the AKI live JSON contract to ordinary CSV uploads.

## Development verification

From the repository root, after setting up the website Python environment:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s filter/simple_app -p test_filter.py -v
```

For this standalone tool alone, Python 3.11+ and its standard library are sufficient; see the operation README for startup commands. When changing cleaning behavior or limits, update the implementation and tests together, and keep the full user-facing rule in the operation README.
