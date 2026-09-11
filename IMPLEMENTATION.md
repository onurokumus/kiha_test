# Implementation handoff

Updated: 2026-09-11. Branch `feature/resumable-multipart-upload`; baseline
`6b7d033` (Linux source-catalog compatibility/initial ready count committed).
Initial working tree clean, apart from the known ignored `.pytest_cache`
permission warning. Current changes are the user's requested UI fixes.

## Current milestone / acceptance

**UI polish implemented and verified.** This
explicit request takes priority over the independent Phase 11b side panel.

- Show time notes and Export selected plots have icon-only toolbar controls,
  descriptive tooltips and keyboard-accessible names/state.
- Plot filter dialog uses readable styled fields, with labels above controls.
- Full-test selector in the right panel uses available width for test names.
- XY variable choices read X then Y, retaining the actual axis values/handlers.
- Clicking overlapping scatter points opens the selection menu above the
  divider and adjacent plots; menu remains usable on resize/zoom and by keyboard.

## Implementation / decisions

- `TimeSeriesGrid`, `MultiPlotExportControls`, shared `PlotToolbarButton` CSS:
  icon-only controls reuse note visibility and export handlers. Notes use a
  pressed toggle; existing browser tests are updated for the accessible role.
- `PlotFilterDialog` has its own stylesheet. It no longer inherits export-dialog
  flex label styling, which conflicted with `FilterRow`. The row now uses two
  columns, 36px controls, 13px inputs and 12px labels instead of inline-header
  widths and 8px labels. Existing filter values/methods remain unchanged.
- `SelectedPointsPanel` test control grows with its row and wraps in narrow
  desktop panes. `XYPlot`, Settings and unavailable-axis recovery order X/Y
  without exchanging data or saved state.
- `PointSelectionMenu` portals to body outside the scatter panel's isolated
  clipping context. Scoped styling replaces injected global styles; viewport
  clamping, scrolling, Escape and keyboard selection preserve multi-selection
  and non-consuming outside dismissal.

## Verification

- Final frontend `npm.cmd run build` and `npm.cmd run lint` pass; existing
  bundle-size notice only.
- `backend\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider`:
  **453 passed, 370 subtests**, 46.02s, two existing dependency deprecations.
  Cache disabled for the pre-existing ignored cache permission issue.
- `python scripts/verify_xy_time.py --frontend-port 3196 --backend-port 8196`
  passes: 8 real exports, keyboard/axis choices/settings/persistence,
  zoom/pan/maximize/1100px/125%/150%, 21 fixture files unchanged, no page errors.
- New `verify_ui_polish.py` (3191/8191): icon/hover/keyboard, all filter layouts,
  test width, actual XY axes, maximize/1100px/125%/150%; seven unchanged files.
- New `verify_scatter_point_menu.py` with Vite on 3151: five-point stacking
  hit-tests, long names/search/scroll/multi-selection/keyboard/focus, resize and
  actual browser zoom. All API calls GET-only; no page/console errors.
- `verify_compact_plot_controls.py --frontend-port 3154 --backend-port 8154
  --regressions` passes: eight real exports, notes/filters/all-mode actions,
  failure/retry, nine slots/gestures, Y-axis and rendering/crosshair checks,
  keyboard/resize/maximize/browser zoom; 21 source files unchanged.
- Screenshots inspected and independent review complete. Stable note tooltip
  avoids stale opposite-action text after toggle. Final whitespace check passes.
  Owned servers/fixtures/profiles cleaned. Full commands/evidence:
  `docs/UI_POLISH_VERIFICATION.md`.

## Next steps

Current milestone is complete. The user requested a commit and push to `origin`
on the current branch. This checkpoint accompanies the verified UI-fix commit;
the commit/push follow-up reuses the passing checks above, with no subsequent
implementation changes. Production deployment remains outstanding.

The preceding Linux fix still needs production deployment/confirmation; see
`docs/LINUX_CATALOG_FIX.md`. Feature backlog remains Phase 11b collapsible
variable/filter side panel. Preserve completed waterfall/multi-variable
auto-split/Split multi-plots/XY-time behavior and verification documents.
