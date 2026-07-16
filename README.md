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

## First use

Test data is not stored in git, so a fresh clone starts empty:

1. Drag a test CSV anywhere into the window (or use **⬆ Upload CSV**).
   The time column and sample rate are auto-detected during ingest.
2. Open the **Split** tab to define test points — auto-split from an
   ID-like column, or place and drag them manually — then **save**.
3. Analyze: the scatter shows the test points of every loaded test;
   click points to overlay them in the time / spectrum / XY views.
4. The **Edit** tab holds test metadata, column rename/drop, NaN policy,
   trimming, and test rename/delete.

## Development

- Backend tests: `backend\.venv\Scripts\pip install -r backend\requirements-dev.txt`
  then `backend\.venv\Scripts\python -m pytest backend\tests`
- Frontend build/type-check: `cd frontend && npm run build`
- Architecture notes and gotchas: [CLAUDE.md](CLAUDE.md), [docs/MVP.md](docs/MVP.md)
