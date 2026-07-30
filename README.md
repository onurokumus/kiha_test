# PTT — Propeller Test Tool

Web tool for uploading, splitting, filtering, and plotting propeller/motor
test-rig data (thrust, torque, rpm, vibration @ 2 kHz, temperatures — any
column schema). No database: each test is a folder of human-readable JSON
plus Parquet for the bulk samples.

## Install (Windows)

Prerequisites — skip any you already have:

```bat
winget install Git.Git
winget install Python.Python.3.13
winget install OpenJS.NodeJS.LTS
```

> Python **3.13** specifically: on 3.14 a Polars bug on Windows forces the
> backend to serialize native reads (still works, just slower under load).
> Node 18+ is required by vite.

One-time setup:

```bat
git clone https://github.com/onurokumus/kiha_test.git
cd kiha_test

cd backend
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

cd ..\frontend
npm install
cd ..
```

## Run

Double-click **`start.bat`** — backend on http://127.0.0.1:8000, UI on
http://localhost:3000 (both as minimized windows; the backend auto-restarts
if it ever crashes). **`stop.bat`** shuts both down.

## Install (Linux)

The Python 3.13 pin is Windows-only (a Polars bug there) — on Linux any
Python **3.11+** works, and Node must be **18+**. Ubuntu 24.04 satisfies
both out of the box:

```bash
sudo apt update && sudo apt install -y git python3 python3-venv nodejs npm

git clone https://github.com/onurokumus/kiha_test.git
cd kiha_test

cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd ../frontend
npm install
cd ..
```

On older distros: get Python 3.11+ from the deadsnakes PPA (Ubuntu) or
pyenv, and Node 18+ via nvm.

Run with **`./start.sh`** (backend + UI in the background, logs in
`backend/backend.log` and `frontend/vite.log`), stop with **`./stop.sh`**.
Or run the two halves manually in separate terminals:

```bash
cd backend && .venv/bin/python run.py     # http://127.0.0.1:8000
cd frontend && npm run dev                # http://localhost:3000
```

## First use

Test data is not stored in git, so a fresh clone starts empty:

1. Drag a test CSV anywhere into the window (or use **⬆ Import CSV**).
   Confirm the import's time basis before upload: auto-detect a clock, choose
   an exact CSV column, or generate a named time column from a sample rate.
   Working time is always stored in seconds beginning at 0; the untouched raw
   CSV remains available for download.
2. Open the **Split** tab to define test points — auto-split from an
   ID-like column, or place and drag them manually — then **save**.
3. Analyze: the scatter shows the test points of every loaded test;
   click points to overlay them in the time / spectrum / XY views.
4. The **Edit** tab holds derived-variable equations and reusable formula
   recipes, existing-column rename/drop, metadata, NaN policy, trimming, and
   test rename/delete.

## Derived variables and formula recipes

The **Edit** tab can materialize new numeric columns from ordered equations.
Choose a variable from the live column list to insert its exact `{column_name}`
reference at the expression cursor, then validate a sampled preview before
applying:

```text
power_mech_w = {torque_nm} * {rpm} * 2 * pi / 60
vib_xy_g = sqrt({vib_x_g} ** 2 + {vib_y_g} ** 2)
```

Equations support arithmetic, comparisons, `IF`/`where`, `abs`, `sqrt`,
`log`, `log10`, `exp`, trigonometric functions, `min`, `max`, and `clip`.
Rows execute from top to bottom, so a later equation may reference an earlier
result. Existing columns require an explicit **replace** choice, and the time
column is protected.

Applying equations rewrites the working Parquet data and plot pyramids through
the same staged rebuild used by other edits; the untouched original CSV is not
changed. Saved equation sets are reusable across tests as formula recipes in
the human-readable `data/formula_recipes.json` file. Existing columns can be
renamed independently in **Rename or remove existing columns**.

## Plot filters

Each time plot has an independent filter control. Available processing includes
Butterworth low/high/band-pass and band-stop filters, moving average, detrend,
and a robust **Despike** filter for short single- or multi-sample excursions.
When a filter is active the plot shows only the processed trace, with a clear
filtered-state marker; the raw samples are not drawn underneath it.

Despike settings are expressed in time rather than sample counts. Its context
window must be longer than twice the maximum spike duration, and only detected
runs within that duration are repaired. The UI reports both detected events
and repaired samples. Original stored data is never rewritten by plot filters.

## Uploads

Large CSVs use a resumable multipart protocol. The browser splits each file
into server-selected 16 MiB chunks, hashes every chunk with SHA-256, and sends
up to three chunks concurrently. The backend records only verified chunks and
atomically promotes the completed file to `raw.csv` before ingestion starts.

A transient network failure retries only the affected chunk. Backend restarts
also preserve valid partial uploads. If the page is refreshed, the browser
cannot retain access to the local `File`; select the same file again and PTT
will verify its already-committed chunk hashes before resuming. Canceling an
upload removes its partial server-side data and releases the test name.

Time setup is part of the resumable session identity. In **Auto-detect** and
**Use CSV column** modes, a supplied Hz value is a fallback for an unusable
clock. **Generate from sample rate** makes the chosen Hz authoritative and
stores `sample index / Hz` in the requested time column (default `time_s`).
Clock strings such as `11:00:19.687` are converted automatically; values in a
numeric time column are interpreted as seconds.

For measured time, the normal sample rate is inferred from continuous regions
rather than the full file span. Timestamp jumps are preserved by inserting the
expected time rows with NaN signal values, so plots show a real break and row
indices remain aligned with elapsed time. Filters run independently on each
side of those gaps, and a spectrum request that crosses a gap asks for a
continuous range instead. Generated time cannot detect missing source rows and
therefore treats every uploaded row as consecutive.

SHA-256 protects against accidental corruption and against resuming with the
wrong local file. It is not authentication: on an untrusted network, serve PTT
over HTTPS and add access control at the reverse proxy, because a digest sent
over the same unauthenticated HTTP connection cannot stop an active attacker.

## Development

- Backend tests: `backend\.venv\Scripts\pip install -r backend\requirements-dev.txt`
  then `backend\.venv\Scripts\python -m pytest backend\tests`
- Frontend build/type-check: `cd frontend && npm run build`
- Architecture notes and gotchas: [CLAUDE.md](CLAUDE.md), [docs/MVP.md](docs/MVP.md)
