# Plot appearance verification

Verified 2026-10-09 for the requested shared line-thickness and scatter-size
controls in Analyze's Options panel. Both preferences range from 50% to 300%
as typed percentages with arbitrary decimals, reset to 100%, and persist as
`ptt.plot-appearance.v1`. The requested follow-up replaced the initial sliders
with text input fields; values are no longer restricted to 25% steps.
The latest compact layout shows only inline Line/Scatter fields and a reset icon.

## Compact layout follow-up

```powershell
python scripts/verify_plot_appearance.py --compact-only --output "$env:TEMP/ptt-plot-appearance-compact"
```

All six focused groups pass. Appearance-only TP Time/XY popovers measure 360px
wide with inputs and the reset icon on one row; the extra heading, explanation,
helper text and previews are absent. Full Time and Spectrum retain their 520px
popover and working trace-style, X-axis and log-scale controls. Light/dark
1050px desktop windows at actual 100%/150% zoom keep content and inline validation
inside the viewport. Keyboard progression through Line, Scatter and Reset,
reset-to-100%, blur recovery and Escape/focus return pass.

Screenshots were visually reviewed. The run produced no page errors, dataset
writes or source fingerprint changes and stopped its owned servers. Evidence:
`C:/Users/onuro/AppData/Local/Temp/ptt-plot-appearance-compact/report.json`
and the same directory's `compact-*.png` files. Earlier rendering and text-input
suites were not repeated for this layout-only change.

## Text input follow-up

```powershell
python scripts/verify_plot_appearance.py --inputs-only --output "$env:TEMP/ptt-plot-appearance-inputs"
```

All six focused groups pass after the text-input change:

- Keyboard selection and typing apply 137.5%, 146.25% and 82% immediately,
  preserving exact saved percentages and existing Time/XY plot instances,
  native data, visibility and axes. Four XY cards retain their state.
- Both fields show an inline accessible warning for empty, nonnumeric, minus,
  Infinity, 49.9% and 300.1% drafts. Invalid drafts leave the last applied
  geometry and saved values intact; blur restores the valid value.
- Enter accepts valid decimals; Escape returns focus and discards invalid
  drafts. Exact values and reset-to-100% survive reload.
- Light/dark 1050px desktop windows at actual 150% Chromium zoom keep fields
  and validation text inside the viewport. Changes work in maximized and
  restored views. Valid/invalid screenshots were visually reviewed.

The run retained the same isolated fixtures, API write guard and owned server
lifecycle described below. No page errors, dataset writes or source fingerprint
changes occurred. Evidence: `C:/Users/onuro/AppData/Local/Temp/ptt-plot-appearance-inputs/report.json`
and that directory's `inputs-*.png` screenshots. The earlier 21-group rendering
matrix was not rerun for this control-only follow-up; its helpers were updated
to use text fields so the complete suite remains reusable.

## Focused browser checks

```powershell
python scripts/verify_plot_appearance.py
python scripts/verify_plot_appearance.py --supplemental-only --output "$env:TEMP/ptt-plot-appearance-extra"
```

Both commands passed: 16 primary groups and five supplemental groups. The
verifier uses Python Playwright's private Chromium profile, temporary copied
demo recordings, and owned hidden Vite/Python 3.13 servers on ports 3128/8028.
No existing server is stopped or reused. API mutations are blocked except
read-only PNG packaging/progress requests. A controlled browser-only fixture
adds overlapping overview markers and a reference curve for cluster coverage.
User dataset files and copied native source samples are fingerprinted before
and after; all remained unchanged. There were no page errors or blocked writes.

- Accessible slider labels, limits, keyboard increments, focus progression,
  Escape/focus return, independent settings, reset and browser reload pass.
- Actual uPlot widths change for selected-TP Time, full line/envelope,
  multi-variable comparisons, FFT, log PSD and multi-flight Time. Existing plot
  instances, native data arrays, values, axis ranges and visibility survive.
  Four linked plots retain their drag-zoomed X/Y bounds.
- Both selected-TP and full-test XY point diameters change independently of
  line thickness without resetting their plot/data/axes. Overview selected and
  ordinary markers, clusters and reference markers also scale.
- Live SVG `r.animVal.value` matches the requested size after prior hover and
  300% → 50% → 100% changes. This check found frozen SMIL radius animations:
  changing the HTML attribute alone did not change the visible marker. The
  implementation now uses a CSS radius transition; the final supplemental run
  passes. Export snapshots restore the configured radius rather than hover size.
- Cluster counts remain unchanged and readable. Split previews pick up the
  shared preference when entered and reflect subsequent preference changes.
- Real line and XY PNG downloads use the scaled styles and identical source
  canvas pixels. The live canvas is unchanged after export. The overview PNG
  retains selected/reference marker radii, cluster labels, range bars and an
  opaque white background. All three downloaded PNGs were visually reviewed.
- Light/dark themes, 1500px/1050px desktop windows, actual Chromium 100%, 125%
  and 150% zoom, nine cards and maximize/restore pass. Options stays within the
  viewport with complete readable controls. Screenshots were visually reviewed.
  A supplemental assertion confirms XY no longer shows an unrelated Line
  summary inherited from full-test mode.

Evidence is in the host temporary directory:

- `C:/Users/onuro/AppData/Local/Temp/ptt-plot-appearance/report.json`
- `C:/Users/onuro/AppData/Local/Temp/ptt-plot-appearance-extra/report.json`
- The same folders contain theme/zoom screenshots, three PNG exports and the
  captured overview SVG.
- `ptt-plot-appearance-extra/options-final-light.png` and
  `options-final-dark.png` refresh the final controls after the wording/summary
  fixes. Both were visually reviewed at 1500px and 1050px respectively.

The initial Windows sandbox could not create the disposable fixture directory;
the native browser commands completed with approved escalation. Both runs
stopped their owned servers. Backend scientific suites were not part of this
frontend appearance-only browser milestone; normal build/lint/helper results
are recorded in `IMPLEMENTATION.md`.
