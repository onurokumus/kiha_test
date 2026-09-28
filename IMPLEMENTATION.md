# Implementation handoff

Updated: 2026-09-28. Branch `feature/resumable-multipart-upload`.

## Current milestone / acceptance

**User-requested scatter filter debugging is complete and verified.** Parameter
columns select by mouse without dismissing the drawer; test/TP, label and range
filters combine correctly, preserve edits and remain usable with keyboard,
desktop resize and browser zoom. This request takes priority over independent
Phase 11b. Prior Full test comparison remains complete; its evidence is retained
in `docs/FULL_TEST_VARIABLES_VERIFICATION.md`.

## Implementation and decisions

- `FilterControls.tsx` records owning React pointer events across the column-menu
  portal before document outside dismissal. It respects handled Escape events.
  Search-parent state matches the visible child action; Add expands Parameters;
  draft rows can be cleared; missing saved columns remain explicitly named.
- Valid numeric bounds commit immediately, preserving close/collapse edits and
  eliminating stale debounce work. Incomplete exponent input remains editable.
  Reversed bounds have an accessible warning and hook-level rejection.
- `useScatterFilter.ts` rejects invalid intervals before provisional unloaded
  statistics. Other combination, missing-column and pending-data behavior stays
  compatible. Backend calculations and aggregate precision are unchanged.
- Legacy `any` means min/max interval overlap, not exact individual-sample
  membership. UI now says Range overlap; serialized values remain compatible.
  `docs/MVP.md` and type/method comments match actual semantics.
- `App.tsx` clarifies that pending/failed statistics can leave parameter filters
  incomplete, retaining existing loading, error and Retry behavior.

## Verification

- Frontend build and lint pass; existing Vite bundle-size notice only.
- `node --test frontend/tests/*.test.mjs`: **40 pass**, including eight new hook tests.
- Native Python 3.13 backend suite: **497 tests / 477 subtests pass**, 49.27 seconds;
  two existing dependency deprecations.
- New `scripts/verify_scatter_filters.py`: **six groups pass**, zero browser errors:
  real mouse selection/search, test/label combinations, all modes, invalid bounds,
  numeric close paths, keyboard, persistence/missing columns and resize/125%/150% zoom.
- Existing `scripts/verify_scatter_point_menu.py` passes, including first outside
  click, keyboard, maximize/restore, resize and browser zoom.
- Independent source review, 150% screenshot inspection and whitespace checks pass.
  See `docs/SCATTER_FILTERS_VERIFICATION.md` for exact commands and limits. Browser
  tests use isolated GET-only API mocks and profiles; no user datasets are modified.

## Git checkpoint / next steps

This checkpoint includes all pending filter controls/CSS, scatter hook, App status
wording, type comment, eight hook tests, six-group browser script, MVP/TODO/handoff
and verification report at the user's request. Publication target is the existing
feature/resumable-multipart-upload branch of onurokumus/kiha_test. No deployment
is part of this request.
No outstanding verification blocker. Build output uses the repository's normal
ignored dist directory; browser evidence is under ignored data/verification.

Stop at this completed user-requested milestone. Next backlog remains **Phase 11b
(collapsible variable/filter side panel)**. Exact per-sample range membership would
require a separate backend capability; Range overlap intentionally preserves the
existing aggregate semantics.
