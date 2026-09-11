# Requested UI fixes — 2026-09-11

The user's six UI issues were handled together ahead of the independent Phase
11b side panel. Analysis methods and saved axis/filter values are unchanged.

## Changes and acceptance

| Issue | Result |
| --- | --- |
| Show time notes looks awkward | Icon-only pressed button; shared hover/focus tooltip reads “Show or hide time notes”; accessible name and persisted visibility retained. |
| Export selected plots takes too much room | Matching grid/download icon opens the existing export dialog; tooltip, disabled reasons and focus return retained. |
| Filter settings fields are tiny/poorly styled | Dedicated dialog styling avoids export-dialog label rules. Two-column fields have 36px controls, 13px values, 12px labels and comfortable spacing. All existing filter types/validation/handlers retained. |
| Full-test name is clipped by narrow selector | Right-panel test selector grows with available row width and wraps in narrower desktop panes; applies to Full test and full-source Spectrum/XY. |
| XY variables read in reverse order | X is on the left and Y on the right, with visible labels. Settings and unavailable-axis recovery follow X/Y order without exchanging values. |
| Five-point scatter menu goes under divider/plots | Menu portals to the document body outside the scatter panel's clipping/stacking context. Long names wrap; list scrolls; position adjusts to viewport/menu changes. |

The scatter menu retains multi-selection and outside dismissal without consuming
the next action. Its entries now support Enter/Space, and Escape/Close return
focus to the overview. Styles are scoped instead of injecting global scrollbar
rules. Native modal dialogs remain above the floating menu.

## Verification

All commands passed from the repository root unless noted:

```powershell
# In frontend
npm.cmd run build
npm.cmd run lint

# Repository root; Windows backend Python 3.13
backend\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider
python scripts/verify_ui_polish.py --frontend-port 3191 --backend-port 8191
python scripts/verify_xy_time.py --frontend-port 3196 --backend-port 8196
python scripts/verify_compact_plot_controls.py --frontend-port 3154 --backend-port 8154 --regressions

# With a Vite server on 3151; this check mocks all API traffic
python scripts/verify_scatter_point_menu.py --url http://127.0.0.1:3151
git diff --check
```

- Backend: **453 tests and 370 subtests passed**, 46.02 seconds. Two existing
  dependency deprecations; cache disabled for the pre-existing ignored cache
  permission issue. Frontend retains its existing bundle-size notice.
- Focused UI suite: rendered hover tooltips, icon-only controls, keyboard
  visibility/export actions, modal focus return, all filter-field layouts,
  test-selector width, actual XY axis callbacks, maximize/restore, 1100px,
  actual 125%/150% browser zoom. Normal dialog numeric fields are 239px wide.
  Seven fixture files unchanged; no page errors or source writes.
- Scatter suite: five overlapping points at the panel boundary, hit-testing
  above the actual divider and neighboring canvases, long-name wrapping,
  search, live selected state, keyboard multi-select, internal wheel scrolling
  without chart zoom, first outside click activates maximize, Escape/Close,
  open-menu resize to 1100×650, actual 125%/150% zoom. GET-only API fixtures;
  no page or console errors.
- Existing XY-time suite: measured/generated time axes, settings and session
  restoration, eight real exports, keyboard/zoom/pan/maximize/desktop zoom;
  21 fixture files unchanged and no page errors.
- Existing compact-controls suite plus Y-axis/rendering regressions: all plot
  modes, nine slots, filters/notes/actions, eight native CSV/PNG downloads,
  failure/retry, gestures, crosshair alignment, scatter redraw/hover, keyboard,
  resize and browser zoom. 21 fixture files unchanged; no page errors.
- Existing annotation/context-menu/compact-control checks now locate the notes
  button and its pressed state instead of the former checkbox. The compact
  suite exercises the shared note visibility behavior.

Screenshots and results are in ignored `data/verification/ui-polish/`,
`scatter-point-menu/`, `xy-time/`, and `compact-plot-controls/`. Root and
implementing agents inspected normal/150% dialog, XY and scatter screenshots.
All owned servers, temporary datasets and browser profiles were cleaned up.
Independent read-only review found and resolved stale action-tooltip wording;
final implementation build/lint and whitespace review pass.

## Remaining scope

The user requested committing and pushing these verified changes to GitHub.
The follow-up reuses the checks above; implementation has not changed since
verification. Production deployment remains outstanding.
Phase 11b collapsible variable/filter side panel remains the next feature
milestone. The earlier Linux deployment confirmation remains separately tracked
in `LINUX_CATALOG_FIX.md`.
