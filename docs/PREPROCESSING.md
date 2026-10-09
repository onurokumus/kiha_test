# Saved test preprocessing

Uploads > **Pre-process** configures one filter independently for each selected
parameter. Saving creates a separately named test. The source's current stored
samples, uploaded CSV, test points and notes are preserved. The new test has its
own full-resolution samples and plot pyramid; unselected parameters and the time
column are copied unchanged. Test-point boundaries are copied without shifting
or resampling. Existing materialized formula columns are copied or filtered as
selected, rather than recalculated from changed dependencies.

Both tests are available to Analyze, Split, Edit and exports. The Uploads row
identifies filtered copies and opens their saved preprocessing settings. Create
another version from the original source to avoid accidentally applying the
same preprocessing twice. The source reference records its identity and data
revision at creation, including the name at that time.

**Plot filters remain additional, temporary operations.** They process the
stored samples of whichever test is selected, using their existing plot/TP
scope. On a preprocessed test, a plot's “Original” trace means the saved samples
before that plot filter; the original acquisition is in the source test. The
preprocessing record also travels in analysis export metadata.

## Apply one filter to multiple parameters

Check parameter rows to open the shared filter editor, or use **Select all** to
target every signal parameter. The time column remains locked. When search or
**Configured only** limits the list, **Select visible** adds those results to the
selection. Existing selections remain checked, with hidden selections counted
beside the total and explained in the editor.

Choose a filter and its settings, then click **Apply filter to selected**. This
replaces each selected parameter's filter while keeping all other settings.
Choose **None** and **Remove filters from selected** to clear only that group.
Click a parameter name to inspect or edit it individually; click the selected
count to return to the shared editor. **Clear selection** clears the checkboxes;
**Clear all filters** removes the configured recipe.

The shared editor has a separate draft. Save remains disabled until pending
changes are applied or discarded, including when more parameters are selected
after an earlier Apply. **Review bulk settings** returns to an unapplied draft
from individual editing. Applying settings prepares the recipe; **Save filtered
copy** starts the existing whole-recording processing and retains the source.

## Methods and missing samples

Preprocessing uses the existing operators in `backend/app/dsp.py`, on complete
native columns before plot reduction. Butterworth low/high/band-pass/band-stop
uses forward/backward second-order sections with SciPy's odd endpoint padding;
cutoffs must be strictly below half the sample rate. Moving average uses a
rounded native-sample window with nearest endpoint extension. Detrend removes a
linear least-squares trend. Despike uses the existing local-median/MAD detector
and selected linear or median replacement. See [the filter method](FILTER_METHOD.md)
for the detector's units and interpretation.

Operator contracts were checked against the official
[sosfiltfilt documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.sosfiltfilt.html),
[uniform_filter1d documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.uniform_filter1d.html),
and [detrend documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.detrend.html).

Known acquisition gaps divide processing into independent continuous regions.
Retained acquisition history is honored even after Edit filled those intervals;
filled values inside the gaps stay as stored and cannot affect neighboring
observed samples. Legacy missing gap history is reported as a limitation.
Sparse missing/nonfinite samples within a region are temporarily interpolated
for calculation, then their original missing representation is restored. Arrow
nulls stay null. A filter that cannot process a valid region fails the copy
instead of publishing partially filtered data. Input constraints and warnings
are reported with the affected parameter. Recording/region endpoints still
have filter boundary effects.

## Storage and execution

`GET /api/tests/{name}/preprocess` supplies metadata, source identity/revision,
saved recipe if present, and the sample limit. `POST` accepts a new output name,
that identity/revision, and a list of `{column, filter}` records. Filter fields
use the API's snake_case spellings. Duplicate names, time-column filters,
duplicate parameters, stale sources and invalid filter settings are rejected.

The server reserves an independent output, returns HTTP 202, and processes in
the background. Progress is exposed through the existing test catalog. It
filters/stages one complete column at a time, merges columns in bounded batches,
and builds the pyramid before publishing `ready`. A failed copy remains an
error entry that can be removed through Manage; the source is unaffected.
Interrupted jobs are marked failed on server restart. Closing the dialog does
not cancel accepted server work.

