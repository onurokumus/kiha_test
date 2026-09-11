# XY exports — Phase 6f

Subsequent [Phase 6g](ANALYSIS_METADATA_VERIFICATION.md) adds default metadata ZIPs,
and [Phase 6h](EXPORT_PROGRESS_VERIFICATION.md) adds progress and cooperative
cancellation. Those reports supersede delivery/cancellation limitations below;
the numerical contract and recorded milestone evidence remain applicable.

Completed 2026-09-10. **339 backend tests /234 subcases**, frontend build/lint,
real XY downloads and existing Spectrum/time-export browser regressions pass.

## Scope and numerical contract

XY has per-plot **Export** and **Export selected plots** above the grid. Single
plots download CSV or PNG; selected slots produce separate CSVs in a ZIP or a
combined 2x2/3x3 PNG. Slot order/identity, duplicate variables and shared layout
limits follow the [time-export contract](MULTI_PLOT_EXPORT_VERIFICATION.md).
Restore a maximized plot to choose multiple slots.

XY uses current stored variables, including saved edits/equations. Temporary
time-plot filters do not apply. CSV contains only the chosen X and Y variables
plus source, time, sample and method identifiers. There is no sorting,
interpolation, resampling, spectral estimation or joining of independent tests.
Both coordinates retain their stored units. A row is kept only when **both X
and Y are finite**; missing time is blank unless time itself is a chosen axis.
This follows [NumPy's finite-value definition](https://numpy.org/doc/stable/reference/generated/numpy.isfinite.html).
CSV serializes the original Arrow coordinate columns, with no six-decimal
rounding, using [Arrow's CSV writer](https://arrow.apache.org/docs/python/generated/pyarrow.csv.write_csv.html).

`POST /api/xy-export` accepts:

```json
{
  "kind": "xy",
  "column": "load_N",
  "x_column": "position_mm",
  "method_version": "kiha-xy-v2",
  "sources": [{
    "test": "example",
    "tp_id": 7,
    "expected_i0": 100,
    "expected_i1": 10010,
    "expected_time_column": "time"
  }],
  "x_range": null,
  "y_range": null
}
```

The shared `column` field is Y. For Full source, omit `tp_id` and provide the
loaded `t0`/`t1`; null means the whole test. A Full request has one source.
Saved TPs resolve authoritative half-open rows, including legacy/open-ended
definitions. Full retains the application's nominal-time-to-row convention.
Required expected bounds/time-column and method version guard stale context.
TP identity must be confirmed by the display response; missing optional legacy
method/bounds metadata permits display but disables CSV with a reload message.

`POST /api/xy-export/bundle` takes `{layout, plots: [{slot, request}]}`. Each
request is a single-plot payload. Layout is `2x2` or `3x3`; slots are unique,
ascending, one-based values in 1–9 and fit the chosen layout capacity.

CSV reads complete original pairs from Parquet in source row order, independently
for each source. Default/reset axes mean **all native finite pairs**, including
extrema omitted by display stride. A changed axis range crops its coordinate
inclusively; the other axis can remain unrestricted. Explicit X/Y zoom or pan
therefore exports pairs in the current rectangle. Bounds use only float64
arithmetic tolerance scaled to their magnitude; no absolute tolerance floor
that would swallow small-unit ranges. Cropping never changes the TP/Full time
scope. Box zoom, wheel and Shift/middle-drag operate on both XY axes; double-click
resets. The XY wheel guard now accepts micro/nanounit spans.

Rows include source test, saved TP ID (blank for Full), source i0/i1/fs,
time-column identity, method version, `source=stored`, `prefilter=none`,
`missing_values=omit_nonfinite_pairs`, optional X/Y crop bounds, absolute
`sample_index`, actual `time_s`, `<X column> [X]` and `<Y column> [Y]`.
If X=Y there is one coordinate column named `<column> [X/Y]`. Unequal source
sample rates, time origins, TP lengths and missing values remain independent.
Sources with zero kept pairs contribute no rows; a wholly empty plot fails.

## Display, images and failure handling

`GET /api/tests/{test}/xy` now accepts exact `tp_id` and a single `y_col` that
preserves comma/Unicode-containing names. Legacy comma-separated `y` lists
remain supported; supplying both forms is rejected. Display method
`kiha-xy-v2` returns exact source bounds/time/fs, unrounded coordinates and
sample indices, and complete finite/omitted pair counts. Stride is relative
to source i0, then nonfinite pairs are removed. If stride misses every finite
pair, the first finite pair is retained and explicitly flagged. Display still
uses stride reduction; it does not promise to preserve native extrema.

CSV never reconstructs data from display JSON. PNG copies the native uPlot
canvas with current axes/ranges, visible legend and colors. Wrapped annotations
record source rows/actual boundary times/fs, finite/missing counts, display
stride/fallback and method. Both axes identify their variable. An image
represents the current reduced display; it is not a full-resolution data export.

Hidden application TPs and legend sources are excluded. All-hidden legends
report an error. Loading, partial or failed requests block export; hiding a
legend cannot conceal a failed request. A changed Full time interval invalidates
the previous plot immediately, and late responses cannot overwrite a new
source context. Edit-variable controls occupy their own header row so Export
remains reachable.

Time, Spectrum and XY reuse mounted export registration, single/multi builders,
native PNG composition, spooled CSV/ZIP staging, read locks, native read slots,
row budgets and cleanup. Resolution validates both XY columns before native
reads and again under the source lock. Parquet iterators close explicitly even
when a consumer exits or serialization fails. A failing source/slot produces
JSON without a partial attachment; Retry rebuilds the request. Closing a dialog
or changing context aborts the browser request and suppresses late delivery.

Limits remain 128 source references and 8M requested source rows per request
or bundle, before crop/missing-pair removal, including repeated plot slots.
Spools roll to disk above 2 MiB. PNG limits remain 8192px per edge, 16M output
pixels, 16M aggregate input capture pixels and 64K annotation characters/card.
Limits fail explicitly; no silent provenance truncation or downscaling.

## Verification

Run from the repository root:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests
python -X utf8 -u scripts/verify_xy_exports.py
python -X utf8 -u scripts/verify_spectrum_exports.py --frontend-port 3160 --backend-port 8160
python -X utf8 -u scripts/verify_multi_plot_exports.py --frontend-port 3170 --backend-port 8170
python -X utf8 -u scripts/verify_plot_exports.py --frontend-port 3160 --backend-port 8160
# frontend directory:
npm.cmd run build
npm.cmd run lint
```

Seventeen new backend tests /27 subcases cover exact saved and legacy TP rows,
full precision, unsorted source order, complete native counts, stride fallback,
all-missing sources, X=Y, time as either axis, exact comma/Unicode column names,
both-axis and tiny-unit crops, Full bounds, unequal tests/rates/large TP IDs,
stale/busy/invalid contexts, aggregate/actual budgets, per-slot ZIP requests,
late empty-slot failure and temporary/native file cleanup. CSV values are
checked against independent source arrays, not display responses.

The isolated browser harness uploads two tests, warms statistics serially,
uses a separate profile, and downloads real CSVs/ZIPs/PNGs. Its independent
native oracle reads Parquet using backend Python 3.13 and filters original
rows; the global Python runtime handles only stdlib/Playwright. It compares
every downloaded coordinate, index/time and identity against that oracle.
Actual 125%/150% browser zoom uses the existing extension harness. Native image
pixels and captured annotation text/layout are checked. Evidence lives under
ignored `data/verification/xy-exports/`. The first attempt needed a harness
correction to expand the existing selection tray before hiding a TP; another
attempt needed to wait for Retry to enable CSV before sending its keyboard
activation. The complete final run passes without production workarounds.

Final evidence:

- **13 single CSVs +10 ZIPs (29 entries) =42 CSV entries**, all compared against
  **14 independent native reference cases**. Every pair, row/time/source/TP
  identity, default/native-extrema inclusion and crop matches the oracle.
- **18 real PNGs**: single/selected 2x2/3x3/nine slots, independent crops,
  hidden legends, Full/TP and maximized desktop views. Native pixels, wrapped
  annotations, selected slot positions and blank cells checked; representative
  actual single/combined/maximized PNGs and the 150% dialog visually reviewed.
- Exact saved rows despite different nominal labels, source grids/rates,
  nonfinite-pair omission, X=Y, tiny precision, comma/Unicode column names,
  existing temporary time-filter exclusion, box zoom, wheel, Shift-pan/reset
  and wheel zoom below 1e-9 all pass.
- Application TP visibility, hidden/all-hidden legends, per-slot crops,
  maximize/restore guard, duplicate slots and all nine selected plots pass.
- Real late-slot empty-crop failure returns JSON/no attachment; Retry succeeds.
  Closing a held ZIP suppresses delivery. Partial source failure blocks both
  formats; source Retry succeeds. Legacy metadata disables CSV; missing TP
  identity rejects the plot. A held old Full response cannot replace new TP data.
- Real Full time-plot zoom supplies the exact loaded Full XY interval. Resetting
  that interval while XY stays mounted removes stale data and disables export.
- 1100px desktop resizing, actual 125%/150% browser zoom, keyboard/Escape/focus,
  edit-variable and export access, both TP and Full source all pass. All **14
  fixture files unchanged**; no unexpected browser errors or writes.
- Existing Spectrum regression: **36 CSV entries /13 PNGs /20 native cases**,
  all 20 source files unchanged. Existing time regressions: **19 CSVs /9 PNGs**
  (single), **13 ZIPs /61 entries /6 PNGs** (multi), all 21 fixture files unchanged.
  Original/filtered/both, native DSP/crop settings, failures, Close and desktop
  interactions remain compatible.
- Owned ports 3150/8150, 3160/8160 and 3170/8170 are released; temporary fixtures
  and browser profiles removed. No dependency changes. Build size/deprecation
  and Git CRLF/permission warnings remain non-failing.

## Remaining limits and next milestone

CSV reads current stored values; PNG captures the loaded display. Expected
bounds/time-column do not detect same-shape value edits. There is no immutable
dataset revision or global cross-test/cross-slot snapshot. Metadata columns
are not the complete provenance sidecar, especially equation/edit history.
Long exports do not yet report staged progress or cooperatively cancel native
work. Maximum-size throughput/memory/disk capacity is unmeasured; Chromium
desktop is covered, Firefox/Safari are untested. The existing Windows cold
statistics-cache replacement race remains; fixtures warm the cache serially.

Next: Phase 6g analysis provenance metadata sidecars generated from actual
export settings/results, followed by Phase 6h progress and cancellation.
