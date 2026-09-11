# Compact plot controls verification

2026-09-11. User-requested right-drag removal, visible-data Y fitting, and compact
plot headers, also completing the shared-header portion of Phase 11.

## Browser setup and commands

`scripts/verify_compact_plot_controls.py` owns a temporary backend data root,
uploaded fixtures, Chromium profile, browser-zoom extension, and two child servers.
Its backend always uses `backend/.venv/Scripts/python.exe` (Python 3.13); global
Python imports only stdlib and Playwright. Existing user data and browser profiles
are not accessed. Server ports must be free; unrelated listeners are never reused.

```powershell
python scripts/verify_compact_plot_controls.py --frontend-port 3310 --backend-port 8310
python scripts/verify_compact_plot_controls.py --regressions-only
```

Both commands passed. The optional `--regressions` flag runs the same native
checks followed by the existing Time Y and rendering suites. Those suites use
isolated intercepted GET fixtures, with no backend writes. A test-browser-only
Vite module hook exposes existing uPlot instances for assertions; production
source is not instrumented.

## Native results

- Time, Full test, Spectrum, and XY headers have exactly two toolbar buttons:
  maximize/restore and the ellipsis. Compact original TP means remain visible.
  Header bounds remain usable in all nine slots.
- Right-button, Alt-right-button, and Shift-right-button drags leave all axes and
  the selection rectangle unchanged. A plain right click opens the shared menu.
  Left selection zoom, Shift-left pan, middle pan, wheel zoom, and reset remain
  functional. Overview context-menu reset remains available.
- An independent Python oracle checks finite displayed sample centers and clipped
  line intersections against Time/Full/Spectrum automatic Y bounds (10% padding).
  Hidden series and null gap edges are excluded by the production utility tests.
- Spectrum initial automatic range, menu reset, and double-click reset retain a
  null saved viewport after the Y refit. Wheel zoom saves an explicit X viewport.
- Eight real CSV/PNG metadata ZIP downloads cover all four modes. CSV bytes match
  exact replay through the existing native export endpoints. PNGs are decoded and
  inspected. Failure and Close paths remain available through the menu.
- Existing annotation handlers create source-specific markers and intervals with
  exact TP-relative timing, handle failed save/retry, and preserve draft-discard
  guards. Overlay toggles reuse persisted state without another filtering request.
- Native filter and Spectrum analysis dialogs open from the menu; Escape returns
  focus to the ellipsis. Export and notes dialogs do the same. Right-click,
  Shift+F10, Enter, arrows, Home/End, typeahead, Escape, Tab, and outside dismissal
  work. Tab can now move to the focusable plot canvas after the compact toolbar.
- Four modes, nine slots, resize to 1100px, maximize/restore, reload, actual 125%
  and 150% Chromium zoom, and XY variable-editor layout pass. Failed and stale
  trace states retain menu explanations and safe export/annotation availability.
- All 21 fixture source files remain byte-identical. Annotation files are separate
  intentional fixture writes. Zero page errors were recorded. Temporary data,
  profiles, and owned servers were cleaned after the native run.

The maintained Time Y regression also passes exact manual bounds and validation,
independent Y axes, Alt-wheel/Alt-selection, sparse/constant/all-missing traces,
automatic visible-X fitting, Auto after manual bounds, menu-to-Y-editor reopening,
filter apply/clear through its new dialog, saved ranges, slot context changes,
pending-wheel reset, double-click reset, actual 125%/150% zoom, and editor/cursor
geometry. At 150%, the helper waits for the native focus scroll before opening
the menu, whose documented scroll behavior is dismissal. The existing rendering
suite passes cursor synchronization, resizing, maximize/restore, reduced motion,
scatter zoom/pan/reset, filters, selection, tooltip bounds, and keyboard actions.
Both regression suites report zero console/page errors and clean their profiles.

## Screenshots and limits

Evidence is ignored under `data/verification/compact-plot-controls/`. Parent
visually reviewed `Test points-1.5-0.png`, `XY-edit-1.5.png`, and the other mode/menu
captures. `results.json` records the eight downloads, source-file list, and errors.
The Time Y suite writes its editor/zoom captures under
`data/verification/time-y-zoom/`.

Automatic Y fitting uses displayed arrays. The pre-existing Time display API
rounds to six decimal places (`backend/app/store.py`, `_json_array`), so the tiny
fixture's approximately 4e-10 samples appear as zero and receive zero-constant
padding. Compact original statistics and native CSV preserve source precision.
No backend rounding or scientific export behavior was changed.

An X window containing no finite displayed line segment has nothing to fit.
An existing plot retains a usable preceding Y scale; a plot rebuilt by Reset Y
can use the full-data fallback. Regression coverage requires finite ordered Y
bounds and unchanged X in this case. Visible data returning to the window resumes
automatic fitting. XY continues to support its explicit two-axis viewport.

Initial verification failures were test-maintenance issues: disabled menu items
include their explanatory text in accessible names, moved controls require menu
selectors, and the previous exact global-Y assertion is no longer valid for a
visible-X fit. One native attempt coincided with production HMR edits and returned
to the saved TP mode; the final run with stable sources passed all modes.
