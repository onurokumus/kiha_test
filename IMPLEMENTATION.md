# Implementation handoff

Updated: 2026-09-14. Branch `feature/resumable-multipart-upload`.

## Current milestone / acceptance

**Requested CSV test-point ID quoting fix is complete and verified.** This independent
user-requested fix takes priority over Phase 11b. Acceptance: generated
`test_point_id` values export as plain integers across Uploads/Split, time,
Spectrum, XY and waterfall CSVs, with exact digits and unchanged sample values,
headers, source-column collision handling, blank full-test IDs and text escaping.

## Implementation

- Shared `backend/app/csv_values.py:test_point_id_scalar` uses int64 and exact
  scale-zero decimal256 for IDs up to 76 digits. IDs beyond Arrow's numeric
  capacity retain the previous quoted text representation to preserve every
  digit; no floating-point conversion, CSV postprocessing or quote disabling.
- All five export paths use the helper; CSVs inside bundles share these paths.
  Waterfall also now accepts IDs above int64 without overflow. No frontend,
  scientific-method, source-data or persistence changes for this fix.
- Extended endpoint tests check actual unquoted CSV tokens for saved/draft,
  multibatch, cross-test, zero/negative/int64-boundary and oversized IDs. The
  >76-digit fallback remains lossless; parsed values and bundle checks remain.

## Current verification

- Focused export tests: 108 passed / 134 subtests using native Python 3.13.
- Full backend suite: `backend/.venv/Scripts/python.exe -m pytest backend/tests
  -p no:cacheprovider -q`: 490 passed / 445 subtests, 44.68s; two existing
  dependency deprecations. Frontend `npm run build` / `npm run lint` pass
  (existing bundle-size notice). Independent scoped review found no issues.
  No plot/UI changes, so no new
  browser interaction checks are needed for this serialization-only fix.
- Commit checkpoint recheck: 490 backend tests / 445 subtests pass (45.54s),
  frontend build/lint and all 26 helper tests pass. Independent review and
  whitespace checks pass; existing dependency and bundle-size notices remain.

## Previous waterfall color milestone (complete)

- WaterfallPlot has an accessible compact form. Apply/Enter commits both limits;
  invalid drafts retain the current display. Auto clears only the active mode.
  Draft input survives grid refinement; commits retain keyboard focus.
- Nine session slots retain exact variable identity and independent linear/log
  limits. Source and FFT settings do not reset manual limits. Another variable
  never inherits an unrelated range. Legacy sessions remain Auto; malformed
  explicit files reject, while browser recovery normalizes invalid entries.
- Color changes redraw the existing grid without another FFT request. All maps
  in a slot share the range; out-of-range values use endpoint colors. Log inputs
  are log10(U), with zero at the bottom color. Hover/CSV stay linear and unclipped.
- Color ticks increase precision for narrow ranges, omit unrepresentable
  midpoints and reserve measured label space. PNG text retains exact manual
  limits, and metadata includes range, transform and manual/Auto mode.
- Entry points: frontend/src/components/plots/WaterfallPlot.tsx and CSS,
  TimeSeriesGrid.tsx, App.tsx, utils/waterfallColorRange.ts,
  services/analysisSession.ts/sessionFiles.ts. Method/user guide:
  docs/WATERFALL_FFT.md. No backend or scientific calculation changes.

## Previous waterfall verification

- `backend/.venv/Scripts/python.exe -m pytest backend/tests -p no:cacheprovider`:
  489 passed, 48.95s, Python 3.13.14; two existing dependency deprecations.
- Frontend `npm run build`, `npm run lint`, `node --test tests/*.test.mjs`:
  pass; 26 helper tests, eight new color validation/session/label cases.
  Existing bundle-size notice only. Independent code review and diff checks pass.
- New `scripts/verify_waterfall_color.py` uses isolated ports 3352/8352,
  temporary uploaded sources/profile and native Python 3.13 backend. All nine
  groups pass: validation, shared/clamped bitmap, no color FFT requests,
  viewport/keyboard/maximize, separate modes/slots, actual session Save/Open,
  duplicate-variable slots, CSV parity, PNG limits, narrow-range labels/PNG,
  details-close to keyboard Export, and 1100px/125%/150% desktop checks.
  Five export packages, 14 unchanged fixture source files, no browser errors.
  See docs/WATERFALL_COLOR_VERIFICATION.md for commands and evidence.
- Narrow-range, 150% desktop and log PNG screenshots reviewed: readable bounds,
  distinct ticks, correct source colors and no overlapping controls.
- Existing `scripts/verify_waterfall_detail.py` (3351/8351) passes all eight
  groups unmodified on stable final source: grid refinement/legacy settings,
  errors/Retry, keyboard/axes, real exports and desktop zoom. Earlier runs
  transiently dismissed a menu after closing Analysis details. The exact
  hover/details/export sequence, 30 repeated diagnostic cycles and the final
  full regression all pass; the cause was not reproduced. Details-close focus
  uses preventScroll; no broader menu change was made.
- Evidence: ignored `data/verification/waterfall-color/` and
  `data/verification/waterfall-detail/`. Browser tools use global Python only for
  stdlib/Playwright; native calculations always use the repository 3.13 runtime.

## Git checkpoint / prior work

The requested checkpoint on `feature/resumable-multipart-upload` includes the
waterfall color controls, state/session plumbing, tests and docs, plus the shared
CSV scalar helper, five exporter edits and regression assertions. The previous
waterfall detail and constant-interval auto-split work is in 057ad64. Existing
user datasets are preserved and excluded from Git.

Prior waterfall detail and auto-split acceptance/evidence remain documented in
docs/WATERFALL_DETAIL_VERIFICATION.md and
docs/MULTI_VARIABLE_AUTOSPLIT_VERIFICATION.md.

## Next steps

Stop at this completed milestone boundary. No remaining verification blocker.
Next backlog remains Phase 11b (collapsible variable/filter side panel).
No deployment or existing-data rewrite is part of this request.
