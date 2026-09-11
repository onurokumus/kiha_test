# Phase 6a — original/filtered time-plot overlay

Completed 2026-09-10 on `feature/resumable-multipart-upload` (baseline `873c80c`).
The earlier Phase 1–5 and user changes remain in the working tree.

## Behavior and scientific scope

Test-point and Full test plots now expose an **Original** toggle alongside an
active valid filter. Filtered-only remains the default, including for older
saved sessions. Enabling it draws a thin, subdued dashed original trace under
the solid filtered trace. TP colors remain associated with the same selected
point; expanded legends explicitly identify original and filtered series.
Envelope min/max bands remain separate for each data source.

`plotShowOriginal` is a separate nine-slot session field, shared between TP and
Full test presentations. It survives reload, layout changes and maximize/restore.
Changing this display setting does not rebuild filter settings or trigger a DSP
request. Filter controls still determine the processing; clearing the filter
returns to ordinary original traces. Header state describes the traces actually
displayed, including original fallback or filtered data while originals load.
Partial failures retain available traces and explain the missing results with
Retry. Obsolete responses cannot overwrite a newer filter result.

The audit found a necessary alignment correction: TP originals used saved
half-open row indices, while filters used inclusive display-time bounds and
subtracted the saved display start. `/filter?tp_id=...` now resolves the same
authoritative `[start_idx,end_idx)` rows and actual first-sample origin as the
original TP endpoint. Filtering and envelope reduction stay inside those rows.
The response includes `tp_id`, `i0/i1`, unrounded `time_origin_s` and `relative_t`
computed before six-decimal serialization. Combining TP and time-window scope
is rejected. Legacy time-only and open-ended definitions use the shared bounds
resolver. Full-test window semantics remain compatible.

TP originals and filtered results have independent time arrays and reduction
budgets; uPlot receives separate facets rather than pairing reduced samples by
position. Full-test responses must match representation, bounds and exact time
arrays, with valid parallel signal lengths. Incompatible completed responses
produce an explicit error and original fallback instead of silently disappearing.

Filtering remains temporary and read-only. **Original** means current stored
samples, including prior edits and derived variables. TP statistics remain
explicitly original; scatter, Spectrum and existing CSV exports do not inherit
time-plot filters. TP filters process the complete selected TP regardless of
client-side zoom. Full-test filters process the viewed window and may run again
after zoom/pan. Expanded controls describe that scope.

[FILTER_METHOD.md](FILTER_METHOD.md) records the actual residual-MAD detector,
parameters/defaults, units, libraries, all seven filter kinds, missing-sample and
gap handling, processing edges, and reduction behavior with primary references.

## Verification

Commands are from the repository root unless noted otherwise:

| Check | Result |
| --- | --- |
| `backend\.venv\Scripts\python.exe -m pytest backend\tests -q` | **260 passed, 128 subcases** |
| Focused overlay/despike/gap/window-budget suite | **34 passed, 24 subcases** |
| `npm.cmd run build` in `frontend` | Passed after final UI labels |
| `npm.cmd run lint` in `frontend` | Passed after final UI labels |
| `python -X utf8 -u scripts\verify_filter_overlay.py` | Passed with real isolated backend and Chromium |
| `python -X utf8 -u scripts\verify_time_y_zoom.py --url http://127.0.0.1:3110` | Passed against owned isolated regression servers |
| `python -X utf8 -u scripts\verify_rendering.py --url http://127.0.0.1:3110` | Passed against the same isolated regression servers |
| `git diff --check` | Passed |

The 11 new backend tests / 20 subcases cover authoritative saved indices despite
inconsistent display times; exact and nonzero/sub-microsecond origins; raw,
local/automatic envelopes and bounded strided lines; adjacent TP exclusion from
processing; legacy/open points; acquisition gaps and too-short segments;
null/NaN/infinite restoration; invalid/missing/combined scope; and byte/mtime
preservation across all seven filter kinds. Existing detector and full-test
window tests remain green.

