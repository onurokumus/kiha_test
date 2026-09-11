# Implementation handoff

Updated: 2026-09-11. Branch `feature/resumable-multipart-upload`.

## Current milestone / acceptance

**Requested multiple component sets and temperature/power columns complete and
verified.** This request took priority over independent Phase 11b.

- Upload/Edit support up to 16 named propeller/motor/ESC sets with stable IDs.
  Each set has independent RPM and optional temperature/power mappings in Edit.
- Legacy single-set metadata remains readable without a migration write;
  canonical empty lists stay empty. Local/server-only upload resume is preserved.
- Statistics use each set's positive finite RPM exposure outside acquisition
  gaps. Optional signals have separate finite masks and observed-second weights;
  explicit C/F/K and W/kW units normalize summaries to C/W.
- Atomic revision checks, column rename/drop, test lifecycle and complete export
  provenance preserve all sets. Invalid saved configuration has explicit read
  errors and an intentional replacement path.

## Implementation / consequential decisions

- Canonical `component_sets` and `component_sets_revision`; absent key reads
  legacy fields as `legacy` / `Set 1`. Same hardware cannot appear twice in one
  test. First-set legacy fields remain read projections; legacy-only writes
  advance the canonical revision, and old writers are blocked after migration.
- Upload binds named hardware; columns are selected after schema ingestion.
  Temperature belongs to motor totals; the selected power signal describes set
  operation for all hardware types. No power-type inference or energy integration.
- New shared ComponentSetsPicker uses current values when asynchronous component
  creation finishes. UUIDs use getRandomValues for the deployed plain-HTTP origin.
- component_stats.py `component-usage-v2` retains bounded source caching with
  exact signal/unit context. Distinct sets legitimately share a dataset ID;
  duplicate folders do not. Invalid optional telemetry affects only that source's
  metric, preserving valid RPM and other source measurements.
- Safe frontend read helpers display malformed metadata without reviving legacy
  projections; notes-only saves preserve it. Valid guarded replacements repair
  it. Export provenance records invalid saved settings without blocking plots.
- Method/API: docs/COMPONENT_STATISTICS_METHOD.md. Detailed verification and
  browser harness notes: docs/COMPONENT_SETS_VERIFICATION.md.

## Verification

- Final full backend: **476 passed**, 49.48s. Python 3.13 environment; cache
  provider disabled for known ignored .pytest_cache permissions. Two existing
  dependency deprecations only.
- Frontend build and lint pass after final production edits; existing bundle-size
  notice only. Independent review findings resolved; whitespace check passes.
- New scripts/verify_component_sets.py (3196/8196): real two-set upload/local and
  server-only resume; independent native RPM/temperature/power oracles; missing
  coverage; F/kW conversions; delayed-create concurrent edits; save failure/retry,
  draft guards, conflict/reload, removal/recalculation, trash/restore and malformed
  settings read/notes/repair. randomUUID disabled to exercise plain-HTTP support.
  Keyboard, 1100px, actual 125%/150% browser zoom and screenshots verified.
  Eighteen source files unchanged; no page errors; owned servers/fixtures cleaned.
- Existing component browser suite (3200/8200) and statistics suite (3290/8290)
  updated to canonical controls/revisions and pass all original checks. Thirty-six
  and 24 source files unchanged, respectively; lifecycle/real data edit checks,
  failure/retry/unmount and desktop zoom pass. No page errors; cleanup passes.
- Evidence: ignored data/verification/component-sets, components and
  component-statistics. Browser script uses global Python for stdlib/Playwright
  only and project Python 3.13 in the isolated server process.

## Working tree / prior work

Initial tree already contained the completed despike fix in TimePlot.tsx,
scripts/verify_filter_overlay.py, docs/FILTER_METHOD.md, its verification report,
TODO.md and this handoff. Despike verification/limits are in
docs/DESPIKE_TRACE_VERIFICATION.md. The component sets checkpoint covers backend,
frontend, tests, browser scripts and documentation. The user requested committing
and pushing both verified changes on 2026-09-11, with separate despike and
component-set commits. Deployment remains separate.

## Next steps

This bounded feature is ready. Next backlog milestone remains Phase 11b,
collapsible variable/filter side panel. Prior Linux/UI production deployment
confirmation remains separate (docs/LINUX_CATALOG_FIX.md). No automatic
multi-client synchronization: reload metadata or refresh statistics after other
clients edit. No component-statistics export or historical lifetime ledger.
