# PTT — Propeller Test Tool

Web tool for uploading, splitting, filtering, and plotting propeller/motor test-rig
data (thrust, torque, rpm, vibration @ 2048 Hz, temperatures; ~100+ columns, up to
1 h per test). Successor to `../FMS` (rotorcraft flight-test viewer). Maintained
entirely by Claude — write comments/docs for future Claude sessions (invariants,
gotchas), not human onboarding.

## Origin of the code (2026-07-15 fork)

- `frontend/` forked from `../FMS/frontend` — the polished UX chassis (Recharts
  scatter overview, clustering, filter/selection panels). Still speaks FMS's old
  Flask API and fixed rotorcraft schema; genericization is the main pending work.
- `backend/` forked from `../other_small_project/backend` — the working engine
  (FastAPI + polars/scipy). Taken wholesale; FMS's Flask+SQLite backend was
  deliberately dropped (fixed 45-column schema, whole-file pandas reads).
- Both source repos stay untouched as reference. The prototype frontend
  (`../other_small_project/frontend`, React+uPlot) is the donor for the split
  view, DSP controls, and upload UI in later phases.
- Spec: `docs/MVP.md`. Prototype feature log: `docs/PROTOTYPE_FRONTEND_FEATURES.md`.
  FMS UX docs: `docs/fms-ux/`.

## Hard requirements (from user)

1. No database — ASCII/JSON metadata + Parquet bulk samples, one folder per test
   under `data/tests/<name>/` (meta.json, status.json, testpoints.json,
   data.parquet, pyramid/L{16,256,4096}.parquet, raw.csv).
2. Upload + edit test data via UI.
3. Split tests into test points (manual + auto from ID column).
4. Signal filtering: FFT/Welch, Butterworth LP/HP/BP/BS, moving average, detrend.
5. Tests have different variable names/counts — nothing may hardcode a schema.

## Critical constraints

- **Python 3.13 only** (backend/.venv). Polars on Windows + Python 3.14 produced
  reproducible native access violations (whole-process crash). locks.py gates
  concurrent native reads to 1 on win32+py>=3.14; on 3.13 it allows 4.
  Override: `KIHA_MAX_CONCURRENT_READS` (set in run_backend.bat).
- Keep the prototype's safeguards (per-test RW locks, atomic JSON writes, crash
  recovery, auto-restart wrapper). They cost ~nothing and exist for real crashes.
- Lock ordering (locks.py): process-wide read slot -> per-test lock. Never invert.
- Recharts cannot render 2 kHz time series. Time plots must use windowed pyramid
  reads (backend serves raw when viewport <= ~6000 samples, min/max envelope
  otherwise). Recharts stays only for TP-level aggregate scatter (~100s of points,
  via /api/tests/{name}/tp_stats).

## Commands

- Backend dev:  `backend\.venv\Scripts\python.exe backend\run.py` (port 8000)
- Backend tests: `backend\.venv\Scripts\python.exe -m pytest backend\tests`
- Frontend dev: `cd frontend && npm run dev` (port 3000)
- Both, minimized with auto-restart: `start.bat` / `stop.bat`
- Test data: generate via `python ..\other_small_project\generate_dummy_data.py
  --duration 60 --name demo_60s` (CSV lands in dummy_data/), upload via
  `POST /api/tests/upload`.

## Plan status

- [x] Phase 0+1 (2026-07-16, root commit c32166b) — fork + backend swap (the
      prototype backend came over wholesale, so the swap happened at fork
      time). Verified: 12/12 pytest on 3.13.14; demo_60s (93.5 MB, 122880
      rows x 112 cols) uploaded via API and ingested; window (raw + envelope)/
      spectrum/filter/xy/tp_stats/autosplit/testpoints endpoints all green;
      40 parallel mixed read+DSP requests in 0.36 s, no native crash.
- [x] Phase 2 (2026-07-16) — frontend genericized and wired to the new API.
      Everything is meta.json-driven: axis pickers, 3x3 time grid (editable
      via Edit Plots), TP-label + per-column aggregate range filters
      (mean/min/max/any-sample computed client-side from tp_stats). Scatter =
      per-TP mean of chosen X/Y columns. Selecting TPs fetches
      /testpoints/{id}/data traces (relative time, ordered min/max reduction)
      and overlays them from t=0. Deleted: dataGenerator, maneuverTree,
      plotConfig constants, duplicate api types, stray FMS artifacts. `tsc`
      now passes — `npm run build` is the build path again. Dev proxy:
      vite '/api' -> 127.0.0.1:8000 (no CORS in dev); backend CORS also
      allows :3000. Verified in headless Edge via global playwright
      (channel msedge): 2 TPs selected -> 18 line paths, 0 console errors.
      Playwright gotchas: selected dots add glow circles (nth() indices
      shift — click by pre-selection pixel coords), and constant-value
      series are flat lines with zero-height bboxes (wait with
      state:'attached', not 'visible').
