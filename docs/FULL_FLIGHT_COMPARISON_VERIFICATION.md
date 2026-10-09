# Full-flight Time and XY comparison

## Time basis moved into Align (2026-10-09)

The Elapsed time / Stored time dropdown is now inside Align above the flight
offsets. Both time basis and offsets are drafts until Apply alignment; Cancel or
Escape discards both. SearchableSelect renders its popup within the nearest
native dialog so it remains in the modal top layer and is eligible for pointer
and keyboard input. Toolbar selectors continue using the document body.

Build/lint pass (bundle `index-DFd70Mnb.js`, existing size advisory). Five focused
native browser groups pass: selector placement and mouse/keyboard interaction;
first Escape closes the popup, second closes the dialog and returns focus;
Cancel/Escape preserve applied basis and offsets; Apply commits both with correct
native displayed times and reload persistence; compact light/dark 1050px windows
at actual 100/150% zoom retain bounded, clickable menus. Screenshots visually
reviewed. No app errors, mutation HTTP or changed source fingerprints; owned
servers stopped. Backend/helper suites were not rerun for this control change.
The existing comparison verifier was updated to use the new dialog flow and
syntax-checked. Focused evidence: host temp `ptt-flight-toolbar/basis-report.json`
and `basis-align-*.png` screenshots.

## Toolbar styling follow-up (2026-10-09)

The Flights picker now lives beside the selected-flight chips. The repeated
Flights count heading is replaced by a picker badge (visible/selected when any
flight is hidden). Alignment, Fit all and export are grouped at the right with
consistent 32px controls. Eye/remove icons, understated flight colors and offset
badges make chip actions clearer. Long selections scroll within their own strip;
the complete action group moves below it in narrower desktop panes. Align closes
the native modal before returning focus to its opener; the dialog is centered
with space between its explanation and input rows.

Production build and lint pass; final bundle `index-D98E8fWr.js`. Existing bundle
size advisory remains. No backend or analysis logic changed; those suites were
not rerun for this refinement. A focused temporary native Chromium verifier
passes **12 groups**: keyboard multi-select/remove; Align keyboard opening,
Cancel/Apply/Escape and focus return; hide/show trace changes; light/dark themes
at 1500/1050px and real 100/125/150% zoom; 12 long flight names at the same sizes
with horizontal scrolling and keyboard access; and long-name dialog bounds.
No page errors, blocked writes, or changed original/generated source hashes.
Owned servers stopped and user servers were unchanged. Screenshots were visually
reviewed, including compact 150% and long-name dark mode.
The final dialog spacing/centering refinement additionally passes focused
1050px checks at actual 100% and 150% zoom with 12 long names: both-axis centering,
viewport bounds, scroll access to the last field/actions, draft/apply behavior
and keyboard focus return. Evidence is in adjacent `modal-report.json` and
`align-many-dark*.png` screenshots; those were also visually reviewed.

Evidence: `C:/Users/onuro/AppData/Local/Temp/ptt-flight-toolbar/report.json` and
adjacent `toolbar-*.png` files. The temporary verifier is
`C:/Users/onuro/.codex/visualizations/2026/10/09/01a11f95-6530-7483-b28d-3a1f3c1b31f6/flight_toolbar_check.py`;
it reuses the native fixture/hidden-server helpers below and was run with
`python -B -X utf8 <script-path>` using approved native Windows access.

## Original comparison implementation

Implemented for the explicit 2026-10-09 request, starting from `1d61084` on
`codex/ptt-ui-rework-2026-09-28`. This milestone covers Full test Time and
full-source XY. Spectrum and Waterfall keep their existing active-test source
and native time range.

## Behavior and data contract

- The shared searchable Flights picker supports up to 20 recordings. Checkboxes
  remain open while choosing; selected chips show/hide or remove a flight.
  Adding a second flight starts comparison with time since recording start.
- Flight hues remain stable across plots and visibility changes. A Time plot
  retains up to six existing variables, distinguished by line patterns. Original
  and filtered traces share the variable pattern; originals are thinner/lighter.
  The variable legend includes actual line samples and visibility toggles.
- Stored/elapsed time and optional shifts apply to every Time plot. The displayed
  coordinate is `stored_time + time_offset`; elapsed mode uses
  `time_offset = -recording_start + manual_shift`. A displayed crop is translated
  back into each source's native interval. Alignment changes reset obsolete
  crops; adding/hiding/removing flights preserves the current view. Fit all
  flights resets the relevant Time or XY view.
- Native sample arrays, uneven sample rates, gaps, min/max bands and filter
  results remain independent. No cross-flight interpolation, resampling,
  normalization, or scientific calculation changes were introduced.
- XY retains each source's native paired point order. A shift affects X and/or Y
  only when that axis is the source's actual time column. An ordinary signal
  with the same column name in another source is not treated as time.
- A flight missing a variable does not suppress valid traces from other flights.
  Coverage details identify missing signals; request failures retain successful
  results and provide Retry. A failed Time filter explicitly falls back to its
  original data. Filtered CSV waits for successful participating filters.
