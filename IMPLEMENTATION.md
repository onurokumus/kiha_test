# Implementation handoff

Updated: 2026-09-11. Branch `feature/resumable-multipart-upload`; baseline
`db95fcd`. Initial working tree was clean. This checkpoint contains the completed
waterfall FFT milestone for the user-requested commit/push to `origin`
(github.com/onurokumus/kiha_test). Generated evidence stays in ignored
`data/verification/waterfall/`.

## Current milestone / status

**Waterfall FFT: complete and verified.**
Explicit user request takes priority over independent Phase 11b. Match the
attached 2D heatmap: X frequency, Y elapsed time, magnitude in color. User
clarified the PowerPoint is a style reference only. Entering Waterfall defaults
to one active-test/variable map; selected-source comparison is opt-in. The two
synthetic source maps shown during QA were isolated fixtures, never user data.
Ordinary FFT/Welch stays available. See docs/WATERFALL_FFT.md and
docs/WATERFALL_VERIFICATION.md.

## Implemented / decisions

- `backend/app/waterfall.py`: full-resolution stored data, exact saved TP or
  existing Full interval resolver. Mean-centered complete periodic-Hann FFT
  windows, peak-amplitude normalization, no padding/order tracking/time filters.
  Default L=1024/50% overlap; L=64..16384 and 0/25/50/75% overlap. Known gaps
  reject; isolated missing values interpolate by index with counts disclosed.
- Every window calculated in batches <=64. Grid <=256 time x 512 frequency
  cells retains maxima over explicit edges, with nominal elapsed centers and
  actual source center timestamps. No silent window changes or native-bin CSV
  claims. Same 8M-source-sample limit as Spectrum.
- `WaterfallPlot.tsx`/CSS: per-source facets, shared per-variable linear/log10
  scale, hover, linked two-axis zoom, wheel/Shift/Alt gestures, keyboard Home,
  maximize/restore, context menu, analysis details. Container-relative chart
  heights support desktop zoom. Failed sources remain explicit; stale/partial
  exports are blocked. Abort suppresses late client responses.
- Estimator selector adds Waterfall FFT. Window/overlap/mode/viewport session
  persistence and file validation are additive and compatible with old sessions.
  Existing Per rev state is preserved when switching, but waterfall uses Hz.
- `waterfall_export.py` reuses locked staging/progress/cancellation and metadata
  sidecars for single/multi grid CSV and PNG. CSV uses current stored values,
  linear magnitudes and intersecting cell bounds; PNG uses loaded values.
  All sources in a slot are captured; selected layouts support 2x2/3x3.

## Verification

- Final full backend: **447 passed, 360 subtests**; includes 11 new waterfall
  tests. Frontend build/lint and all 13 helper tests pass. Existing dependency
  deprecations and bundle-size notice only.
- Native waterfall suite passes: two sources, numerical exports/metadata,
  linked X/Y zoom, pan, reset, maximize/reload, options persistence, failure/
  Retry/held late responses, nine-slot CSV, selected2x2 PNG, Full interval,
  1100px and actual125%/150% zoom, no page errors; 14 source files unchanged.
  Final container-relative sizing and the corrected single-active-test default
  also pass. Final screenshots visually checked.
- Old Spectrum export script uses obsolete pre-compact-header Export button.
  Current compact-controls/menu regression suite passes completely: eight real
  Time/Full/Spectrum/XY CSV/PNG downloads, exact API replay, keyboard, nine slots,
  resize/maximize/125%/150%, no page errors, 21 source files unchanged. Its old
  scroll-into-view element handle raced canvas replacement; the verification
  script now uses locator hover (automatic re-resolution) before right-click.
- Dedicated prolonged waterfall Cancel browser case not exercised; shared
  cancellation is retained with batched calculation checkpoints.

## Next steps

No work remains for this feature milestone. TODO and verification notes are
complete; diff/whitespace review passes. No production sample data was edited.
The commit-only follow-up reuses the passing checks above; implementation has
not changed since verification. All owned browser/server processes and fixtures
are cleaned up. Next backlog milestone remains Phase 11b collapsible
variable/filter side panel. Preserve prior completed multi-variable auto-split,
Split multi-plots, XY time axes and their verification documents.