The existing 8,000,000-sample filter ceiling also bounds whole-recording
preprocessing. This is a per-column full-resolution computation, not a streaming
DSP algorithm. Large despike windows can be expensive. A disk-space preflight
estimates the independent upload copy, saved samples and temporary staging;
later I/O failures still fail the output safely.

The original CSV download is the **original uploaded file**, which may differ
from the source's current stored data after previous Edit operations. The
filtered CSV download exports the complete saved working samples of the new
test. Subsequent edits to either test remain independent.

Component associations remain available on the copy for context. Component
usage statistics exclude these analysis copies so one recording does not count
twice toward physical runtime.

## Verification

Production build and lint pass. All 133 current frontend helper tests
pass, including ten new preprocessing cases for names/collisions, filter
serialization, native sample validation, duration rounding, source/recipe
matching and guarded request errors. Concurrent plot-appearance work is present
in the shared tree and is outside this milestone.

`scripts/verify_preprocess.py` prepares a disposable 1,200-row, 34-column fixture
with saved test points and independent SciPy reference arrays. Ten Chromium
groups pass: per-test keyboard opening/Escape/focus, loading retry, parameter
search/clear-all/validation, real light/dark 100/125/150% browser zoom and desktop
resizing, rejected POST retry, duplicate-submit protection and status retry,
full native numerical parity, saved recipe/raw and filtered CSV downloads,
injected worker failure/retry, and persistence through reload. Original
sample/time/metadata/pyramid/TP/raw/identity/status file hashes are unchanged;
the ordinary derived TP-statistics cache is excluded from the source hash guard.
No JavaScript page errors or unexpected writes occurred. Expected injected HTTP
failures are recorded separately. Large light and compact dark screenshots were
visually inspected.

Reproduction (PowerShell, from the project root):

```powershell
python -X utf8 scripts/verify_preprocess.py --prepare --data-dir "$env:TEMP\ptt-preprocess-unique"
# Start backend with KIHA_DATA_DIR pointing only at that isolated directory;
# start Vite with VITE_API_BASE pointing at the isolated API.
python -X utf8 scripts/verify_preprocess.py --url http://127.0.0.1:8090/ --api http://127.0.0.1:8019/api --data-dir "$env:TEMP\ptt-preprocess-unique"
backend\.venv\Scripts\python.exe -m pytest backend\tests -q -p no:cacheprovider
# From frontend:
npm run build
npm run lint
node --test tests/*.test.mjs
```

Windows sandbox restrictions required approved native execution for atomic
fixture renames, the browser, and the Vite production build. The initial build
failed on sandbox `realpath` access; the approved build passed. The original
browser harness needed to keep its injected failure active across React's
development StrictMode requests; that verifier issue is fixed. Browser evidence:
`%TEMP%/ptt-preprocess-verification/results.json` and adjacent screenshots/CSVs.
The final native Python 3.13 backend suite passes **531 tests / 511 subtests**,
including all seven filter kinds, row-group seams, exact original/TP/raw
preservation, null/NaN/infinity, acquisition gaps before/after fills, source
deletion independence, stale source/name/parameter/disk/sample guards,
concurrent reservation and worker rechecks, restart/failure recovery, additional
plot DSP, provenance, and exclusion from duplicate component totals. This run
also includes concurrent Split/appearance changes; those are not owned here.
The existing Starlette/httpx deprecation and Vite bundle-size advisory remain.

Three additional read-only browser groups pass (**13 total**): plot moving
average on top of the saved low-pass result matches independent native values
for every sample without modifying the saved recipe or samples; dense Despike
controls and output naming remain reachable by scrolling/keyboard in both
themes at 1050x700 and actual 100/125/150% zoom. Follow-up evidence:
`%TEMP%/ptt-preprocess-verification/followup-results.json` and screenshots.
Three final lifecycle groups pass (**16 browser groups total**) on the final
backend: a real native acquisition gap causes a short-segment worker failure,
which publishes no sample file or broken original download; retry with a valid
window creates an independent copy with exact 1,390-row/gap preservation; a
successful POST with a deliberately lost response is recovered as the matching
output with exactly one submission. Original hashes remain unchanged. Evidence:
`%TEMP%/ptt-preprocess-verification/lifecycle-results.json` and screenshots.

