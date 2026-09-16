# Implementation handoff

Updated: 2026-09-16. Branch `feature/resumable-multipart-upload`.

## Current milestone / acceptance

**Requested Full test multi-variable comparison and resolution-label fix are
complete and verified.** This user-requested feature takes priority over the
independent Phase 11b side panel. Up to six variables share a plot; Edit plots
has searchable + addition and a compact color/count legend with removal.
Filters, saved sessions, exports and desktop interactions retain all variables.

## Implementation and decisions

- `FullTestVariables.tsx`/CSS adds the styled +, source-colored selectors and
  dropdown legend. Original overlays use lighter dashed strokes of each
  variable's hue; filtered traces are solid. Keyboard boundaries and last-add
  focus are preserved. Expanded plots retain the live numeric value legend.
- `PlotHeader.identityControl` places the Full test selector in normal flow.
  The raw/line/env resolution text sits underneath without absolute overlap.
  Other plot headers keep their existing behavior.
- `FullTestPlot.tsx` batches all columns in existing window/filter requests,
  checks every array and shared time/row/mode/level alignment, and uses ordered
  variables in request/export context guards. One shared Y axis retains native
  units, with no normalization or changed DSP. Filter settings apply to all
  variables. Errors fall back explicitly to originals with Retry.
- `App.tsx`, `TimeSeriesGrid.tsx`, `analysisSession.ts` and `sessionFiles.ts`
  persist nine independent `fullPlotExtraColumns` lists (max five extras).
  Legacy sessions default empty; browser recovery sanitizes, explicit files
  reject invalid entries. Temporarily missing variables remain dormant across
  source switches; other modes keep their existing single primary variable.
- `backend/app/plot_export.py` adds optional `columns` to legacy `column`:
  unique, primary first, max six, Full-test-only when multiple. Shared staged
  export reads original native values and filters all signals together before
  the existing crop. CSV and metadata include every variable. CSV/PNG and
  selected-plot bundles share mounted plot handlers; PNG records colors/styles.

## Verification

- Full backend: `backend/.venv/Scripts/python.exe -m pytest backend/tests
  -p no:cacheprovider -q`: **497 tests / 477 subtests pass**, 50.69 s, native
  Python 3.13.14. Two existing dependency deprecations.
- Frontend `npm.cmd run build`, `npm.cmd run lint`: pass (existing Vite
  bundle-size notice). `node --test tests/*.test.mjs`: **32 pass**, including
  six new session persistence/recovery cases.
- `python scripts/verify_full_test_variables.py`: **nine groups pass** on
  isolated 3353/8353. Real additions/removals, capacity and keyboard focus,
  missing-schema source switch/return, raw/envelope/filtered alignment/colors,
  native CSV/PNG/bundle downloads, actual session Save/Open/reload, mode switch,
  live expanded values, crosshair/drag/wheel/reset, filter failure/Retry,
  1100px and actual 125%/150% zoom. **13 source files unchanged**, zero page errors.
- Existing `python scripts/verify_filter_overlay.py --frontend-port 3354
  --backend-port 8354`: **ten groups pass unmodified**, seven sources unchanged,
  TP/Full legacy overlays, stale/failure/misalignment handling, nine-slot and
  desktop gesture/resize/maximize/zoom regression.
- Edit/legend/150% desktop/exported PNG images reviewed; independent code
  reviews and whitespace checks pass. See
  [full verification](docs/FULL_TEST_VARIABLES_VERIFICATION.md). Browser evidence
  is ignored under `data/verification/full-test-variables/` and
  `data/verification/filter-overlay/`. Early test harness selectors were
  corrected; no outstanding failure or verification blocker.

## Git checkpoint / next steps

The user requested a GitHub commit and push after verification. This checkpoint
contains the feature's frontend controls/rendering/state/session plumbing, time
CSV backend/type/tests, helper and browser tests, and TODO/handoff/verification
docs on `feature/resumable-multipart-upload`. Existing datasets and dependencies
are preserved. No deployment was requested. An inaccessible ignored pytest
cache can produce a harmless Git status warning.

Stop at this completed milestone. Next backlog remains **Phase 11b
(collapsible variable/filter side panel)**. Earlier CSV quoting, waterfall color,
detail and auto-split milestones remain complete; their evidence is linked from
TODO.md and the corresponding verification documents.
