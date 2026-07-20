# Code Review — PTT (ptt/)

Full re-review of the `ptt/` tree (backend `app/`, frontend `src/`, scripts, docs, tests)
on **2026-07-17**, after the XY overhaul, DSP-controls, per-plot-filter and Uploads-page
work landed. Supersedes the previous version of this file. Items are ordered by
**decreasing severity within each section**. Nothing has been fixed; this is a findings
list only. File references are relative to `ptt/`.

Resolved since the last review (removed from the lists below):
- Selected-points bar clipping its own controls (old 1.13) — fixed via `minHeight: 42`.
- Constant-column Spectrum/XY uPlot RangeError/OOM (old 1.15b) — fixed via `safeRange`
  (`TimePlot`/`FullTestPlot` still use uPlot's default `snapNumY`; the near-flat
  precision-underflow case remains theoretically reachable there).
- FMS placeholder text in `PointSelectionMenu` ("tail, flight, maneuver") — now
  "Search by test, TP, label...". (The FMS comment in `CustomScatterTooltip` remains, §3.)

### Fixed 2026-07-19 (fourteenth pass — perf hot paths: uPlot lifecycle + vectorized loops — 78/78 pytest, `npm run build` green, headless-Edge verified)

- **2.4:** the four plot components (`FullTestPlot`/`TimePlot`/`SpectrumPlot`/
  `XYPlot`) no longer `destroy()` + `new uPlot()` on every data change. A shared
  `utils/uplotSync.syncPlot` keeps the instance alive and feeds new data through
  `setData`, rebuilding only when a `structKey` (series labels/roles + pixel size
  + expanded state) changes — so a same-structure refetch (the common zoom/pan
  case, ×9 linked plots per step) is now an in-place data swap, not a canvas
  teardown + plugin re-init + layout reflow. Gotchas handled: (a) the reused
  instance keeps its build-time closures, so each plot routes its pan/zoom +
  `setSelect` commit through a "latest" `onRangeChangeRef`/`onZoomChangeRef`
  (`handleActiveTimeZoom` is unmemoized and closes over `viewMode`); (b) TimePlot's
  zoom is a scale range, not data, so it is kept OUT of the struct key and, on
  reuse with an active zoom, uses `setData(data, false)` + `setScale` to avoid an
  auto-range flash; (c) the per-run cleanup was removed in favour of a single
  unmount-only `clearPlot`, so React StrictMode's mount/unmount/mount still leaves
  exactly one live instance; (d) the pan/zoom plugin's flush-on-destroy still fires
  on a real rebuild (env↔raw / expand / resize) — reuse relies on the plugin's own
  commit timer, which stays alive with the instance. Verified in headless Edge on
  all four view modes: tagging the `.uplot` DOM nodes then zooming shows the SAME
  nodes survive a data change (setData path), while expand/env→raw rebuild them;
  x-linked plots co-reuse (drag-zoom one → both survive), env 1:256↔1:16↔raw
  transitions and dbl-click reset all work, 0 console/page errors.
- **2.6:** `store.read_testpoint_trace`'s envelope reduction no longer runs a
  Python loop over ~4000 buckets with several small numpy calls per bucket per
  column. Each batch now computes per-bucket min/max with `np.minimum.reduceat`
  / `np.maximum.reduceat` over the interior's contiguous bucket runs
  (`np.searchsorted` assigns bucket ids; runs are maximal constant segments),
  and the first-occurrence argmin/argmax via a `positions`-masked reduceat — so
  the only remaining Python loop is a scalar cross-batch merge over runs (no
  numpy calls in it), which is needed to keep the running min/max exact across a
  bucket that straddles two batch/row-group seams. Ties still resolve to the
  earliest sample and all-NaN/±inf buckets still emit one NaN at the bucket's
  first-sample time — verified byte-for-byte against a brute-force reference over
  2000 randomized cases (NaNs, infs, constant runs, row-group sizes 4/7/16/65536,
  budgets 4…8000, random TP bounds) plus a new permanent regression test
  (`test_vectorized_envelope_matches_brute_force_across_row_groups`). A 3M-row ×
  3-column whole-test envelope now resolves in ~190 ms (parquet-read bound).
- **2.5:** `utils/pointClustering.clusterPoints` no longer does an O(n²) nested
  distance scan. Points are bucketed into a uniform `clusterRadius`-sized grid
  (`Map<"col,row", number[]>`); each point's candidate neighbors are gathered
  from its own cell + the 8 around it (a neighbor within `clusterRadius`
  Euclidean is at most one cell away on each axis) and the SAME exact radius test
  + greedy order decide membership, so the output is identical — verified against
  the old scan over 5000 randomized overlap-heavy point sets (varied radii /
  minPts / chart sizes, 0 mismatches). Neighbor lookup is now O(n) average, so
  the `clusteredData` recompute on every pan frame (`MainScatterPlot`) no longer
  grows quadratically with the library size (100 tests × 50 TPs was 12.5M
  checks/frame). The pan-end recompute throttle noted in §2.5 was left out on
  purpose — it would stop clusters updating live during a pan, and the O(n) grid
  already removes the quadratic blow-up.

### Fixed 2026-07-19 (thirteenth pass — upload hardening + lock-registry eviction — 77/77 pytest, `npm run build` green)

- **4.12:** `/api/tests/upload` now caps the transfer. `config.MAX_UPLOAD_BYTES`
  (default 20 GB, `KIHA_MAX_UPLOAD_BYTES` override) is checked against the declared
  `content-length` BEFORE the dir is reserved (an over-cap upload 413s without
  writing a byte or creating a dir) and again against the running byte count while
  streaming (content-length can be absent/wrong). The first chunk is sniffed for a
  NUL byte (`UPLOAD_SNIFF_BYTES` = 8 KB) — a text CSV never contains one, so a
  mistaken binary drop 400s early; both rejections close the file and discard the
  partial dir so a retry does not 409. Two new upload tests (oversize → 413 with no
  dir; binary → 400 with the dir discarded).
- **4.8:** `locks.drop_test_lock(*names)` removes a test's `ReaderWriterLock` from
  `_test_locks` under the registry guard; `api_delete_test` and `api_rename_test`
  call it (for the vanished/old name) after the dir move completes, so the registry
  no longer leaks a lock per deleted/renamed test for the life of the process. Safe
  because delete/rename/restore are serialized under `catalog_write`, and any thread
  already holding the lock keeps its own reference while a later request lazily
  recreates one. Two new lifecycle tests assert the entry is gone after delete/rename.

### Fixed 2026-07-18 (twelfth pass — chart-margin constant + shared test base — 73/73 pytest, `npm run build` green)

- **4.7:** the main scatter's geometry lives in one `constants/scatterGeometry.ts`
  — `SCATTER_MARGIN` (the Recharts `margin` prop) plus `PLOT_INSET` / `PLOT_INSET_X`
  / `PLOT_INSET_Y` (the plot-area pixel insets, with the left inset documented as
  margin.left + the rendered y-axis width). `MainScatterPlot`'s `margin` prop and
  its two `rect.width - 70` / `rect.height - 50` clustering-geometry spots, plus
  `useMainPlotZoom`'s `- 50` / `- 70` / `- 10` cursor-ratio math, now all read
  those constants. Values are unchanged (50/70/10/50), so behavior is identical —
  but a future margin tweak can no longer silently desync zoom-at-cursor and
  clustering.
- **6.11:** new `tests/_base.py` `DataDirTestCase` owns the `TemporaryDirectory` +
  per-module `TESTS_DIR`/`TRASH_DIR` patching (via `addCleanup`, so no `tearDown`).
  All seven data-dir test files now subclass it and just call `super().setUp()` —
  the copy-pasted setUp/tearDown dance is gone and the easy-to-forget `TRASH_DIR`
  patch is automatic. It also patches `TESTS_DIR` on every module that binds it
  (main/store/ingest/edit/dsp/split), so tests are more fully isolated than the
  ad-hoc per-file patch lists were.

### Fixed 2026-07-18 (eleventh pass — poll cost + test-picker dedup — 73/73 pytest, `npm run build` green)

- **2.9:** `store.list_tests` no longer rglob-sizes every file of every test on
  each 2 s poll. `_cached_dir_size` caches a `ready` test's size keyed on its dir
  `mtime_ns` (a rebuild flips status away from `ready` first, and every atomic
  JSON/parquet write renames a file in the dir → bumps the dir mtime, so the key
  is safe); mid-write tests (receiving/ingesting/rebuilding) are still recomputed
  live because an in-place append can grow the file without bumping the dir mtime.
  Two new lifecycle tests: a second poll of a ready test does NOT re-walk the tree,
  a new file invalidates it, and an ingesting test recomputes every poll.
