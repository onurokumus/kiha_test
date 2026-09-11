# Phase 4a: test-point time-plot Y-axis zoom

Completed 2026-09-09. Implements the first Phase 4 TODO item only. Signal calculations, units, backend APIs, filtering methods and exported results are unchanged.

## Behavior and design

- Each Test points time plot has a visible **Y axis** button. Its editor shows current numeric bounds, accepts explicit minimum/maximum (including scientific notation), and provides **Reset Y**. Blank, non-finite, reversed, equal and numerically too narrow bounds are rejected without changing the plot. Keyboard users can open with Enter, edit/submit, close with Escape, and return focus to the trigger. The editor is portaled outside grid clipping and stays inside the desktop viewport.
- **Alt+wheel** changes only that plot's Y span by 0.85 per wheel event around the pointer; the reverse wheel direction uses the reciprocal. **Alt+left-drag** selects a vertical range in either direction, with a 10 CSS pixel threshold. The Y rectangle is local; different variables never share a Y range. Interrupted Y drags remove their listeners/selection on window blur or plot destruction.
- Plain wheel/drag and Shift/middle-drag retain linked time zoom/pan. Ctrl+wheel in TP plots is left available to the browser. **Reset Y** fits the current raw or filtered TP traces while keeping time zoom. uPlot mode 2 computes this automatic Y extent over the retained trace arrays, not just samples inside the visible X interval; this preserves the prior method.
- The shared **Reset zoom** button appears for X or Y zoom. It and plot double-click reset the linked TP time range and all TP Y ranges, including slots hidden by maximization or layout density. A reset cancels a pending wheel commit so it cannot restore the old zoom afterward.
- Ranges live in App state, above the grid that unmounts other slots during maximize. Version-1 session autosave now includes `timeYRanges`: up to nine `{context, range}` entries or nulls. Legacy sessions default to automatic Y. Each slot remembers its most recent manually set context, encoded with JSON from the variable and sorted visible test/TP identities and intervals. Another variable/selection uses automatic Y; returning to the previous context restores that range unless replaced or reset. Filter changes preserve manual Y because the variable and units remain the same.
- Fixed build-time X ranges were replaced with callbacks reading current state, fixing reset after a zoomed rebuild/reload. Current Y state also participates in auto-ranging after an X gesture. Range changes reuse the existing uPlot instance; sizing/series changes use the existing rebuild path.

## Implementation and method checks

- State, validation, context and compatibility: `frontend/src/utils/timePlotRanges.ts`, `services/analysisSession.ts`, `App.tsx`, `TimeSeriesGrid.tsx` and `SelectedPointsPanel.tsx`.
- Controls: `components/plots/TimeYAxisControls.tsx` and its stylesheet. Plot integration: `TimePlot.tsx`. Optional Y gestures and reset cancellation: `utils/uplotPanZoom.ts`; Full test and Spectrum callers keep their X-only configuration.
- Verified against installed uPlot 1.6.32 source (`frontend/node_modules/uplot/dist/uPlot.esm.js`): mode-2 scale accumulation, default `snapNumY` using `rangeNum(min,max,0.1,true)`, explicit-scale behavior, `setSelect` event control and null-bound auto-ranging. Its TypeScript `setScale` declaration omits runtime-supported null bounds; a documented adapter confines that cast.
- Explicit Y spans must exceed `max(abs(min), abs(max), 1e-12) * 1e-9`, with finite endpoints and finite span. This protects tick generation from degenerate ranges. It is a display precision guard, not preprocessing or a change to stored samples.

## Reproduce

Use the existing dependencies. Start an isolated frontend from `frontend`:

```powershell
$env:BROWSER = 'none'
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3100 --strictPort
```

From the repository root:

```powershell
python -X utf8 -u scripts/verify_time_y_zoom.py --url http://127.0.0.1:3100
python -X utf8 -u scripts/verify_rendering.py --url http://127.0.0.1:3100
backend/.venv/Scripts/python.exe -m pytest backend/tests
```

From `frontend`: `npm.cmd run build` and `npm.cmd run lint`.

Stop the isolated Vite process afterward. The browser harness uses global Python 3.14 only for Playwright/stdlib, with installed Playwright 1.59.0 and Chromium. The backend suite uses project Python 3.13.14. No dependencies were installed.

The new runner creates a temporary Chromium profile/extension for `chrome.tabs.setZoom`; it asserts actual browser zoom/device-pixel ratio. All API requests use isolated GET fixtures. Only the browser-served copy of the transformed `uplotSync.ts` module gains an inspection reference on each plot root; source files and the production build contain no test hook. Assertions read the actual uPlot X/Y scales, not only saved state. Temporary profile/extension files are removed; screenshots go to ignored `data/verification/time-y-zoom/`.

## Results

- `verify_time_y_zoom.py`: PASS. Legacy automatic ranges; constant, all-missing and sparse traces; keyboard bounds/validation/Escape/focus; independent Y wheel and forward/reverse drag; local selection rectangle; existing X wheel/drag/Shift/middle pan; local reset; all-slot maximize/density/reload/shared reset; variable/selection/visibility context changes; filtering/clear/reset; double-click and pending-wheel reset; real session parser rejecting unsafe values; nine-slot and test/interval identity checks; 1100/1440 desktop windows and actual 125%/150% browser zoom. No console/page errors. Observed cursor error stayed below 1.3 CSS pixels. Inspected initial/editor screenshots, including 150% zoom.
- `verify_rendering.py`: PASS. Existing Uploads alignment, scatter ranges/tooltip/selection/zoom/pan/reset, synchronized TP cursors, resize/maximize/restore, keyboard and reduced-motion checks. No console/page errors.
- Backend: **231 passed**, two pre-existing dependency deprecation warnings (Starlette/httpx and AnyIO).
- Frontend build and lint: PASS. Existing Vite bundle-size warning remains non-failing.
- `git diff --check`: PASS. Previous user and Phase 1–3 changes remain intact and uncommitted. No user datasets were changed.

## Limits and next work

- Chromium verification only; no Firefox/Safari or maximum-size dataset performance run. Fixture filtering checks the UI/request/range lifecycle, not the DSP implementation; the unchanged backend suite covers the latter. No new live upload/export check was needed for this display-only milestone.
- Explicit save/reopen UI and broader session compatibility remain Phase 8. The new field extends the existing autosave; it does not implement that phase early. As with the current session format, test identity uses the existing test name plus TP ID. Stable IDs/deletion handling remain later backlog work.
- Very narrow explicit ranges are rejected by the documented numerical guard; extreme numeric magnitudes retain uPlot's existing tick-format limits. Existing very long axis-caption clipping remains Phase 11.
- Next: Phase 4b, selected-variable mean/statistics with a compact presentation; resolve standard-deviation meaning/presentation from existing statistics and user context before exposing it. Split-data identifiers remain Phase 5. Preserve Phase 2 spectral-export prerequisites for Phase 6.

Harness correction: searchable variable triggers are buttons (the search input is the combobox); the existing filter select is reliably located by its `Filter this plot only` title. Early extended runs used the wrong selectors and timed out with no browser errors; corrected runs passed.
