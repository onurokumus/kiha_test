# Implementation handoff

Updated: 2026-09-11. Branch `feature/resumable-multipart-upload`; inherited
baseline `873c80c`. This checkpoint collects the completed analysis, export,
metadata, session, component, plot and auto-split work for the user-requested
commit/push to `origin` (github.com/onurokumus/kiha_test). Generated `dummy_data/`
datasets remain local and are now ignored, consistent with `data/`.

## Current milestone / status

**User-requested multi-variable auto-split: complete and verified.**
This explicit request took priority over the independent Phase 11b redesign.

The implemented mode extends the original constant-value method: changing any
selected variable starts a new run. The optional question about numeric
conditions was unanswered; the existing-method assumption was stated before
implementation. No threshold, tolerance or steady-state detection is implied.

Acceptance passed: 1-9 distinct variables, clear zero/missing/minimum semantics,
read-only preview before Apply, explicit replacement of the unsaved draft,
normal Save/export, stale-request/draft guards, keyboard and desktop zoom,
and exact native row preservation through unchanged boundaries.

## Implemented / decisions

- New POST /api/tests/{name}/split/preview accepts structured JSON
  {columns,ignore_zero,min_len_s}; legacy single-variable /split/auto remains.
  Reads projected full-resolution source columns under existing native read
  locks, with busy checks. Any exact value change starts a contiguous tuple run;
  missing/nonfinite in any selected column breaks/excludes the row. Optional
  zero exclusion applies to any selected variable, after missing exclusion.
- Duration is native row count / fs_hz, independent of timestamp quantization.
  Source start/end timestamps and half-open native indices remain precise.
  Returns test_points, executed options, fs_hz, sample_count and disjoint
  missing/zero sample counts plus short-run count. Rejects >1000 points rather
  than silently truncating. Validates columns, minimum, sample rate and clocks.
- Inline AutoSplitPanel opens on demand from the compact toolbar. Searchable
  unique variable rows allow all numeric signals with sampled ID recommendations;
  minimum duration and exclusions are explicit. Per-test browser preferences
  retain rules. Preview table paginates at 50 rows and shows criterion-consistent
  durations. Use N test points stages the reviewed result; Save persists it.
- Rule/draft/source/loading changes and Close abort/invalidate preview results.
  Empty/error proposals cannot apply. Candidate discovery failure leaves manual
  variable selection usable, with Retry. No pre-preview replacement dialog.
- indexTestPoints preserves valid native boundary indices. patchTestPoint clears
  only the index of a time-edited edge; other edits leave native rows intact.
  Draft comparison includes native indices, and saved/draft CSV use the same rows.

## Entry points

- backend/app/split.py, split endpoints in main.py, backend/tests/test_split.py.
- frontend/src/components/split/AutoSplitPanel.{tsx,module.css}, SplitView.tsx.
- frontend/src/services/api.ts, types/index.ts, utils/testPointExport.ts,
  frontend/tests/testPointExport.test.mjs.
- scripts/verify_multi_variable_autosplit.py;
  docs/MULTI_VARIABLE_AUTOSPLIT_VERIFICATION.md.

## Verification

- Commit checkpoint rerun (2026-09-11): 436 backend tests and 13 frontend
  helper tests pass; frontend build/lint and staged whitespace checks pass.
  Browser evidence below is from milestone verification, not rerun for this
  commit-only request. Existing dependency/bundle-size warnings remain.
- Final backend Python 3.13 pytest: **436 passed, 354 subtests**. Two existing
  dependency deprecations. Frontend build/lint pass; existing bundle-size notice.
- 13 Node/TypeScript helper tests pass, including three native-index regressions.
- New native Chromium suite on 3340/8340: six groups, 16 preview requests, zero unexpected
  errors. Exact tuple/exclusion counts, read-only Preview/Apply, draft invalidation,
  Save/CSV parity, held late responses after rule change/Close, candidate failure/
  Retry, different schemas, keyboard, 1100px and actual 125%/150% zoom pass.
- Ten source files remain byte-identical. Screenshots visually reviewed; fixture,
  profile and owned-server cleanup completed. Existing Split exports on 3110/8110 and
  multi-plots on 3320/8320 pass against final changes. Independent review and scoped
  diff checks pass. No production regression found in final browser checks.

## Continuity / next steps

No work remains for this milestone. Next backlog item: Phase 11b collapsible
variable/filter side panel. Numeric condition-based auto-split is a separate
potential follow-up if requested.

Earlier Split multi-plots and XY time-axis work are both complete. The old
handoff's pending-Split statement was stale and is corrected. Preserve their
implementation and evidence in docs/SPLIT_MULTI_PLOTS_VERIFICATION.md and
XY_TIME_VERIFICATION.md, along with the other completed work in this checkpoint.

Preserve desktop-only scope, Python 3.13, atomic writes/read locks, original
samples and retention. Preview cancellation suppresses stale client results;
an in-flight native backend read may finish. Existing unrelated limits: cold
Windows TP-statistics cache race; comma-containing Time/Split display columns;
six-decimal Time preview values. Structured auto-split JSON avoids comma parsing.
