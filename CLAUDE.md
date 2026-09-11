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

Target devices and input: follow `AGENTS.md`. This application is desktop/laptop-only with keyboard and mouse; mobile and touchscreen interaction are out of scope. Preserve desktop resizing and keyboard accessibility. Right-click menus are allowed, including menu-only secondary actions.

1. No database — ASCII/JSON metadata + Parquet bulk samples, one folder per test
   under `data/tests/<name>/` (meta.json, status.json, testpoints.json,
   data.parquet, pyramid/L{16,256,4096}.parquet, raw.csv).
2. Upload + edit test data via UI.
3. Split tests into test points (manual + auto from ID column).
4. Signal filtering: FFT/Welch, Butterworth LP/HP/BP/BS, moving average, detrend.
5. Tests have different variable names/counts — nothing may hardcode a schema.

## Critical constraints

- **Python 3.13 only on Windows** (backend/.venv); Linux production supports
  Python 3.11 (see `deployment_guide.md`). Use `paths.is_link_or_junction` for
  portable link checks; `Path.is_junction` requires 3.12 and broke populated
  source/trash catalogs on the deployment baseline. Polars on Windows + Python 3.14 produced
  reproducible native access violations (whole-process crash). locks.py gates
  concurrent native reads to 1 on win32+py>=3.14; on 3.13 it allows 4. The gate
  is version-derived — run_backend.bat deliberately does NOT set
  `KIHA_MAX_CONCURRENT_READS` (that would force the gate open on a 3.14 venv,
  bug 1.4); set it by hand only to lower the limit for debugging.
- Keep the prototype's safeguards (per-test RW locks, atomic JSON writes, crash
  recovery, auto-restart wrapper). They cost ~nothing and exist for real crashes.
