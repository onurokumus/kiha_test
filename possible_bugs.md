# Code Review — PTT (ptt/)

Review of the full `ptt/` tree (backend `app/`, frontend `src/`, scripts, docs, tests).
Items are ordered by **decreasing severity within each section**. Nothing has been fixed;
this is a findings list only. File references are relative to `ptt/`.

---

## 1. Likely bugs

### 1.1 `±Inf` values in data crash `/data` and `/filter` with a 500 (invalid JSON)
`store._nan_to_none` (`backend/app/store.py:80`) only converts **NaN** to `None`; `±inf`
passes through. Starlette's `JSONResponse` renders with `allow_nan=False`, so any window
containing an infinity raises `ValueError: Out of range float values are not JSON
compliant` → 500. The sibling helper `store._json_numbers` (`store.py:241`, used by the
TP-trace path) correctly checks `math.isfinite`. `pl.scan_csv` parses the literal string
`inf` as a float infinity (it is not in `NULL_VALUES`, `ingest.py:25`), so a single `inf`
cell in an uploaded CSV permanently breaks the full-test view of that column, while the
TP view of the same data works. `dsp.filtered_window` inherits the bug via `_nan_to_none`
(and filters/detrend can themselves produce inf from extreme values).

### 1.2 A backend crash during a rebuild leaves the test stuck in `rebuilding` forever
`_recover_interrupted_ingests` (`backend/app/main.py:35-49`) only converts `ingesting` →
`error` on startup. A process crash (or `stop.bat`) during an `/edit` rebuild leaves
`status.json` at `rebuilding` permanently: the frontend polls forever, `/edit` returns 409
("not ready"), and nothing ever repairs it. Related crash-consistency gap in
`edit._rebuild` (`backend/app/edit.py:90-120`): `data.parquet` is replaced **before** the
pyramid is rebuilt and meta.json rewritten — a crash in that window leaves new data with
stale meta and a half-rewritten pyramid (the pyramid is rebuilt in place, not
write-tmp-then-rename like the parquet).

### 1.3 `run_backend.bat` unconditionally overrides the Python-3.14 crash gate
`locks.py:74-77` deliberately defaults to **1** concurrent native read on win32 + Python ≥
3.14 because concurrent polars collects produced whole-process access violations. But
`run_backend.bat:8` sets `KIHA_MAX_CONCURRENT_READS=4` unconditionally, which wins over
the version check. If the venv is ever recreated with 3.14 (the exact failure scenario the
gate exists for), the gate is silently forced open. The bat's comment ("the venv pin keeps
the read gate at 4") mis-describes this — the env var, not the pin, sets the gate.