- [x] Phase 3 (2026-07-16) — time plots on uPlot (canvas), Recharts remains
      only for the TP scatter. Right panel has two modes (toggle in the
      selected-points bar): 'Test points' = TP overlay from t=0 using uPlot
      mode-2 facets (per-series time arrays, no resampling; TimePlot.tsx),
      'Full test' = whole-test browsing via windowed /data reads
      (FullTestPlot.tsx): envelope drawn as min/max band, drag-zoom
      re-fetches (100 ms debounce + AbortController), all 9 plots share the
      range (x-link) + uPlot cursor sync (keys in constants/uplotTheme.ts).
      Verified: zoom envelope:16 -> raw:1 transition, 9 linked refetches per
      zoom, 0 errors. uPlot sizing: containers measured by ResizeObserver;
      legend only when expanded (its height is subtracted after create).
- [x] Phase 4 (2026-07-16) — the tool is self-sufficient. Header gains
      Analyze/Split tabs + Upload CSV button; drag-drop .csv anywhere
      uploads; the test list polls every 2 s while any ingest runs.
      Split tab = ported prototype editor (components/split/): windowed
      SplitPlot with TP regions as an HTML overlay (click label to select,
      drag start/end handles), auto-split from ID candidates (proposal
      only — Save PUTs the TestPointsFile wrapper with recomputed
      start/end_idx), TP table edit, testpoints.json load/download.
      Returning to Analyze clears selection/stats caches (TP defs changed).
      Right panel gains a third mode 'Spectrum' (FFT/Welch + log toggles in
      the selection bar, computed over the full-test zoom range; drag =
      client-side freq zoom). Expanded full-test plots get a DSP filter row
      (Butterworth LP/HP/BP/BS, moving avg, detrend) drawn as a dashed
      overlay — only when the server returns the identical time axis.
      Verified end-to-end in headless Edge including a real 190 MB upload
      (demo_120s) through the UI. Utility CSS classes (.btn/.panel/.input/
      .badge/...) live in App.css.
- [x] Phase 5 (2026-07-16) — data editing + XY + perf validation. Backend:
      PATCH /tests/{name}/meta replaces the free-form user_meta block;
      POST /tests/{name}/edit schedules a background rebuild (app/edit.py:
      column rename/drop, trim to [t0,t1], NaN policy zero_fill/interpolate
      — 'drop rows' deliberately excluded, it breaks uniform fs; missing
      CSV cells are NULLS while computed gaps are NaN, fills must cover
      both; interpolate collects in RAM, everything else streams). Rebuild
      rewrites data.parquet + pyramid, clips testpoints, status
      'rebuilding' -> 'ready'. Frontend: Edit tab (components/edit/) with
      metadata editor, column table, NaN/trim panels, test rename/delete;
      App poller reloads schema-derived state when a rebuild lands
      (rebuildPending ref). 4th right-panel mode 'XY' (uPlot mode-2 points
      via uPlot.paths.points(), shared X column picker in the selection
      bar). 18/18 backend tests. perf_1h (1 h, 7.4M rows x 112 cols)
      serving latencies: full-hour view 14 ms, 60 s zoom 37 ms, raw 2 s
      window 13 ms, FFT 27 ms, filter 38 ms, tp_stats 31 ms — targets
      (<2 s / <300 ms / <1 s) beaten by 1-2 orders of magnitude.

- [x] Multi-test analyze (2026-07-16, user request) — the scatter shows TPs
      from EVERY ready test at once (FMS-style; tests play the role tail
      numbers played there). App caches metaByTest/tpsByTest/statsCache
      (stats keyed test->col->tpId); TP identity is `${test}:${tpId}`
      everywhere (selection, filters, chips, tooltips). Scatter axes and
      filter parameters use the UNION of all tests' columns; a TP renders
      only if its test has stats for both axes; a parameter filter drops
      points whose test lacks the column. The header dropdown now only
      picks the ACTIVE test (grid full/spectrum/xy modes, Split/Edit tabs)
      — switching it no longer clears selection or filters.
      FilterControls rebuilt FMS-style: two 200px tree panels
      (Tests & Test Points with expand/collapse/search/indeterminate
      parents; Labels with search) + Parameters panel with numbered rows
      and '+ Add'. invalidateTest(name) drops all caches for one test
      (rebuild/rename/delete/split-save paths). SplitPlot data trace is
      colorFor(i+1) (orange first) so it can't melt into the blue TP
      region overlays.

## Windows gotchas (hard-won)

- os.replace onto a file a reader holds open raises PermissionError
  (WinError 5). store.write_json_atomic retries briefly — do NOT remove
  that loop; status polling collides with background-job status writes.
- PowerShell 5.1 `-Encoding utf8` writes a BOM; json.loads then fails and
  _read_json returns None (test shows status 'unknown'). Write JSON files
  from Python, or use UTF8Encoding($false).
- /xy dedupes x==y selects (a grid cell whose column equals the shared X
  axis would otherwise 500 on polars duplicate-column select).
- Frontend: handleTestChange early-returns on the same test name — a
  redundant change event would null meta without re-running the load
  effect (deps [currentTest] unchanged), wedging the UI.

## API gotchas (learned during Phase 0 verification)

- POST /split/auto returns a BARE LIST proposal (does not persist); client
  must wrap it in the TestPointsFile shape {version, test, source_file,
  fs_hz, test_points} and PUT /testpoints.
- /filter takes `cols` + `type`; /spectrum takes `col` + `mode` (fft|welch).
- TP data responses nest time per series (series.<col>.t) with time_origin_s;
  full-test /data responses have a top-level t array.
