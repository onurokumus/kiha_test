# Implementation handoff

Updated: 2026-09-11. Branch `feature/resumable-multipart-upload`.

## Current milestone / acceptance

**Requested high-detail waterfall is complete and verified.** This user request
takes priority over independent Phase 11b. Acceptance passed: preserve 0–200 Hz
native bins; offer 0.5/0.25/0.1 Hz choices; refine frequency/time viewports without
moving FFT frames; persist settings and export the same grids; retain legacy
manual settings/axes; verify nearby tones, errors and desktop interactions.

## Findings / implementation

- V1 compressed the whole band before zoom. V2 selects visible native bins and
  original anchored frames first, then bounds the grid to 512 × 2048 cells.
  Same-context old grids remain visible while debounced refinement loads;
  exports wait for the matching grid. Empty viewports retain reset domains.
- Fresh default is 0–200 Hz / 0.25 Hz / 75% overlap. 0.5/0.25/0.1 Hz need real
  2/4/10-second windows, rounded up to even sample counts. Short sources fail
  explicitly; no silent downgrade, zero padding or smoothing. Large windows
  use bounded sample batches. Native spacing and displayed cell size are shown.
- Legacy API/export calls keep exact v1 behavior. Old sessions retain manual
  window, overlap, full band and old viewport context. Preview/CSV v2 requests
  share detail parameters; exported nperseg is the executed per-source value.
- Entry points: backend/app/waterfall.py, waterfall_export.py, main.py;
  frontend WaterfallPlot.tsx, SelectedPointsPanel.tsx, TimeSeriesGrid.tsx,
  constants/waterfall.ts, analysisSession.ts/sessionFiles.ts and api/types.
- Scientific method, limits and API contract: docs/WATERFALL_FFT.md. Longer
  windows blur rapid changes; bin spacing is not two-tone resolving power.

## Verification

- backend/.venv/Scripts/python.exe -m pytest backend/tests -p no:cacheprovider:
  489 passed, 50.85s, Python 3.13.14. Focused waterfall: 19 tests/18 subtests.
  Two existing dependency deprecations only.
- Frontend npm run build/lint and node --test tests/*.test.mjs: pass, including
  18 helper tests (5 new session tests). Existing bundle-size notice only.
- New native Chromium suite: all 8 groups pass, close 84/85 Hz tones, 0.1 Hz,
  both-axis refinement, old manual axes, short/partial/late failures and Retry,
  empty-view export blocking/Home recovery, six actual export packages with
  five CSV entries/two PNGs, exact displayed-cell parity. 1100px, maximize,
  keyboard and actual 125%/150% zoom pass. 14 source files unchanged; no browser
  errors. Screenshots reviewed. Owned fixtures/profile/servers cleaned.
- Existing scripts/verify_waterfall.py passes unmodified: two sources, nine
  slots, CSV/PNG, source intervals, FFT/Welch and desktop regressions; another
  14 fixture files unchanged. Independent code review and diff whitespace pass.
- Commands/results: docs/WATERFALL_DETAIL_VERIFICATION.md. New script
  scripts/verify_waterfall_detail.py uses isolated ports 3351/8351; existing
  suite uses 3350/8350. Browser runner uses global Python/Playwright only;
  native backend always uses 3.13. Evidence is in ignored data/verification/.
- No product failures/blockers. Harness corrections preserve source identity
  when reseeding, use normalized uploaded timestamps and observe bundle routes.

## Working tree / prior work

Waterfall work began at 22329ac with the verified auto-split correction already
uncommitted (split backend/tests/UI/verifier plus TODO, handoff and types). Keep
that work intact. Current additional changes cover waterfall backend/API/export,
plot/control/session plumbing, tests and docs. Despike and component-set work are
committed (16e28aa and 22329ac).

The prior auto-split correction is also complete: at least two equal consecutive
tuples are required independently of minimum duration. Its original verification
and entry points remain in docs/MULTI_VARIABLE_AUTOSPLIT_VERIFICATION.md; all
25 split tests still pass in the current full suite. No prior changes were reverted.

## Next steps

This checkpoint includes the completed waterfall milestone and prior auto-split
correction. Stop at this boundary.
Next backlog remains Phase 11b (collapsible variable/filter side panel).
Production deployment is separate; no deployment or existing-data rewrite is
part of this request.
