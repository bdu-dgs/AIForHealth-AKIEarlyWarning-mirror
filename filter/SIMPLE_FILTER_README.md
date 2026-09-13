# Local CSV Cleaning Tool

Double-click `start_filter.bat`. The browser opens the local address automatically; press Ctrl+C in the console to stop the service.
The existing `start.bat`, AKI backend, and frontend are unchanged. The new version is in `simple_app/`, uses Python 3.11+, and requires only the standard library.

1. Select a CSV/TSV file, set its encoding and delimiter, and click “Upload locally”.
2. Add column filters and choose whether all or any conditions must match.
3. Configure output columns, missing-value row removal, a deduplication key, and a constant fill value.
4. Click **Porcess**, then review the first 100 rows and processing statistics.
5. Download the complete UTF-8 BOM CSV. Enable “Ask where to save each file” in the browser and choose a local directory outside cloud synchronization.

## Processing semantics

- Fields are read as text, preserving leading zeros in patient identifiers. Text comparisons are case-sensitive; numeric comparisons do not match unparsable values or missing values.
- Empty strings are always missing. Additional markers can be configured, one per line. When trimming is enabled, leading and trailing whitespace is removed from all fields first.
- Order: trim whitespace, filter conditions, remove rows with missing values, deduplicate by the selected key while keeping the first row, fill constants, and select output columns.
- Multiple deduplication keys are combined; different missing markers represent the same missing key value.
- A row is removed when any selected missing-value column is empty. Filling cannot restore rows removed earlier.
- Each run starts from the original upload. The tool does not automatically convert units, detect outliers, assign AKI labels, or impute statistics, avoiding unconfirmed changes to research meaning.
- Supports up to 50 MB, 200,000 rows, and 1,000 columns. It is intended for small or medium extracted tables, not the complete `CHARTEVENTS` table.
- The source file is not modified. Output preserves original text, including text beginning with formula characters; when using Excel, import untrusted CSV files as text.

## Local data boundary

The backend binds only to `127.0.0.1` and uses an automatically selected available port. Frontend scripts and styles are local, with no CDN, analytics, or external API. Host, Origin, and session tokens are checked; the browser resource policy allows same-origin resources only. The backend contains no code for uploading data externally.

The program does not write uploaded content or processing results to disk or log request contents. Data stays in Python memory and is released when the session is cleared or the program closes; this is not an operating-system-level secure-erasure guarantee. This is a single-user tool, and multiple browser tabs share one session.

The program files may be located in a user-selected OneDrive directory; OneDrive is separate synchronization software whose behavior the program cannot control. Keep source data and downloaded results in a local directory that is not synchronized. The browser controls the download location.

## Files

- `simple_app/server.py`: local HTTP backend and cleaning logic.
- `simple_app/index.html`, `app.js`, `style.css`: browser frontend.
- `simple_app/test_filter.py`: synthetic-data cleaning and HTTP tests.

Run tests:

`.venv\Scripts\python.exe -B -m unittest discover -s simple_app -p test_filter.py -v`