- **6.6:** the identical ready-test `<option>` list (disabled + "(status)" suffix)
  triplicated in `SelectedPointsPanel`/`SplitView`/`EditView` is now one
  `controls/TestOptions` component rendered inside each view's own `<select>` —
  per-view select styling (SelectStyle vs `.input`, widths, labels) is unchanged,
  only the option-rendering is single-sourced.

### Fixed 2026-07-18 (tenth pass — status/helper consolidation + doc reconciliation + frontend dedup — 71/71 pytest, `npm run build` green)

- **4.3 (fully closed):** new `app/status.py` owns the public `write_status` and
  the `INGEST_LIKE`/`BUSY_STATUSES` constants; `main`/`edit`/`ingest` import them
  instead of the underscore-private `ingest._write_status`. `_bucket_minmax` moved
  to `store.bucket_minmax` (public), so `dsp` and `ingest` share it and **`dsp` no
  longer imports `ingest` at all**. No underscore-private helper is imported across
  modules anymore.
- **4.5:** the "is this test busy?" predicate is single-sourced on both sides —
  backend `status.BUSY_STATUSES`, frontend `constants/status.ts`
  (`BUSY_STATUSES` + `isBusyStatus`, documented as the mirror). App's poll
  condition and both UploadView busy checks call `isBusyStatus`; the Header keeps
  three separate per-status counts on purpose (one badge each).
- **3.2 / 3.5 / 3.6 / 3.7:** MVP.md reconciled with the implementation — NaN policy
  is global-only with "drop rows" excluded (§7); the readers convert idx↔time via
  the assumed uniform `fs` with a full-file quantization-aware jitter scan and a
  generated-axis fallback (§12); the serving rule is raw ≤ max(6000, budget) then
  the finest pyramid level fitting the per-plot budget with bucket-merge (§5,
  matches the 2.3 fix, reconciles the 5000/6000 numbers); multi-series-per-plot and
  the x-link toggle are marked not-implemented-in-MVP (§4).
- **6.5:** `getJson` now delegates to `sendJson(path, { signal })` — the
  error-detail extraction lives in one place.
- **4.11:** the `@keyframes spin` that `SelectedPointsPanel` injected into
  `document.head` at import time now lives in `App.css`; the module-scope DOM
  side effect is gone.

### Fixed 2026-07-18 (ninth pass — window-budget + serializer + dedup + dead code — 71/71 pytest, `npm run build` green)

- **2.3 + 6.1:** the window-serving envelope path is deduplicated and now honors
  the per-plot budget. Three shared helpers live in `store` — `plot_budget(px)`
  (~2 pts/px, floored 1000, capped `POINT_BUDGET_CAP`), `pick_pyramid_level(
  n_raw, budget)` (finest level whose bucket count fits the *budget*, not the
  fixed 8000 cap), and `merge_over_cap(t, series, budget)` (the min-of-mins/
  max-of-maxes over-cap merge) — and both `store.read_window` and
  `dsp.filtered_window` call them. A 3×3 grid of ~400 px cells (frontend sends
  `px = clientWidth`) now receives ~1000-point envelopes instead of up to 8000;
  a bugfix here is applied once. New `test_window_budget.py` builds a real
  120k-row pyramid and asserts a 100 px plot gets a strictly smaller payload
  than a 4000 px plot (both ≤ their own budget), the spike survives, and a
  narrow window still serves raw.
- **2.7 + 6.10:** `_nan_to_none` and `_json_numbers` collapsed into one public
  `store.to_json_list` used by every window/TP/filter response. It rounds with
  `np.round(arr, 6).tolist()` (C, not a per-value Python `round(float(v))`) and
  substitutes None for NaN/±inf only when the all-finite fast path misses.
- **6.3 (backend half):** `store._compute_tp_stats` now resolves each TP's row
  range via `_testpoint_bounds` (empty/reversed ranges caught → reported n=0)
  instead of its own inline copy, so the scatter aggregate covers exactly the
  same samples as the CSV export / TP trace. Frontend copies (`SplitPlot.
  effectiveEnd`, `App.handleScatterToggle`) still duplicate — kept in §6.3.
- **4.4:** `PUT /testpoints` and `POST /testpoints/upload` now 400 on a list
  with repeated `id`s (`_reject_duplicate_ids`, before the lock) — previously
  `read_testpoint_trace` silently picked the first match and the frontend
  `${test}:${tpId}` selection key would collide. Two new lifecycle tests.
- **4.13:** CORS no longer allows the dead prototype vite port (`:5173`).
- **4.14:** unused `pandas==3.0.2` dropped from `requirements-dev.txt`.
- **3.8:** `main.py`'s module docstring points at `backend\run.py` (with the
  SelectorEventLoop/logging rationale) instead of the bare-uvicorn command that
  skips the Windows crash workaround.
- **5.1 / 5.3 / 5.4 / 5.5 / 5.6 / 5.10:** dead code removed —
  `useScatterFilter.resetOnTestChange`, the `ZoomRefArea` type, `ClusterDot`'s
  never-passed `isHighlighted` prop (and its memo compare), the write-only
  `testCheckboxRefs` map (the `indeterminate` assignment stays), the
  `.plotWrapperHidden` CSS class, and `formatValue`'s unused `decimals` param.
- **6.7:** `round3` is now a single `utils/formatters.ts` export imported by
  `SplitView` and `SplitPlot` (was defined identically in both).
- NOT done here: the rest of §2 (2.4–2.9), §3 doc items (3.2/3.5/3.6/3.7), §4
  (4.1–4.12 incl. the `_write_status`/`_bucket_minmax` private imports of 4.3),
  §5.9 (unreachable palette entries — left as harmless headroom), and the
  frontend-side dedup of §6.

### Fixed 2026-07-17 (§7 "quick wins" pass — 37/37 pytest, `npm run build` green)

- **1.3 (recovery half):** `_recover_interrupted_ingests` now flips a crashed `rebuilding`
  test to `error` on startup. The `edit._rebuild` crash-consistency window (parquet
  replaced before pyramid/meta) is NOT addressed and stays in §1.3 below.
- **1.8:** `/edit` now re-checks `status == ready` under `test_write` before flipping to
  `rebuilding`, so two racing requests can't both schedule.
- **1.15:** `TimePlot` filter overlays now filter to TPs whose own test has the column
  (matches `SpectrumPlot`/`XYPlot`), so cross-test selections no longer 400.
- **2.2 (caching half):** `tp_stats` is now cached in a `tp_stats.json` sidecar keyed on
  the mtime fingerprint of `testpoints.json` + `data.parquet`. A cache MISS still scans
  the full raw column (the pyramid/row-range optimization is not done) — kept in §2.2.
- **3.3:** CSV export now exists — `GET /export` (full or `[t0,t1]` window, column subset),
  `GET /testpoints/{id}/export` (exact TP bounds), `GET /raw` (original upload). Wired to
  per-TP ⬇ buttons (Selected Points bar + Split table) and ⬇ CSV on the Uploads page.
- **6.2:** window-bounds math unified into `store.window_bounds`, used by
  `read_window`/`read_xy`/`dsp`/export. (The floor-vs-round inconsistency of §1.10 is
  unchanged — `window_bounds` still floors the start; `_testpoint_bounds` still rounds.)

### Fixed 2026-07-17 (eighth pass — cross-test read starvation + doc/dead-code cleanup — 61/61 pytest, `npm run build` green)

- **2.1:** `locks.data_read` now acquires the per-test READ lock BEFORE the process-wide read
  slot (was slot-first). A read of a write-locked test (rebuild/ingest holding `test_write`)
  therefore blocks on `test_read` without holding a slot, so it can no longer park on the
  semaphore and stall reads of every OTHER test. Safe from deadlock because writers never
  take a slot (no thread holds a slot while waiting for a per-test lock). New `test_locks.py`
  proves a read of an idle test finishes promptly while a reader is parked on a write-locked
  test (the test times out under the old ordering) and that the slot still bounds concurrency.
  CLAUDE.md lock-ordering note updated.
- **3.1:** the four stale "mode bar broadcasts to ALL plots" DSP comments
  (`constants/filters.ts`, `FilterRow.tsx`, `TimePlot.tsx`, `FullTestPlot.tsx`) now describe
  the actual per-plot model (no shared/broadcast control).
- **3.10:** the FMS "tail number, flight, … maneuver" comment in `CustomScatterTooltip` now
  says "test name, test point, and label".
- **3.11:** `TpTraceResponse` gains the `i0`, `i1`, `point_budget` fields the backend already
  returns.
