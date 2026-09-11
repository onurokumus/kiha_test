# Waterfall FFT verification

2026-09-11; branch `feature/resumable-multipart-upload`, baseline `db95fcd`.
Scope: user-requested Spectrum waterfall comparable to the attached 2D
time/frequency/color maps. This independent feature takes priority over Phase
11b. Method and limitations are in [Waterfall FFT](WATERFALL_FFT.md).

## Numerical and API checks

`backend/.venv/Scripts/python.exe -m pytest backend/tests -q`:
**447 passed, 360 subtests** (Python 3.13). Two existing dependency deprecations.
Eleven new waterfall tests cover:

- Analytic amplitude and Nyquist endpoints, periodic Hann normalization and
  an independent direct DFT on an off-bin sinusoid.
- A chirp's moving ridge; exact max reduction across all time/frequency cells;
  a 524,288-sample signal with late activity retained in the bounded grid.
- Exact saved TP rows, nonzero source time origins, window-center times,
  discarded incomplete tails, interpolation without deleting rows.
- Invalid windows/overlap, short/all-missing signals, known acquisition gaps,
  invalid/mixed bounds and unknown columns.
- CSV values equal loaded grid values, explicit bounds, viewport intersection,
  metadata packages, stale rows/rates, selected-plot bundles, settings defaults.

An initial new settings test used the wrong model class name; the test import
was corrected, and the final entire suite above passes.

## Frontend and browser

`npm.cmd run build`, `npm.cmd run lint` and `node --test tests/*.test.mjs` in
`frontend`: pass, including all 13 existing helper tests. Existing Vite bundle
size notice remains.

`python scripts/verify_waterfall.py` uses private Chromium/profile/zoom extension,
temporary uploaded sweep signals in two tests, and owned servers on 3350/8350.
The backend subprocess uses the project's Python 3.13 runtime. Checks include:

- Entering Waterfall defaults to a single active-test/variable map, even with
  multiple TPs selected. Comparison requires explicit Selected TPs; a saved
  explicit comparison choice survives reload. Test data remains isolated.
- Two independently calculated sources and separate maps, shared color scale,
  frequency/time axes, magnitude/source-time hover, linear/log color.
- Linked rectangle zoom on both axes, wheel frequency zoom, Shift pan, keyboard
  Home reset, menu reset, maximize, restore and reload with saved axis ranges.
- Actual CSV/PNG ZIP downloads and analysis metadata; nine-slot CSV ZIP and
  selected 2×2 PNG; Full-test interval request/export parity.
- Window/overlap persistence, individual source failure with disabled exports,
  Retry, held late response discarded after mode change, FFT/Welch switching.
- Desktop resizing to 1100px, actual 125%/150% browser zoom, chart geometry,
  no JavaScript page errors, and all **14 source files byte-identical**.

The browser test exposed a mode-1 uPlot X-range callback overriding explicit
wheel zoom. It now retains explicit frequency bounds, and the test asserts both
axes change. Screenshots and exported PNGs are visually reviewed. Evidence is
in ignored `data/verification/waterfall/`; temporary datasets/profile/extensions
and owned processes are cleaned up by the harness.

## Additional regression checks / limits

The old `verify_spectrum_exports.py` entry point still targets the removed
visible Export button from before the compact-header milestone; it stops at
that selector before exporting. No production change was made to support it.
Use the current compact-controls/menu entry point for existing-mode regressions.

The final `python scripts/verify_compact_plot_controls.py --frontend-port 3370
--backend-port 8370` run passes in full: menu/annotation/overlay/Y-axis groups,
Spectrum viewport semantics, all four original plot modes, eight native CSV/PNG
downloads with exact API replay, nine slots, resize/maximize/125%/150%, no page
errors and 21 source files unchanged. Earlier runs encountered a detached canvas
during the verification helper's `scroll_into_view_if_needed`; it held an old
element during normal resize replacement. The helper now uses locator hover,
which automatically re-resolves the element. No production plot change was
needed for this regression-harness issue.

Waterfall export uses the existing shared staging/progress/cancellation path
with checkpoints between FFT batches. A dedicated prolonged waterfall Cancel
browser case was not added. Native-window CSV, manual color limits, order
tracking and 3D surfaces are outside this milestone; grid CSV contains explicit
aggregate bounds and linear maxima. Analysis is capped at 8,000,000 samples,
and known gaps require choosing a continuous interval. Ordinary FFT/Welch
estimation and native-bin exports remain unchanged.
