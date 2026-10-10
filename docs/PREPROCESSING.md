# Reversible flight preprocessing

Uploads > **Pre-process** updates a flight under its existing name. There is one
flight in the library. Its current samples are used by Analyze, Split, plots and
CSV exports; its original samples are retained internally while filters are
active. The configured datasheet has no Pre-process action.

Every update starts from the retained original, never from an already-filtered
result. Reopen Pre-process to edit the saved recipe. Choose filters and click
**Apply preprocessing**. To return to the original, use **Clear all filters** and
**Restore original data**. Removing one parameter's filter restores that
parameter's original values while the remaining filters stay applied.

The original means the stored recording immediately before the first
preprocessing update, including any earlier trims, fills and materialized
formula columns. It is not necessarily identical to the uploaded CSV. A
successful full restore removes the internal backup; a later preprocessing
operation captures the then-current stored recording as its original.

## Selecting parameters

Check individual parameter rows, or use **Select all** for every signal
parameter. Time is always locked. **Select visible** adds the parameters shown
by search or Configured only. Hidden selections remain checked and are counted.

The group editor has its own draft. **Apply filter to selected** copies that
filter to each checked parameter. Choose None and **Remove filters from
selected** to clear only the group. These actions prepare the recipe; the footer
applies the complete recipe to the flight. Unapplied group changes must be
applied or discarded before saving. A parameter name opens its individual
settings; the selected-count badge returns to the group editor.

No output name or second flight is created. There is no original-data download
control while preprocessing is active. Existing unprocessed rows retain their
ordinary download behavior; a processed/restored flight's CSV exports its
current stored samples.

## Comparing the saved result with the original

For a flight with saved preprocessing, choose **Compare** in Uploads or open
Pre-process and choose **Compare data**. The retained original stays inside the
same flight. This view reads both versions without changing either one.

Choose a parameter and use **Both**, **Original** or **Filtered** to control
the visible traces. A subdued dashed original and a stronger filtered trace
share time and value axes. The filter summary describes the saved recipe for
that parameter; parameters without a saved filter use their original values.

Zoom to inspect a time interval, pan across the recording, or reset to the full
range. Expand the comparison for more plotting space. PNG exports capture the
displayed comparison. Wide windows show paired min/max envelopes; zooming in
loads native samples and enables exact paired differences. Envelope extrema
are never subtracted to imply a sample-level difference.

Drag across the plot to zoom, Shift-drag to pan and double-click to reset.
The visible + / − and Reset zoom buttons provide the same controls. With the
plot focused, use + / −, the arrow keys and Home. Both themes support expanded
views and desktop browser zoom; short windows keep the plot accessible through
the dialog's scrollable body while its header and footer stay visible.

Raw cursor differences use Filtered minus Original. RMS difference and maximum
absolute difference use every finite aligned pair in the loaded native window,
before display formatting. Missing/nonfinite pairs and known acquisition gaps
are excluded. Broad views reuse stored float32 min/max pyramids and reread
partial edge buckets from native data. Buckets overlapping known acquisition
gaps are hidden; comparison details explain the reduction. No additional rounding
is applied to native sample values or timestamps.

Comparison uses the last **saved** preprocessing result. Switching between
Filters and Compare data preserves unapplied filter drafts and shows a notice
when drafts differ from the saved result. Temporary Analyze plot filters are
not applied in this view. Clear all preprocessing filters and apply to restore
the original as the flight's active data; comparison is then no longer needed.

The comparison endpoint requires the active source identity and revision from
the preprocessing snapshot. Both versions are read under one test read lock.
Stale or unavailable sources produce an explicit error instead of mixing data
from different revisions. Legacy version-1 named copies have no direct original comparison
because this workflow cannot assume their earlier source is still unchanged.

## Data and analysis behavior

Name, durable source UUID, time values, sample count, saved test-point definitions,
notes, component assignments and annotations stay with the same flight. Names,
notes, points and assignments can still be edited. Rename and trash/restore move
the internal original with the flight.

Restore original data before changing columns, trimming, filling missing samples
or applying equations in Edit. Both the UI and backend guard these operations
while preprocessing is active so a later restore cannot silently erase such
changes. Formula preview and ordinary metadata edits remain available.

