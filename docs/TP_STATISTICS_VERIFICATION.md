# Test-point statistics verification

Verified 2026-09-10 on Windows, branch `feature/resumable-multipart-upload`, baseline `873c80c` plus the preserved uncommitted Phases 1–4 work. Phase 4b is complete.

## Behavior and calculation

Each Test Points time-plot header now has a compact mean button. Opening it shows a row per visible selected TP with its mean, **population** standard deviation, finite/total and excluded sample counts, test/TP identity, time interval and exact zero-based half-open row bounds. This follows the user's choice to keep SD in details. Multiple means appear as a range of individual TP means; values are never pooled. Hidden points are excluded from the display.

Statistics use all finite samples in the complete saved TP, read from the current stored Parquet column through `store._testpoint_bounds`. They do not use reduced plot vertices or the visible zoom window. “Original” means the stored signal before the current plot filter; prior data edits, derived variables and ingestion gap rows remain part of that stored dataset. Counts include stored gap rows, excluded from calculations when non-finite.

For N finite samples, the arithmetic mean is `sum(x) / N` and population SD is `sqrt(sum((x - mean)^2) / N)`. Samples have equal weight, with no time weighting, unit conversion or Bessel correction. Both values use the variable's stored units. Empty/all-invalid ranges return null statistics; one finite sample has SD zero. These semantics were checked against the official [NumPy mean documentation](https://numpy.org/doc/stable/reference/generated/numpy.mean.html) and [NumPy std documentation](https://numpy.org/doc/stable/reference/generated/numpy.std.html). The installed backend uses NumPy 2.4.2, float64 accumulation and `ddof=0`. A scaled fallback prevents overflow or complete variance underflow for extreme finite values; results still have float64 precision.

X/Y zoom, pan, maximize and filters do not recalculate these whole-TP values. While a filter is active the caption explicitly says **Original mean(s)** and details explain that the plotted signal is filtered. Filtered statistics and original/filtered overlays remain outside this milestone.

## Implementation and compatibility

- `backend/app/store.py` adds `summary = {method: "finite-population-v1", mean, std_population, i0, i1}` to `/tp_stats`. Existing six-decimal scatter mean/min/max remain compatible. Cache version 2 lazily recomputes old entries and retains existing fingerprints, native-read locks, atomic writes, invalidation and manual rebuild paths.
- `frontend/src/App.tsx` shares the stats cache and generation/request-epoch guards. Scatter/filter columns retain their scope; additional time-plot columns are requested only for visible selected tests and displayed slots. Errors wait for explicit retry. Details and scatter use the same retry handler.
- `TimePlotStatistics.tsx` supplies keyboard-accessible, portaled details with viewport bounds, scrolling, focus restoration, missing-column/loading/error/retry and legacy API states. Headers use five significant digits, details twelve, with the complete returned value in the cell title. Older API responses explicitly identify the rounded mean and unavailable SD/row bounds.
- Visual QA found scatter hover cards could cover keyboard-opened details. Details now stack above those cards and below page confirmations.
- Repeated browser zoom and keyboard maximize/restore exposed a maximum-update-depth crash in the pre-existing shared `PageTooltip`. Diagnostics showed repeated identical layout positions. `FeedbackProvider.tsx` now compares the committed position before scheduling state updates and avoids unchanged anchor updates. An updater-only equality guard was insufficient. Temporary diagnostics were removed; the regression repeats the zoom/restore sequence three times.

## Verification

From the repository root:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests
python -X utf8 -u scripts\verify_tp_statistics.py
```

From `frontend`:

```powershell
npm.cmd run build
npm.cmd run lint
# Separate terminal for the existing mocked browser suites:
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3100 --strictPort
```

Then, from the repository root:

```powershell
python -X utf8 -u scripts\verify_time_y_zoom.py --url http://127.0.0.1:3100
python -X utf8 -u scripts\verify_rendering.py --url http://127.0.0.1:3100
```

| Check | Result |
| --- | --- |
| Backend Python 3.13 full suite | **240 passed**, including 9 new summary tests and five extreme-value subcases |
| Frontend build and lint | Passed; existing bundle-size warning only |
| Live isolated statistics browser suite | Passed; no unexpected console/page errors |
| Existing rendering and Y-axis browser suites | Passed after the final tooltip fix; no console/page errors |
| Visual review | Details readable at 1100px desktop width and actual 125%/150% browser zoom; long names wrap and tables scroll within the viewport |

Backend coverage includes population versus sample SD, full-resolution versus reduced traces, saved/legacy/open-ended TP bounds, missing/NaN/infinite samples, empty/singleton/constant/tiny/high-offset/extreme data, finite JSON, legacy cache upgrade/hits and data/TP/manual rebuild invalidation.

The live suite uploads three isolated 5000-row fixtures through the API, saves TPs and compares results against independent Python `math.fsum` calculations. Its first TP uses rows `[10, 2010)`, with 1999 finite samples out of 2000, mean **5** and population SD **22.444417478249886**. A reduced 100-point trace gives a different vertex average. It checks visible values/counts/bounds, a forced 503 and keyboard retry, tiny/all-missing/constant columns, selected-test request scope, multiple TP selection/hiding, missing columns across tests, actual moving-average filtering/clear, variable changes, legacy API responses, focus/Escape, resize, browser zoom and maximize/restore. Crosshair offsets remain below two CSS pixels.

The script owns temporary backend/frontend ports 8110/3110, refuses occupied ports, starts the backend with its Python 3.13 venv and cleans up its children and temporary dataset/profile in `finally`. Global Python runs only stdlib/Playwright. Existing rendering scripts mock API data in isolated profiles. No user datasets or browser settings changed. Ignored screenshots/logs/results are in `data/verification/tp-statistics/`; failed-run artifacts may remain alongside final successful results.

## Limitations and next milestone

Exact cache misses still scan a full column and compute every saved TP in that test. Added columns are limited to displayed selections, but maximum-size datasets and cold-cache latency were not benchmarked. Validation used Chromium on Windows; Firefox/Safari and other operating systems were not tested. Existing backend deprecation and Git configuration-access warnings are non-failing.

Existing plot/scatter serialization rounds some values to six decimal places, so a tiny signal's accurate summary can be nonzero while its trace rounds to zero. This milestone preserves those display and analysis paths and the spectral interval/reduction limitations from Phase 2. Statistics describe complete original TPs, not filtered or zoom-window values.

Next: **Phase 5**, consistent TP identifiers in split-data CSV downloads from Uploads and Split, including the TP ID in Split filenames. Preserve Phase 2 spectral-export prerequisites before Phase 6.