### 1.4 Non-numeric (string) columns 500 several endpoints
Hard requirement #5 is "nothing may hardcode a schema", but a test whose CSV contains any
text column breaks:
- `split.id_candidates` (`backend/app/split.py:28`): `np.allclose(vals, np.round(vals))`
  on an object/string array raises `TypeError` → the **whole** `/split/candidates`
  response 500s (the Split tab's auto-split panel dies for that test).
- `store.read_window` raw mode (`store.py:111`): `.to_numpy().astype(np.float64)` on a
  string column → `ValueError` → 500 instead of a clean 400.
- `split.autosplit` (`split.py:48`) same cast → 500.
`store._batch_float64` shows the right pattern (catch and raise a explicit
`ValueError("not numeric")` that maps to 400); the other paths need the same.

### 1.5 Requesting the time column via `cols` 500s `/data` and `/filter`
The time column is in `meta["columns"]`, so it passes the unknown-column validation
(`main.py:391`, `main.py:444`). Raw mode then selects `[tcol] + cols` → duplicate polars
select → error; envelope mode looks for `{tcol}__min`, which doesn't exist in the pyramid.
`/xy` dedupes exactly this case (`store.py:447-449`, documented in CLAUDE.md); `/data` and
`/filter` don't.

### 1.6 Two concurrent `/edit` requests both get scheduled
`api_edit` checks `status != 'ready'` (`main.py:331-332`) **before** taking any lock; the
status is only flipped to `rebuilding` later (`main.py:373-374`). Two POSTs racing through
validation both schedule background rebuilds; they serialize on `test_write`, but the
second runs ops validated against the pre-first-rebuild schema (e.g. dropping an
already-dropped column) and lands the test in `error`.

### 1.7 Auto-split proposal loses each run's last sample once saved
`split.autosplit` returns `end_idx = en` (exclusive — includes the run's last sample) but
`end_s = t[en-1]` (`split.py:70-72`). `SplitView.save` recomputes indices from times:
`end_idx = round((end_s - dataStart) * fs) = en - 1` (`frontend/src/components/split/SplitView.tsx:120-124`).
So saving an untouched auto-split proposal silently shrinks every test point by one sample
relative to the proposal that was previewed. The two representations should agree on
whether `end_s` is the last-included sample time or the exclusive boundary.

### 1.8 Stale async responses can repopulate invalidated frontend caches
`invalidateTest` (`frontend/src/App.tsx:109-132`) deletes cached meta/TPs/stats but cannot
cancel in-flight fetches. A `fetchMeta`/`fetchTpStats` started before a rebuild completes
resolves afterwards and writes **pre-rebuild** meta/stats back into the cache; because the
cache is then populated, the loader effect never refetches. Renamed/dropped columns can
linger until the next manual invalidation. In-flight requests should be keyed/ignored on
invalidation (a generation counter or AbortController per test).

### 1.9 `/xy` with `max_pts=0` → ZeroDivisionError
`stride = max(1, math.ceil(n_raw / max_pts))` (`store.py:443`); `max_pts` is an
unvalidated query param (`main.py:403-415` clamps only the upper bound). `max_pts=0`
→ 500. (`/testpoints/{id}/data` validates `ge=4`; `/xy` should validate similarly.)

### 1.10 Jitter/sample-rate detection only looks at the first 4096 rows
`ingest._ingest_csv` derives `dt`, `fs`, and `jitter_warning` from the first 4096 samples
only (`backend/app/ingest.py:166-176`) — 2 s of a 2048 Hz file. Rate drift, gaps, or
jitter later in the file are never detected, yet the entire windowed reader converts
time↔index with the assumed uniform `fs`. Windows/TP boundaries will silently point at
wrong data for such files. (MVP.md §12 claims ingest warns when Δt deviates >1%.)

### 1.11 Scatter click-disambiguation uses stale pixel positions
`MainScatterPlot` accumulates every rendered dot's pixel position in a ref
(`frontend/src/components/plots/MainScatterPlot.tsx:52`, `318-345`) and never clears it.
After zoom/pan/axis changes, points that moved or are no longer rendered (clustered,
off-viewport) keep their old coordinates, so the 15-px "nearby points" logic can pop the
selection menu with wrong points or when only one point is actually under the cursor.

### 1.12 Rebuild-completion polling can fail to start
The poll interval only runs while `tests` state contains a busy status or
`rebuildPending` is set **and the effect re-runs** (`frontend/src/App.tsx:204-231`).
`handleRebuildStarted` (`App.tsx:492-499`) sets the ref and refreshes the list, but if
that one `fetchTests()` fails ("poller will catch up" comment), no state changes, the
effect never re-evaluates, and the completed rebuild is never detected until the user
interacts.

### 1.13 Selected-points bar clips its own controls — FIXED 2026-07-17
`SelectedPointsPanel`'s container is fixed at `height: 42` with `flexWrap: 'wrap'` and
`overflow: 'hidden'` (`frontend/src/components/controls/SelectedPointsPanel.tsx:95-97`).
With several selected TPs plus the spectrum/XY controls, content wraps to a second row
that is invisible — including the "Reset Time Zoom" and "Edit Plots" buttons.
Fixed as a side effect of moving the DSP filter row into the bar: the container is now
`minHeight: 42` with no overflow clipping, so wrapped rows are visible.

### 1.14 Transiently wrong filter overlay after pan/zoom in FullTestPlot
The overlay guard is `fwin.mode === win.mode && fwin.t.length === win.t.length`
(`frontend/src/components/plots/FullTestPlot.tsx:160-164`). After a pan at constant zoom,
the new window frequently has the same mode and length, so the **old range's** filtered
curve is drawn over the new window until the 300 ms-debounced refetch lands. Comparing
first/last timestamps (or `i0`/`i1`) would close the gap.

### 1.15 `PATCH /meta` during a rebuild hangs instead of 409ing
`api_patch_meta` (`backend/app/main.py:308-320`) takes `test_write` without checking
status; during a multi-minute rebuild the request just blocks on the lock (likely into a
client timeout). Every other mutating endpoint rejects busy tests.

### 1.15b Constant-column Spectrum/XY plots throw uncaught RangeError in uPlot — FIXED 2026-07-17
Rendering a spectrum or XY plot of a constant-valued column (e.g. `tp_id` inside one TP)
produces a degenerate y-range; uPlot's `numAxisSplits` tick loop underflows its increment
and throws `RangeError: Invalid array length` (uncaught pageerror, axes of that plot
malformed). Observed 2026-07-16 in headless Edge simply by switching to Spectrum and XY
modes with a TP selected and `tp_id` in the 3×3 grid. Worse on the XY **x** scale: mode-2
x auto-range is `[dataMin, dataMax]` with zero padding (`snapNumX`), so a constant X
column made the tick loop spin forever and crash the tab with OOM (user-reported).
Fixed by `safeRange` in `constants/uplotTheme.ts`, wired into both scales of
`XYPlot`/`SpectrumPlot`. `TimePlot`/`FullTestPlot` still use uPlot's default `snapNumY`
(handles exactly-flat data; the near-flat precision-underflow case remains theoretically
reachable there).

### 1.16 NaN in the time column breaks full-test plots
`read_window` runs `t` through `_nan_to_none`, so a NaN time becomes `null` in `win.t`;
`FullTestPlot`/`SplitPlot` feed that straight into uPlot aligned data, whose x array must
be ascending numbers (TimePlot filters null t explicitly; the others don't). A NaN cell in
the time column corrupts rendering of the whole window.

---

## 2. Performance bottlenecks

### 2.1 Global read slots are held while blocked on a per-test lock (cross-test head-of-line blocking)
`with_test_read` acquires the process-wide `_data_read_slots` semaphore **before**
blocking on the per-test read lock (`backend/app/locks.py:117-127`). During a long rebuild
or ingest (minutes for a 1 h test) any 4 in-flight reads targeting the busy test park
themselves holding all 4 slots → **every read of every other test stalls** until the
rebuild finishes. Fix: acquire `test_read(name)` first and take a slot only around the
native read (writers never take slots, so the inversion cannot deadlock), or time-box the
per-test wait. The CLAUDE.md lock-ordering note documents the current order but not this
consequence.

### 2.2 O(n²) clustering re-runs on every pan/zoom frame
`clusterPoints` is a nested loop over all points (`frontend/src/utils/pointClustering.ts:63-100`)
and `clusteredData` recomputes whenever `bounds` changes (`MainScatterPlot.tsx:190-221`) —
i.e. on **every pan frame** once >500 points make clustering kick in. 3000 points ≈ 4.5M
distance checks per frame, on the UI thread, right when the user is dragging. A uniform
grid hash (bucket by `clusterRadius`) makes it O(n), and the recompute could be throttled
to pan-end.

### 2.3 `tp_stats` scans the entire raw column per request
`store.tp_stats` (`backend/app/store.py:474-475`) collects the full column (7.4M rows ≈
60 MB for perf_1h) even when TPs cover a fraction of the file, once per
(test, column) — and the frontend requests x-axis, y-axis, and every filter column for
every test. There is no caching; revisiting the Analyze tab recomputes everything
(`App.tsx:263-269` invalidates on every Split→Analyze switch). Options: read only the row
ranges the TPs span (`_iter_parquet_slice` already exists), compute from the pyramid
(MVP.md §4 claims this is what happens), and/or persist a stats sidecar invalidated on
split-save/rebuild.

### 2.4 Envelope point budget ignores the plot's pixel width
In `read_window`, `px` only affects the raw-vs-envelope threshold; the envelope level is
chosen against `POINT_BUDGET_CAP` (8000), not `budget` (`store.py:116-121`). The 3×3 grid
(~400 px-wide plots, budget 1000) still receives up to 8000-point envelopes × 9 plots per
zoom — ~8× more JSON than the screen can show. Selecting the level against `budget` would
cut payloads and serialization time proportionally.

### 2.5 Pure-Python per-bucket loop in `read_testpoint_trace`
The envelope reduction loops over buckets in Python and does several small numpy calls per
bucket per column (`store.py:345-374`) — up to ~4000 buckets × N columns per TP fetch.
`np.minimum.reduceat`/`np.argmin` over reshaped bucket views would vectorize this.

### 2.6 Per-value Python rounding in JSON serialization
`_nan_to_none` / `_json_numbers` build lists with a Python-level `round(float(v), 6)` per
sample. With 9 plots × up to 8000 points per response this is measurable; `np.round(arr,
6)` + `tolist()` with a vectorized finite-mask would do the same work in C.

### 2.7 Full-column collects in split helpers
`split.autosplit` collects the whole time + ID columns (`split.py:45-48`, ~120 MB for a
1 h test) and `id_candidates` collects all 112 columns at stride (`split.py:18-19`).
Acceptable today, but both could push `gather_every`/column selection down and stream.

### 2.8 uPlot instances are destroyed and rebuilt on every data change
`FullTestPlot`/`TimePlot`/`SpectrumPlot`/`XYPlot` tear down and reconstruct the chart on
each fetch (`FullTestPlot.tsx:166-256` et al.). uPlot's `setData`/`setScale` handles
in-place updates far cheaper; with 9 linked plots refetching per zoom step this is
needless GC and layout churn.

---

## 3. Documentation / comment mismatches

### 3.1 MVP.md §7 promises NaN options the implementation deliberately dropped
The spec lists "Drop rows containing NaN" and "per column or globally" policy choice.
`edit.py` supports only global `keep_gaps|zero_fill|interpolate` and deliberately excludes
drop-rows (good reason, documented in code) — the spec was never updated.

### 3.2 MVP.md §3 export features do not exist
"Export: full test data as CSV; individual test points as CSV" — there is no export
endpoint or UI anywhere (only the `testpoints.json` download in the Split tab; `raw.csv`
is stored but not downloadable).

### 3.3 MVP.md §4: "per-TP aggregates come from the pyramid, so this is cheap"
`tp_stats` reads the full-resolution raw column, not the pyramid (see 2.3). The stated
rationale for offering all four aggregation modes is not what the code does.

### 3.4 MVP.md §12: ingest jitter warning is much weaker than described
"ingest warns if Δt deviates > 1% from nominal" — only the first 4096 samples are checked
(see bug 1.10), and time is *not* "derived from the time column, not from row index" in
the readers: all window math converts via the assumed uniform `fs`.

### 3.5 `main.py` docstring recommends a launch path that bypasses the crash workaround
`backend/app/main.py:3`: "Run: uvicorn app.main:app --reload --port 8000". Running uvicorn
directly skips `run.py`'s SelectorEventLoop setup (its whole reason to exist per its own
docstring) and CLAUDE.md's documented command.

### 3.6 `run_backend.bat` comment mis-describes the read gate
`backend/run_backend.bat:4-5`: "the venv pin keeps the read gate at 4" — the explicit
`KIHA_MAX_CONCURRENT_READS=4` on line 8 sets the gate, and it would also force 4 on a
3.14 venv (bug 1.3). The comment implies the pin alone is what protects the setting.

### 3.7 FMS leftovers in user-facing text and comments
- `PointSelectionMenu` search placeholder: "Search by tail, flight, TP, maneuver..."
  (`frontend/src/components/plots/PointSelectionMenu.tsx:147`) — PTT has tests/TPs/labels,
  no tails/flights/maneuvers.
- `CustomScatterTooltip` comment: "Label with tail number, flight, test point, and
  maneuver" (`CustomScatterTooltip.tsx:26-27`).

### 3.8 MVP.md §4: "X-axis link toggle" was implemented as always-on
Full-test plots are permanently x-linked (shared range + cursor sync); there is no toggle
button as specced. Fine as a product decision, but the spec/implementation disagree.

### 3.9 Minor threshold drift
MVP.md §5 says raw is served at "≤ ~5,000" points; code uses `MAX_POINTS_RAW = 6000`
(`backend/app/config.py:16`), and CLAUDE.md says "~6000". The tildes make this arguably
fine, but the three documents quote three numbers.

### 3.10 `TpTraceResponse` type understates the payload
The backend also returns `i0`, `i1`, `point_budget` (`store.py:407-423`);
`frontend/src/types/index.ts:112-121` omits them. Harmless today, but the type is the only
API contract documentation the frontend has.

---

## 4. Maintainability / infrastructure issues

### 4.1 No CI, no backend lint/type tooling, zero frontend tests
Backend has pytest (18 tests) but nothing runs it automatically; there is no
ruff/flake8/mypy config. The frontend has eslint + `tsc` in the build script but not a
single test. For a tool "maintained entirely by Claude", an automated gate (even a GitHub
Action running pytest + `npm run build`) is the cheapest way to keep future sessions
honest.

### 4.2 Private helpers imported across modules
`main.py` and `edit.py` import `_write_status` from `ingest`; `dsp.py` imports
`_nan_to_none` from `store` and `_bucket_minmax` from `ingest`. Underscore-private names
acting as a shared API guarantee accidental breakage; promote them into a small shared
module (e.g. `app/serialize.py`, `app/status.py`) with public names.

### 4.3 Per-test lock registry grows without bound
`_test_locks` (`backend/app/locks.py:67`) keeps a `ReaderWriterLock` for every test name
ever touched — including deleted and renamed ones — for the life of the process. Tiny
objects, but it is an unbounded leak in a long-running server and makes the registry
misleading for debugging.

### 4.4 Duplicate test-point IDs are never validated
`PUT /testpoints` and the upload path accept any list; nothing (backend or Split editor)
enforces unique `id`s. `read_testpoint_trace` silently picks the first match
(`store.py:264`), and frontend selection keys `${test}:${tpId}` would collide.

### 4.5 Two parallel styling systems
Utility CSS classes in `App.css` (`.btn`, `.input`, `.panel`, `.badge`) coexist with
duplicated inline-style objects (`constants/styles.ts` `buttonStyle`/`SelectStyle`, plus
ad-hoc inline styles with the same values in most components). Same colors and paddings
are declared in at least three places; theming changes require a repo-wide hunt.

### 4.6 Hardcoded chart-margin math must stay in sync by hand
The Recharts margins (`margin={{ top: 10, right: 20, bottom: 40, left: 35 }}`,
`MainScatterPlot.tsx:638`) are re-derived as magic `- 70` / `- 50` offsets in three
places: `useMainPlotZoom.ts:33-34`, `MainScatterPlot.tsx:200-201` and `450-451`. A margin
tweak silently breaks zoom-at-cursor and clustering geometry.

### 4.7 `stop.bat` / `stop.sh` kill by port
Both kill *whatever* listens on 8000/3000 — on a dev machine that can be an unrelated
process. The Windows script at least tries the window title first; the shell script is
`fuser -k` only. A pidfile written by `start.*` would be safer.

### 4.8 `start.bat` has no preflight checks
`start.sh` verifies the venv and `node_modules` exist and prints remediation; `start.bat`
just launches minimized windows that die instantly on a fresh clone, with the error hidden
in a closed console.

### 4.9 Module-scope DOM side effect
`SelectedPointsPanel.tsx:274-288` appends a `<style>` tag to `document.head` at import
time. Works, but it is invisible to React, runs before any render, and the keyframes
belong in `App.css`.

### 4.10 CORS still allows the prototype's port
`main.py:64` allows `localhost:5173` / `127.0.0.1:5173` — the old prototype's vite port;
PTT uses 3000. Dead allowance, harmless but misleading.

### 4.11 No upload size limit
`/api/tests/upload` streams anything to disk before ingest validates it; a mistaken
multi-GB non-CSV drop fills the data volume with no cap or content sniffing.

---

## 5. Dead / unused code

### 5.1 `useScatterFilter.resetOnTestChange`
Defined and returned (`frontend/src/hooks/useScatterFilter.ts:111-113`, `198`) but no
caller destructures it — a leftover from the single-test era (test switching no longer
clears filters by design).

### 5.2 `healthCheck` in the API layer
`frontend/src/services/api.ts:282-284` — never called anywhere.

### 5.3 `ZoomRefArea` type
`frontend/src/types/index.ts:150-153` — no references outside its declaration.

### 5.4 `.plotWrapperHidden` CSS class
`frontend/src/components/plots/TimeSeriesGrid.module.css:24-26` — the grid returns `null`
for hidden plots instead of applying it.

### 5.5 `ClusterDot.isHighlighted` prop
Declared and styled (`frontend/src/components/plots/ClusterDot.tsx:8,16,22-25`) but
`MainScatterPlot` never passes it; the highlight branch is unreachable.

### 5.6 Write-only `testCheckboxRefs` map
`FilterControls.tsx:169` maintains a `Map<string, HTMLInputElement>` that is populated and
cleaned but never read — `indeterminate` is set directly inside the ref callback, so the
map itself does nothing.

### 5.7 Shadow `import json` inside `api_upload_testpoints`
`backend/app/main.py:546` re-imports `json` locally; it is already imported at module
top (`main.py:6`).

### 5.8 Stale eslint-disable comments in `MainScatterPlot`
Lines 96 and 521 disable `@typescript-eslint/no-unused-vars` for `latestMouseEvent` and
`shapeRenderer` — both are used (`handleMouseMove`, `<Scatter shape=...>`), so the
suppressions are dead and mask future real warnings.

### 5.9 Unreachable 10th selection color
`constants/colors.ts` has 10 colors but `MAX_SELECTED_POINTS = 6` (`App.tsx:73`); colors
7–10 are unreachable, and the 10th (`#569cd6`) would collide with the unselected-point
blue if the cap were ever raised.

### 5.10 `formatValue`'s `decimals` parameter
`frontend/src/utils/formatters.ts:1` — no caller ever passes a second argument.

---

## 6. Duplicate code that could be commonized

### 6.1 Window-serving envelope logic duplicated between `store` and `dsp` (highest value)
`store.read_window` (`store.py:116-148`) and `dsp.filtered_window` (`dsp.py:113-167`)
contain verbatim copies of: pyramid level selection, bucket-aligned slicing, and the
entire `merge`/`_merged` over-cap bucket-merge block, plus the parallel raw/envelope
response shapes. Any bugfix (e.g. the `budget` issue in 2.4, or the inf issue in 1.1)
must be applied twice today. Extract `pick_level(n_raw)`, `merge_buckets(t, series)`, and
a response builder into one place.

### 6.2 Window-bounds computation exists three times
`dsp._window_bounds` (`dsp.py:24-33`) vs inline copies in `store.read_window`
(`store.py:92-99`) and `store.read_xy` (`store.py:434-441`) — same clamp/round rules;
`store` should own one `window_bounds(meta, t0, t1)` used everywhere.

### 6.3 Test-point end resolution implemented four times
"explicit idx → time×fs → next TP's start → end of data" lives in
`store._testpoint_bounds` (`store.py:159-196`), inline again in `store.tp_stats`
(`store.py:479-489`), in `SplitPlot.effectiveEnd`
(`frontend/src/components/split/SplitPlot.tsx:31-37`), and in `App.handleScatterToggle`
(`App.tsx:472-489`). At minimum `tp_stats` should call `_testpoint_bounds`; the two
frontend copies should share one helper.

### 6.4 Four plot components repeat the same three blocks
`TimePlot`, `FullTestPlot`, `SpectrumPlot`, `XYPlot` each contain near-identical copies
of: (a) the ResizeObserver box-tracking effect, (b) the post-create legend-height
`setSize` shrink, and (c) the ~30-line edit-mode column `<select>` with identical inline
styles, plus the shared container/button className plumbing. A `usePlotBox()` hook, a
`shrinkForLegend(u, box)` util, and a shared `<PlotHeader>` component would delete
~250 lines and make the four views actually uniform.

### 6.5 `getJson` / `sendJson` have identical bodies
`frontend/src/services/api.ts:31-44` vs `106-119` — the error-detail extraction is
copy-pasted; `getJson` can delegate to `sendJson(path, { signal })`.

### 6.6 Test-picker `<select>` triplicated
The same ready-test dropdown (options, `disabled`, "(status)" suffix) appears in
`SelectedPointsPanel.tsx:137-148`, `SplitView.tsx:186-195`, and `EditView.tsx:172-181`.

### 6.7 `round3` defined twice
`SplitView.tsx:373` and `SplitPlot.tsx:315` — identical one-liner.

### 6.8 Two near-identical color palettes
`constants/colors.ts` (`COLORS`, selection colors) and `constants/uplotTheme.ts`
(`PALETTE`, `colorFor`) share 7 of their entries in different orders; consolidating into
one palette module would prevent the two views drifting apart visually.

### 6.9 `buttonStyle`/`SelectStyle` duplicate `.btn`/`.input`
`constants/styles.ts` re-declares in JS exactly what `App.css` utility classes define
(see 4.5) — pick one mechanism.

### 6.10 `_nan_to_none` vs `_json_numbers`
Two backend helpers doing the same job with different (one buggy — see 1.1) semantics
and different rounding entry points; unify into a single finite-safe serializer.

### 6.11 Backend test boilerplate
All three test files repeat the same `TemporaryDirectory` + `patch.object(<module>,
"TESTS_DIR", ...)` setUp/tearDown dance (`tests/test_lifecycle.py:18-37`,
`tests/test_edit.py:19-31`, `tests/test_testpoint_data.py:15-24`); a shared base class or
pytest fixture would remove it and make adding the (missing) `TRASH_DIR` patch in new
files harder to forget.

### 6.12 SpectrumPlot / XYPlot fetch scaffolding
The `dead`-flag + AbortController + 100 ms timer + `tpFingerprint` + per-TP
`Promise.all` with eligibility filtering is structurally identical in both
(`SpectrumPlot.tsx:81-146`, `XYPlot.tsx:78-156`); a `useTpSourcedFetch` hook would
collapse them.