Stored formula outputs are filtered only when selected. They are not recomputed
from newly filtered dependencies. Physical component usage uses the original
recording and current component assignments, counting the flight once; filters
on RPM, temperature or power therefore do not change physical-use statistics.

**Plot filters remain additional, temporary operations.** They use the current
stored samples, with their existing plot/TP scope. A plot's Original trace means
before that plot filter; it does not expose the hidden preprocessing baseline.
Saved preprocessing provenance is included in analysis export metadata.

The app refreshes data and statistics after same-name updates, including when
the dialog was closed during processing or an inactive flight was updated.
Selected test points and their colors remain selected while traces reload.

## Methods and missing samples

Existing operators in `backend/app/dsp.py` process complete native columns before
plot reduction. Butterworth low/high/band-pass/band-stop uses forward/backward
second-order sections with SciPy's odd endpoint padding. Cutoffs must be strictly
below half the sample rate. Moving average uses a rounded native-sample window
with nearest endpoint extension. Detrend removes a linear least-squares trend.
Despike uses the existing local-median/MAD detector with linear or median
replacement. See [the filter method](FILTER_METHOD.md) for units and interpretation.

Operator contracts were checked against the official
[sosfiltfilt documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.sosfiltfilt.html),
[uniform_filter1d documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.uniform_filter1d.html),
and [detrend documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.detrend.html).

Known acquisition gaps divide filtering into independent continuous regions.
Retained acquisition history still applies after earlier Edit fills. Values
inside those gaps remain as stored. Sparse missing/nonfinite samples outside
these gaps are interpolated only for calculation, then their original missing
representation is restored; Arrow nulls stay null. Unfilterable short regions
fail the update instead of publishing partly filtered data. Endpoint effects
remain inherent to the selected filter. Legacy missing gap history is reported.

The existing 8,000,000-sample per-column filter ceiling remains. Clearing filters
can restore the original even when the current filter ceiling would prevent a
new filtering job. Large despike windows may be expensive.

## Storage, API and recovery

`GET /api/tests/{name}/preprocess` returns the current source identity/revision,
original scientific metadata, saved version-2 recipe, latest operation and sample
ceiling. Source snapshots do not require saved test-point intervals to be valid;
whole-record processing preserves those definitions without resolving them.
Session recovery still validates test-point intervals separately.

`POST` accepts `{request_id, source_id, source_revision, filters}`. The output name
has been removed. Filters are `{column, filter}` records using snake_case filter
fields. An empty list restores original data. Stale identity/revision, duplicate
parameters, time-column filters and invalid settings are rejected. A UUID request
ID makes retries idempotent, including a lost response or completed restore;
reusing that ID with different settings is rejected.

The existing flight becomes rebuilding and publishes an operation with state
running, completed or failed. Filtering stages one full column at a time and
merges in bounded batches. `.preprocess-original` stores original sample data,
pyramid and scientific metadata inside the flight directory; it is never listed
as a flight. `.preprocess-work` contains the staged result and rollback journal.

Only a completed result replaces active data/pyramid/metadata. Per-test locking
prevents readers seeing partially published files. The journal retains the
previous active artifacts until ready is committed. Worker errors and restart
recovery restore the previous active version. A failed update returns the flight
to ready with an explicit operation error so it remains usable. If rollback
itself fails, recovery artifacts are preserved and the flight requires server
attention. Disk preflight estimates staging/backup needs; later I/O errors use
this same rollback path. Closing the dialog does not cancel an accepted job.

Legacy version-1 filtered copies are not automatically merged, renamed or deleted.
They remain independent existing flights. Their own stored samples become the
baseline if the new workflow is used on them; removing the new filters restores
that baseline and its legacy provenance. These legacy copies remain excluded
from duplicated physical component usage.

Expected missing/damaged identity, metadata or source-file permission problems
return actionable conflicts and log details. Identities are never silently
replaced. Production errors can be inspected with:

```bash
sudo journalctl -u ptt-backend --since "15 minutes ago" --no-pager -n 150
```

## Verification

