# Local AKI ICU Workbench

This document explains how to use the course project's website. The current local workspace supports patient entry, historical observations, file exchange, and dataset preview. The AKI model, validation thresholds, and clinical effectiveness have not been implemented.

## Documentation map

| Document | Scope |
|---|---|
| This document | Installation, startup, routine page operations, and common startup issues |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Website architecture, backend and model integration, and development workflow |
| [DATA-CONTRACT.md](DATA-CONTRACT.md) | Normative JSON fields, time, revisions, fingerprints, and threshold definitions |
| [VALIDATION.md](VALIDATION.md) | Executed tests, environment, results, and unverified scope |
| [HANDOFF-CHECK.md](HANDOFF-CHECK.md) | Historical differences between the original handoff summary and the actual repository |

## Installation and startup

Environment: Windows x64 or ARM64 and Python 3.11 or later. Setup reuses Node.js 22.13+ with npx when available; otherwise it automatically installs the pinned official Node.js 24.21.0 release for the current user. Internet access is required for the first dependency installation; routine operation does not require a cloud server or external network resources.

After obtaining the website version from GitHub:

```powershell
git clone https://github.com/kk-235/AIForHealth-AKIEarlyWarning.git
cd AIForHealth-AKIEarlyWarning
.\Setup-AKI.cmd
.\Start-AKI.cmd
```

If the local website code has not been committed or pushed, a GitHub clone still contains only the remote version and does not automatically include local changes.

`Setup-AKI.cmd` first checks Node.js, then creates the project Python environment, installs pinned dependencies, and builds the frontend. Automatic Node.js installation downloads only from nodejs.org, verifies the archive against the official SHA256 list, and extracts it to `%LOCALAPPDATA%\Programs\NodeJS\node-v24.21.0-win-<architecture>`. It updates the current process and user PATH without requiring administrator access or changing antivirus settings. Existing terminals may need to be reopened. Download or checksum failures stop setup with an error; rerun after resolving the network or file issue. Python must still be installed separately. `Start-AKI.cmd` starts the local service and opens `http://127.0.0.1:8765`. The terminal immediately prints the full URL (clickable in terminals that support links). After the health check succeeds, the Windows default browser opens. Running the script again opens an existing AKI service on the same port instead of reporting a port conflict, unless an explicit --data-dir was supplied. Use `Start-AKI.cmd --no-browser` to suppress automatic browser opening. Keep the server terminal open; press Ctrl+C to stop. After closing the terminal or restarting the computer, run the startup script again.

If Python is not on `PATH`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup-windows.ps1 -PythonExecutable "C:\actual\path\python.exe"
```

The `ExecutionPolicy` setting applies only to this script process. If dependencies stop working after moving or renaming the project, run `Setup-AKI.cmd` again. Frontend dependencies use files in the current project and do not rely on links to an old project directory. The ignored `dashboard/web/.pnpm-store` cache avoids pnpm global project-registration links and uses additional local disk space.

The default real-time data directory is `%LOCALAPPDATA%\AKIWorkbench\data`. To change it:

```powershell
.\Start-AKI.cmd --data-dir "D:\AKI-local-data"
```

Do not place the local data directory in a synced folder or let multiple computers directly share the same SQLite file. If the port is occupied, stop the previous service, or run `Start-AKI.cmd --port 8766` and use the corresponding port.

The browser message “127.0.0.1 refused to connect” usually means that the service is not running. Start `Start-AKI.cmd` and keep its terminal window open. If the terminal shows an error, address that error instead of only refreshing the page.

## Using the pages

- **Patient overview:** Each patient has one horizontal card. The left side shows a photo or name placeholder, ID, bed, and reminders; the middle shows observation curves; and the right shows prediction curves. Search, reminder filtering, and pagination are supported.
- **Patient detail:** Select a patient card to open it. Switch between variables and model-result series, load a longer history, replay information available at a selected time, and inspect model-provided drivers and data freshness.
- **Input information:** Register a patient, upload a photo, append one or more observations, or import a JSON/ZIP exchange file for this site.
- **Dataset preview:** Read-only display of the project's MIMIC CSV subset, matching heart rate and oxygen saturation by patient, hospital admission, and ICU record. It does not write to the real-time database or calculate risk.

A new database is empty; it does not automatically add demo patients or simulated risk. When no model is available, the risk area shows a waiting state.

## Local CSV dataset preview

Create `icu_pre_admission_data` in the project root and place these files inside it:

- `selected_icu_stays.csv`
- `CHARTEVENTS.csv`

After startup, visit `http://127.0.0.1:8765/dataset`, or select “Dataset preview”. `ADMISSIONS.csv` is not currently read.

This page matches `SUBJECT_ID`, `HADM_ID`, and `ICUSTAY_ID`, and displays each ICU record independently. If source files do not contain names or photos, the patient ID is used without inventing identity information. The current display uses heart rate `ITEMID 211/220045` and oxygen saturation `646/220277`, based on the [MIT-LCP MIMIC-III vital-sign codes](https://github.com/MIT-LCP/mimic-code/blob/main/mimic-iii/concepts_postgres/firstday/vitals_first_day.sql).

MIMIC source timestamps have no timezone. The page preserves the original strings and plots the hour difference `CHARTTIME - INTIME`. A negative value indicates a measurement before ICU admission in that file. Dates such as the year 2100 are de-identified dates; the page does not change them to today or use the current date to decide whether they are expired.

The page rereads the data about every 10 seconds and also supports manual refresh. When updating CSV files, write a complete temporary file first and then replace the target atomically. The current preview is intended for small subsets: at most 64 MB and 200,000 rows per file, with at most 2,000 selected ICU records. A complete dataset should first go through an independent offline extraction workflow.

`icu_pre_admission_data` is ignored by Git. When cloning the repository to another computer, provide authorized data files separately.

## Data and model boundaries

The field contract for real-time input and model exchange is in [DATA-CONTRACT.md](DATA-CONTRACT.md), and integration steps are in [DEVELOPMENT.md](DEVELOPMENT.md).

Current data quality displays only the latest measurement time for each variable, the time since the viewing time, and the number of records in the last hour; there is no composite confidence formula. Risk plots use red above and green below. Model-provided confidence affects display prominence only and does not change the risk probability. No AKI alert is generated when the threshold is missing or unlocked.

This project did not build a Windows EXE, installer, or portable package. Completed and incomplete test scope is recorded in [VALIDATION.md](VALIDATION.md).
