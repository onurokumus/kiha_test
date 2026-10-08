# Compact shared plot values — 2026-10-08

Completed the explicit request ahead of independent Phase 11b on
`codex/ptt-ui-rework-2026-09-28`. Hovering any right-side plot now shows compact
values in the 1/4/9 layouts and maximized view. **Values**, beside 1/4/9, now
offers **Current**, **All** and **None**: only the plot under the mouse, every
visible plot together, or no compact boxes. The browser remembers this separate
display preference (`ptt.plot-values.v1`); it defaults to All and works without
storage. Previous true/false choices migrate to All/None; invalid values use All.

The styling follow-up replaces the clipped native field with the existing
SearchableSelect popup. The trigger sizes to the full Values + mode label;
a small chevron opens three themed rows with the existing selected checkmark,
hover and focus styles. Values omits search/footer/duplicate hover help. All
other shared selectors retain their existing footer and search behavior.

No plot height is reserved. Boxes contain no visible title/table headings, one
short row per trace, small padding and six significant digits. The supplied
TP-04 Test_ID example remains approximately 140×30 CSS pixels. Same-test/source
and redundant Original text are omitted. Cross-test names, overlay roles,
envelope min/max, Hz/order, log units and XY axis tags remain where needed.
Full labels remain accessible; unusually large comparisons show a remaining
trace count rather than overflowing the viewport.

In All mode, the per-grid coordinator shares actual coordinates for matching axis identities.
Unlike XY variables use relative pointer positions with explicit x/y tags;
each trace resolves its own nearest visible pair, including repeated/unsorted X.
Faceted time arrays retain independent actual sample indices. Missing or
out-of-crop samples remain unavailable. Full envelopes retain extrema; Spectrum
log values use explicit log10 units. Waterfall shows frequency/time cell bounds
and linear U, marked max for reduced cells, even with log colors. Detailed frame
provenance stays in data/exports. No analysis method, data or scale is changed.

Current mode renders only the mouse-owned plot; moving to another plot hides
the old box. Mode changes cancel pending callbacks and dismiss stale boxes.
Readouts dismiss on mouse leave, drag, keys, resize, scroll, data/scale changes
and teardown. Maximized line-plot legends remain. Waterfall's smallest nine-slot
150% canvas is 15px tall: readouts also use its facet source-label area to retain
all three scientific rows inside the card. Scrolled-out facets stay hidden.

Validation:

- `npm run build` and `npm run lint` pass. Final bundle: `index-B-cpAS3P.js`,
  982 modules; the existing bundle-size advisory remains.
- `node --test tests/*.test.mjs` passes all 96 frontend helpers. Six hover tests
  cover actual samples/nulls/hidden traces, independent time arrays, nearest XY
  pairs/crops, number formatting and compact source/processing labels. Eight
  coordinator/preference tests cover axis identity/units, different ranges/widths,
  unavailable crops, RAF coalescing, None, teardown, Current ownership, pending
  mode changes and migration/defaults. There are eight such tests in total.
- `python -B -X utf8 scripts/verify_plot_hover_values.py --backend-python
  D:/okumus/kiha_test/backend/.venv/Scripts/python.exe` passes 43 native browser
  groups with copied ptt_demo_run_a/b and a private Chromium profile. The
  `--waterfall-only` option supports focused source/layout/choice/export checks.
- All visible cards in Time, Full line/envelope/comparisons, FFT, PSD log,
  order and XY show numerically verified plotted samples. Hovering one card
  updates the current/all/no readouts according to the selected mode in each
  1/4/9 layout. Escape/leave/drag and maximize/restore
  preserve inspection and existing expanded legends.
- Mouse selection and Enter/Space open the custom popup. Home/End/ArrowDown
  navigate options; Enter commits. Escape returns focus without changing the
  choice, outside click closes, and Tab continues to the next layout control.
  Selected checkmark, all three full labels, omitted search/footer, <=184px
  menu width and <=124px height pass. Full label and popup viewport checks
  cover both themes at actual 100/125/150% browser zoom. Light/dark menu captures
  are visually reviewed (`values-dropdown-light.png` / `values-dropdown-dark.png`).
- Keyboard navigation selects None/Current/All; axes, data and uPlot
  instances stay unchanged. None/Current survive layout changes and reload;
  previous on/off preferences and invalid stored choices recover correctly.
- Both themes and actual 100%/125%/150% browser zoom keep nine-slot boxes inside
  the viewport; resizing to 1000×750 passes. Waterfall linear/log colors pass
  all layouts with verified magnitudes and cell bounds; 150% nine-slot,
  maximize/three-way choice and selected-point facet visibility checks pass.
- A real PNG download dismisses all hover boxes and preserves the plotted
  canvas. No page errors or blocked mutation attempts. Original datasets and
  copied source samples are fingerprinted unchanged. Owned hidden Python 3.13/
  Vite child servers stop; existing user servers remain untouched.

Report, compact example, theme/zoom/Waterfall screenshots, PNG and logs:
`%TEMP%/ptt-plot-hover-values/`. Compact, dark nine-slot and both Waterfall
screenshots were visually reviewed. Backend tests were not rerun for this
frontend-only change. Concurrent Sessions-removal changes are preserved.
Included in the user-requested complete Git checkpoint (2026-10-08). The next independent
backlog milestone remains Phase 11b's collapsible variable/filter panel.