- Lock ordering (locks.py): per-test read lock -> process-wide read slot
  (data_read). Writers never take a slot, so a read blocked on a write-locked
  test does NOT hold a slot and can't stall reads of other tests (bug 2.1).
  The catalog lock is independent of both.
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
  --duration 60 --name demo_60s` (CSV lands in dummy_data/), then upload through
  the UI or the resumable `/api/uploads` protocol documented under API gotchas.

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
- [x] Upload overhaul (2026-07-16, historical implementation superseded by the
      2026-07-29 resumable protocol below; user bug report: "upload silently does
      nothing / ingest starts minutes later") — root causes: (a) multipart
      spooling hid the whole transfer from UI and logs, (b) the 6 s notice
      auto-clear erased "uploading…" mid-flight, (c) Node requestTimeout
      killed >5 min uploads. That superseded version used a raw-body streaming
      endpoint which wrote status 'receiving' when headers arrived and logged
      receive/ingest start+finish; client disconnect / truncation discarded
      the partial test dir. Its frontend used XHR upload with per-file header
      chips (live %, sticky dismissible error chips
      — error text truncates but the ✕ never clips), duplicate names
      pre-checked before sending bytes, names sanitized to the backend
      charset, poller runs during uploads so the 'receiving' row shows
      up. Vite dev/preview requestTimeout=0. Verified end-to-end in
      headless Edge (98 MB button upload + synthetic drag-drop) plus
      slow-chunked and mid-transfer-abort probes against uvicorn; 24/24
      pytest. Playwright drop gotcha: dispatch DragEvent on a node INSIDE
      #root — body is the root's parent, React never sees events
      dispatched there.
- [x] Resumable multipart uploads (2026-07-29) — replaced the single raw-body
      request with durable sessions and server-selected 16 MiB chunks. The
      frontend SHA-256 hashes each chunk and sends three multipart requests in
      parallel; exact retries are idempotent and only verified chunks count
      toward progress. `POST /api/uploads` reserves a name, GET resumes,
      PUT `/chunks/{index}` commits one chunk, POST `/complete` atomically
      promotes `.upload/raw.csv.uploading` to `raw.csv`, and DELETE cancels.
      Valid `receiving` manifests survive backend restarts; seven-day stale
      sessions are recoverable/cleanable. Browser resume metadata lives in
      localStorage, but a refresh loses the `File` permission: the user must
      reselect the file, after which every already-committed local chunk hash
      is checked before it is skipped. Completed storage and ingestion remain
      unchanged.
- [x] Overlap clustering revived (2026-07-16, user request: "can't see if two
      points are on top of each other") — the forked FMS cluster machinery
      (pointClustering.ts, ClusterDot, cluster->PointSelectionMenu) was wired
      but dormant: shouldEnableClustering required >500 points (FMS perf
      thresholds; PTT scatters are ~tens of TPs). Now it's overlap
      disambiguation: always on for >=2 points (no zoom cutoff — coincident
      points never separate by zooming), radius 30->14 px (dot r=6, so 14 =
      "visually touching"), minPointsForCluster 3->2, zero-range guard in
      clusterPoints (constant column => all stack => must still cluster),
      calculateZoomLevel deleted. Cluster hover tooltip says "N overlapping
      points"; toggle button shows at >=2 points (was >500). Verified in
      headless Edge: 6x "2"-badges among 104 TPs, cluster click lists both
      TPs (cross-test pair at same condition), zoom-in splits near-neighbors.
      Playwright gotcha: query [data-menu-container] with count(), not
      isVisible() (strict-mode).
- [x] Mode-bar test picker + panel sources (2026-07-16, user request) —
      the header test dropdown is gone; a 'test:' picker appears in the
      right-panel mode bar only when a single-test view needs it (Full
      test, or Spectrum/XY sourced from 'full'), and in the Split/Edit
      toolbars. Spectrum and XY have an 'of: TPs | full' source toggle
      (states specSource/xySource, default 'tp'): TP source overlays one
      spectrum / XY point cloud PER selected test point, computed over the
      TP's own range in its own test (cross-test), drawn in TP colors.
      SelectedTestPoint carries endS (open-ended TPs resolved to next TP /
      data end at selection time) because those fetches need a real range.
- [x] Grid minimize broken with TPs selected (2026-07-16, user bug report)
      — TWO stacked causes, both selection-dependent:
      (1) CSS grid track lock (the real "won't minimize"): 1fr tracks are
      minmax(auto, 1fr), and unlike FMS's Recharts (ResponsiveContainer,
      no intrinsic size) uPlot canvases have hard pixel sizes. After
      minimizing, the ex-expanded plot's ~950px canvas became its track's
      auto minimum -> its row/col stayed expanded-sized, the other 6 cells
      crushed to <40px where plots refuse to render (blank). Stable
      feedback loop (canvas keeps cell big, cell keeps canvas big), so the
      grid NEVER recovered; with nothing selected there are no canvases,
      which is why minimize worked then. Fix: min-width/height: 0 +
      overflow hidden on .plotWrapper (TimeSeriesGrid.module.css) — keep
      those lines or the bug returns in every uPlot grid mode.
      (2) PointSelectionMenu's invisible full-screen backdrop ate the
      first click anywhere after a menu selection (menu deliberately stays
      open for multi-select; latent since FMS, surfaced by always-on
      clustering opening the menu on nearly every dot click). Fix:
      backdrop deleted; menu closes on document-level mousedown/wheel
      outside the panel WITHOUT consuming the event. Panel keeps
      [data-menu-container] (MainScatterPlot.handleMouseDown pan guard).
      Verified in headless Edge: expand->minimize returns to uniform 3x3
      (9 canvases) in ALL four view modes, twice in a row; menu still
      multi-selects, closes via X/outside-click. Separate PRE-EXISTING
      find while sweeping modes: uPlot numAxisSplits throws RangeError
      'Invalid array length' for constant-column plots in Spectrum/XY
      (degenerate y-range -> tick incr underflow); logged in
      possible_bugs.md, not fixed.
- [x] Grid zoom styling + pan/wheel-zoom (2026-07-16, user report: "zoom
      with mouse selection does not have proper styling; also drag etc") —
      uPlot ships light-theme CSS: .u-select defaults to rgba(0,0,0,.07),
      invisible on #1e1e1e, so the drag-zoom rectangle looked broken.
      Global override in App.css, scoped `.uplot .u-select` so specificity
      beats uPlot.min.css regardless of bundle CSS order (accent-tinted
      fill + inset box-shadow edge lines — NOT borders: uPlot hides the
      select by zeroing its size and borders still paint a 2px ghost on a
      zero-width box; also neutral crosshair color). Cursor sync mirrors
      the drag rectangle live on all 9 linked plots — that's uPlot sync,
      not a bug. New utils/uplotPanZoom.ts xPanZoomPlugin:
      wheel = x-zoom around cursor (0.85/notch), shift-drag or middle-drag
      = x-pan; plain drag stays select-zoom. Pan gestures are kept away
      from uPlot's select machinery via cursor.bind.mousedown (the
      supported hook — capture-phase listeners DON'T work: at the event
      target, capture/bubble fire in registration order and uPlot binds
      first). setScale gives instant local feedback (uPlot setScales
      assigns explicit min/max directly, bypassing a fixed range array —
      verified in source, so it works on TimePlot's pinned x scale);
      commits go upstream trailing-debounced 120 ms for wheel / on mouseup
      for pan — NEVER per mousemove (a commit re-renders all 9 linked
      plots and TimePlot rebuilds its uPlot per zoomDomain change).
      destroy hook FLUSHES (not drops) a pending wheel commit: plots are
      rebuilt whenever data lands, dropping would lose the last ticks.
      Wired into TimePlot + FullTestPlot (commit = shared time zoom) and
      SpectrumPlot (no commit — freq zoom is client-side); XYPlot left
      alone (2D auto-scaled point cloud). Backend already clamps
      out-of-range windows (store.read_window), so panning past the data
      edges self-heals on refetch.

- [x] XY overhaul (2026-07-17, user request: "crashes due to memory even with
      single test point; want a different X variable per plot") — the OOM was
      NOT data volume: uPlot mode-2 x auto-range is [dataMin, dataMax] with
      ZERO padding (snapNumX), so a constant X column over the plotted range
      (common inside one TP — tp_id, setpoints) collapses the scale to zero
      width and numAxisSplits' tick loop (`val += incr` until val > scaleMax)
      never advances past scaleMax → pushes ticks until the tab dies (bug
      1.15b's RangeError = same loop dying earlier). Fix: safeRange in
      constants/uplotTheme.ts — pads flat/near-flat auto-ranges (span <=
      mag*1e-9 → ±mag*1e-3, else ±5%) — wired into BOTH scales of XYPlot and
      SpectrumPlot. Only auto-ranging calls scale.range fns; explicit
      setScale min/max (drag-zoom/pan) bypasses them, so zoom semantics are
      untouched. Second half: per-plot X columns — App state xyXCols[9]
      (index-aligned with plotConfigs, validated/defaulted alongside it),
      shared "X:" picker removed from SelectedPointsPanel; in Edit Plots
      mode each XY cell shows "[Y] vs [X]" selects (X changes only that
      plot).

- [x] DSP controls discoverable (2026-07-17, user request: "fft/welch as
      dropdown — can't tell which is selected; also I don't see moving
      average, high-pass, low-pass") — the FFT/Welch toggle button showed
      only the CURRENT mode, and the Butterworth/moving-avg/detrend row
      existed but only on EXPANDED full-test plots. Now: Spectrum bar has a
      labeled <select> (FFT magnitude / Welch PSD); Full-test bar has the
      whole filter row (none/LP/HP/BP/BS/moving avg/detrend + order/f1/f2/
      window + Nyquist hint) SHARED across all 9 plots. Shared state:
      App.filterUi (raw strings) -> buildFilterSpec (constants/filters.ts)
      -> FullTestPlot.filterSpec prop; each plot still fetches its own
      column's /filter overlay. Per-plot status: collapsed cells get a
      'filt'/'filt!' header badge (tooltip = error text), expanded plots
      keep the badge/warning/error text row (inputs removed). Side effect:
      SelectedPointsPanel is now auto-height (minHeight 42, was fixed 42 +
      overflow hidden) so wrapped controls/chips are visible — fixes
      possible_bugs 1.13.

- [x] TP filtering + per-plot filters (2026-07-17, user request: "filter test
      points as well. also plots individually"; refined same day: "move the
      filtering down to the 3x3 grid with a button near maximize/minimize")
      — filter state is per-plot ONLY: App.plotFilters (FilterUi[9],
      index-aligned with plotConfigs) -> buildFilterSpec ->
      plotFilterSpecs; there is NO shared/broadcast filter control (a
      mode-bar broadcast row existed for a few hours and was removed at
      user request). Each TimePlot/FullTestPlot cell header has a ≈ button
      next to the expand button: toggles that cell's FilterRow
      (components/controls/FilterRow.tsx, the shared kind+params
      fragment); row is also always visible when expanded. ≈ icon color =
      FILTER_COLOR when a filter is active, red on error (tooltip = error
      text). TimePlot TP filtering: per visible TP, /filter over the TP's
      own absolute range in its OWN test ([tp.start_s, endS] — cross-test
      correct, backend uses each test's fs), t shifted to relative, drawn
      as dashed segments in the TP's color (envelope -> min+max pair,
      raw -> one); fetched once per (spec, TP set, column) — zoom stays
      client-side like the raw traces. Verified headless: ≈ on 9/9 cells
      in both modes, opening one row + setting low-pass fetched ONLY that
      plot (TP-ranged t0/t1), closing the row keeps the filter active
      (yellow ≈), detrend on another cell -> 1 request; 0 pageerrors.
      Nyquist hint uses the ACTIVE test's fs (informational only —
      cross-test TPs still filter with their own test's fs server-side).
      Collapsed-cell filter row is a FLOATING overlay (user: "should
      appear left of the button so it doesnt squeeze the plots"):
      position absolute in the header, left:0 right:58 (keeps ≈/expand
      clickable), zIndex 6, elevated bg + shadow; wrapped param lines
      float OVER the canvas top instead of pushing it down (canvas bbox
      verified identical open/closed, even with band-pass's 4 inputs).
      Expanded plots keep the normal in-flow row below the header.

- [x] Uploads page (2026-07-17, TODO item: "upload page with upload history,
      upload status etc.") — 4th header tab 'Uploads'
      (components/upload/UploadView.tsx): dashed drop-zone/picker panel,
      history table of every test newest-first (status chip, uploaded date,
      original source file, size on disk, duration/rows/cols/fs, ingest
      time), per-row Analyze→ (jumps to Analyze with that test active) and
      Delete with an undo strip (restore from data/trash; restoreTest added
      to api.ts). Backend list_tests() gained source_file/created_at/
      edited_at/ingest_seconds/size_bytes — created_at falls back to dir
      birthtime for receiving/error tests without meta.json (same UTC ISO
      format, so lexicographic sort stays chronological), size_bytes grows
      live during 'receiving' (shown as "N MB received"). Upload initiation
      records `source_file` in its JSON session metadata before any chunks are
      sent. UploadItem gained testName so the page merges a local
      in-flight transfer (progress bar row) with the server's 'receiving'
      row instead of showing both. App: poller also runs while the Uploads
      tab is open (live status is the page's point); tab renders without
      meta AND on the no-tests screen; loading early-return skips
      tab==='uploads'. RACE FIX (found in headless verify, also applied to
      EditView's handleTestGone): after delete/rename, refresh the test
      list BEFORE invalidateTest, batched in one render — pruning
      metaByTest while `tests` still holds the vanished name makes the
      meta-loader effect refetch it and 404-spam the console. Verified
      headless (Edge): upload→receiving→ready in-table, original filename
      shown, Analyze nav, delete→undo→restore→delete, 0 console errors;
      26/26 pytest.

- [x] Quick wins (2026-07-17, from possible_bugs.md §7) — (1) CSV export:
      GET /export (full test or ?t0&t1 window, optional ?cols; time column
      always first + deduped), GET /testpoints/{id}/export (exact saved-TP
      bounds), GET /raw (original upload via FileResponse, named from
      meta.source_file). All three stream via store.stream_csv (pyarrow
      write_csv per batch, header on the first only). The StreamingResponse
      body acquires locks ITSELF (locks.data_read = read slot + per-test read
      lock) because the endpoint has already returned — and released any
      decorator lock — by the time the body streams, and a rebuild could swap
      data.parquet mid-download. Frontend: per-TP ⬇ (SelectedPointsPanel chips
      use the saved-TP endpoint; Split table uses /export?t0&t1 so UNSAVED/
      edited rows export too) + Uploads-page ⬇ CSV (raw). Download URLs are
      plain <a href download> (services/api.ts rawCsvUrl/exportCsvUrl/
      testPointCsvUrl — no fetch). (2) tp_stats.json sidecar cache keyed on the
      mtime_ns fingerprint of testpoints.json + data.parquet (both are replaced
      atomically, so any TP save / upload / rebuild / hand-edit invalidates it);
      written under the caller's per-test READ lock — safe because writers are
      excluded and a racing reader at worst overwrites its own freshly added
      column (recomputed next request, never wrong). (3) rebuild crash recovery:
      _recover_interrupted_ingests now flips a crashed 'rebuilding' test to
      'error' too (edit._rebuild's parquet-before-pyramid window is still not
      transactional — see possible_bugs 1.3). (4) /edit re-checks status==ready
      UNDER test_write before flipping to 'rebuilding' (two racing /edits can no
      longer both schedule). (5) TimePlot filter overlays now skip TPs whose own
      test lacks the column (cross-test 400 fix). (6) window-bounds math unified
      into store.window_bounds (read_window/read_xy/dsp/export). 37/37 pytest,
      npm run build green. NOT done: the pyramid/row-range tp_stats speedup
      (cache miss still scans the full raw column) and a sortable TP-table view.

- [x] Rebuild atomicity + manual stats refresh (2026-07-17, user follow-up to
      the quick wins) — (1) edit._rebuild is now STAGED: new data.parquet ->
      data.parquet.tmp and new pyramid -> pyramid.tmp are built while the live
      files stay untouched, then swapped in with fast atomic renames (each
      os.replace has a non-existent destination = plain rename; runs under
      test_write so no reader holds a file open) and meta/status written LAST as
      the commit signal. Crash during the build => original test fully intact;
      crash in the ms swap window => status 'rebuilding' -> recovery flips to
      'error'. WINDOWS GOTCHA baked in: build_pyramid and the fact-read now
      close their pyarrow ParquetFile via `with` — os.replace on the STAGED file
      fails with WinError 32 if pyarrow still holds it open (the original code
      dodged this only because it built the pyramid AFTER swapping). _rebuild
      also drops tp_stats.json. (2) Decision: tp_stats stays EXACT full-res (NOT
      the pyramid approximation — accuracy over speed); the sidecar cache makes
      repeats instant, an exact scan on a cache miss is accepted. MVP.md §4
      corrected to say so. (3) POST /tests/{name}/tp_stats/rebuild (store.
      rebuild_tp_stats): recomputes the columns the sidecar currently holds into
      a fresh dict, then ONE atomic write — old averages keep serving until it
      lands. Uploads page '↻ stats' button per ready test; App.handleStatsRebuilt
      drops ONLY statsCache[name] (not selection/meta/TPs) so the scatter
      refetches. 41/41 pytest, npm run build green.

- [x] Column-model overhaul + Settings page (2026-07-19, user requests: "grid
      messed up / only 2 plots", "join the column lists", "first selected TP's
      columns prioritized", "settings page") — all frontend. (1) Grid refill
      fix: App's plotConfigs seeding used to keep the shrunken intersection
      after visiting a small-schema test (2-col test -> 111-col test showed 1
      plot); it now always refills to min(9, available). (2) Grid columns are
      SELECTION-driven: gridColumns = selected TPs' columns in selection order
      (first-selected TP leads) + active-test filler, so the 3x3 stays full;
      used by TP mode and tp-sourced Spectrum/XY (full-test views keep the
      active test's columns only). plotsUserEdited flag: Edit Plots picks are
      session-sticky (valid kept, new columns append); resets when selection
      empties. TPs lacking a cell's column simply don't draw there (existing
      per-test trace guard). (3) Scatter axis defaults = bestAxisPair (module
      fn in App.tsx): X = column in the most tests, Y = column co-occurring
      with X in the most tests — so a narrow, oddly-named test being active
      can't blank every other dataset (a dot needs BOTH axes in its own test).
      axesUserSet flag mirrors plotsUserEdited. (4) 5th header tab 'Settings'
      (components/settings/SettingsView.tsx + constants/settings.ts):
      localStorage 'ptt.settings.v1' (deliberately NOT backend — per-browser
      UI prefs, no API). Preferred scatter X/Y, per-SLOT grid columns
      (positional, 9 selects — the plotted/Y variable in every view mode),
      per-SLOT XY pairs (see 5), default view mode / spectrum estimator /
      logY / clustering, upload fallback fs (stored in the upload-init JSON,
      only used for unusable time columns). Edits accumulate
      in a DRAFT (App.settingsDraft — hoisted so tab switches keep it;
      "unsaved changes" badge) and take effect ONLY via Save:
      App.handleSettingsSave persists then DIFFS old vs new and applies just
      the changed fields (so re-saving can't yank the session's view mode);
      Revert discards. Export downloads the on-screen settings as
      ptt-settings.json; Import parses+normalizeSettings()s a file INTO THE
      DRAFT for review (never auto-saves; bad files -> inline error).
      normalizeSettings is the single loader/import coercer (unknown keys
      dropped, legacy single xyXCol migrates to all-9 slots). Precedence
      everywhere: auto < saved preference < in-session pick; saving a
      changed column pref clears the matching session flag (axesUserSet /
      plotsUserEdited / xyYCols=[] / xyXCols=[]). Preferences naming
      not-loaded columns stay dormant, shown "(not loaded)" in the pickers.
      Settings renders from BOTH App render paths (no-tests screen too) and
      the loading gate skips it like Uploads. (5) XY mode is per-cell on
      BOTH axes (user: "why don't we have Y columns for XY mode?"): runtime
      xyYCols[9] ('' = follow the shared grid slot) + xyXCols[9];
      TimeSeriesGrid overrides cfg/onConfigChange for the XY branch so Edit
      Plots' "[Y] vs [X]" selects write xyYCols — editing an XY Y no longer
      touches the time/spectrum grids. Verified headless (Edge): draft does
      NOT apply pre-Save, survives tab switch, Save applies + persists,
      Revert discards, export file round-trips, import lands as draft then
      saves, legacy xyXCol migrates, XY cell pairs current_a vs torque_nm
      while the TP grid keeps tp_id, selection prioritization + axis-default
      rescue still green, 0 console errors; npm run build green.

- [x] Elapsed-time import setup (2026-07-30, real KiHa data report) — clock
      strings such as `11:00:19.687` were correctly parsed as seconds since
      midnight but incorrectly plotted at ~39,620 s. Measured axes are now
      normalized in Parquet before pyramid generation (`t - t[0]`,
      `meta.t_start=0`, original origin retained as
      `source_time_origin_s`). Every picker/drop path stages files on Uploads
      before transfer and offers Auto / exact CSV column / generated `i/fs`
      modes, a custom generated column name, and per-import Hz. These options
      are immutable resumable-session identity and persist in the manifest and
      browser resume record. Auto with no time-looking source now adds
      `time_s` instead of sacrificing the first signal. Time plots explicitly
      label the x-axis `Time (s)`. Follow-up: measured timestamp dropouts no
      longer bias Hz because normal timing is inferred from continuous
      intervals. Expected missing rows are inserted as NaN signal gaps,
      recorded in `time_gap_ranges`; filters process each continuous region
      independently and spectra reject ranges that cross a dropout. Generated
      time remains consecutive because absent source rows are unknowable.

- [x] Despike + filtered-only plots (2026-07-30, user request) — the per-plot
      filter menu now includes a robust Hampel-style despike processor with a
      time-based context window, maximum event duration, MAD threshold,
      absolute change floor, and linear/local-median replacement. Candidate
      samples are grouped so multi-sample plateaus are handled as one event;
      runs longer than the configured maximum are preserved, window > 2× max
      duration is enforced, NaNs are restored, and known acquisition gaps are
      hard boundaries. `/filter` returns repaired-sample and event counts.
      Filtered data now REPLACES the raw trace while active (solid line/band);
      stale results are hidden during recompute, failure falls back to raw with
      an explicit error, and the controls show labelled units, validation,
      clear/reset, active state, and despike counts.

- [x] Derived variables + formula recipes (2026-07-30, user request) —
      Edit now has an ordered equation workbench with exact `{column}` insertion
      at the caret, operator/function shortcuts, sampled server preview,
      explicit existing-column replacement, and reusable global recipes stored
      atomically in `data/formula_recipes.json`. Expressions are parsed through
      a strict AST allowlist and translated to Polars expressions; no eval path
      exists. Formula batches are standalone edits and materialize Float64
      columns through the staged Parquet/pyramid rebuild, normalize infinities
      to NaN, invalidate TP stats, and persist dependency provenance that stays
      coherent through later rename/drop edits. The existing column table is
      explicitly labelled for rename/remove and keeps the time column
      protected. Backend: 168 tests + 62 subtests green. Frontend build/lint
      green. Isolated Playwright coverage includes cursor insertion, preview,
      recipe save/load/delete, materialization, derived-column rename,
      provenance, and zero console/page errors.

## Windows gotchas (hard-won)

- os.replace onto a file a reader holds open raises PermissionError
  (WinError 5). store.write_json_atomic retries briefly — do NOT remove
  that loop; status polling collides with background-job status writes.
- os.replace of a STAGED parquet also needs the SOURCE closed: pyarrow
  ParquetFile (and polars mmap reads) hold the file open, so os.replace of
  data.parquet.tmp -> data.parquet raises WinError 32 while any pf handle on
  the tmp file is alive. build_pyramid + edit._rebuild's fact-read wrap their
  ParquetFile in `with` for exactly this. Directory swaps (pyramid.tmp) use
  two renames with non-existent destinations, never os.replace onto a
  populated dir (which fails on Windows regardless).
- PowerShell 5.1 `-Encoding utf8` writes a BOM; json.loads then fails and
  _read_json returns None (test shows status 'unknown'). Write JSON files
  from Python, or use UTF8Encoding($false).
- /xy dedupes x==y selects (a grid cell whose column equals the shared X
  axis would otherwise 500 on polars duplicate-column select).
- Frontend: handleTestChange early-returns on the same test name — a
  redundant change event would null meta without re-running the load
  effect (deps [currentTest] unchanged), wedging the UI.

## API gotchas (learned during Phase 0 verification)

- Phase 10 `GET /api/component-statistics` reports current active-test use, not a
  lifetime ledger. Edit saves explicit `component_rpm_column` with
  `expected_component_rpm_revision`; column rename/drop follows/clears it.
  Runtime = positive finite RPM rows / fs, excluding acquisition gaps. Mean/SD
  weight seconds across sources. `component_stats.py` caches only numerical
  summaries, never assignments/totals; read catalog -> test -> native slot.
  Imports/edits retain separate `acquisition_gap_ranges` through fills/trims;
  null means lost legacy history, not empty gaps. Do not replace DSP's existing
  `time_gap_ranges` behavior with this provenance. See
  `docs/COMPONENT_STATISTICS_METHOD.md` and verification for policy/limits.

- Phase 8a analysis recovery uses `GET /api/analysis-sources`: ready tests lazily
  receive a separate `source_identity.json` UUID that survives rebuild/rename/
  trash/restore. Additive autosave `sources` references resolve these IDs, with
  conservative sample/TP revision checks and explicit legacy-name reconnection.
  Metadata and TP GETs accept optional `expected_source_id` guards. Retain loaded
  source references until data invalidation; do not advance old plot references
  to background metadata changes. `docs/SESSION_RECOVERY_VERIFICATION.md` records
  the contract/limits. Phase 8b adds portable named JSON Save/Open through
  `SessionControls` / `sessionFiles`: validate, preview, recheck sources, then
  apply through App's shared recovery routine. Save captures live state instead
  of reading debounced localStorage. Source checks/Close never overwrite the
  workspace; explicit Open accepts recovered references. Spectrum X and XY X/Y
  viewports are per-slot, with UUID/revision/variable/source/interval context;
  programmatic uPlot synchronization is muted so auto-ranges cannot overwrite
  manual ranges. See `docs/SAVED_SESSIONS_VERIFICATION.md` for format and limits.

- Phase 7d trash uses `data/trash/<UUID>/entry.json` plus `data/` containing the
  preserved test folder. `GET /api/trash` migrates legacy name folders under the
  catalog writer lock. Restore by UUID via `POST /api/trash/{id}/restore` with
  `{name}`; a conflict never overwrites active data. The legacy name route only
  accepts an unambiguous copy. Permanent `DELETE /api/trash` requires an explicit
  `{ids:[...]}` snapshot and reports partial failures; `deleting` entries cannot
  restore. One-hour expiry still runs on the next test delete. Trash UUIDs identify
  deletion occurrences; Phase 8a dataset identities are separate. See
  `docs/TRASH_VERIFICATION.md`.

- Phase 7a `meta.json` has optional plain-text `description` (1,000 Unicode
  code points) and `notes` (20,000), separate from legacy `user_meta` keys.
  `/api/tests/{name}/meta` PATCH changes only supplied fields; explicit
  `user_meta` still replaces its block, and empty strings clear text. Use the
  returned metadata in App rather than a second fetch after saving. Upload
  `description` is immutable session identity, stored in manifest/resume records
  and passed to ingest; later edits only affect current meta. Missing fields in
  legacy manifests/records default empty. See `docs/TEST_NOTES_VERIFICATION.md`.

- Upload is a five-route resumable protocol:
  `POST /api/uploads` initializes from
  `{name,source_file,uploader_name,size_bytes,last_modified_ms,fs_hz,time_mode,time_column}`
  (`uploader_name` is UI-required self-reported attribution but remains optional
  in the API for legacy sessions/clients);
  `GET /api/uploads/{id}?name=...` reports durable chunks;
  `PUT /api/uploads/{id}/chunks/{index}?name=...` accepts exactly one
  multipart `file` plus `X-Chunk-SHA256`;
  `POST /api/uploads/{id}/complete?name=...` atomically publishes `raw.csv`
  and schedules ingest; `DELETE /api/uploads/{id}?name=...` removes only the
  matching receiving/failed session (never ingesting/ready). The server derives
  offsets and exact lengths;
  never trust a client-provided offset. Defaults are 16 MiB chunks and three
  concurrent browser requests. Lifecycle remains
  receiving -> ingesting -> ready|error.
- A chunk is committed in this order: validate size/hash, write its
  server-derived range, flush+fsync, atomically persist commit metadata. Exact
  retries return the existing commit; conflicting retries 409. Completion
  requires every expected range and atomically renames the partial file, so
  ingest never observes holes or an unfinished CSV.
- A backend restart preserves a valid `receiving` manifest. A browser refresh
  preserves only localStorage metadata, not the local `File`; require the user
  to reselect it and verify hashes of every committed local chunk before
  skipping bytes. Cancel if the original file is unavailable.
- Upload limits are `KIHA_MAX_UPLOAD_BYTES` for the complete file and
  `KIHA_UPLOAD_CHUNK_BYTES + KIHA_UPLOAD_MULTIPART_OVERHEAD_BYTES` for one
  multipart request. `KIHA_UPLOAD_STALE_AGE_S` defaults to seven days and
  `KIHA_UPLOAD_DISK_RESERVE_BYTES` defaults to 1 GiB. Keep these synchronized
  with nginx's `client_max_body_size`.
- Node's HTTP server normally kills request bodies slower than 5 min
  (`requestTimeout=300 s` default). The vite plugin
  `ptt:unlimited-upload-time` sets it to 0 on dev+preview servers. Requests are
  now bounded chunks rather than a whole multi-GB body, but retaining the
  override protects very slow development links.
- kiha.* loggers only print because run.py calls logging.basicConfig —
  uvicorn's dictConfig wires only its own uvicorn.* loggers, and bare
  INFO records are otherwise dropped silently (logging.lastResort is
  WARNING+). Don't remove that basicConfig.
- POST /split/auto returns a BARE LIST proposal (does not persist); client
  must wrap it in the TestPointsFile shape {version, test, source_file,
  fs_hz, test_points} and PUT /testpoints.
- /filter takes `cols` + `type`; /spectrum takes `col` + `mode` (fft|welch).
  Optional `rpm_col` on /spectrum adds mean/min/max absolute RPM over the identical
  sample window; Spectrum uses that mean to convert Hz to order (`Hz * 60 / RPM`).
  Phase 6a TP filters use `/filter?tp_id=...` with saved half-open row bounds;
  never substitute inclusive `t0/t1`. Use returned `relative_t` for each TP's
  independent filtered facet. Full-test `/filter?t0&t1` remains window-based.
  `plotShowOriginal` is separate display/session state, not a filter parameter.
  Scope and verification: `docs/FILTER_METHOD.md`, `docs/FILTER_OVERLAY_VERIFICATION.md`.
- Phase 6b single time-plot CSV uses `POST /api/plot-export`, staged all-or-error
  before attachment headers. `dsp.filtered_samples` is the shared unrounded DSP;
  do not export reduced `/filter` JSON. Full-test export preserves successful
  t0/t1/px/display processing context; TP filtering uses complete saved rows.
  Null x_range exports complete source rows; explicit ranges crop actual sample
  centers after processing. PNG snapshots current uPlot canvas/visible legend.
  Scope/limits/verification: `docs/PLOT_EXPORT_VERIFICATION.md`.
- Phase 6e Spectrum CSV uses `POST /api/spectrum-export` and `/bundle` with
  `dsp.spectrum_samples`, never reduced display JSON. Native frequency bins
  retain linear amplitude / per-Hz PSD under order or Log Y; frequency crop
  follows complete interval estimation. Expected saved bounds/fs/method/RPM
  guard stale context. Time/Spectrum share staging, budgets and mounted PNG
  capture; see `docs/SPECTRUM_EXPORT_VERIFICATION.md`.
- Phase 6f XY CSV uses `POST /api/xy-export` and `/bundle`: native finite X/Y
  pairs in source row order, exact saved TP or loaded Full rows, no interpolation
  or temporary filters. Default axes mean all pairs; explicit X/Y ranges crop
  both coordinates. `kiha-xy-v2` display uses unrounded stride samples with full
  finite/missing counts; `/xy?tp_id&y_col` preserves exact bounds/column names.
  Never export reduced XY JSON. Expected rows/time-column guard stale scope;
  full context includes the loaded time range. See `docs/XY_EXPORT_VERIFICATION.md`.
- Phase 6g plot dialogs default to CSV/PNG plus `analysis.json` in a ZIP;
  uncheck metadata for file-only delivery. Numerical API `include_metadata`
  defaults false for compatibility; the bundle's top-level flag controls its
  manifest. `analysis_metadata.py` collects source/equation context inside the
  executing source read lock; never reconstruct it from a later metadata read.
  Display responses retain matching loaded context. PNG captures clone it with
  axes/visible traces and `POST /api/plot-image-export` packages JSON-line + PNG
  bytes without source rereads. `kiha-analysis-v1` records actual methods/counts
  and artifact SHA-256; file stats are not immutable source revisions. Unknown
  units and unretained edit history stay explicit. JSON is capped at 2 MiB,
  PNG at 64 MiB/8192px edge/16M pixels. No new persistence or dependencies.
  See `docs/ANALYSIS_METADATA_VERIFICATION.md`.
- Phase 6h keeps staged binary export endpoints and adds ephemeral
  `/api/export-progress` POST + token GET/DELETE, with `X-Export-ID` on the
  binary request. `export_progress.py` uses a request-scoped ContextVar for
  stage/count updates and cooperative checks; no retained files or detached
  jobs. Eight active/64 total trackers, 45s polling lease, 120s terminal TTL.
  Preserve read-lock-before-slot ordering and cancellable lock waits. A canceled
  worker must unwind before its spools close; never abandon a native worker.
  The disconnect monitor cannot consume ASGI receive during image upload, and
  needs an explicit stop flag because nested cancel scopes can consume cancel.
  Frontend `useExportTask` and `ExportTaskStatus` share outcomes/Cancel/focus;
  `services/exportProgress.ts` polls and uses separate keepalive cancellation.
  Freeze every PNG canvas/provenance synchronously before any yield; only
  composition/encoding of owned captures yields. Native calls/toBlob finish
  their current block. See `docs/EXPORT_PROGRESS_VERIFICATION.md`.
- TP data responses nest time per series (series.<col>.t) with time_origin_s;
  full-test /data responses have a top-level t array.
- CSV export/download endpoints (GET /export, /testpoints/{id}/export, /raw)
  return a streamed text/csv attachment, NOT JSON — the frontend hits them as
  <a href download>, never via getJson. The streaming body locks itself
  (locks.data_read); do NOT wrap these with @with_test_read (the decorator lock
  releases when the endpoint returns, before the body streams).
- Phase 5 TP exports append `test_point_id` (the definition ID). A colliding
  source column gets an unused `source_`-prefixed name; full/raw exports are
  unchanged. Uploads' Split CSV disclosure and Split use the same TP endpoint.
  No indices means saved bounds; paired `start_idx`/`end_idx` means an unsaved
  half-open draft with `_draft` filename. Split shares Save's conversion through
  `utils/testPointExport.ts`. Do not revert Split to inclusive `/export?t0&t1`.
  Verification and compatibility details: `docs/SPLIT_EXPORT_VERIFICATION.md`.
