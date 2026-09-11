# Despike single-line Test points display

2026-09-11; baseline `e802dc1`, branch `feature/resumable-multipart-upload`.
User-requested fix ahead of the independent Phase 11b side panel.

## Cause and change

`TimePlot.tsx` requested `display=auto`. For long saved test points the filter
endpoint returns a min/max envelope; the faceted TP plot renders its two edges
as separate lines. The same automatic branch existed in the user's reference
commit, `4ba8a178aca59a1dbe1878b7128305beb04aa768`, so this comparison does not
establish a change to the despike detector itself.

TP filters now request the existing `display=line` representation. Adding
Despike keeps one chronological filtered line per TP at any supported length.
All TP filter kinds share this presentation. CSV source requests also use Line
to keep the executed representation consistent with the displayed request.

No backend calculation changed. Exact half-open saved rows, actual TP-relative
time origins, gap handling and despike repair counts are preserved. Original
overlays remain optional and use independent time facets. Full test retains its
Auto/Line/Min-max selector and envelope bands.

Line display performs DSP on every source sample, then uses the existing stride
reduction above 8000 samples. Brief extrema or missing runs can be omitted from
the displayed line. Full-resolution CSV remains unrounded and unreduced;
PNG captures the displayed lines. See [filter method](FILTER_METHOD.md).

## Verification

Passed from the repository root unless noted:

```powershell
# In frontend
npm.cmd run build
npm.cmd run lint

# Windows Python 3.13; skip the pre-existing inaccessible pytest cache
backend\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider
python scripts/verify_compact_plot_controls.py --frontend-port 3197 --backend-port 8197
python scripts/verify_filter_overlay.py --frontend-port 3198 --backend-port 8198
```

- Build/lint pass, with the existing bundle-size notice.
- **453 backend tests / 370 subtests pass** (46.37s); two existing dependency
  deprecations. Existing filter-overlay backend cases verify long line reduction
  after full-rate filtering, exact bounds/origins, gaps and API envelopes.
- Compact-controls browser suite: eight real CSV/PNG downloads, exact API replay,
  all plot modes, overlay, keyboard, zoom/pan, nine slots, resize/maximize/restore,
  actual 125%/150% browser zoom, failure/retry and reload. All 21 source fixture
  files unchanged, no page errors, owned servers/data/profile cleaned.
- Exported TP PNG and 150% desktop screenshot inspected: one filtered legend
  entry per TP, optional dashed originals, readable controls and plot layout.
- Independent review of the production diff found no blocking issues.
- Extended filter-overlay browser suite passes with 600- and 20,000-sample TPs:
  add Despike through the filter dialog, exactly one filtered series per TP,
  exact arrays/relative origins, optional original styles, independent plots,
  hide/reveal/reload, partial/all failures and Retry, obsolete-response rejection,
  client-side X/Y zoom and pan, keyboard, 1100px resize, nine slots,
  maximize/restore and actual 125%/150% zoom. Full-test Line and Min-max retain
  their correct arrays/bands, reject misalignment, and recover from failures.
  Delayed originals leave the current filtered data visible and labeled.
  Seven source files remain unchanged; no browser writes or unexpected errors.
- Final focused run also asserts the real automatic-envelope baseline and
  verifies the 20,000-sample Line response uses level 3 with at most 8000 points.
  All 57 browser filter requests and the complete suite pass after these checks.
- Inspected `tp-despike-single-lines.png` and the maximized 150% view: the long
  signal displays one waveform, without separate upper/lower curves. Final
  whitespace/diff review passes.

Screenshots and machine-readable evidence live under ignored `data/verification/filter-overlay`
and `data/verification/compact-plot-controls`.
