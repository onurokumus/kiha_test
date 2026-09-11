# Phase 9 — plot context menus

Date: 2026-09-10. Branch `feature/resumable-multipart-upload`, baseline `873c80c`.

## Scope and action contract

The code/working-tree audit confirmed Phase 8b was complete. This milestone adds
custom right-click and visible three-dot access to existing analysis plot actions.
It does not change scientific calculations, export formats, retention, session
schemas or annotation storage. Prior dirty/untracked files remain intact.

| Plot | Actions and scope |
| --- | --- |
| Test-point time | Existing CSV/PNG dialog; original/filtered overlay for this slot; linked time/all-TP-Y reset; independent slot Y reset; create marker/interval, manage notes, shared annotation visibility |
| Full test time | Existing CSV/PNG dialog; slot overlay; linked time reset; create/manage notes and shared visibility |
| Spectrum / XY | Existing CSV/PNG dialog and existing independent automatic-axis reset |
| Test-point overview | Existing overview axis reset only |

All previous visible controls remain. The menu is restricted to plot canvases;
Split/Edit data-editing canvases and ordinary controls retain their native menus.
Overview contains aggregate values without a time-domain source, so it does not
offer time markers or introduce a new export path. Header redesign is Phase 11.

`PlotActionMenu.tsx` renders a body portal with viewport-constrained size/position,
accessible menu/checkable-item roles and disabled-state explanations. Right-click,
Shift+F10, the keyboard Context Menu key, and Enter/click/arrow on the visible
trigger open the same menu. Arrow keys wrap; Home/End, one-letter search, Enter,
Space, Escape and Tab work. Disabled items remain focusable for their explanation.
Escape returns to the plot menu trigger. Existing modal close handlers return to
their visible Export/Notes controls. Outside input, focus movement, window blur,
resize/scroll, another plot menu, source changes and unmount dismiss the menu.

`PlotExportControls` and `PlotAnnotations` expose typed imperative `open` handles;
menu opening reuses their existing modal/task/draft state and validation. Overlay
calls the existing display-only setter and never creates a DSP request. Reset
uses the same callbacks as current axis controls, with explicit linked/local labels.

## Time-note creation

Right-click snapshots the actual uPlot X coordinate and displayed interval.
Keyboard/button marker creation uses the view center; interval creation uses the
visible X interval, intersected with the selected source and current stored bounds.
The existing editor lets the user choose a test/TP before saving. Relative TP
coordinates use that trace's actual source origin, not rounded TP descriptors.
Changing the selected source recomputes the draft against that source. A click
outside a source leaves time blank instead of silently moving to a boundary.
A draft is never automatically saved. Normal validation, source-change rejection,
revision conflicts, failed-save retry and unsaved-draft guards remain in force.

## Verification

- `backend\.venv\Scripts\python.exe -m pytest backend\tests`: **406 passed**;
  two existing dependency deprecations.
- Frontend `npm.cmd run build` and `npm.cmd run lint`: **passed**; existing Vite
  bundle-size warning remains.
- `python scripts/verify_plot_context_menu.py` (3270/8270): **passed**
  eight real CSV/PNG metadata ZIP downloads (Time, Full, Spectrum, XY), CSV byte
  equality with exact API replay, PNG decode, shared overlay/reset/notes, native
  menu scope, keyboard/focus/dismissal, source-specific annotation writes and
  503 retry/draft guards, gap-rejected Spectrum export, desktop/maximize/zoom.
  Explicit nine populated slots, stale-context dismissal without reopening, failed
  trace/filter loads, recovery/reload and disabled annotation/image actions pass;
  21 source/metadata files unchanged, no page errors.
- `python scripts/verify_annotations.py` (3190/8190): **passed** existing real
  CRUD/conflict/failure, exact TP/Full projection, trim preservation, single/multi
  PNG text/metadata, keyboard/pan/resize/maximize/125%/150%; 21 files unchanged.
  Updated its legacy name-only fixture to include the current source catalog,
  required by Phase 8 recovery; the application recovery guard is unchanged.
- Native Spectrum suite (3260/8260): **36 CSV entries / 13 PNGs passed**, including
  independent numerical references, selected 2x2/3x3/nine-slot exports,
  stale/partial/failure/Close guards, keyboard and real zoom; 20 files unchanged.
- Native XY suite (3250/8250): **42 CSV entries / 18 PNGs passed**, including
  native-pair precision/crops, single/multi/nine-slot exports, failures/stale/Close,
  keyboard and real zoom; 14 files unchanged.
- Existing rendering and Time Y suites passed against isolated servers on
  3280/8280: hover/crosshair/error bars, wheel/drag/pan/reset, independent Y,
  session/range context, reduced motion, desktop resize/maximize and actual zoom.
- Menu screenshots at 1100px and actual 125%/150% zoom visually inspected: menu
  stays above adjacent plots, text stays contained, and header controls wrap.
- Final `git diff --check`: passed.

All browser data/profiles and backend/frontend servers are owned temporary
fixtures. Global Python executes only stdlib/Playwright; native processing uses
backend Python 3.13. Screenshots/downloads/logs are under ignored
`data/verification/plot-context-menu` and each existing suite's output directory.

## Limits and continuity

No new touch/mobile behavior or editor-canvas actions. Multiple overlaid sources
remain explicitly selectable in Notes; a right-click does not infer the nearest
trace's test identity. Spectrum/XY have no time-domain annotation actions. Long
axis-caption/header layout polish remains Phase 11. Other browser engines have
not been exhaustively tested. Existing cold Windows statistics-cache race is
avoided by sequential fixture prewarming; reload checks wait for debounced session
autosave before navigating; the comma-containing Time column
limitation remains unrelated.

Next milestone: Phase 10 component statistics, first settling running-sample,
missing-RPM and trashed/deleted-test contribution policies. Assignment/correction
already exists; the earlier Phase 7 statistics clause remains open until totals
and reassignment invalidation are implemented.
