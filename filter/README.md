# Local Filter

A small, local-only CSV/TSV cleaning application with a Python backend and a browser frontend. Load a file, configure filtering and cleaning rules, preview the result, and download the cleaned CSV.

The interface opens in **English** by default. Click **Simplified Chinese** or **English** in the page header to switch languages without losing your file, rules, or results. Reloading the page resets the interface language to English. Uploaded column names, cell values, filenames, and user-entered filter values are never translated.

## Scope

This README describes the standalone application in `simple_app/`, launched with `start_filter.bat`. Any existing `backend/`, `frontend/`, `run.py`, `start.bat`, or root `requirements.txt` files belong to the separate AKI processing application and are not required for Local Filter.

Local Filter is a general-purpose table cleaning tool. It does **not** diagnose AKI, assign clinical labels, automatically convert measurement units, or validate clinical outliers.

## Features

- CSV, TSV, and other delimited text inputs.
- UTF-8, GB18030/GBK, and UTF-16 decoding.
- Comma, tab, semicolon, and pipe delimiters.
- Text equality, inequality, containment, numeric comparisons, and missing-value filters.
- AND / OR combinations of filter conditions.
- Optional whitespace trimming, missing-row removal, deduplication, and constant-value filling.
- Selectable output columns.
- Preview of the first 100 rows and counts for each processing step.
- Complete result download as a UTF-8 CSV with a BOM.
- English and Simplified Chinese interface, including validation messages.
- No third-party Python packages, CDN assets, or external API calls.

## Requirements

- Python **3.11 or later**.
- A modern browser, such as Microsoft Edge, Chrome, or Firefox.
- Sufficient RAM to hold the uploaded table and the processed result.

No `pip install` step is needed for this standalone application.

## Quick start

### Windows

From the repository folder, double-click:

```text
start_filter.bat
```

The launcher uses `.venv\Scripts\python.exe` if present; otherwise, it uses the Windows Python launcher (`py -3`). If Python is installed but `py` is unavailable, run:

```powershell
python -B simple_app/server.py
```

### macOS / Linux

From the repository folder:

```bash
python3 -B simple_app/server.py
```

The server binds to `127.0.0.1` on an automatically selected available port and opens the local page in your default browser. If the browser does not open, copy the URL printed in the terminal. Keep the terminal open while using the application. Press **Ctrl+C** to stop it.

Do not open `index.html` directly: the interface needs the local Python server.

## Workflow

1. Choose a CSV or TSV file. Set its encoding and delimiter, then click **Load file locally**.
2. Review the detected columns and original-data preview.
3. Click **+ Add a filter**, choose a column and comparison, and enter a value if needed.
4. Select whether all conditions or any condition must match.
5. Configure the column controls:

   | Control | Behavior |
   | --- | --- |
   | Keep | Include this column in the output. |
   | Drop if missing | Remove a row if this column contains a missing value. |
   | Deduplication key | Use this column as part of the combined duplicate key. |
   | Fill missing | Replace missing values in this column with the specified constant. |
   | Fill value | The replacement text; used only when Fill missing is checked. |

6. Click **Porcess** to run the configured cleaning steps. This button spelling is retained from the original interface requirement in both languages.
7. Review **Cleaned results** and click **Download cleaned CSV**.
8. Use **Clear session data** when finished, or stop the server.

Every processing run starts from the original loaded file, rather than applying additional changes to the previous result.

## Cleaning semantics

Operations run in this order:

1. Trim leading/trailing whitespace, if enabled.
2. Apply filters.
3. Drop rows missing values in any selected missing-row column.
4. Deduplicate using the combined selected key, retaining the first matching row.
5. Fill missing values with configured constants.
6. Select output columns, preserving their original order.

All fields are initially read as **text**, preserving identifiers such as `00123`. Text comparisons are case-sensitive and exact; numeric operators parse finite numbers, and nonnumeric values do not match. Missing values never match ordinary comparison operators, including inequality; use **Is missing** or **Is not missing** explicitly.

Empty strings are always missing. The default additional markers are `NA`, `N/A`, `NULL`, `null`, and `NaN`; edit the list with one marker per line. Missing markers are case-sensitive. When deduplicating, different missing markers represent the same missing key value. Filling happens after row removal and deduplication, so it cannot restore rows already removed. No selected filters means all rows pass filtering; no deduplication key means deduplication is disabled.

