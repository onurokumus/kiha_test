# Phase 6b — single time-plot CSV and PNG export

Subsequent [Phase 6g](ANALYSIS_METADATA_VERIFICATION.md) adds default metadata ZIPs,
and [Phase 6h](EXPORT_PROGRESS_VERIFICATION.md) adds progress and cooperative
cancellation. Those reports supersede delivery/cancellation limitations below;
the numerical contract and recorded milestone evidence remain applicable.

Completed 2026-09-10 on `feature/resumable-multipart-upload`, baseline `873c80c`.
Earlier user and Phase 1–6a changes remain uncommitted and preserved.

## User-visible behavior

Each Test points and Full test plot now has **Export**, opening a native modal
outside grid clipping. CSV offers **Original**, **Filtered**, or **Original and
filtered**. PNG independently captures the currently visible plot. Keyboard
entry, radio selection, Escape, focus restoration and desktop resizing work.
Pending/error/partial results disable the affected choices with an explanation;
original CSV can still be exported while its filter is computing. Preparation,
download handoff and errors are distinct states, and a failed download can be
retried in the same dialog. Closing aborts browser delivery and suppresses a
late PNG download; it does not claim to cancel an active server calculation.

CSV includes only the plotted signal and identifiers needed to interpret its
rows: `source_test`, `test_point_id`, `sample_index`, `time_s`, `tp_time_s`, then
`<column> [original]` and/or `<column> [filtered]`. Full-test TP fields are blank.
Source/time values retain Arrow precision; filtered values come from full-rate
float64 processing before reduction or six-decimal plot serialization. Original
nulls are blank and original NaN/infinities retain their source representation;
computed nonfinite filtered values are blank. No variable resampling or unit
conversion is introduced. Existing Uploads/Split/selected-TP CSV links remain
unchanged.

Unzoomed CSV exports contain **complete source rows**: complete saved visible
TPs, or the complete Full test request. Reduced/rounded plot endpoints can end
at an earlier bucket timestamp; treating those as an exact default CSV boundary
would silently lose final samples. After explicit X zoom/pan, CSV keeps inclusive
actual sample centers inside the current plotted X range, with only float64
arithmetic tolerance. TP X uses seconds relative to its actual first sample;
Full test X uses stored seconds. Y zoom never removes rows. A local gesture
preview is detected even during the brief delay before React commits its range.

Hidden/ineligible TPs are excluded. Native legend hiding excludes a TP from CSV
when all its displayed series are hidden; for remaining TPs the explicit CSV
data choice applies. In Full test mode selected-TP shading does not restrict CSV
rows. Filenames identify source/multiple sources, variable, mode and data choice.

## Processing and consistency

`POST /api/plot-export` receives the captured source/settings/scope. The extracted
`dsp.filtered_samples` is shared by the plot and export paths, so CSV never
reconstructs samples from reduced arrays or uses a second filter implementation.
TPs filter the complete authoritative saved half-open row interval before X
cropping. Full test preserves the successful request's `t0/t1`, pixel budget and
display mode, including envelope bucket shoulders; export then removes shoulders
outside requested source rows and applies any explicit X crop. Resizing alone
does not replace that captured processing budget. Details and primary method
references are in [FILTER_METHOD.md](FILTER_METHOD.md).

Expected row bounds reject changed saved intervals instead of silently exporting
another range. Original TP bounds come from available full-resolution summaries
or saved indices; filtered bounds come from the successful filter response.
Legacy definitions without those fields still use the shared backend resolver.
Within each source test, its group resolves/processes/serializes under the existing
read lock and native-read slot. Different test groups are sequential snapshots,
not a global atomic revision. Same-shape sample edits since plotting are not yet
detected by the bounds guard.

All sources must finish before attachment headers are sent. The backend stages
CSV in a spooled temporary file (memory up to 2 MiB, then temporary disk), then
streams the completed file without dataset locks. Preparation failure, success,
body cancellation and failed transport before headers all close the temporary
file. The browser waits for the complete Blob before offering the download.
There are no source data or metadata writes. Limits are **128 sources and
8,000,000 aggregate requested rows before X cropping**.

## PNG artifact

The helper copies the actual uPlot canvas, including axes, traces, min/max bands
and Full test TP shading, onto an opaque canvas at its native pixel density.
It adds a wrapped title, source/TP IDs, actual filter settings, current X/Y bounds,
processing warnings and a complete styled legend. Cursor/selection overlays,
buttons and transient hover values are not part of the image. It never changes
the live plot, refetches data or stretches a compact chart to fake resolution.

Hidden series are omitted from the legend. File labels and filter information
describe actual visible trace kinds; TP repair counts/warnings include only
visible filtered TPs and explicitly cover complete TPs, even after X zoom.
Full-test repair counts similarly describe the complete requested window.
Metadata and pixels are captured synchronously before asynchronous PNG encoding,
so subsequent view changes cannot mix contexts. Labels wrap without ellipsis.
Limits are 8,192 pixels per edge, 16 million pixels, and 64,000 metadata characters;
oversize images fail with a clear message rather than dropping context. Download
URLs are revoked and temporary canvas memory released. Filenames remain bounded
while retaining `.png`.

