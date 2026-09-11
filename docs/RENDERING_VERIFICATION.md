# Phase 1 rendering verification

Completed 2026-09-09. Frontend rendering fixes only; calculation methods, units, statistics, backend APIs, exports, and persisted formats are unchanged.

## Fixes and evidence

- Upload row actions share flex centering. Before the fix the CSV text was 3.5 CSS pixels above center; afterward the measured offset was under 0.5 CSS pixels at 100%, 125%, and 150% browser zoom. Keyboard focus and Enter activation retain the original download endpoint.
- Hover cards render through a body portal, outside the scatter panel's clipping/stacking ancestors. Long test/point/variable names and unbroken labels wrap inside the card, which paints above the adjacent signal canvas. Placement responds to resizing/scrolling and Escape dismisses the card; re-entry restores it.
- The installed Recharts 2.15.4 `ErrorBar` implementation keys bars by pixel coordinates. Identical overlapping ranges therefore collide during React reconciliation. `ScatterRangeBars.tsx` instead keys by existing test-point identity and uses Recharts' actual axis scales and plot-area clipping. Min/max distances, colors, cap sizes, and point selection semantics are retained.
- uPlot 1.6.32 caches the pointer rectangle. Animating scale on plot ancestors changes that geometry during entry/maximize/restore. Those animations now change opacity only, with reduced-motion support. No duplicate cursor listeners were added: uPlot already refreshes geometry on resize, scroll, and `setSize`.
- Real 150% desktop zoom exposed an existing wide-but-short grid failure: width-only responsive rules allowed row heights to collapse below uPlot's minimum rendering size. Rows now retain a 220px minimum with scrolling; expanded plots reserve 320px for controls, axes, and legend. This keeps plots rendered when the workspace stacks vertically.

## Reproduce the checks

Use the existing frontend dependencies. In one PowerShell terminal:

```powershell
cd D:\okumus\kiha_test\frontend
$env:BROWSER = 'none'
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3100 --strictPort
```

In another terminal at the repository root:

```powershell
python -X utf8 -u scripts/verify_rendering.py --url http://127.0.0.1:3100
python -X utf8 -u scripts/verify_browser_zoom.py --url http://127.0.0.1:3100
```

The browser-only scripts require Python Playwright and its Chromium browser. This run used the already-installed Playwright 1.59.0 under global Python 3.14. The backend continues to use its Python 3.13 virtual environment; browser scripts import no backend/native data libraries. If browser dependencies are absent, install Playwright into a suitable test environment with `python -m pip install playwright==1.59.0` and `python -m playwright install chromium`.

Both scripts use a fresh browser profile and mocked GET responses, with four test points (two identical means/ranges), long metadata, and legacy metadata omissions. They make no backend data mutations. The browser-zoom script creates a temporary extension/profile and uses `chrome.tabs.setZoom`, asserting the reported zoom, device-pixel ratio, and CSS viewport changes; it does not substitute CSS zoom or pinch zoom. Temporary files are removed on exit. Screenshots are written beneath ignored `data/verification/`.

## Coverage and results

- `verify_rendering.py`: PASS. Cursor alignment and synchronized X positions at 1100/1440/1700px desktop widths; pointer entry during maximize/restore; keyboard maximize/restore; wheel zoom, Shift-drag pan, reset, reduced motion. Scatter checks cover four zoom/pan/reset cycles with stable line counts and exact reset coordinates, asymmetric 4/6 range proportions, axis toggles, filtering/removal, selection, tooltip wrapping, stacking, Escape, and re-entry. No console or page errors.
- `verify_browser_zoom.py`: PASS. Actual 125% and 150% zoom with keyboard maximize/restore and native window resize, scrolling a short plot pane into view, CSV centering/keyboard activation, wrapped viewport-contained scatter tooltips, and range rendering. Maximum observed cursor offset was 1.23 CSS pixels (2px assertion tolerance). No console or page errors.
- `npm run build`: PASS (TypeScript + Vite); non-failing warning for the existing large frontend bundle.
- `npm run lint`: PASS.
- `backend/.venv/Scripts/python.exe -m pytest backend/tests`: 200 passed. Two dependency deprecation warnings (Starlette/httpx and AnyIO).
- `git diff --check`: PASS.

## Test constraints and follow-ups

- Verification was on Chromium, not Firefox/Safari. Browser fixtures validate rendering, not live upload/download transfer; existing backend tests cover export transport. Chromium download links bypass Playwright route mocks, so the CSV check intercepts the native click, verifies keyboard activation and URL, and avoids contacting real data.
- SVG horizontal/vertical lines have zero-width/height boxes: wait for `attached`, not Playwright `visible`. At high zoom, scroll the actual plot into view before sending mouse coordinates; offscreen pointer moves do not test cursor alignment.
- Long axis captions still clip; exceptionally lengthy metadata can produce a card taller than the viewport. Both are recorded as Phase 11 presentation follow-ups rather than expanding this milestone into a details-panel redesign.