All owned verification servers were stopped; user servers/data were untouched.
Application restart is needed to load the new backend routes in an already
running deployment. No deployment was performed. No implementation
work remains in this milestone; the next independent backlog item is Phase 11b.

### Bulk filter follow-up (2026-10-09)

Production build/lint and all ten preprocessing helper tests pass. The isolated
`scripts/verify_preprocess_bulk.py` suite passes nine Chromium groups against
`index-B-wmAcm6.js`: checkbox/keyboard selection, global and visible selection,
hidden targets, cloned independent settings, selected-only removal, validation,
unapplied-draft guards and discard/review focus, and the exact saved recipe.
Thirty-six signal columns yield exactly 34 serialized filters after two are
cleared, with time excluded. Arbitrary names (`__proto__`, `constructor`,
`toString`) retain correct individual/bulk behavior and missing-sample notices.

Both themes pass at 1440x1000 and compact 1100x780 desktop windows with actual
100/125/150% browser zoom. Dense controls, scrolling, fixed dialog footer and
keyboard focus were exercised; screenshots and independent code review pass.
Every API request is intercepted: the accepted job exists only in memory, no
backend or user recordings are accessed, and the source fixture is unchanged.
No unexpected requests, page errors or console errors occurred. The owned
preview was stopped. No backend/scientific changes or backend test rerun were
needed for this frontend-only follow-up; the existing Vite size advisory remains.

Reproduction after building the frontend (use an owned preview process):

```powershell
# From frontend:
npm run preview -- --host 127.0.0.1 --port 8107 --strictPort
# From the project root, with Python + Playwright available:
python -X utf8 scripts/verify_preprocess_bulk.py --url http://127.0.0.1:8107/ptt/
# From frontend:
node --test tests/preprocessing.test.mjs
```

Evidence: `%TEMP%/ptt-preprocess-bulk-verification/results.json` and the adjacent
light/dark screenshots. The user-requested Git checkpoint includes preprocessing
and bulk selection with their tests and documentation. No deployment; next
independent milestone remains Phase 11b.

### Preprocessing load failure (2026-10-09)

The reported deployed request was `GET /api/tests/s200/preprocess` returning
HTTP 500. A local 15-row, 1Hz reproduction confirmed that saved empty, reversed
or out-of-range test points could break this request: preprocessing reused a
session-recovery snapshot that resolves every saved point interval, then
discarded those point references. Such definitions can arrive through the
existing point save/import API.

Preprocessing now uses a sample-only source snapshot. Its identity, revision
hash and source-change guards remain the same; session recovery still validates
point intervals independently. Full-record filtering preserves the original
point definitions and all source bytes. Expected source identity, metadata and
file-access failures return actionable HTTP 409 details and log the exception.
Damaged identities are never replaced; failed sample verification does not
create a legacy identity file.

Five new regressions cover supported point imports and the complete filtered
copy, numerical parity, source/point preservation, damaged identity on GET and
POST, missing samples, metadata errors and service-account permission failures.
Focused preprocessing/recovery checks pass 31 tests/21 subtests; the full native
Python 3.13 suite passes 537 tests/520 subtests. Build/lint, independent code review
and whitespace checks pass. No frontend or filtering-method changes were needed.
Existing Vite size and Starlette dependency advisories remain.

The private host `heliweb1` cannot be resolved from the development environment.
These are confirmed local causes; the screenshot alone does not identify the
exact production exception. After deploying the updated backend and restarting
`ptt-backend`, retry Pre-process. If it still fails, capture its service traceback:

```bash
sudo journalctl -u ptt-backend --since "15 minutes ago" --no-pager -n 150
```

The follow-up is committed on the existing feature branch; no deployment or
production data changes were performed here.
