# Full-test variable comparison

Completed 2026-09-16 on `feature/resumable-multipart-upload`.

## Usage and behavior

- In Full test, choose **Edit plots**, then use the styled **+** beside a plot's
  variable dropdown. Search and select another variable; at most six variables
  can share a plot. Already selected variables cannot be added again.
- The color/count dropdown shows every selected variable and its trace style.
  In edit mode, remove additional variables there with the adjacent × buttons.
  The primary dropdown still changes the first variable. Outside edit mode,
  the compact title shows source-colored dots and the extra-variable count.
- Colors follow source-column identity across slots and removal/addition.
  Applying the plot filter processes all its variables. With **Show original
  with filtered**, originals use a lighter dashed stroke of the same hue and
  filtered traces use a stronger solid stroke. Each envelope has its own band.
- All signals retain original units on one shared Y axis; there is no
  normalization, resampling, independent Y scaling, or scientific-method change.
  The folded legend and image export explicitly describe the shared scale.
  Expanded plots retain the existing horizontally scrollable live value legend.
- Display resolution appears once as a compact ratio such as `1:16` beside
  the layout buttons in the existing shared Analysis controls row, outside
  every plot header and canvas. Its hover/focus tooltip explains sampled
  lines or grouped extrema. These labels and placement were updated on 2026-09-28;
  the verification results below describe the original 2026-09-16 milestone.
- The nine slots retain independent additional-variable lists in autosave and
  session Save/Open. Old sessions default to no extras. Temporarily unavailable
  variables stay dormant when viewing another test and reappear on return.
  Other plot modes continue to use their existing primary-variable behavior.

## Implementation contract

`FullTestPlot` requests every selected column in one `/data` and, when active,
one `/filter` request. It checks array shape and original/filtered timestamps,
mode, level, and row bounds for all columns before drawing. Requests and export
guards include ordered variable identity; aborted/stale results cannot be
relabelled as a changed comparison. A failed filter clearly falls back to the
original variables and exposes Retry.

`fullPlotExtraColumns` is an additive session field of nine lists, up to five
unique nonempty additional column names per slot. Browser recovery sanitizes
invalid entries; explicit session-file import rejects them. Primary columns,
filters, original toggles, and slot order retain existing semantics.

Time CSV export accepts optional `columns` alongside legacy `column`, with the
primary first, unique names, and a six-column limit. Multiple columns require
Full test sources. The existing locked/staged export path validates all columns,
reads their original Arrow values, and applies the shared full-resolution DSP
function once to all selected signals before the existing time crop. Each
column has explicit `[original]`/`[filtered]` headers after shared source, sample,
test-point and time identifiers. All-column metadata includes formula provenance.
Single and selected-plot CSV/PNG exports use the same mounted plot handlers.
PNG records all colors, series styles, original/filtered provenance and shared
Y-axis semantics. The default single-column API remains compatible.

Entry points: `FullTestPlot.tsx`, `FullTestVariables.tsx`/CSS,
`utils/fullTestVariables.ts`, `PlotHeader.tsx`, `SearchableSelect.tsx`,
`TimeSeriesGrid.tsx`, `App.tsx`, `analysisSession.ts`, `sessionFiles.ts`,
`backend/app/plot_export.py`.

## Verification

Commands from repository root (frontend commands run in `frontend`):

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests -p no:cacheprovider -q
npm.cmd run build
npm.cmd run lint
node --test tests/*.test.mjs
python scripts/verify_full_test_variables.py
python scripts/verify_filter_overlay.py --frontend-port 3354 --backend-port 8354
```

- Full backend suite: **497 tests / 477 subtests pass**, Python 3.13.14, 50.69 s.
  Two existing dependency deprecations. New export cases cover six variables,
  reserved names and int64 precision, seven filters × three display modes,
  original/filtered/both numerical parity and cropping, bundles/hashes/metadata,
  missing/stale secondary variables and legacy TP compatibility.
- Frontend build/lint pass; existing Vite bundle-size notice. **32 helper tests
  pass**, including six new comparison session validation/recovery tests.
- New isolated browser suite: **nine groups pass**. Native uploaded 32,768-row
  fixtures verify searchable and keyboard addition, six-variable capacity,
  duplicate exclusion, removal, last-add focus and Tab/Shift+Tab boundaries,
  stable colors, aligned envelopes/lines, temporary missing-schema switching,
  actual CSV/PNG/metadata downloads and selected-slot CSV bundle, saved JSON
  Save/Open and reload, inactive-mode retention, live expanded values,
  crosshair alignment, drag/wheel zoom/reset, filter failure/Retry,
  1100px desktop and actual 125%/150% browser zoom.
- Existing unmodified overlay regression: **ten groups pass**, covering TP and
  single-variable Full test styles/numerical parity, stale/misaligned responses,
  errors/Retry, linked X and independent Y gestures, nine-slot headers,
  maximize/restore and desktop zoom.
- Browser instrumentation exists only in the temporary browser response for
  uPlot inspection. Native calculations always run in repository Python 3.13;
  global Python runs only Playwright/stdlib. Both suites use temporary sources,
  owned child servers and isolated profiles. New suite preserves all **13**
  source files; existing overlay suite preserves **7**. No browser page errors.
- Reviewed edit-mode, filtered legend, 150% desktop and exported PNG images:
  readable color/style labels, no header overlap or clipped dropdowns.
- Independent source reviews and `git diff --check` pass. Early harness runs
  required correcting exact UI selectors (`Min/max`, descriptive test options);
  the final suite passes on stable source.

Evidence (ignored): `data/verification/full-test-variables/` and
`data/verification/filter-overlay/`. No user datasets or installed dependencies
changed. Next backlog milestone remains Phase 11b, the collapsible
variable/filter side panel.