- **5.2:** unused `healthCheck` removed from `services/api.ts`.
- **5.8:** the two stale `eslint-disable no-unused-vars` directives in `MainScatterPlot`
  (`latestMouseEvent`, `shapeRenderer` — both used) removed, so
  `--report-unused-disable-directives` no longer flags them. (Pre-existing unrelated lint
  issues remain — that's §4.1, no lint gate.)

### Fixed 2026-07-17 (seventh pass — frontend interaction/cache races — `npm run build` green, browser-verified)

- **1.20:** the split-table `start (s)`/`end (s)` cells now commit only on blur (new
  `NumberCell` with local edit state) instead of on every keystroke. The row-sort reads the
  committed `start_s`, so clearing a field (which used to commit `Number('') === 0` and
  teleport the row to the top) or typing an intermediate value no longer reorders the row
  under the cursor; an empty/invalid entry reverts on blur. Browser-verified: clearing and
  typing in a lower row's start left the order stable; blur committed and reordered as
  expected, 0 console errors.
- **1.18:** the `FullTestPlot` filter-overlay guard now also requires `fwin.i0 === win.i0 &&
  fwin.i1 === win.i1`, not just matching mode+length. A pan at constant zoom keeps the same
  mode and length, so the old range's filtered curve was being drawn over the new window
  until the 300 ms-debounced refetch landed; the range check drops the overlay until the
  matching filtered window arrives.
- **1.16:** `MainScatterPlot` clears its accumulated dot-pixel-position map whenever
  `renderData` changes reference (data/axes/zoom/clustering), so points that dropped out of
  the current frame can no longer linger with stale coordinates and get falsely counted by
  the 15 px "nearby points" click test. The shape renderer repopulates the map with exactly
  the currently-drawn dots on the same render.
- **1.11:** a per-test generation counter (`testGen`), bumped by `invalidateTest`, now guards
  the meta and tp_stats cache writes: a fetch that started before an invalidation (e.g. a
  rebuild landing) captures the generation at launch and drops its result if it changed —
  so a slow pre-rebuild response can no longer repopulate the cache with stale schema/stats.
  `invalidateTest` also clears `metaInFlight` so a fresh fetch starts immediately.

### Fixed 2026-07-17 (sixth pass — frontend wedge/poll/plot robustness — `npm run build` green, browser-verified)

- **1.9:** the app no longer wedges on "Loading PTT Backend…". Three changes in `App.tsx`:
  (a) the meta/TP prune now drops caches only for tests GONE from the list (not merely
  non-ready), so a rebuild keeps the active test's meta instead of blanking the whole UI
  mid-rebuild; (b) a new effect resets a dangling `currentTest` (active test deleted/renamed
  elsewhere) to another ready test — or the no-tests screen — instead of leaving it pointing
  at pruned meta; (c) a transient `fetchMeta`/`fetchTestPoints` failure now schedules a
  bounded retry (`metaRetry`/`scheduleMetaRetry`) so a dropped request can't leave the app
  stuck with no error and no way to reach Uploads. Browser-verified: deleting the active
  test out from under the app left it un-wedged (header present, 0 console errors).
- **1.17:** `tab` added to the status-poll effect deps. Opening the Uploads tab now reliably
  (re)starts polling via the effect itself, not just as a side effect of `handleTabChange`'s
  one `fetchTests`.
- **1.19:** `FullTestPlot` and `SplitPlot` now drop samples whose time serialized to null
  (a NaN in the time column) across every parallel array before feeding uPlot, matching
  `TimePlot`. Largely defensive now that ingest guarantees a finite time axis (third pass),
  but it protects pre-fix tests and hand-built parquets; the guard is a no-op for finite
  data (browser-verified: SplitPlot still renders normally).
- Not done here (frontend §1 remainder): 1.11 (stale fetches repopulating invalidated
  caches — needs generation-keyed/aborted fetches), 1.16 (scatter stale pixel ref), 1.18
  (filter overlay staleness after pan), 1.20 (split-table live-reorder on keystroke).

### Fixed 2026-07-17 (fifth pass — async upload no longer blocks the event loop — 59/59 pytest, `npm run build` green)

- **1.5:** `api_upload` is `async` (it must consume `request.stream()`), but it acquired the
  threading `catalog_write` lock and did all disk/status/discard I/O directly on the event
  loop. If a delete held `catalog_write` during a multi-GB trash rmtree, the upload froze
  the whole uvicorn loop. Fix: every synchronous blocking step now runs in a worker thread
  via `run_in_threadpool` — the catalog-lock reservation (`_reserve_upload_dir`), the
  per-chunk `raw.csv` writes, the `ingesting` status write (whose atomic-replace retries
  can sleep on Windows), and `_discard_partial_upload`. The `test_write` hold across the
  stream was dropped: the atomically-published `receiving` status is the real guard —
  every mutating endpoint 409s on it before its lock (delete/rename via INGEST_LIKE,
  edit/patch/testpoints via `_reject_if_busy` from the fourth pass), a duplicate name 409s
  on the existing dir, and ingest is only scheduled after the transfer — so nothing else
  writes the test dir while it streams. This also structurally reinforces the 4.2
  status-before-lock invariant. Verified live: with a delete holding `catalog_write` for
  0.56 s (9k-file rmtree), a concurrent upload was correctly delayed 0.52 s **yet
  `/api/health` stayed at ≤24 ms** the whole time; mid-transfer abort still discards the
  dir and a retry does not 409.

### Fixed 2026-07-17 (fourth pass — rebuild-safety + split off-by-one + crash gate — 59/59 pytest, `npm run build` green)

- **1.12:** `PATCH /meta`, `PUT /testpoints`, `POST /testpoints/upload` now `_reject_if_busy`
  (status in `BUSY_STATUSES = receiving|ingesting|rebuilding`) BEFORE taking `test_write`,
  and re-check under the lock — so a rebuild in progress returns 409 immediately instead
  of parking the request on the lock (and a threadpool worker) for the whole rebuild. The
  two `@with_test_write` decorators were replaced with explicit pre-checked bodies; the
  now-unused `with_test_write` import and the shadow `import json` (5.7) are gone.
- **1.10:** `split.autosplit` now emits an EXCLUSIVE-boundary `end_s` (next run's first
  sample, or one step past the last sample for the final run) so the frontend's
  `round((end_s - t_start)*fs)` save round-trip reproduces `end_idx = en` instead of
  `en-1`. Saving an untouched proposal no longer drops each test point's last sample.
  (The floor-vs-round divergence between `window_bounds` and `_testpoint_bounds` is
  unchanged — a sub-sample overlay offset, left as noted.)
- **1.4 / 3.9:** `run_backend.bat` no longer hardcodes `KIHA_MAX_CONCURRENT_READS=4`; the
  read gate is derived from the running Python version in `locks.py` (so a 3.14 venv is
  correctly gated to 1). Comment + CLAUDE.md note corrected.

### Fixed 2026-07-17 (third pass — CSV robustness + read 500s — 53/53 pytest, `npm run build` green)

- **1.1 (CSV dialect + non-numeric columns):** ingest now sniffs the dialect
  (`ingest.sniff_dialect`: separator among `;`/tab/`|`/`,` by header+row-consistency,
  decimal comma when the sep isn't `,` and `digit,digit` occurs) and passes
  `separator`/`decimal_comma` to `pl.scan_csv` (+ `truncate_ragged_lines`). The time
  column is parsed to float seconds (`_time_seconds_expr`: numeric passthrough or
  `[HH:]MM:SS[.f]` clock — `19:39,2` → 1179.2). Non-numeric columns (text notes,
  bool/datetime) are RECORDED in `meta.skipped_columns` and skipped; every kept data
  column is cast to Float64 so parquet/pyramid/readers see a uniform numeric schema
  (this also closes most of 1.6 — data.parquet no longer holds non-float columns).
  `meta` gains `csv_separator`, `decimal_comma`, `time_quantized`, `skipped_columns`.
- **1.14 (full-file jitter) + 3.5 (jitter half):** fs is now derived from the FULL
  time column span (immune to 0.1 s clock quantization) and jitter is scanned over the
  whole file, quantization-aware (a coarse-but-uniform clock is NOT flagged; backward
  time / multi-step gaps are). 3.5's other half (readers convert via assumed `fs`, not
  the time column) is unchanged.
- **Corrupted / too-coarse time column now ingests (user follow-up):** when the time
  column cannot yield a valid increasing `fs` (non-finite/unparseable, non-increasing, or
  a single repeated coarse stamp — the real 2-row `Example_DataKiHaX_...csv` sample),
  ingest GENERATES a perfect uniform axis at an assumed rate instead of failing. The rate
  comes from `?fs=` on upload (validated `gt=0`), else `config.DEFAULT_FS_HZ` (2048 Hz);
  the parquet time column is rewritten with the ramp before the pyramid is built, and
  `meta` records `time_source` (`measured`|`generated`) with `t_start=0` for generated.
  So the provided sample now lands in `ready` (119 cols, generated 2048 Hz axis).
- **1.2:** `store._nan_to_none` is finite-safe — NaN *and* ±inf → None — so an inf cell
  (or an overflowing filter/detrend) no longer 500s `/data` / `/filter`.
- **1.7:** `/data` and `/filter` share `_data_columns` (validate + dedupe + drop the
  time column), so `cols` naming the time column no longer 500s.
- **1.13:** `/xy` `max_pts` is bounded `Query(ge=4, le=20000)` (was `min(max_pts,20000)`;
  `max_pts=0` → ZeroDivisionError 500, now a clean 422).
- **1.6 (hardening):** `split.id_candidates` wraps the round/unique check in try/except so
  one odd column skips instead of 500ing the whole candidate list.
- NOT done here: the pre-existing "Unhandled API error" log traceback when a *background*
  ingest fails cleanly (status is correctly set to `error` first; the re-raise for direct
  callers just logs noise after the 200 response). The 3.5 fs-vs-time-column reader gap,
  and everything else in §1 below, remain.

### Fixed 2026-07-17 (second pass — 41/41 pytest, `npm run build` green)

- **1.3 (fully closed):** `edit._rebuild` is now staged — the new `data.parquet` and
  pyramid are built in `*.tmp` paths while the originals stay untouched, then swapped in
  with fast atomic renames and meta/status written last. A crash during the minutes-long
  build leaves the original test fully intact; a crash in the millisecond swap window
  leaves status `rebuilding`, which restart recovery now flips to `error`. (`build_pyramid`
  and the fact-read now close their pyarrow handles via `with` so Windows `os.replace` on
  the staged file can't hit a sharing violation.) The rebuild also drops the `tp_stats.json`
  sidecar.
- **2.2 / 3.4 (resolved by decision, not the pyramid path):** per the maintainer, exact
  full-resolution stats matter more than speed, so the pyramid/row-range approximation is
  deliberately NOT adopted. The sidecar cache (first pass) already removes the repeat cost;
  a cache miss does one exact full-column scan by design. MVP.md §4's "from the pyramid"
  claim was corrected to match. New: a manual `POST /tests/{name}/tp_stats/rebuild`
  (Uploads page "↻ stats" button) recomputes cached columns into a fresh sidecar and swaps
  it in atomically, so the previous averages keep serving until it lands.

---

## 1. Likely bugs

### 1.1 Ingest hard-fails on ANY column that doesn't cast to float — one text column kills the whole upload — FIXED 2026-07-17 (see the third-pass Fixed block above)
`build_pyramid` casts every data column with `.astype(np.float64)`
(`backend/app/ingest.py:106`). A single string column (operator note, firmware version,
timestamp text) raises `ValueError`, the whole ingest lands in `error`, and the test is
unusable — violating hard requirement #5 ("nothing may hardcode a schema"). Real data is
the driver here: `Example_DataKiHaX_GroundTest2607161341_39.csv` (see TODO.md) is
**semicolon-separated with decimal commas and a clock-time `TIME` column** (`19:39,2`) —
today it fails at three independent layers: `pl.scan_csv` default comma separator parses
each line as one column, decimal commas parse as strings, and `detect_time_column` +
the `dt` math (`ingest.py:176-186`) die on non-numeric time values with a cryptic error.
Non-numeric columns should be detected and skipped/kept-as-metadata, and the CSV dialect
(separator, decimal, time format) sniffed or user-selectable.

### 1.2 `±Inf` values in data crash `/data` and `/filter` with a 500 (invalid JSON) — FIXED 2026-07-17 (see the third-pass Fixed block above)
`store._nan_to_none` (`backend/app/store.py:114-115`) only converts **NaN** to `None`;
`±inf` passes through. Starlette's `JSONResponse` renders with `allow_nan=False`, so any
window containing an infinity raises `ValueError` → 500. The sibling helper
`store._json_numbers` (`store.py:275`, TP-trace path) correctly checks `math.isfinite`.
`pl.scan_csv` parses the literal string `inf` as float infinity (not in `NULL_VALUES`,
`ingest.py:26`), so a single `inf` cell permanently breaks the full-test view of that
column while the TP view works. `dsp.filtered_window` inherits the bug via `_nan_to_none`
(and filters/detrend can themselves produce inf from extreme values).

### 1.3 A backend crash during a rebuild leaves the test stuck in `rebuilding` forever — FIXED 2026-07-17 (see the Fixed block above)
`_recover_interrupted_ingests` (`backend/app/main.py:42-56`) only repairs
`INGEST_LIKE = ("receiving", "ingesting")`. A process crash (or `stop.bat`) during an
`/edit` rebuild leaves `status.json` at `rebuilding` permanently: the frontend polls
forever, `/edit` 409s, and nothing ever repairs it. Related crash-consistency gap in
`edit._rebuild` (`backend/app/edit.py:83-120`): `data.parquet` is replaced **before** the
pyramid is rebuilt and meta.json rewritten — a crash in that window leaves new data with
stale meta and a half-rewritten pyramid (the pyramid is rebuilt in place, not
write-tmp-then-rename like the parquet).

### 1.4 `run_backend.bat` unconditionally overrides the Python-3.14 crash gate — FIXED 2026-07-17 (see the fourth-pass Fixed block above)
`locks.py:74-77` deliberately defaults to **1** concurrent native read on win32 +
Python ≥ 3.14 (concurrent polars collects produced whole-process access violations). But
`run_backend.bat:8` sets `KIHA_MAX_CONCURRENT_READS=4` unconditionally, which wins over
the version check. If the venv is ever recreated with 3.14 (the exact failure scenario
the gate exists for), the gate is silently forced open. The bat's comment ("the venv pin
keeps the read gate at 4") mis-describes this — the env var, not the pin, sets the gate.

### 1.5 Sync locks taken inside the async upload endpoint can stall the whole event loop — FIXED 2026-07-17 (see the fifth-pass Fixed block above)
`api_upload` is `async def` but acquires the threading `catalog_write()` lock directly on
the event loop (`main.py:317`), and `_discard_partial_upload` does `catalog_write()` +
`test_write()` + `shutil.rmtree` the same way (`main.py:280-291`). If a DELETE holds
`catalog_write` while purging a multi-GB trash entry (`_purge_trash` runs **inside** the
delete's lock, `main.py:184`), an upload starting at that moment blocks **the uvicorn
event loop itself** — every request from every client freezes until the rmtree finishes.
The `test_write` held across `await request.stream()` is safe only because every mutating
endpoint 409s on status before touching the lock — an invariant enforced nowhere (see
also 1.12, 4.2).

### 1.6 Non-numeric (bool/datetime) columns 500 several read endpoints — LARGELY FIXED 2026-07-17 (ingest now keeps only numeric data columns, §1.1; `id_candidates` hardened)
When ingest *does* succeed with non-float columns (polars-inferred datetime columns cast
via `.astype(np.float64)` without error), the read endpoints still break:
- `split.id_candidates` (`backend/app/split.py:28`): `np.allclose(vals, np.round(vals))`
  raises `TypeError` on datetime64 → the **whole** `/split/candidates` response 500s.
- `store.read_window` raw mode (`store.py:145`) and `split.autosplit` (`split.py:48`)
  `.astype(np.float64)` on a genuinely non-numeric column → `ValueError` → 500 instead of
  a clean 400. `store._batch_float64` (`store.py:266-272`) shows the right pattern (catch
  and raise `ValueError("not numeric")` mapped to 400).

### 1.7 Requesting the time column via `cols` 500s `/data` and `/filter` — FIXED 2026-07-17 (see the third-pass Fixed block above)
The time column is in `meta["columns"]`, so it passes the unknown-column validation
(`main.py:456-459`, `509-512`). Raw mode then selects `[tcol] + cols` → duplicate polars
select → error; envelope mode looks for `{tcol}__min`, which doesn't exist in the pyramid.
`/xy` dedupes exactly this case (`store.py:481-483`, documented in CLAUDE.md); `/data`
and `/filter` don't.

### 1.8 Two concurrent `/edit` requests both get scheduled
`api_edit` checks `status != 'ready'` (`main.py:397-398`) **before** taking any lock; the
status is only flipped to `rebuilding` later (`main.py:439-440`). Two POSTs racing through
validation both schedule background rebuilds; they serialize on `test_write`, but the
second runs ops validated against the pre-first-rebuild schema (e.g. dropping an
already-dropped column) and lands the test in `error`.

### 1.9 A failed meta fetch for the active test wedges the app on the loading screen — FIXED 2026-07-17 (see the sixth-pass Fixed block above)
The loading gate `loading || (currentTest && !meta)` (`frontend/src/App.tsx:683`)
replaces the **entire UI including the header** with "Loading PTT Backend...". The meta
loader effect (`App.tsx:174-203`) retries only when `tests` or `metaByTest` change — on a
transient `fetchMeta` failure it just `console.error`s, nothing changes, the poller isn't
running in steady state, and the app is stuck on the loading screen with no error, no
retry, and no way to reach the Uploads tab. The same wedge occurs if the active test is
deleted/renamed from another browser window while polling is active (the prune removes
its meta but `currentTest` still points at it).

### 1.10 Auto-split proposal loses each run's last sample once saved — FIXED 2026-07-17 (save half; floor-vs-round divergence still open — see the fourth-pass Fixed block above)
`split.autosplit` returns `end_idx = en` (exclusive — includes the run's last sample) but
`end_s = t[en-1]` (`split.py:70-72`). `SplitView.save` recomputes indices from times:
`end_idx = round((end_s - dataStart) * fs) = en - 1`
(`frontend/src/components/split/SplitView.tsx:119-124`). So saving an untouched proposal
silently shrinks every test point by one sample relative to what was previewed. Related
family: `_window_bounds` **floors** the start index (`int(...)`, `dsp.py:31`,
`store.py:132`, `store.py:474`) while `_testpoint_bounds`/`tp_stats`/`_clip_testpoints`
**round** — the same TP boundary can map to different sample indices depending on the
code path (e.g. the TP filter overlay window vs the raw TP trace can be offset by one
sample).

### 1.11 Stale async responses can repopulate invalidated frontend caches — FIXED 2026-07-17 (see the seventh-pass Fixed block above)
`invalidateTest` (`frontend/src/App.tsx:127-150`) deletes cached meta/TPs/stats and clears
`statsInFlight` keys, but cannot cancel in-flight fetches (and never touches
`metaInFlight`). A `fetchMeta`/`fetchTpStats` started before a rebuild completes resolves
afterwards and writes **pre-rebuild** meta/stats back into the cache; because the cache
is then populated, the loader never refetches. Renamed/dropped columns can linger until
the next manual invalidation. In-flight requests should be generation-keyed or aborted on
invalidation.

### 1.12 `PATCH /meta`, `PUT /testpoints` and `POST /testpoints/upload` hang during a rebuild instead of 409ing — FIXED 2026-07-17 (see the fourth-pass Fixed block above)
`api_patch_meta` (`main.py:374-386`) takes `test_write` without checking status;
`api_put_testpoints` / `api_upload_testpoints` (`main.py:598-618`) use `@with_test_write`,
which acquires the lock before the meta check. During a multi-minute rebuild (or a
'receiving' upload) these requests park on the lock — each one pinning a threadpool
worker (default ~40) — instead of returning the 409 every other mutating endpoint gives.

### 1.13 `/xy` with `max_pts=0` → ZeroDivisionError — FIXED 2026-07-17 (see the third-pass Fixed block above)
`stride = max(1, math.ceil(n_raw / max_pts))` (`store.py:477`); `api_xy` clamps only the
upper bound (`min(max_pts, 20000)`, `main.py:481`). `max_pts=0` → 500.
(`/testpoints/{id}/data` validates `ge=4`; `/xy` should validate similarly.)

### 1.14 Jitter/sample-rate detection only looks at the first 4096 rows — FIXED 2026-07-17 (full-file scan + quantization-aware; see the third-pass Fixed block above)
`_ingest_csv` derives `dt`, `fs`, and `jitter_warning` from the first 4096 samples only
(`ingest.py:176-186`) — 2 s of a 2048 Hz file. Rate drift, gaps, or jitter later in the
file are never detected, yet the entire windowed reader converts time↔index with the
assumed uniform `fs`. Windows/TP boundaries silently point at wrong data for such files.
(MVP.md §12 claims ingest warns when Δt deviates >1%.)

### 1.15 TimePlot's per-plot filter fetches ignore per-test column eligibility
The TP filter-overlay effect maps over **all** visible TPs
(`frontend/src/components/plots/TimePlot.tsx:128-158`) without checking
`columnsByTest[s.test].includes(cfg.key)` — unlike `SpectrumPlot`/`XYPlot`, which filter
eligible TPs first. With cross-test selections, a plot whose column doesn't exist in one
TP's test gets a 400 ("unknown columns") on every fetch: the ≈ icon turns permanently red
with a misleading error even though every drawable trace filtered fine.

### 1.16 Scatter click-disambiguation uses stale pixel positions — FIXED 2026-07-17 (see the seventh-pass Fixed block above)
`MainScatterPlot` accumulates every rendered dot's (and cluster's) pixel position in a
ref (`frontend/src/components/plots/MainScatterPlot.tsx:52`, `306`, `521-525`) and never
clears it. After zoom/pan/axis changes, points that moved or are no longer rendered keep
their old coordinates, so the 15-px "nearby points" logic can pop the selection menu with
wrong points or when only one point is actually under the cursor.

### 1.17 Rebuild/uploads polling can fail to start; `tab` is missing from the poll effect's deps — FIXED 2026-07-17 (deps half; the handleRebuildStarted single-fetch fragility remains — see the sixth-pass Fixed block above)
The poll effect reads `tab` in its bail-out condition (`App.tsx:235`) but the dep array is
`[tests, uploadsActive, currentTest, invalidateTest]` (`App.tsx:260`). Opening the Uploads
tab only starts polling because `handleTabChange` happens to `fetchTests().then(setTests)`
— if that one request fails (`catch(() => {})`), the tab shows a frozen list. Same
pattern in `handleRebuildStarted` (`App.tsx:599-606`): if its single `fetchTests` fails,
no state changes, the effect never re-runs, and the completed rebuild is never detected
until the user interacts.

### 1.18 Transiently wrong filter overlay after pan/zoom in FullTestPlot — FIXED 2026-07-17 (see the seventh-pass Fixed block above)
The overlay guard is `fwin.mode === win.mode && fwin.t.length === win.t.length`
(`frontend/src/components/plots/FullTestPlot.tsx:145-148`). After a pan at constant zoom
the new window frequently has the same mode and length, so the **old range's** filtered
curve is drawn over the new window until the 300 ms-debounced refetch lands. Comparing
first/last timestamps (or `i0`/`i1`) would close the gap.

### 1.19 NaN in the time column breaks full-test plots — FIXED 2026-07-17 (see the sixth-pass Fixed block above)
`read_window` runs `t` through `_nan_to_none`, so a NaN time becomes `null` in `win.t`;
`FullTestPlot`/`SplitPlot` feed that straight into uPlot aligned data, whose x array must
be ascending numbers (`TimePlot` filters null t explicitly; the others don't). A NaN cell
in the time column corrupts rendering of the whole window.

### 1.20 Split-table `start (s)` edits live-reorder the row under the user's cursor — FIXED 2026-07-17 (see the seventh-pass Fixed block above)
The TP table renders `[...tps].sort((a, b) => a.start_s - b.start_s)`
(`SplitView.tsx:292`) and the start input commits on **every keystroke**
(`SplitView.tsx:333-339`). Clearing the field to retype commits `Number('') = 0`, which
teleports the row to the top of the table mid-edit; typing an intermediate value that
crosses another TP reorders the rows under the focused input. Commit-on-blur (like the
existing clamping) or a debounced input (like `FilterControls.NumericInput`) would fix it.

---

## 2. Performance bottlenecks

### 2.1 Global read slots are held while blocked on a per-test lock (cross-test head-of-line blocking) — FIXED 2026-07-17 (see the eighth-pass Fixed block above)
`with_test_read` acquires the process-wide `_data_read_slots` semaphore **before**
blocking on the per-test read lock (`backend/app/locks.py:117-127`). During a long
rebuild or ingest (minutes for a 1 h test), any 4 in-flight reads targeting the busy test
park themselves holding all 4 slots → **every read of every other test stalls** until the
rebuild finishes. Fix: acquire `test_read(name)` first and take a slot only around the
native read (writers never take slots, so the inversion cannot deadlock), or time-box the
per-test wait.

### 2.2 `tp_stats` scans the entire raw column per request — CACHED 2026-07-17 (repeat cost gone; exact scan on miss kept by decision)
`store.tp_stats` collects the full column (7.4M rows ≈ 60 MB for perf_1h) once per
(test, column). A `tp_stats.json` sidecar now caches the result (fingerprinted on
`testpoints.json` + `data.parquet` mtime), so repeat visits are instant. The
maintainer chose to keep the **exact** full-resolution scan on a cache miss rather than
approximate from the pyramid — accuracy over speed. Reading only the TP row ranges
(`_iter_parquet_slice`) would still cut a cold miss and preserve exactness; left as an
optional future refinement, not required.

### 2.3 Envelope point budget ignores the plot's pixel width — FIXED 2026-07-18 (see the ninth-pass Fixed block above)
In `read_window`, `px` only affects the raw-vs-envelope threshold; the envelope level is
chosen against `POINT_BUDGET_CAP` (8000), not `budget` (`store.py:149-153`). The 3×3 grid
(~400 px-wide plots, budget 1000) still receives up to 8000-point envelopes × 9 plots per
zoom — ~8× more JSON than the screen can show. Selecting the level against `budget` would
cut payloads and serialization time proportionally. (Same logic duplicated in
`dsp.filtered_window`, see 6.1.)

### 2.4 uPlot instances are destroyed and rebuilt on every data change — FIXED 2026-07-19 (in-place setData via syncPlot; see the fourteenth-pass Fixed block above)
`FullTestPlot`/`TimePlot`/`SpectrumPlot`/`XYPlot` tear down and reconstruct the chart on
each fetch (`FullTestPlot.tsx:150-241` et al.). uPlot's `setData`/`setScale` handles
in-place updates far cheaper; with 9 linked plots refetching per zoom step this is
needless GC and layout churn (and it is why the pan/zoom plugin must flush pending
commits on destroy).

### 2.5 O(n²) clustering re-runs on every pan frame — FIXED 2026-07-19 (grid hash; see the fourteenth-pass Fixed block above)
`clusterPoints` is a nested loop over all points
(`frontend/src/utils/pointClustering.ts:69-106`) and `clusteredData` recomputes whenever
`bounds` changes (`MainScatterPlot.tsx:176-209`) — i.e. on every pan frame, and since the
overlap-disambiguation change it is on for ≥2 points. Fine for today's tens of TPs, but
cost grows quadratically as the library grows (100 tests × 50 TPs = 12.5M distance checks
per pan frame). A uniform grid hash (bucket by `clusterRadius`) makes it O(n), and the
recompute could be throttled to pan-end.

### 2.6 Pure-Python per-bucket loop in `read_testpoint_trace` — FIXED 2026-07-19 (reduceat-vectorized; see the fourteenth-pass Fixed block above)
The envelope reduction loops over buckets in Python and does several small numpy calls
per bucket per column (`store.py:379-408`) — up to ~4000 buckets × N columns per TP
fetch. `np.minimum.reduceat`/`np.argmin` over reshaped bucket views would vectorize this.

### 2.7 Per-value Python rounding in JSON serialization — FIXED 2026-07-18 (see the ninth-pass Fixed block above)
`_nan_to_none` / `_json_numbers` build lists with a Python-level `round(float(v), 6)` per
sample. With 9 plots × up to 8000 points per response this is measurable;
`np.round(arr, 6)` + `tolist()` with a vectorized finite-mask would do the same work in C.

### 2.8 Full-column collects in split helpers
`split.autosplit` collects the whole time + ID columns (`split.py:45-48`, ~120 MB for a
1 h test) and `id_candidates` collects all 112 columns at stride (`split.py:17-19`).
Acceptable today, but both could push column selection down and stream.

### 2.9 `/api/tests` stats the whole data tree on every 2 s poll — FIXED 2026-07-18 (see the eleventh-pass Fixed block above)
`list_tests` → `_dir_size` rglobs and stats every file of every test directory
(`store.py:55-65`) plus reads two JSON files per test, on each poll tick and before every
upload batch. Cheap now (~10 files/test), but it is O(library size) work per 2 s for a
value (live size) only the Uploads page uses — compute size only for `receiving` tests,
or cache per-test size keyed on mtime.

---

## 3. Documentation / comment mismatches

### 3.1 Frontend DSP comments still describe the removed broadcast filter row — FIXED 2026-07-17 (see the eighth-pass Fixed block above)
`constants/filters.ts:1-3` ("controls live in the mode bar … apply to ALL full-test plots
at once"), `FilterRow.tsx:15-17` ("Used both for the set-all-plots row and per-plot
overrides"), `TimePlot.tsx:24-26` and `FullTestPlot.tsx:21-23` ("The mode bar broadcasts
to all plots") — the shared/broadcast filter control was removed the same day it was
added (CLAUDE.md: "there is NO shared/broadcast filter control"). Filter state is per-plot
only (`App.plotFilters`); all four comments now contradict the code they annotate.

### 3.2 MVP.md §7 promises NaN options the implementation deliberately dropped — FIXED 2026-07-18 (see the tenth-pass Fixed block above)
The spec lists "Drop rows containing NaN" and "per column or globally" policy choice.
`edit.py` supports only global `keep_gaps|zero_fill|interpolate` and deliberately
excludes drop-rows (good reason, documented in code) — the spec was never updated.

### 3.3 MVP.md §3 export features do not exist
"Export: full test data as CSV; individual test points as CSV" — there is no export
endpoint or UI anywhere (only the `testpoints.json` download in the Split tab; `raw.csv`
is stored but not downloadable). §6 also says "Export materializes a CSV on demand".

### 3.4 MVP.md §4: "per-TP aggregates come from the pyramid, so this is cheap" — RESOLVED 2026-07-17 (doc corrected)
`tp_stats` reads the full-resolution raw column, not the pyramid — intentionally, for
exactness. MVP.md §4 was corrected to describe the actual design (exact scan + cached
sidecar) instead of the never-implemented pyramid path.

### 3.5 MVP.md §12: ingest jitter warning is much weaker than described — FIXED 2026-07-18 (doc corrected; see the tenth-pass Fixed block above)
"ingest warns if Δt deviates > 1% from nominal" — only the first 4096 samples are checked
(bug 1.14), and time is *not* "derived from the time column, not from row index" in the
readers: all window math converts via the assumed uniform `fs`.

### 3.6 MVP.md §5 serving rule differs from the implementation — FIXED 2026-07-18 (see the tenth-pass Fixed block above)
Spec: "serve from the **coarsest** pyramid level that still gives ≥ ~2 points/pixel".
Code: picks the **finest** level whose point count fits the fixed 8000 cap, ignoring
pixel width entirely (`store.py:149-153`, bug 2.3). Same §5 "raw at ≤ ~5,000 points" vs
`MAX_POINTS_RAW = 6000` (`config.py:16`) vs CLAUDE.md's "~6000" — three documents, three
numbers.

### 3.7 MVP.md §4 features quietly not implemented — FIXED 2026-07-18 (doc corrected; see the tenth-pass Fixed block above)
"Multiple series in a single plot (multi-line, shared time axis, per-series y-axis
scaling if units differ)" — every grid cell plots exactly one column (SplitPlot supports
multi-col internally but is always called with one). "X-axis link toggle" — implemented
as always-on (no toggle). Fine as product decisions, but the spec/implementation
disagree.

### 3.8 `main.py` docstring recommends a launch path that bypasses the crash workaround — FIXED 2026-07-18 (see the ninth-pass Fixed block above)
`backend/app/main.py:3`: "Run: uvicorn app.main:app --reload --port 8000". Running
uvicorn directly skips `run.py`'s SelectorEventLoop setup (its whole reason to exist) and
CLAUDE.md's documented command.

### 3.9 `run_backend.bat` comment mis-describes the read gate
`run_backend.bat:4-5`: "the venv pin keeps the read gate at 4" — the explicit
`KIHA_MAX_CONCURRENT_READS=4` on line 8 sets the gate, and it would also force 4 on a
3.14 venv (bug 1.4).

### 3.10 FMS leftover comment in `CustomScatterTooltip` — FIXED 2026-07-17 (see the eighth-pass Fixed block above)
`CustomScatterTooltip.tsx:47`: "Label with tail number, flight, test point, and maneuver"
— PTT has tests/TPs/labels; no tails, flights, or maneuvers.

### 3.11 `TpTraceResponse` type understates the payload — FIXED 2026-07-17 (see the eighth-pass Fixed block above)
The backend also returns `i0`, `i1`, `point_budget` (`store.py:441-448`);
`frontend/src/types/index.ts:133-142` omits them. Harmless today, but the type is the
only API contract documentation the frontend has.

---

## 4. Maintainability / infrastructure issues

### 4.1 No CI, no backend lint/type tooling, zero frontend tests
Backend has pytest (26 tests) but nothing runs it automatically; there is no
ruff/flake8/mypy config. The frontend has eslint + `tsc` in the build script but not a
single test. For a tool "maintained entirely by Claude", an automated gate (a GitHub
Action running pytest + `npm run build` + eslint) is the cheapest way to keep future
sessions honest.

### 4.2 The "status check before lock" invariant is implicit and load-bearing
Correctness of holding `test_write` across the whole upload transfer (`main.py:333`)
depends on **every** mutating endpoint rejecting busy tests by status before touching the
lock — but nothing enforces it, three endpoints already violate it (bug 1.12), and the
async endpoint acquires threading locks on the event loop (bug 1.5). A small helper
(`require_status(name, "ready")` + a `guarded_write(name)` that asserts the status was
checked, plus `anyio.to_thread` for lock acquisition in async code) would make the
invariant structural.

### 4.3 Private helpers imported across modules — FIXED 2026-07-18 (see the tenth-pass Fixed block above)
`main.py` and `edit.py` import `_write_status` from `ingest`; `dsp.py` imports
`_nan_to_none` from `store` and `_bucket_minmax` from `ingest`. Underscore-private names
acting as a shared API guarantee accidental breakage; promote them into a small shared
module (e.g. `app/serialize.py`, `app/status.py`) with public names.

### 4.4 Duplicate test-point IDs are never validated — FIXED 2026-07-18 (see the ninth-pass Fixed block above)
`PUT /testpoints` and the upload path accept any list; nothing (backend or Split editor)
enforces unique `id`s. `read_testpoint_trace` silently picks the first match
(`store.py:298`), and frontend selection keys `${test}:${tpId}` would collide.

### 4.5 The busy-status string list is duplicated in five places — FIXED 2026-07-18 (see the tenth-pass Fixed block above)
`("receiving", "ingesting", "rebuilding")` appears as backend `INGEST_LIKE` (without
`rebuilding`, `main.py:39`), the App poll condition (`App.tsx:230-231`), the Header badge
counts (`Header.tsx:90-96`), `UploadView.StatusChip` (`UploadView.tsx:65`) and the
UploadView `busy` row flag (`UploadView.tsx:368-369`). Adding a status means five touch
points; a shared constant (and mirroring it in `types/index.ts`) would prevent drift —
this is exactly how `rebuilding` got left out of restart recovery (bug 1.3).

### 4.6 Two parallel styling systems
Utility CSS classes in `App.css` (`.btn`, `.input`, `.panel`, `.badge`) coexist with
duplicated inline-style objects (`constants/styles.ts` `buttonStyle`/`SelectStyle`, plus
ad-hoc inline styles with the same values in most components). Same colors and paddings
are declared in at least three places; theming changes require a repo-wide hunt.

### 4.7 Hardcoded chart-margin math must stay in sync by hand — FIXED 2026-07-18 (see the twelfth-pass Fixed block above)
The Recharts margins (`margin={{ top: 10, right: 20, bottom: 40, left: 35 }}`,
`MainScatterPlot.tsx:626`) are re-derived as magic `- 70` / `- 50` offsets in three
places: `useMainPlotZoom.ts:33-34`, `MainScatterPlot.tsx:186-187` and `438-439`. A margin
tweak silently breaks zoom-at-cursor and clustering geometry.

### 4.8 Per-test lock registry grows without bound — FIXED 2026-07-19 (see the thirteenth-pass Fixed block above)
`_test_locks` (`locks.py:67`) keeps a `ReaderWriterLock` for every test name ever touched
— including deleted and renamed ones — for the life of the process. Tiny objects, but an
unbounded leak in a long-running server and misleading for debugging.

### 4.9 `stop.bat` / `stop.sh` kill by port
Both kill *whatever* listens on 8000/3000 — on a dev machine that can be an unrelated
process. The Windows script at least tries the window title first; the shell script is
`fuser -k` only. A pidfile written by `start.*` would be safer.

### 4.10 `start.bat` has no preflight checks
`start.sh` verifies the venv and `node_modules` exist and prints remediation; `start.bat`
just launches minimized windows that die instantly on a fresh clone, with the error
hidden in a closed console.

### 4.11 Module-scope DOM side effect — FIXED 2026-07-18 (see the tenth-pass Fixed block above)
`SelectedPointsPanel.tsx:255-269` appends a `<style>` tag to `document.head` at import
time. Works, but it is invisible to React, runs before any render, and the keyframes
belong in `App.css`.

### 4.12 No upload size limit or content sniffing — FIXED 2026-07-19 (see the thirteenth-pass Fixed block above)
`/api/tests/upload` streams anything to disk before ingest validates it; a mistaken
multi-GB non-CSV drop fills the data volume with no cap.

### 4.13 CORS still allows the prototype's port — FIXED 2026-07-18 (see the ninth-pass Fixed block above)
`main.py:71` allows `localhost:5173` / `127.0.0.1:5173` — the old prototype's vite port;
PTT uses 3000. Dead allowance, harmless but misleading.

### 4.14 `pandas==3.0.2` in requirements-dev is unused — FIXED 2026-07-18 (see the ninth-pass Fixed block above)
No backend or test file imports pandas (`grep pandas backend/` → only the requirements
line). Dead heavyweight dependency in every dev install.

---

## 5. Dead / unused code

### 5.1 `useScatterFilter.resetOnTestChange` — FIXED 2026-07-18 (removed)
Defined and returned (`frontend/src/hooks/useScatterFilter.ts:111-113`, `198`) but no
caller destructures it — a leftover from the single-test era (test switching no longer
clears filters by design).

### 5.2 `healthCheck` in the API layer — FIXED 2026-07-17 (removed)
`frontend/src/services/api.ts:320-322` — never called anywhere.

### 5.3 `ZoomRefArea` type — FIXED 2026-07-18 (removed)
`frontend/src/types/index.ts:171-174` — no references outside its declaration.

### 5.4 `ClusterDot.isHighlighted` prop — FIXED 2026-07-18 (removed)
Declared and styled (`ClusterDot.tsx:8,22-25`) but `MainScatterPlot` never passes it; the
highlight branch is unreachable.

### 5.5 Write-only `testCheckboxRefs` map — FIXED 2026-07-18 (removed)
`FilterControls.tsx:169` maintains a `Map<string, HTMLInputElement>` that is populated
and cleaned but never read — `indeterminate` is set directly inside the ref callback, so
the map itself does nothing.

### 5.6 `.plotWrapperHidden` CSS class — FIXED 2026-07-18 (removed)
`TimeSeriesGrid.module.css:33-35` — the grid returns `null` for hidden plots instead of
applying it.

### 5.7 Shadow `import json` inside `api_upload_testpoints` — FIXED 2026-07-17 (removed with the 1.12 rewrite)
`backend/app/main.py:612` re-imports `json` locally; it is already imported at module top
(`main.py:6`).

### 5.8 Stale eslint-disable comments in `MainScatterPlot` — FIXED 2026-07-17 (removed)
Lines 96 and 509 disable `@typescript-eslint/no-unused-vars` for `latestMouseEvent` and
`shapeRenderer` — both are used (`handleMouseMove`, `<Scatter shape=...>`), so the
suppressions are dead and mask future real warnings.

### 5.9 Unreachable selection colors
`constants/colors.ts` has 10 colors but `MAX_SELECTED_POINTS = 6` (`App.tsx:88`); with at
most 5 colors in use when the next is assigned, indices 6–9 are unreachable — and the
10th (`#569cd6`) would collide with the unselected-point blue if the cap were ever
raised.

### 5.10 `formatValue`'s `decimals` parameter — FIXED 2026-07-18 (removed)
`frontend/src/utils/formatters.ts:1` — no caller ever passes a second argument.

---

## 6. Duplicate code that could be commonized

### 6.1 Window-serving envelope logic duplicated between `store` and `dsp` (highest value) — FIXED 2026-07-18 (see the ninth-pass Fixed block above)
`store.read_window` (`store.py:139-190`) and `dsp.filtered_window` (`dsp.py:109-173`)
contain verbatim copies of: pyramid level selection, bucket-aligned slicing, and the
entire `merge`/`_merged` over-cap bucket-merge block, plus the parallel raw/envelope
response shapes. Any bugfix (the `budget` issue in 2.3, the inf issue in 1.2) must be
applied twice today. Extract `pick_level(n_raw)`, `merge_buckets(t, series)`, and a
response builder into one place.

### 6.2 Window-bounds computation exists three times
`dsp._window_bounds` (`dsp.py:24-33`) vs inline copies in `store.read_window`
(`store.py:126-134`) and `store.read_xy` (`store.py:467-476`) — same clamp/round rules
(and the shared floor-vs-round inconsistency of bug 1.10); `store` should own one
`window_bounds(meta, t0, t1)` used everywhere.

### 6.3 Test-point end resolution implemented four times — PARTIAL 2026-07-18 (backend `tp_stats` now calls `_testpoint_bounds`; frontend copies remain — see the ninth-pass Fixed block above)
"explicit idx → time×fs → next TP's start → end of data" lives in
`store._testpoint_bounds` (`store.py:193-230`), inline again in `store.tp_stats`
(`store.py:512-523`), in `SplitPlot.effectiveEnd` (`SplitPlot.tsx:31-37`), and in
`App.handleScatterToggle` (`App.tsx:579-595`). At minimum `tp_stats` should call
`_testpoint_bounds`; the two frontend copies should share one helper.

### 6.4 Four plot components repeat the same four blocks
`TimePlot`, `FullTestPlot`, `SpectrumPlot`, `XYPlot` each contain near-identical copies
of: (a) the ResizeObserver box-tracking effect, (b) the post-create legend-height
`setSize` shrink, (c) the ~30-line edit-mode column `<select>` with identical inline
styles, and now (d) the floating collapsed-cell filter-row overlay + expanded filter row
(verbatim in `TimePlot.tsx:296-322`/`376-397` and `FullTestPlot.tsx:266-292`/`351-373`).
A `usePlotBox()` hook, a `shrinkForLegend(u, box)` util, and shared `<PlotHeader>` /
`<PlotFilterOverlay>` components would delete ~300 lines and make the four views
actually uniform.

### 6.5 `getJson` / `sendJson` have identical bodies — FIXED 2026-07-18 (see the tenth-pass Fixed block above)
`frontend/src/services/api.ts:31-44` vs `106-119` — the error-detail extraction is
copy-pasted; `getJson` can delegate to `sendJson(path, { signal })`.

### 6.6 Test-picker `<select>` triplicated — FIXED 2026-07-18 (see the eleventh-pass Fixed block above)
The same ready-test dropdown (options, `disabled`, "(status)" suffix) appears in
`SelectedPointsPanel.tsx:132-143`, `SplitView.tsx:186-195`, and `EditView.tsx:172-181`.

### 6.7 `round3` defined twice — FIXED 2026-07-18 (see the ninth-pass Fixed block above)
`SplitView.tsx:373` and `SplitPlot.tsx:315` — identical one-liner.

### 6.8 Two near-identical color palettes
`constants/colors.ts` (`COLORS`) and `constants/uplotTheme.ts` (`PALETTE`, `colorFor`)
share 7 entries in different orders; consolidating into one palette module would prevent
the two views drifting apart visually.

### 6.9 `buttonStyle`/`SelectStyle` duplicate `.btn`/`.input`
`constants/styles.ts` re-declares in JS exactly what `App.css` utility classes define
(see 4.6) — pick one mechanism.

### 6.10 `_nan_to_none` vs `_json_numbers` — FIXED 2026-07-18 (see the ninth-pass Fixed block above)
Two backend helpers doing the same job with different (one buggy — see 1.2) semantics and
different rounding entry points; unify into a single finite-safe serializer.

### 6.11 Backend test boilerplate — FIXED 2026-07-18 (see the twelfth-pass Fixed block above)
All four test files repeat the same `TemporaryDirectory` + `patch.object(<module>,
"TESTS_DIR", ...)` setUp/tearDown dance (`test_lifecycle.py:18-37`, `test_edit.py:19-31`,
`test_testpoint_data.py:15-24`, `test_upload.py:27-47`); a shared fixture would remove it
and make the (sometimes missing) `TRASH_DIR` patch impossible to forget.

### 6.12 SpectrumPlot / XYPlot fetch scaffolding
The `dead`-flag + AbortController + 100 ms timer + `tpFingerprint` + per-TP `Promise.all`
with eligibility filtering is structurally identical in both (`SpectrumPlot.tsx:82-147`,
`XYPlot.tsx:95-173`); a `useTpSourcedFetch` hook would collapse them (and would have
prevented bug 1.15, where `TimePlot`'s hand-rolled copy forgot the eligibility filter).

---

## 7. Possible improvements & cool features

### Quick wins (hours, high value) — DONE 2026-07-17 (see the "Fixed" block up top)
- ~~**CSV export** (closes 3.3)~~ — `GET /export`, `GET /testpoints/{id}/export`,
  `GET /raw`; per-TP ⬇ buttons + Uploads-page ⬇ CSV.
- ~~**Download raw.csv** from the Uploads page~~ — done (`GET /raw`, `FileResponse` with
  the original source filename).
- **Server-side TP-stats sidecar** — caching done (`tp_stats.json`, mtime fingerprint) +
  a manual `POST /tp_stats/rebuild` (Uploads "↻ stats" button, atomic swap). Exact
  full-column scan on a cache miss is kept by decision (accuracy > speed); the pyramid
  approximation is explicitly declined. A sortable "TP table" view is still an open
  follow-up.
- ~~**`rebuilding` in restart recovery + `/edit` status flip under the lock** (closes 1.8;
  recovery half of 1.3)~~ — done.
- ~~**Column eligibility check in TimePlot's filter effect** (closes 1.15)~~ — done.

### Data robustness (the TODO item)
- **CSV dialect sniffing at ingest**: detect separator (`;`/`,`/tab), decimal comma, and
  encoding from the first KB; parse clock-time / datetime time columns into relative
  seconds; skip (but record in meta) non-numeric columns instead of failing the whole
  ingest (closes 1.1). An upload-preview endpoint ("here's how I'd parse this — 3 columns
  skipped, time column = TIME") would make this transparent.
- **Full-file jitter scan** during the pyramid pass (it already streams every row):
  store max |Δt−dt|/dt and gap locations in meta; surface a warning banner in Analyze
  when time↔index math is unreliable (closes 1.14 / 3.5).

### Analysis features (propeller-specific value)
- **Derived columns**: user-defined expressions (`P_mech = torque * rpm * 2π/60`,
  `eff = thrust / P_elec`, `g/W`) evaluated at ingest or on the fly — the schema-driven
  UI already handles arbitrary columns, so derived ones come for free once computed.
- **Spectrogram / waterfall view** (STFT over time) for vibration columns — the missing
  third view between time and spectrum; scipy.signal.stft server-side, heatmap client-side.
- **RPM-order overlays on spectra**: given an RPM column, draw 1P/2P/blade-pass cursor
  lines (mean RPM over the range × harmonic) so vibration peaks can be attributed at a
  glance; order-tracking mode later.
- **Cursor measurements**: A/B markers on time plots with Δt, Δy, and min/mean/max/RMS of
  the visible window per series — the single most-requested feature class in test-data
  viewers.
- **Test comparison mode**: color scatter points by test (legend per test), and an
  overlay mode plotting the same column from two tests' TPs side by side — the
  cross-test machinery (per-TP fetches in own test) already exists.
- **Density/heatmap XY mode** (MVP §12 names this): stride decimation hides hysteresis
  loops; a server-side 2D histogram fixes it and is cheap.

### UX polish
- **Persist analysis sessions**: save/restore selection, axes, filters, plot configs,
  zoom to a JSON (`data/sessions/`) — makes analyses shareable and survives reloads.
- **PNG export per plot** (uPlot canvas → `toDataURL`, Recharts → svg serialize); later a
  one-click "report" (test meta + TP table + selected plots) as printable HTML.
- **Keyboard shortcuts**: 1–9 expand grid cell, Esc collapse/close menu, ←/→ pan, R reset
  zoom.
- **Wheel-zoom/pan in SplitPlot** (it's the only uPlot view without `xPanZoomPlugin`) and
  snap TP handles to integer sample times.
- **Live notice for TP-definition changes**: after split-save, refresh Analyze caches
  immediately instead of waiting for the next tab switch.

### Infrastructure
- **CI**: GitHub Action running `pytest`, `npm run build`, `eslint`; add `ruff` for the
  backend (closes 4.1). Codify the headless-Edge verification as a Playwright smoke test
  — every phase already does it by hand.
- **WebSocket or SSE status channel** to replace the 2 s poll (removes 2.9 and makes the
  Uploads page truly live).
- **Upload hardening**: size cap + first-KB content sniff (4.12), and chunked/resumable
  uploads for multi-GB files over flaky links.