## Verification

| Command/check | Result |
| --- | --- |
| `backend\.venv\Scripts\python.exe -m pytest backend\tests -q` | **282 passed, 162 subcases** |
| `npm.cmd run build` in `frontend` | Passed |
| `npm.cmd run lint` in `frontend` | Passed |
| `python -X utf8 -u scripts\verify_plot_exports.py` | Passed: 19 actual CSV and 9 PNG downloads |
| `python -X utf8 -u scripts\verify_plot_export_ui.py` | Passed: centered modal, focus, two additional PNGs with hidden legend series |
| Existing `verify_time_y_zoom.py` and `verify_rendering.py` with `--url http://127.0.0.1:3110` | Both passed against owned isolated servers |
| `git diff --check` | Passed |

The 22 new backend tests / 34 subcases cover all seven filter kinds, full-precision
and large integer values, a 140,006-row source spanning Arrow/Parquet batch boundaries,
exact saved/legacy/open bounds, cross-test origins/sample rates/IDs, actual X
cropping, full-test envelope context, gaps/nulls/infinities, column collisions,
invalid/busy/missing/changed sources, aggregate limits, read-only hashes/mtimes,
late-source failure and temporary-file cleanup on success/failure/cancellation.
Existing detector/reduction tests remain green after the DSP extraction.

The browser harness uses real isolated uploads and API responses. Its numerical
oracle runs only in the project's Python 3.13 backend environment and compares
downloaded CSV against full-rate source/DSP arrays. Known spike repairs, tiny
values, missing cells, exact row counts/identities and excluded variables receive
additional assertions. A captured request replay checks browser routing/encoding.
Final counts include:

- Unzoomed cross-test TP original/filtered/both: **7,700 rows each**.
- Explicit TP X crop: **5,750 rows**; Y zoom retains all source rows.
- Unzoomed Full Line and envelope: **9,000 rows each**; zoomed Full test:
  **7,632 rows** for X `[0.672, 8.304]` on this fixture.
- PNG magic/dimensions, native browser decoding, opaque/nonempty pixels and trace
  colors, followed by visual review of actual downloaded PNGs.
- Injected CSV failure produces no file, then Retry works; pending filtered
  choices are disabled while original remains available; holding PNG encoding
  and closing the dialog produces no late download.
- Independent plot filters, hidden/ineligible TPs, keyboard/Escape/focus,
  maximize/restore, 1100px desktop width, real 125%/150% browser zoom and narrow
  three-column nine-slot mode. The final focused test verifies the centered
  dialog and PNG scope/counts/filenames when native legend series are hidden.
- **21 original/stored/pyramid/metadata/TP files unchanged**, zero unexpected
  browser errors or dataset mutations in the completed fixture runs.

Existing Y-axis and rendering suites cover precise/gesture/session ranges,
repeated resize/maximize/restore, crosshair alignment, scatter redraws and tooltip
stacking/keyboard behavior. Their existing checks passed without regression.

Evidence is ignored under `data/verification/plot-exports/`; regression server
logs are under `data/verification/plot-export-regressions/`. Browser harnesses
use temporary data/profiles and own only their child servers. Main export ports
3130/8130, focused UI ports 3140/8140, and regression ports 3110/8110 are released
after completion. No user datasets or browser profiles are used.

## Known limits and next milestone

- An initial cold parallel fixture-statistics load encountered a Windows
  `PermissionError` reading `tp_stats.json` through `store._read_json` while its
  cache was replaced. `store.py` is unchanged by Phase 6b. The export harness
  warms fixture statistics sequentially to isolate export behavior; the existing
  cold-cache race remains a separate follow-up. It is not counted as an export
  success or silently dismissed as an injected error.
- Server DSP/staging has no progress or cooperative cancellation yet. Closing
  suppresses browser delivery, but an active SciPy call may continue. The browser
  buffers the CSV Blob, and maximum-size performance was not tested.
- Bounds guards do not establish immutable source revisions. CSV reads current
  stored data; PNG snapshots the loaded plotted data. Sidecar provenance/revision
  handling remains later Phase 6 work.
- PNG retains the displayed reduction and precision. It is an image of the
  current view; full-resolution numerical data is supplied by CSV.
- Chromium desktop is verified. Firefox/Safari and maximum-size data/images were
  not tested. Existing dependency deprecations, bundle-size and Windows Git
  permission/line-ending warnings remain non-failing.

Next: **Phase 6c — selected multi-plot time-series export, including 2×2 and 3×3**,
reusing these source/processing/image paths. Keep metadata sidecars and long-export
progress/cancellation as explicit subsequent work. Resolve the Phase 2 spectral
interval/reduction/units/metadata prerequisites before Spectrum export. The
combined single/multi-plot TODO item remains open until its full scope is complete.

Phase 6c implementation and its subsequent verification are recorded in
[MULTI_PLOT_EXPORT_VERIFICATION.md](MULTI_PLOT_EXPORT_VERIFICATION.md). This 6b
report retains the original single-plot acceptance results and shared contract.