Comparison follow-up completed on 2026-10-10:

- Full backend suite: 563 tests and 534 subtests pass, including 15 comparison
  tests covering independent numerical pairs/statistics, tiny-value/time
  precision, gaps, cropped bucket edges, large windows, read-only hashes,
  stale identity/revision and missing/linked/corrupt source guards.
- All 140 frontend helpers (five new comparison tests), production build and
  lint pass. Independent backend/frontend reviews pass.
- Ten native groups in `scripts/verify_preprocess_comparison.py` pass on
  `index-BckEXm4y.js` / `index-Drwb0811.css`: direct Uploads/tab access,
  independently computed original/filtered values and cursor differences,
  unchanged parameters, trace toggles, zoom/pan/reset, native PNG, filter draft
  retention, stale/error retry and late response rejection, partial selection
  round trips, 24,000-sample envelopes refining into native windows, and both
  desktop themes at actual 100/125/150% zoom with expand/restore.
- All ten groups in the existing same-flight verifier pass on that final build,
  retaining apply/update/restore, background/failure/retry behavior and selected
  analysis traces. Final screenshots and exported PNG reviewed. All five new
  comparison fixture flight directories remain unchanged, with no extra flight
  or unexpected browser errors/writes. Owned servers, profiles and data cleaned.

Run `python -X utf8 scripts/verify_preprocess_comparison.py` after building the
frontend. Evidence: `%TEMP%\ptt-preprocess-comparison-verification\results.json`,
`native-overlay-light.png`, `plot-dark-1.5-True.png`, `large-envelope-dark.png`
and `comparison.png`. The existing suite's evidence directory is listed below.
Both suites use isolated synthetic data. No production deployment was performed;
deploy frontend and backend together to load the new GET comparison endpoint.

Completed on 2026-10-09:

- Backend: 548 tests and 522 subtests pass, including stale guards, independent
  SciPy parity, gaps/nulls, unchanged source identity and metadata, byte-exact
  restore, publication crash points, repeated rollback recovery, rename and
  trash/restore, destructive-edit guards and physical component totals.
- Frontend: all 135 helper tests (12 preprocessing helpers), production build
  and lint pass. Independent backend transaction/recovery review found no
  actionable issues. Existing Vite bundle-size advisory and Starlette
  dependency deprecation remain.
- `scripts/verify_preprocess_inplace.py`: ten native browser groups pass on
  `index-CWAJpLwb.js` / `index-CVAZx315.css`. Covers exact full CSV low-pass/bulk
  parity, updated filters rebuilt from the original, full restore, same name
  and catalog identity, selected TP trace refresh, inactive-flight updates,
  closing while running, failed-operation reopen/retry, lost response with one
  submission, reload, datasheet exclusion and bulk Select all/visible.
- Both desktop themes pass at actual 100/125/150% zoom, including dense
  1100x780 windows, scrollable content, fixed header/footer, keyboard controls
  and reachable bulk Apply. Screenshots were visually inspected. No unexpected
  page errors or writes; expected injected load/conflict/disconnect failures
  are recorded. Owned servers, profiles and synthetic datasets were cleaned up.

The native browser suite exposed a transient Windows PermissionError while
polling status during atomic replacement. Bounded JSON-read retries now handle
this race; permanent permission errors still surface, with regression coverage.

Reproduction commands (repository root, except build/lint in frontend):

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests
node --test frontend/tests/*.test.mjs
python -X utf8 scripts/verify_preprocess_inplace.py
# In frontend:
npm run build
npm run lint
```

Build the frontend before running the browser verifier. Its global Python
requires Playwright; scientific work uses the project's Python 3.13 environment.
Evidence: `%TEMP%\ptt-preprocess-inplace-verification\results.json` and
`selection-{light,dark}-{1,1.25,1.5}.png` / `editor-*` in the same directory.
No production deployment or user-data migration was performed. Deploy frontend
and restart backend together because the preprocessing POST contract changed.

The earlier `verify_preprocess.py` and `verify_preprocess_bulk.py` document the
superseded named-copy workflow and its historical checks. Use the new in-place
verifier for the current API and UI.