The new browser harness uploads an isolated 9,000-row fixture through the real
resumable API, saves deliberately mismatched display times and row indices, and
uses real original/filter responses. Browser-side uPlot inspection is injected
only by the test harness; production code has no debug hook. Checks include:

- Actual TP raw/envelope data arrays, saved scope/origin, independent facets,
  distinct trace styles and explicit original statistics labels.
- Legacy defaults, independent slot choices, no display-toggle filter requests,
  hide/reveal and persistence through reload.
- Partial/all failures with honest original fallback and Retry; delayed obsolete
  responses; delayed originals with accurate loading/filtered-data labels;
  Full test failure/retry and rejection/recovery of an incompatible time axis.
- Full test Line and min/max responses, two/four plotted series, separate bands,
  and matching arrays after zoom and pan.
- TP Y and linked X gestures, reset, desktop resize, keyboard toggle and
  maximize/restore, and actual 125%/150% Chromium zoom through `chrome.tabs.setZoom`.
  Nine-slot layout mode was checked at 150% with four configured variables in
  three columns, verifying narrower header wrapping and contained controls.
- Unchanged uploaded/stored data, metadata, pyramid and TP hashes; no browser
  mutations after setup; no unexpected console/page errors. Injected HTTP
  failures are expected.

The existing Y-axis suite also verifies precise/keyboard bounds, malformed and
legacy sessions, missing/constant traces, pending gestures, variable/selection
range context, independent slots, layout/reload and browser zoom. Rendering
regressions cover repeated resize/maximize/restore, crosshair alignment, reduced
motion, scatter error bars, long tooltips and keyboard dismissal. The old Y-axis
fixture was updated to mirror the new TP filter response contract.

Screenshots of grid and expanded TP/Full test overlays at desktop sizes and
125%/150% zoom were visually reviewed. Harness evidence is ignored under
`data/verification/filter-overlay/`; older regression artifacts remain in their
respective verification directories. The live overlay harness checks ports
3120/8120 are free, starts only its own hidden servers, uses Python 3.13 for the
backend, creates a temporary data directory/browser profile and stops its owned
processes. It does not use or alter user datasets. Regression servers used the
same isolation helper at 3110/8110.

The final live run made 53 filter requests (the count varies as peer plots
remount), recorded zero browser writes/unexpected errors and preserved all seven
hashed source/stored/metadata files. Both overlay-server ports were free after
cleanup. Final grid captures wait for every configured plot to settle.

## Limits and next boundary

- This is a display feature, not persistent despiking or an export pipeline.
  Original/filtered full-resolution CSV and image export, metadata sidecars,
  multi-plot layouts, and progress/cancellation remain subsequent Phase 6 work.
- The existing six-decimal plot serialization, float64 limits, independent
  reductions and possible hidden within-bucket missing samples remain. Do not
  use reduced plot responses for numerical subtraction or full-resolution export.
- The residual-MAD method is not textbook common-window MAD. It cannot establish
  which real rig transients are noise; missing-value interpolation can affect
  neighboring outputs. Known gaps remain hard processing boundaries.
- Full-test viewport/bucket context can change edge results. The existing
  `boundary_warning` only flags dataset ends, not every artificial selection
  edge. The 8,000,000-row limit and maximum median window were not stress-tested;
  full-rate filtering loads its processing slice in memory.
- Chromium desktop is verified; Firefox/Safari and maximum-size datasets were
  not tested. Existing dependency deprecations, bundle-size and Windows Git
  permission/line-ending warnings remain non-failing.

Next: **Phase 6b, single-plot CSV/image export** using the actual plotted variables,
scope and selected original/filtered settings. Build full-resolution export
processing and provenance deliberately; resolve the Phase 2 spectral prerequisites
before including spectral exports. Extend to multi-plot layouts and long-export
progress/cancellation at later coherent boundaries.
