# Phase 10 — component statistics verification

2026-09-10, branch `feature/resumable-multipart-upload`, baseline `873c80c`.
Phase 9 was complete in both handoff and actual code. All earlier uncommitted work
and dummy_data were preserved. No dependencies installed or commits created.

## Acceptance and behavior

User confirmed active-test totals: finite RPM > 0 is running; missing RPM and
timestamp gaps are excluded; trash/deletion removes contributions; restore adds
them back. Explicit test RPM selection supplies the shared shaft operating speed
for each associated propeller, motor and ESC. Full method, units, equations,
compatibility and invalidation details: [method](COMPONENT_STATISTICS_METHOD.md).

Components is visible in navigation without needing active test data. It shows
each individual component's runtime minutes, duration-weighted RPM mean/population
SD/min/max, operating-range minutes and per-test contributions/coverage. Native
search/type filters, component selection, source details, Edit settings and Refresh
are keyboard accessible. Unconfigured/incomplete sources remain distinguishable
from measured zero runtime. Unknown legacy gap history cannot become measured use.

Edit's RPM dropdown shares atomic metadata save, revision conflicts, reload,
failed-draft retry, reset and unsaved navigation guards. Save only sends changed
fields. Reference rename/drop follows actual data edits. Acquisition gap provenance
survives new imports, fill and trim without changing existing DSP gap semantics.
The 900 CSS-pixel header wrapping breakpoint keeps the added sixth tab, Import CSV
and Sessions visible in narrow desktop windows at browser zoom; this is not a
mobile/touch feature or a Phase 11 redesign.

## Automated verification

- `backend/.venv/Scripts/python.exe -m pytest backend/tests -q`: **421 passed,
  331 subcases**. Final focused component-statistics run: **15 passed, 19 subcases**.
- `npm.cmd run build` and `npm.cmd run lint` in frontend: pass; existing Vite
  bundle-size warning remains.
- `python -X utf8 -u scripts/verify_component_statistics.py --regressions`: pass,
  on owned 3290/8290 servers with isolated temporary data and Chromium profile.
- `python -X utf8 -u scripts/verify_components.py`: pass, owned 3200/8200 servers;
  all 36 source files unchanged through existing component upload/edit/recovery.
- `git diff --check`: pass.

Fifteen new backend tests cover independent mixed-rate mean/SD/min/max/runtime/
range oracles; null/NaN/infinity/nonpositive/no-RPM distinctions; true 131,103-row
native batching with a single-sample high-RPM extremum; batch-seam and overlapping
gap masks; missing-row versus timestamp-jump exclusions; no TP overlap duplication;
revision validation/no-op/concurrent saves and atomic disk-failure/conflict handling;
cache bounds/presence-sensitive history/context invalidation; source replacement;
association changes; rename/trash/restore/permanent deletion; trim/column rename/drop;
real import/interpolation preserving acquisition gaps; older fill-history rejection;
generated-time warnings; duplicate dataset identity; malformed metadata/references,
bad timestamps/extreme values/Parquet/registry and partial recovery; native-read
versus writer coordination; no source/identity-file writes on statistics reads.

The native browser suite uploads four initial sources at different sample rates.
Its independently calculated combined runtime is 10 seconds, mean 3000 RPM and
population SD 1788.8543819998317 RPM. DOM values and real API responses match these
oracles. It exercises explicit selection, navigation draft rejection, injected 503
save/retry, a real remote RPM revision conflict/reload, reassignment and reload.
Twenty-four source files are byte-identical before lifecycle/data edits.

It then verifies aggregate failure/retry, unsupported-response rejection and
aborted delivery after leaving the page; real trash/conflict-safe restore/permanent
deletion; exact restored file bytes; real trim/reference rename/drop/explicit repair;
and a fifth acquisition-gap fixture that remains excluded after interpolation.
Removing that fixture's gap history reproduces legacy exclusion; restoring the
history restores the contribution. Twenty-four remaining source files stay
unchanged through subsequent read/desktop checks.

Desktop checks include keyboard navigation/save/component selection, search/type
filters, scrollable/focusable tables, 1440px and 1100px windows and actual browser
125%/150% zoom (including 1100px at 150%). Header controls and table regions remain
inside the viewport. Screenshots at ordinary and narrow/zoomed sizes were inspected.
No page errors occurred. All owned servers stopped and temporary data/profiles were
removed. Evidence remains ignored under `data/verification/component-statistics/`.

Existing rendering and Time Y regression suites pass on the same isolated servers:
CSV alignment, hover wrapping/stacking, error bars, X/Y wheel/drag/pan/reset,
maximize/restore/reload, nine-slot layout/context persistence, keyboard focus,
reduced motion and actual 125%/150% browser zoom. Native processing uses the
project's Python 3.13; global Python is only the stdlib/Playwright harness.

## Limits and next milestone

These are measured current-library totals, not a lifetime ledger. RPM units must
be selected correctly by the user. Older filled data without gap provenance is
excluded until original CSV reimport; generated timing cannot recover unknown
source dropouts. Other windows require Refresh. Cold scans of very large libraries
and other browser engines were not exhaustively benchmarked; cache/read batching
is bounded, but a disconnected server scan can finish. No statistics export,
component history/rename/merge/delete or new retention behavior is included.

Existing cold Windows TP-statistics cache race and comma-containing Time column
limitation remain unrelated. Existing two dependency deprecations and Vite bundle
warning remain non-failing. Phase 10 and Phase 7's deferred recalculation clause
are complete. Next: **Phase 11a — shared plot headers/toolbar
polish**, preserving existing action handlers and desktop plot space.