- CSV preserves source identity, native sample indices and timestamps. Comparison
  exports add the offset and displayed time; source-specific missing values are
  blank. PNG captures the current view and records flight/variable/processing
  identities, alignment, colors, patterns and partial-result details.
- Flights, hues, visibility, shifts and comparison time range persist through
  quiet reload with the existing dataset identity checks. Rename follows durable
  identity; same-name replacement is skipped, and changed-source alignment/crops
  reset. Per-variable legend visibility is transient, matching existing plot
  legend toggles. The Split/Edit active test remains independent.

## Automated verification

From `frontend`:

```powershell
npm run lint
node --test tests/*.test.mjs
npm run build
```

All **115 frontend helper tests**, lint and production build pass. Final bundle:
`index-DtOnfjLR.js`. Vite reports the existing large-bundle advisory. The build
needed approved native filesystem access because sandboxed Windows path
resolution failed.

From the repository root, using the available Windows Python 3.13 environment
(the checkout's virtual environment lacks pytest):

```powershell
$env:PYTHONPATH = 'D:\okumus\work_v2\kiha_test\backend'
$env:KIHA_DATA_DIR = Join-Path $env:TEMP 'ptt-full-flight-export-tests'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& 'D:\okumus\kiha_test\backend\.venv\Scripts\python.exe' -B -m pytest backend/tests -p no:cacheprovider -q --tb=short
```

**505 tests and 469 subtests pass**, including 10 new export tests; two existing
dependency warnings remain. Backend fixture SHA-256 fingerprints are unchanged
after single and bundled exports. This test run used disposable fixtures rather
than user data.

New frontend coverage checks native time/XY arrays, alignment/crop translation,
invalid arrays, independent envelopes, original/filtered pairing, all six line
patterns, missing variables, persistence and the initial single-flight viewport
transition. Export coverage checks native timestamps, uneven sources, filtering,
missing-column unions, XY time-axis semantics and backward-compatible schemas.

## Native browser verification

The browser scripts create temporary 100 Hz and 64 Hz recordings with different
native time origins, plus a third source missing a variable. They start owned
hidden Python 3.13/Vite servers and an isolated Chromium profile. The primary
script also checks original dataset fingerprints; generated native sample
fingerprints remain unchanged. Existing user servers are not touched.

```powershell
python -B -X utf8 scripts/verify_full_flight_comparison.py
python -B -X utf8 scripts/verify_full_flight_filters.py
```

All **17 comparison browser groups pass**: searchable keyboard selection, multiple
variables, stable colors/patterns, independent native arrays, flight visibility,
shared zoom, manual shifts, stored/elapsed selection, reload, missing schemas,
native Time/XY CSV and PNG, XY time axes, partial failure/Retry, native crop
translation, the initial single-flight hide/show transition, and independent
Spectrum/Waterfall native source/crop behavior.

The focused Time check passes **nine groups** covering original/filtered overlays,
visual pattern samples and variable toggles, manual Y zoom, shifted filter crops,
independent envelope bands, partial schema coverage, filter fallback/Retry,
unavailable primary selection, and absence of page errors/writes/sample changes.

The desktop matrix passes for both Time and XY: 1/4/9 plots, actual
100/125/150% browser zoom, maximize/restore, shared hover boxes, dark theme and a
1050×800 resized desktop window. The verifier awaits every visible canvas and
pending load state before asserting layouts or capturing screenshots. No page
errors, blocked dataset writes, or changed original/generated source fingerprints
were found. Owned servers stopped after verification; user servers were unchanged.

Evidence on the verification host:

- `C:/Users/onuro/AppData/Local/Temp/ptt-full-flight-comparison/`: report, CSV/PNG
  downloads, desktop screenshots and server logs.
- `C:/Users/onuro/AppData/Local/Temp/ptt-flight-time-filters/`: focused report,
  envelope screenshot and server logs.

Time/XY PNG downloads, the filtered envelope, dark nine-plot view and resized
desktop screenshot were visually reviewed. Plots, legends, time labels and
independent source styles are readable.
Browser verification performs no source-dataset writes. Export-job POSTs in the
primary script are read-only data jobs; the focused script permits GETs only.

## Entry points

Shared controls and state: `FlightSelection`, `SearchableSelect`,
`SelectedPointsPanel`, `App`, `TimeSeriesGrid`, `fullFlightComparison`,
`analysisSession`, and `sessionSources`.

Plots: `MultiFlightTimePlot`, `FullTestPlot`, `FullTestVariables`,
`fullFlightTime`, `XYPlot`, and `xyFlightComparison`.

Exports: backend `plot_export.py` and `xy_export.py`, frontend request types,
and `test_full_flight_export.py`.

Included in the user-requested complete Git checkpoint (2026-10-09).
The next independent backlog milestone remains
Phase 11b's collapsible variable/filter controls.
