# Code Review 2 — PTT (ptt/)

Fresh full review of the `ptt/` tree on **2026-07-20** (backend `app/`, `run.py`, tests,
scripts, frontend `src/`, build config, docs). Complements `possible_bugs.md` /
`remaining_bugs.md`: everything marked fixed there was re-verified as fixed; items below
are either NEW findings or explicitly tagged **(carry-over)** when they were already known
and are still open. Nothing has been fixed; this is a findings list only. Items are ordered
by **decreasing severity within each section**. File references are relative to `ptt/`.

---

## 1. Likely bugs

### 1.1 A slow CSV-export download holds a global native-read slot — and lets a delete freeze the whole catalog — for its entire client-paced duration  *(highest)*
`_csv_response` ([backend/app/main.py:591-605](backend/app/main.py#L591-L605)) wraps the whole
StreamingResponse body in `locks.data_read(name)` ([backend/app/locks.py:132-150](backend/app/locks.py#L132-L150)),
which is per-test read lock **plus one of the N global read slots** (N=4 on py3.13, **N=1 on
win32+py3.14**). A StreamingResponse body advances at the **client's** pace, so a slow client
downloading a multi-GB `/export` pins a slot for minutes-to-hours:
- 4 slow downloads (1 on a 3.14 venv) ⇒ **every windowed read of every test blocks** — the
  entire Analyze UI freezes while raw exports trickle out.
- Worse: `api_delete_test` ([main.py:205](backend/app/main.py#L205)) takes `catalog_write()` and
  *then* parks on `test_write(name)` behind the export's read lock. While parked it **holds
  catalog_write**, so `/api/tests` (the 2 s poller, the Uploads page), upload dir reservation,
  rename and restore all wedge until the download completes. Delete's busy pre-check can't
  catch this because exporting is not a status.

Fix sketch: hold only `test_read(name)` for the stream's lifetime and acquire the read slot
*per batch* inside `store.stream_csv` (the native parquet read is per-batch; the yield back to
the socket shouldn't hold the crash-gate slot). For the catalog wedge: acquire `test_write`
**before** `catalog_write` in delete/rename (lock order is currently catalog→test everywhere,
so this needs care), or take the test lock with a timeout and 409 "test is busy being read".

### 1.2 Returning to the Analyze tab from ANY tab silently clears the active test's TP selection
`handleTabChange` ([frontend/src/App.tsx:577-587](frontend/src/App.tsx#L577-L587)) calls
`invalidateTest(currentTest)` whenever the next tab is `analyze` — and `invalidateTest`
drops the active test's meta/TP/stats caches **and removes its test points from the
selection**. The comment (and CLAUDE.md) justify this as "returning from the split editor:
TP definitions may have changed", but it also fires for Settings→Analyze and
Uploads→Analyze, where nothing can have changed TP definitions. Peeking at Settings costs
the user their selection and triggers a full refetch of the active test. Gate it on the
*previous* tab (`tab === 'split' || tab === 'edit'`).

### 1.3 An exception mid-`build_pyramid` leaks open ParquetWriter handles — the failed test can become undeletable on Windows
`build_pyramid` closes its per-level writers only after the whole streaming pass succeeds
([backend/app/ingest.py:189-191](backend/app/ingest.py#L189-L191) — the `for w in
writers.values(): w.close()` sits *outside* any `try/finally`). If `flush()` raises (bad
value, disk full), ingest lands the test in `error`, but the open `L*.parquet` handles live
until GC — and on Windows `shutil.rmtree`/`api_delete_test` fails with "files in use"
(409) until the process happens to collect them. Wrap the writers in `try/finally` (the
module is otherwise careful about exactly this class of Windows sharing violations).

### 1.4 Upload stream: only `ClientDisconnect`/`OSError` are handled — anything else leaks the open raw.csv and a permanent 'receiving' test
`api_upload`'s receive loop ([backend/app/main.py:412-439](backend/app/main.py#L412-L439))
catches `ClientDisconnect` and `OSError`. Any other exception escaping `request.stream()`
(h11 protocol error, a cancellation variant that isn't mapped to ClientDisconnect) propagates
without `out.close()` or `_discard_partial_upload`: the test dir stays at `receiving` forever
(blocks re-upload of that name — dir exists ⇒ 409) with an open file handle, until a backend
restart's recovery flips it to `error`. A broad `except BaseException: close+discard+raise`
(or `try/finally` for the close) would make the cleanup unconditional.

### 1.5 `edit._rebuild`'s failure handler discards the swapped-aside pyramid — an unlucky failure in the 3-rename commit window is made unrecoverable rather than repaired
In the commit sequence ([backend/app/edit.py:143-147](backend/app/edit.py#L143-L147)) the new
parquet is swapped in first, then the old pyramid is moved to `pyramid.old`, then the new
pyramid is renamed in. If the *third* rename fails (antivirus/indexer holding a file — the
exact failure class the staging redesign fought), the `except` path
([edit.py:170-173](backend/app/edit.py#L170-L173)) discards `tmp_pyramid` AND `old_pyramid`,
leaving the test with the new data.parquet and **no pyramid at all**, status `error`,
delete-and-reupload the only way out. Since the new data.parquet is already committed at that
point, the handler could instead rebuild the pyramid from it (or retry the rename) and still
finish `ready`. Low probability, but the current handler actively destroys the last usable
state instead of repairing a recoverable situation.

### 1.6 `tp_stats` cache-write failure 500s a read that actually succeeded
`store.tp_stats` computes the stats, then `write_json_atomic(cache_path, ...)`
([backend/app/store.py:677-684](backend/app/store.py#L677-L684)). If the atomic replace
exhausts its 6 PermissionError retries (a reader holding tp_stats.json open at the wrong
moment on Windows), the exception propagates and the request 500s — even though the stats
the caller asked for are sitting in memory. The sidecar write is an optimization and should
be best-effort: `try/except OSError: pass` around it.

### 1.7 Stuck "⟳" busy spinner when a plot's filter is cleared mid-fetch (TimePlot + FullTestPlot)
Both filter-overlay effects set `fbusy=true` when the debounced fetch starts and reset it in
`finally(() => !dead && setFbusy(false))`. If the user clears the filter while the fetch is
in flight, the effect re-runs: cleanup sets `dead=true` (so the old `finally` skips the
reset) and the new run takes the early-return branch, which resets `fovers`/`ferror` but
**not `fbusy`** ([frontend/src/components/plots/TimePlot.tsx:134-138](frontend/src/components/plots/TimePlot.tsx#L134-L138),
[FullTestPlot.tsx:117-121](frontend/src/components/plots/FullTestPlot.tsx#L117-L121)). The
spinner sticks until some later filter fetch completes. Add `setFbusy(false)` to the
early-return branch (or reset it in the cleanup).

### 1.8 TP trace fetch failures are never retried — a transient error leaves a selected point permanently empty
App's trace loader ([frontend/src/App.tsx:759-795](frontend/src/App.tsx#L759-L795)) logs a
failed `fetchTestPointTrace` and clears the in-flight guard, but nothing re-runs the effect
(its deps don't change on failure), so the TP stays selected with no traces until the user
deselects/reselects it. Meta fetches got a bounded-retry loop for exactly this (bug 1.9,
`scheduleMetaRetry`); traces should reuse the same mechanism.

### 1.9 `store._size_cache` is never evicted on delete/rename
[backend/app/store.py:56](backend/app/store.py#L56) caches `name -> (mtime, size)` per test
but nothing removes entries when a test is deleted or renamed — the same registry-leak
pattern that was fixed for per-test locks in bug 4.8 (`drop_test_lock`,
[main.py:228](backend/app/main.py#L228) / [main.py:312](backend/app/main.py#L312), which
would be the natural place to also drop the size-cache entry). Unbounded (if slow) growth
over the process lifetime; a deleted-then-recreated name is protected only by the mtime gate.

### 1.10 An empty upload body creates a junk 'error' test instead of failing the request
In `api_upload`, a zero-byte body produces no chunks (NUL sniff never runs), and the
truncation check `if expected and received != expected`
([backend/app/main.py:440](backend/app/main.py#L440)) is skipped when `content-length`
is 0/absent — so the endpoint happily flips the empty dir to `ingesting` and background
ingest fails with "CSV appears to be empty", leaving an `error` test the user must delete.
`if received == 0: discard + 400` after the loop would fail fast (same for a header-only
CSV, which errors in ingest with "need at least 2 samples").

### 1.11 Window-bounds vs saved-TP-bounds still disagree by up to a sample (carry-over, old 1.10 residue)
`store.window_bounds` floors the start index and takes `ceil(...)+1` for the end
([backend/app/store.py:222-236](backend/app/store.py#L222-L236)) while `_testpoint_bounds`
rounds ([store.py:287-324](backend/app/store.py#L287-L324)). Consequence today: the Split
table's ⬇ (a `/export?t0&t1` window for unsaved rows) and the Selected-Points ⬇ (the
saved-TP endpoint) can deliver CSVs differing by one row at each edge for the *same* test
point, and TP filter overlays can be offset a sample from the raw TP trace. Documented and
deliberate so far — but worth closing now that both paths funnel through two single helpers.

### 1.12 `detect_time_column` picks the first `time*`-prefixed column with no user override
[backend/app/ingest.py:38-42](backend/app/ingest.py#L38-L42): a column like
`time_of_flight_ms` (or a *text* `timestamp` note column) wins over a real `t_s` appearing
later; when the picked column can't produce a valid axis, ingest silently generates a
synthetic 2048 Hz axis even though a perfectly good time column existed. There is no way to
pick the time column at upload (only `?fs=` for the fallback rate). At minimum prefer a
column that actually parses to a valid increasing axis before falling back to generated.

### 1.13 Split editor loses unsaved test-point edits without warning
`SplitView` tracks `dirty` but the test picker ([frontend/src/components/split/SplitView.tsx:188-194](frontend/src/components/split/SplitView.tsx#L188-L194)),
the header tabs, and the Analyze switch all discard edits silently — only auto-split
re-proposal has a confirm. A `dirty && !confirm(...)` guard on test change (and ideally tab
change) matches the care taken elsewhere (delete confirms, rebuild confirms).

### 1.14 SpectrumPlot TP-source failures are invisible
Per-TP spectrum errors are caught, logged to console, and null-filtered
([frontend/src/components/plots/SpectrumPlot.tsx:120-124](frontend/src/components/plots/SpectrumPlot.tsx#L120-L124))
— a TP whose range is too short ("range too short for a spectrum") simply doesn't appear,
with no ferror-style indicator (TimePlot's filter overlays surface exactly this class of
per-TP error). Same pattern in XYPlot ([XYPlot.tsx:148-152](frontend/src/components/plots/XYPlot.tsx#L148-L152)).

---

## 2. Performance bottlenecks

### 2.1 Export streaming pins the read gate (see 1.1)
The biggest performance risk is 1.1 above: client-paced CSV downloads occupying the
fixed-size native-read semaphore. Per-batch slot acquisition inside `stream_csv` removes it
without giving up the crash gate.

### 2.2 `_purge_trash` runs inside delete's `catalog_write`
[backend/app/main.py:213-214](backend/app/main.py#L213-L214): expiring multi-GB trash
entries is an rmtree performed while holding the catalog lock — every `/api/tests` poll,
upload reservation, and lifecycle op stalls for its duration (the old 1.5 fix moved
*uploads* off the event loop, but the purge itself still runs under the lock). Purge after
releasing the lock, or in a background task.

### 2.3 Spectrum/XY 'full' mode: 9 cells × 9 independent full-range reads per view
Each grid cell issues its own `/spectrum` (full column read + detrend + FFT of up to
`MAX_FILTER_SAMPLES` = 8M samples) or `/xy` fetch over the same `[t0, t1]`
([frontend/src/components/plots/SpectrumPlot.tsx:92-102](frontend/src/components/plots/SpectrumPlot.tsx#L92-L102)).
For a 1 h test that is nine ~60 MB column scans + nine multi-million-point FFTs per
mode-switch or zoom commit, serialized through the read gate. A short-lived server-side
window cache (or a batched multi-column endpoint like `/data`'s `cols=`) would cut this ~9×.

### 2.4 `read_xy` and `spectrum` still serialize with per-value Python `round()` loops
[backend/app/store.py:638-641](backend/app/store.py#L638-L641) and
[backend/app/dsp.py:191-192](backend/app/dsp.py#L191-L192) — the vectorized
`to_json_list` fix (old 2.7) was applied to window/TP paths only. Up to 20k pairs (xy) /
4k bins (spectrum) per request of Python-level rounding; small but free to fix with the
existing helper.

### 2.5 SplitPlot still tears down and rebuilds its uPlot on every window fetch
[frontend/src/components/split/SplitPlot.tsx:88-163](frontend/src/components/split/SplitPlot.tsx#L88-L163)
was not migrated to `syncPlot` in the fourteenth pass (only the four grid plots were), so
every zoom/pan step during split editing is a canvas teardown + rebuild + overlay remeasure.
Same `structKey`/`setData` treatment applies directly.

### 2.6 `GET /tests/{name}` (meta) consumes a global native-read slot to read one JSON file
`api_get_meta` uses `@with_test_read` ([backend/app/main.py:169-175](backend/app/main.py#L169-L175))
= per-test lock **+ read slot**, but only does `json.loads`. On Analyze mount the frontend
fetches meta for every ready test; on a py3.14 venv (gate = 1) those serialize with real
parquet reads. A meta read needs the per-test lock at most — not the crash-gate slot.

### 2.7 Rename needlessly invalidates the whole tp_stats sidecar
`api_rename_test` rewrites `testpoints.json` (embedded name), which bumps its mtime and
therefore the `_tp_stats_fingerprint` ([backend/app/store.py:645-660](backend/app/store.py#L645-L660))
— the first Analyze visit after a rename recomputes every cached column exactly, full-column
scans included. The rename already rewrites the file; it could recompute/rewrite the sidecar
fingerprint at the same time (contents are unchanged).

### 2.8 Full-column collects in split helpers (carry-over)
`split.autosplit` collects the whole time + ID columns (~120 MB for 1 h);
`id_candidates` collects all columns at stride ([backend/app/split.py:17-19](backend/app/split.py#L17-L19),
[split.py:52-55](backend/app/split.py#L52-L55)). Acceptable today; push column selection
down / stream when it starts to hurt.

### 2.9 `interpolate` NaN policy collects the whole test in RAM
Documented in [backend/app/edit.py:110-114](backend/app/edit.py#L110-L114) (~7 GB for a
1 h × 112-col test) — an OOM lands the test in `error` (original data intact thanks to
staging, but the edit is impossible). A chunked interpolate with carried boundary values
would remove the ceiling.

---

## 3. Documentation / comment mismatches

### 3.1 possible_bugs.md's 4.7 fix claim is incomplete — one hardcoded 70/50 spot survived
The fix history says *all* scatter geometry magic numbers now come from
`constants/scatterGeometry.ts`, but `MainScatterPlot.handleMouseMove`'s RAF pan math still
hardcodes `rect.width - 70` / `rect.height - 50`
([frontend/src/components/plots/MainScatterPlot.tsx:451-452](frontend/src/components/plots/MainScatterPlot.tsx#L451-L452)).
Values are numerically identical to `PLOT_INSET_X`/`PLOT_INSET_Y` today, so no behavior bug —
but a margin tweak would silently desync pan speed from zoom/clustering, which is exactly
what 4.7 was supposed to prevent.

### 3.2 App comment: "Switching back from the split editor" — but the code fires from every tab
[frontend/src/App.tsx:575-576](frontend/src/App.tsx#L575-L576) describes the split-editor
case only; the invalidation runs on every `→ analyze` transition (this is bug 1.2 — fix the
code, then the comment is right).

### 3.3 TODO.md's only item is done
[TODO.md](TODO.md) still lists "handle Example_DataKiHaX_... with weird time-stamp and semi
columns" — implemented in the third pass (dialect sniff, decimal comma, clock time, generated
axis; there's a permanent test for it in `test_ingest_dialect.py`). Stale worklist.

### 3.4 MVP.md §12: "same test name uploaded twice → suffix or prompt user"
[docs/MVP.md:219](docs/MVP.md#L219) — the implementation rejects (client pre-check chip +
server 409); there is no suffixing and no prompt-to-rename flow. Fine as a product decision;
the risk note should say what actually happens.

### 3.5 README quick-start may not match the repo layout
[README.md:25-27](README.md#L25-L27) clones `github.com/onurokumus/kiha_test.git` then
`cd kiha_test; cd backend` — this working tree keeps everything under `ptt/` (and is not a
git repo), so the instructions only work if the published repo has `ptt/`'s *contents* at
its root. Worth a one-time verification that the clone path and the tree agree.

### 3.6 "missing" vs "unknown" for the same state
`store.get_status` returns `{"status": "missing"}` for an absent test
([backend/app/store.py:147](backend/app/store.py#L147)) while `list_tests` defaults an
unreadable status.json to `"unknown"` ([store.py:29](backend/app/store.py#L29)). Two labels
for "no readable status"; the frontend special-cases neither. Trivial, but pick one.

### 3.7 `SpectrumData` type omits `nperseg`
The backend adds `nperseg` to Welch responses ([backend/app/dsp.py:171-175](backend/app/dsp.py#L171-L175));
[frontend/src/types/index.ts:221-229](frontend/src/types/index.ts#L221-L229) doesn't list it.
Same class as the fixed 3.11 (the types are the only API contract doc the frontend has).

### 3.8 SettingsView hint: "X auto = the first grid column"
[frontend/src/components/settings/SettingsView.tsx:241](frontend/src/components/settings/SettingsView.tsx#L241)
— the actual fallback is `gridColumns[0]` of the ordered column *universe* (selection-first:
the first-selected TP's first column when TPs are selected), not "cell 1's column". Nitpick,
but this hint is what users read to predict behavior.

---

## 4. Maintainability / infrastructure issues

### 4.1 No CI; no backend lint/type tooling; zero frontend tests (carry-over)
Backend: 78 pytest tests but nothing runs them automatically; no ruff/mypy. Frontend:
`npm run lint` exists, is not a gate, and still reports pre-existing issues (`e as any` in
MainScatterPlot, a `useMemo` dep warning); not a single frontend test. A GitHub Action
running pytest + `npm run build` + eslint (+ ruff) remains the cheapest way to keep future
sessions honest.

### 4.2 The status-before-lock invariant is still convention, and its pattern is now copy-pasted ×3 (carry-over, worse)
`_reject_if_busy` → `test_write` → re-check-under-lock appears verbatim in `api_patch_meta`,
`api_put_testpoints`, `api_upload_testpoints`
([backend/app/main.py:467-482](backend/app/main.py#L467-L482), [795-807](backend/app/main.py#L795-L807),
[810-824](backend/app/main.py#L810-L824)). A `guarded_write(name)` contextmanager (pre-check,
acquire, re-check) would make the invariant structural *and* delete the triplication.

### 4.3 Test-name validation is scattered and inconsistent per endpoint
Upload/rename validate the charset (`TEST_NAME_RE`); delete/restore/raw do resolve-and-
containment checks; every other endpoint accepts any single path segment (e.g. `..`, which
today just fails to find files outside `tests/`, but only by luck of layout). One
`validate_test_name` FastAPI dependency applied to the router would centralize it.

### 4.4 Two parallel styling systems (carry-over)
`App.css` utilities (`.btn`/`.input`/`.panel`/`.badge`) coexist with
`constants/styles.ts` (`buttonStyle`/`SelectStyle`) plus ad-hoc inline objects re-declaring
the same colors/paddings (e.g. the edit-mode `<select>` style object copied in four plot
components). Theming still needs a repo-wide hunt.

### 4.5 Launch scripts (carry-over, plus one new asymmetry)
`stop.bat`/`stop.sh` kill whatever listens on 8000/3000 (possibly an unrelated process);
`start.bat` has no preflight checks (`start.sh` does); and `start.sh` launches `run.py`
**without** the auto-restart wrapper that `run_backend.bat` provides on Windows — a Linux
crash stays down. A pidfile + a tiny shared restart loop would close all three.

### 4.6 UploadView action messages never clear
`actionError`/`actionNote` ([frontend/src/components/upload/UploadView.tsx:134-135](frontend/src/components/upload/UploadView.tsx#L134-L135))
persist until the next action overwrites them — a stale "averages recomputed" note can sit
under the table for the whole session. Auto-clear (like App's notice) or a dismiss ✕.

### 4.7 `TimePlotConfig {key, label}` — label always equals key
Every construction site does `{ key: c, label: c }`. The two-field type suggests a
distinction (display names? units?) that nothing implements; either collapse to `string`
or actually use `label` (nice hook for future unit-aware display).

### 4.8 Backend log files grow without bound
`run_backend.bat` appends to `backend.log` forever (plus `backend_err.log` /
`backend_run.log` already sitting in `backend/`). Gitignored, but a long-lived rig PC will
accumulate gigabytes; a dated log or simple size-based rotation in the wrapper would do.

---

## 5. Dead / unused code

### 5.1 `locks.with_test_write` has no callers
[backend/app/locks.py:163-170](backend/app/locks.py#L163-L170) — its last two users were
replaced with explicit pre-checked bodies in the fourth pass (by design, see 4.2); the
decorator itself remained. Delete it, or a future endpoint will reach for it and reintroduce
the park-on-lock-during-rebuild bug (1.12) it was retired for.

### 5.2 Unreachable selection colors (carry-over 5.9)
`constants/colors.ts` has 10 entries; `MAX_SELECTED_POINTS = 6`
([frontend/src/App.tsx:170](frontend/src/App.tsx#L170)) makes indices 6-9 unreachable, and
entry #10 (`#569cd6`) would collide with the unselected-dot blue if the cap were ever
raised. Harmless headroom — but the collision deserves a comment before anyone raises the cap.

### 5.3 `XYData.n_raw` is never read by the frontend
Returned by `/xy` and declared in [frontend/src/types/index.ts:64-68](frontend/src/types/index.ts#L64-L68);
`XYPlot` uses only `stride` and `series`. Fine to keep as API surface — just noting it is
currently decorative.

---

## 6. Duplicate code that could be commonized

### 6.1 The four grid plot components still repeat four blocks (carry-over 6.4)
`TimePlot`/`FullTestPlot`/`SpectrumPlot`/`XYPlot` each copy: (a) the ResizeObserver
box-tracking effect, (b) the identical `onCreate` legend-height `setSize` shrink, (c) the
~30-line edit-mode column `<select>` (styled inline three times + `editSelectStyle` in
XYPlot), (d) the floating collapsed-cell filter overlay + expanded filter row
(TimePlot/FullTestPlot, byte-identical styling). A `usePlotBox()` hook,
`shrinkForLegend(u, box)`, and shared `<PlotHeader>`/`<PlotFilterOverlay>` would delete
~300 lines.

### 6.2 Open-ended TP end resolution duplicated on the frontend (carry-over 6.3)
`SplitPlot.effectiveEnd` ([frontend/src/components/split/SplitPlot.tsx:32-38](frontend/src/components/split/SplitPlot.tsx#L32-L38))
vs the inline re-derivation in `App.handleScatterToggle`
([frontend/src/App.tsx:846-853](frontend/src/App.tsx#L846-L853)). Backend half was unified in
the ninth pass; these two remain.

### 6.3 SpectrumPlot / XYPlot fetch scaffolding (carry-over 6.12)
`dead` flag + AbortController + 100 ms timer + `tpFingerprint` + per-TP `Promise.all` with
eligibility filtering is structurally identical in both; a `useTpSourcedFetch` hook would
collapse them (and give 1.14's error surfacing one home).

### 6.4 Null-time sample dropping duplicated
The "drop samples whose time serialized to null across every parallel array" loop exists in
`FullTestPlot` ([frontend/src/components/plots/FullTestPlot.tsx:219-223](frontend/src/components/plots/FullTestPlot.tsx#L219-L223))
and `SplitPlot` ([SplitPlot.tsx:123-127](frontend/src/components/split/SplitPlot.tsx#L123-L127)),
with a per-trace variant in TimePlot. One `dropNullTimes(data)` util.

### 6.5 `api_put_testpoints` vs `api_upload_testpoints`
The two bodies are identical except for payload parsing
([backend/app/main.py:795-824](backend/app/main.py#L795-L824)); one `_save_testpoints(name,
payload)` helper (which would also carry the 4.2 `guarded_write`).

### 6.6 Path-containment check triplicated in main.py
`tests_root = TESTS_DIR.resolve(); (TESTS_DIR / name).resolve(); parent != root → 404`
appears in delete, restore (trash variant), and raw-download
([main.py:206-209](backend/app/main.py#L206-L209), [235-237](backend/app/main.py#L235-L237),
[642-645](backend/app/main.py#L642-L645)) — one `resolve_test_dir(name)` helper (ties into 4.3).

### 6.7 Two near-identical color palettes (carry-over 6.8)
`constants/colors.ts` (`COLORS`, selection) and `constants/uplotTheme.ts`
(`PALETTE`/`colorFor`, traces) share 7 entries in different orders.

### 6.8 `buttonStyle`/`SelectStyle` duplicate `.btn`/`.input` (carry-over 6.9)
See 4.4 — pick one mechanism.

### 6.9 Active-toggle button styling spread repeated 4× in SelectedPointsPanel
The `{...buttonStyle, background: active ? '#1e3a52' : ..., color: ..., border: ...}`
pattern appears for mode buttons, source buttons, the log toggle, and Edit Plots
([frontend/src/components/controls/SelectedPointsPanel.tsx:64-76](frontend/src/components/controls/SelectedPointsPanel.tsx#L64-L76)
et seq.) — a `toggleStyle(active)` helper or the `.btn-toggle` CSS class (which already
implements exactly this look) would remove all four.

---

## 7. Possible improvements & cool features (not bugs)

**Analysis (propeller-specific)**
- **Derived/computed columns**: user expressions evaluated server-side
  (`P_mech = torque*rpm*2π/60`, efficiency, g/W, advance ratio) materialized as virtual
  columns usable in every plot/filter/stat.
- **Spectrogram / waterfall view** (STFT over time) for vibration columns — the pyramid +
  windowed reads make the fetch model straightforward.
- **RPM-order cursors on spectra**: 1P/2P/blade-pass overlay lines derived from an RPM
  column's mean over the analyzed range.
- **Cursor measurements**: A/B markers on time plots (Δt, Δy, min/mean/max/RMS of the
  windowed span); RMS/percentile bands as optional envelope companions.
- **Test comparison mode**: same column overlaid across two tests on one axis pair
  (the multi-test scatter already exists; the time-domain compare doesn't).
- **Density/heatmap XY** (server-side 2D histogram) — replaces stride decimation that can
  hide hysteresis loops (MVP §12 explicitly defers this).
- **Export filtered data**: run the active DSP filter server-side over a range and stream
  the result as CSV (the /filter and /export plumbing both exist; they just don't meet).

**UX**
- **Persist analysis sessions**: selection, filters, zoom, per-plot filters → named JSON
  (Settings import/export already established the pattern).
- **PNG/report export** per plot (uPlot `toDataURL`; Recharts SVG serialize), later a
  one-click multi-plot report.
- **Keyboard shortcuts**: 1-9 expand cell, Esc close/collapse, ←/→ pan, R reset zoom.
- **SplitPlot pan/wheel-zoom parity**: it's the only uPlot view without `xPanZoomPlugin`;
  also snap TP drag-handles to integer sample times.
- **Surface `time_source == "generated"`** as an Analyze banner (flag already in meta) —
  users should know the axis is synthetic; same for `jitter_warning`.
- **Selection-cap feedback**: clicking a 7th TP currently does nothing silently
  (`useTestPointSelection` returns unchanged) — flash the notice bar.
- **Sortable TP table** (open follow-up from the tp_stats work) with per-column stat values.
- **Time-column picker at upload** (fixes 1.12 properly): a small post-sniff confirmation
  showing the detected dialect/time column with an override.
- **Ingest/rebuild progress**: status.json could carry a progress fraction (rows processed);
  the Uploads page already polls and could show a real bar instead of a pulse.
- **Trash management**: list restorable tests server-side (restore currently only works for
  names deleted in this browser session's chips).

**Infrastructure**
- **CI** (4.1) + codify the headless-Edge checks as a Playwright smoke suite — the
  hard-won gotchas in CLAUDE.md are one refactor away from being lost.
- **WebSocket/SSE status channel** to replace the 2 s poll (kills 2.2's poll pressure too).
- **Chunked/resumable uploads** for multi-GB files over flaky links (the raw-body endpoint
  discards on truncation, which is safe but restarts from zero).
- **Parallel multi-file upload**: `handleUploadFiles` awaits sequentially; two files upload
  one at a time today.
- **ETag/If-None-Match on `/api/tests`** so the 2 s poll costs ~nothing when idle.