The application does not modify the source file. CSV output retains field text, including text beginning with formula characters. Import untrusted CSV files as text if opening them in spreadsheet software.

## Local processing and storage

“Load file locally” transfers the selected file from your browser to the Python server **on the same computer**. The server listens only on the loopback address, checks request hosts and origins, and requires a session token for data operations. Frontend resources are served locally, with a same-origin content security policy.

Uploaded contents and processing results are stored in application memory. The application does not save them to the code folder, write request-content logs, call cloud services, or collect analytics. Clearing the session or stopping the server releases application references; this is not an operating-system-level secure erasure guarantee.

**Cloud sync is separate from this application.** If the repository is inside OneDrive or another synced folder, keep real datasets and downloaded results in a separate, nonsynced local directory. The browser controls the download destination; enable its “ask where to save each file” option if needed. This application cannot disable a browser's cloud features or an operating system's backup/sync software.

This is a **single-user tool**. Tabs connected to the same running server share one in-memory dataset. Reloading the page resets the frontend controls; load the file again to configure another run. Clear the session before reloading if you want to release the existing server-side dataset immediately.

## Limits

| Limit | Value |
| --- | --- |
| Uploaded file size | 50 MiB (shown as 50 MB in the UI) |
| Parsed data rows | 200,000 |
| Columns | 1,000 |
| Individual field size | Approximately 2 million characters |
| Preview | First 100 rows |
| Export | Full cleaned result, CSV only |

Headers must be nonempty and unique. Malformed row lengths are rejected rather than silently repaired. Excel workbooks, compressed files, and Parquet are not supported by this standalone tool. Use extracted subsets rather than the complete MIMIC-III `CHARTEVENTS` table.

## Project layout

```text
README.md                  English documentation
start_filter.bat           Windows launcher
simple_app/
  server.py                Local HTTP server and cleaning logic
  index.html               English-default page structure
  app.js                   Upload, rule editing, preview, and download behavior
  i18n.js                  English/Chinese UI translations
  style.css                Local styles
  test_filter.py           Synthetic-data cleaning and HTTP tests
```

## Tests

From the repository folder:

```powershell
python -B -m unittest discover -s simple_app -p test_filter.py -v
```

If using the existing Windows virtual environment:

```powershell
.venv\Scripts\python.exe -B -m unittest discover -s simple_app -p test_filter.py -v
```

Tests use synthetic data and cover filtering, deduplication, filling, leading-zero preservation, CSV round trips, invalid inputs, downloads, session clearing, and local-access restrictions.

For a browser smoke test, load a synthetic file, set a filter, switch to Chinese and back, and confirm that the selected file, filter values, and results remain unchanged. Also trigger a validation error and check it in both languages.

## Sharing with the team on GitHub

Commit the source files, launcher, tests, and documentation. For a standalone repository, copy `simple_app/`, `start_filter.bat`, and this `README.md`, along with an appropriate `.gitignore`. The tool does not require the older AKI application folders or its Python dependencies.

Do **not** commit patient data, source datasets, cleaned outputs, local environments, credentials, or logs. Use synthetic examples for demonstrations and bug reports. The existing `.gitignore` excludes common datasets and local environments, but it is not a complete privacy control: inspect staged files before committing, especially text/TSV files and previously tracked data. Uploading this code to GitHub does not require uploading any input data, and GitHub Pages cannot run this Python backend.

## Troubleshooting

- **Python is not found:** install Python 3.11+ locally, then use one of the launch commands above.
- **The browser page does not open:** use the exact `http://127.0.0.1:<port>` URL printed by the server.
- **Chinese characters appear incorrectly:** choose the source file's encoding and load it again.
- **The entire file appears as one column:** select the correct delimiter and reload.
- **A numeric filter is rejected:** enter a finite number rather than text or a unit-bearing value.
- **No rows remain:** review AND/OR settings, exact text matches, and missing-row rules.
- **No download appears:** check browser download settings or use a standard desktop browser. Results are available only while the server is running and the result has not been cleared or replaced.
