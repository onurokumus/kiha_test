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
- [x] Upload overhaul (2026-07-16, user bug report: "upload silently does
      nothing / ingest starts minutes later") — root causes: (a) multipart
      spooling hid the whole transfer from UI and logs, (b) the 6 s notice
      auto-clear erased "uploading…" mid-flight, (c) Node requestTimeout
      killed >5 min uploads. Now: raw-body streaming endpoint writes
      status 'receiving' the moment headers arrive and logs
      receive/ingest start+finish; client disconnect / truncation
      discards the partial test dir (retry-safe); delete/rename/restart-
      recovery treat 'receiving' like 'ingesting'. Frontend: XHR upload
      with per-file header chips (live %, sticky dismissible error chips
      — error text truncates but the ✕ never clips), duplicate names
      pre-checked before sending bytes, names sanitized to the backend
      charset, poller runs during uploads so the 'receiving' row shows
      up. Vite dev/preview requestTimeout=0. Verified end-to-end in
      headless Edge (98 MB button upload + synthetic drag-drop) plus
      slow-chunked and mid-transfer-abort probes against uvicorn; 24/24
      pytest. Playwright drop gotcha: dispatch DragEvent on a node INSIDE
      #root — body is the root's parent, React never sees events
      dispatched there.
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
      live during 'receiving' (shown as "N MB received"). Upload endpoint
      takes ?source= (original client file name — raw-body uploads land in
      raw.csv, which would otherwise be recorded as the source); uploadTest
      sends it. UploadItem gained testName so the page merges a local
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
      logY / clustering, upload fallback fs (sent as ?fs=, only used for
      unusable time columns; uploadTest gained the param). Edits accumulate
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

- POST /api/tests/upload takes the CSV as the RAW request body (`?name=`
  required) — NOT multipart. Multipart made starlette spool the whole body
  to a temp file before the endpoint ran: minutes of dead air for GB files
  (no status.json, no log, and the 6 s notice auto-clear made the UI look
  idle — the original "upload does nothing" bug). Status lifecycle:
  receiving -> ingesting -> ready|error; aborted/truncated transfers
  self-discard so a retry never 409s. Gotcha: an early 4xx (duplicate/bad
  name) closes the connection before the body is read, which XHR reports
  as an opaque network error — that's why App.handleUploadFiles pre-checks
  duplicates against /api/tests BEFORE sending any bytes.
- Node's HTTP server kills request bodies slower than 5 min
  (requestTimeout=300 s default) — the vite plugin 'ptt:unlimited-upload-
  time' (vite.config.ts) sets it to 0 on dev+preview servers or multi-GB
  uploads through the proxy die mid-transfer with no trace anywhere.
- kiha.* loggers only print because run.py calls logging.basicConfig —
  uvicorn's dictConfig wires only its own uvicorn.* loggers, and bare
  INFO records are otherwise dropped silently (logging.lastResort is
  WARNING+). Don't remove that basicConfig.
- POST /split/auto returns a BARE LIST proposal (does not persist); client
  must wrap it in the TestPointsFile shape {version, test, source_file,
  fs_hz, test_points} and PUT /testpoints.
- /filter takes `cols` + `type`; /spectrum takes `col` + `mode` (fft|welch).
- TP data responses nest time per series (series.<col>.t) with time_origin_s;
  full-test /data responses have a top-level t array.
- CSV export/download endpoints (GET /export, /testpoints/{id}/export, /raw)
  return a streamed text/csv attachment, NOT JSON — the frontend hits them as
  <a href download>, never via getJson. The streaming body locks itself
  (locks.data_read); do NOT wrap these with @with_test_read (the decorator lock
  releases when the endpoint returns, before the body streams).
