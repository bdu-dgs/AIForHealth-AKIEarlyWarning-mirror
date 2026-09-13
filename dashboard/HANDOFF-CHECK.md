# Handoff Reconciliation Record

This document preserves historical facts and differences identified during handoff. It is not the current runtime, interface, or validation guide. Current operation is described in [README.md](README.md), development integration in [DEVELOPMENT.md](DEVELOPMENT.md), and current evidence in [VALIDATION.md](VALIDATION.md).

## Original summary and actual repository

The original summary recorded `main=860b68b377b3408b771310601de96c6a7368f34b` and stated that the repository contained only the initial README. At handoff, `main` was already `fbee3beddbc550cb9a2cc35b5740a1253d897ff6`.

That version already contained the research README, three pipeline notebooks, configuration and artifact notes, and `dashboard/app.py` containing only comments for a Streamlit entry point. Notebook code cells were empty and had no execution output, so there was still no model or runnable website at that time. The remote content was preserved.

## Differences between the local implementation and the old summary

After the user confirmed the technical choices and website build, the local workspace added a React/TypeScript, FastAPI, and SQLite website, Windows source installation and startup scripts. The hourly snapshots and 8/12/24-hour horizons in the original research README are candidate research designs; they do not limit web input frequency or model horizons.

The overview uses one horizontal card per patient, with observations and predictions side by side. Details support longer history and replay. A read-only MIMIC CSV preview, isolated from the real-time database, was added later.

These implementation and validation states are defined by the current source and [VALIDATION.md](VALIDATION.md). Until they are committed and pushed, the remote GitHub repository does not contain the local work.

## Handoff constraints that still apply

- Compare prediction stability across different data cutoffs.
- Select and lock warning thresholds on the validation set, incorporating stability results.
- Update risk, drivers, and trends as new data arrives.
- The final goal includes a fully local Windows workflow that requires no cloud server and can run from source obtained from GitHub.
- The model, composite confidence, packaging, and cross-computer mechanisms still require decisions based on data and user requirements.
- Do not build a Windows portable package, EXE, or installer without an explicit request.
- When GitHub and local work conflict, list the specific conflicts and preserve local work.
- Do not describe a plan, placeholder interface, successful read, or demo result as an implemented model or clinical validation.

This record contains no credentials, secrets, or raw patient data.
