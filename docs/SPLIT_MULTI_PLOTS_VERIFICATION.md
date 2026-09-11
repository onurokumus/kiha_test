# Split multi-line plot verification

Date: 2026-09-11. Scope: the user-requested additional variable previews in Split.
The shared test-point definitions and saved/draft export semantics remain common
to all plots. This is a desktop keyboard/mouse feature.

## Browser setup and command

```powershell
python scripts/verify_split_multi_plots.py --frontend-port 3320 --backend-port 8320
```

The suite uses native Python Playwright and the existing
`verify_data_quality.servers` helper. Global Python runs browser automation; the
backend subprocess runs `backend/.venv/Scripts/python.exe` (Python 3.13). Two real
CSV uploads, saved TP definitions, the browser profile, and a browser-zoom
extension live in a temporary directory. Only the suite's owned servers are
stopped. No dependencies are installed and no user dataset or browser preference
is accessed. Helper `--help` was read before using the existing isolated setup.

Fixtures cover eleven alpha variables, two disjoint beta variables, missing
samples, an entirely missing source variable, and two saved TP intervals. The
all-missing source column is correctly excluded from the available numeric
picker. A separate intercepted successful preview with null values exercises
the card's empty state. An intercepted request asks the real backend for
`display=envelope` on the first card's full view; zoom returns to the normal raw
response, exercising both representations on the same source. The suite
adds a verification reference to uPlot instances through the intercepted Vite
module response; production code does not expose a test API. This permits exact
numerical X-axis assertions alongside real mouse and keyboard interactions.

## Acceptance coverage

- Starts with one plot; keyboard add, picker and remove retain clear focus.
  Variables are distinct, the last plot cannot be removed, and capacity is nine
  or the available variable count.
- Three separate variables share exact X endpoints and TP overlay positions.
  Drag zoom from different cards, double-click, each reset entry, and table zoom
  update the same range. Cursor positions match numerically across cards, and
  the first preview transitions between the backend's envelope and raw modes.
- A TP edge edited on the third chart changes the common draft. Changing a
  variable/removing a plot preserves that draft. Save, reset, native saved/draft
  CSV and TP JSON downloads retain the same boundary semantics.
- Plot choices persist across tab changes and reloads, separately per test.
  Disjoint variable catalogs do not inherit incorrect labels or requests.
- Missing previews and injected read errors stay local to their card; Retry
  recovers. Rapid variable/test switches must not relabel stale response data.
- A tall nine-chart stack remains scrollable. A 1100px desktop window and actual
  125%/150% browser zoom retain reachable pickers, plot bounds and linked gestures.
- Original CSV/Parquet/pyramid bytes remain unchanged during display and TP edits.
  A final explicit fixture-only column removal exercises preference reconciliation.

## Results

The full native Chromium suite **passed**: seven assertion groups, 99 preview
requests, and zero unexpected browser console or page errors. All 10 original
CSV/Parquet/pyramid files were byte-identical through plot and TP edits. The final
column removal was an explicit isolated fixture mutation. Temporary fixtures,
profile and extension were removed, and both owned servers stopped normally.

Screenshots were visually reviewed at 1100px and 150% browser zoom: plot headers,
searchable pickers, Reset/Remove controls and the TP table remain readable and
reachable through normal desktop scrolling. The extension uses
`chrome.tabs.setZoom`; the suite verifies changed devicePixelRatio, rather than
emulating zoom through a CSS transform or screenshot scaling.

Related required checks reported by the parent milestone also pass:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests -q  # 421 passed, 331 subtests
cd frontend
npm.cmd run build
npm.cmd run lint
node --test tests/splitPlotPreferences.test.mjs tests/visibleYRange.test.mjs  # 10 passed
cd ..
python scripts/verify_split_exports.py --frontend-port 3110 --backend-port 8110
python -m py_compile scripts/verify_split_multi_plots.py
```

The existing backend dependency deprecations and Vite bundle-size notice remain.
No new application failure occurred. Initial harness runs corrected Vite's local
identifier renaming, the picker's `combobox` role, and full bounds at metadata
duration (`n/fs`, 120 seconds), rather than the final sample's 119.9 seconds.

Ignored evidence is under `data/verification/split-multi-plots/`:
`results.json`, `nine-plots.png`, `plot-1-1100-1.png`,
`plot-3-1440-1.25.png`, `plot-3-1440-1.5.png`, CSV/JSON downloads and server logs.

Split has no maximize, wheel-zoom or pan controls; those were not introduced by
this request. No scientific method, source-retention or analysis-session file
format change is covered or claimed here.
Rapid selection changes are exercised with normal live requests; the suite does
not claim a deliberately delayed out-of-order network-response test.
The existing comma-containing variable-name limitation in the shared Time API
was not changed or covered; these fixtures use ordinary column identifiers.
