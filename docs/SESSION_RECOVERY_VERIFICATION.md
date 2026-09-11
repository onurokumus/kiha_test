# Phase 8a — source-safe automatic session recovery

Completed 2026-09-10 on `feature/resumable-multipart-upload` (baseline
`873c80c`, existing dirty Phase 1–7d work preserved).

## Boundary and acceptance

Phase 7d was complete in the handoff and code. Phase 8 is split at an actual
dependency: name-only autosave could select a replacement dataset, while absent
variables could shift slots without their DSP settings. This milestone fixes
source identity and automatic recovery end to end. **Explicit save/reopen and
complete Spectrum/XY axis persistence remain Phase 8b.** The combined Phase 8
TODO remains unchecked.

## Contract

- `GET /api/analysis-sources` returns version 1 source snapshots. On the first
  analysis access, ready datasets gain `source_identity.json` with a version and
  UUID. No existing source/metadata file is rewritten by this migration. The
  separate file survives ingestion rebuilds and complete-folder lifecycle moves.
  It is a dataset ID; a trash entry UUID still identifies one deletion occurrence.
- Catalog migration takes catalog writer then per-test writer locks, rechecks
  readiness, and uses the existing atomic/fsync/Windows-retry JSON writer. It
  takes no native read slot. Already-busy tests expose existing identity without
  waiting on their long-running writer. Link/junction targets are not migrated.
- Invalid, unreadable or unwritable identity/snapshot files fail closed. Existing
  invalid IDs are never replaced. Duplicated IDs (for example external folder
  copies) cannot be automatically resolved to one active test.
- A sample revision hashes Parquet size/mtime-nanoseconds plus columns,
  time-column/fs/rows/start/duration and derived-variable provenance. A separate
  revision for each TP covers its authoritative half-open row bounds and stored
  times, using the existing bounds resolver. Names, findings, hardware bindings,
  annotation files and lazy statistics caches do not change sample revisions.
- Autosave keeps the existing `ptt.analysis-session.v1` key and version. The
  additive `sources` array distinguishes modern references (including explicit
  empty/null IDs) from legacy documents without the field. Only referenced
  current/selected/TP-filter sources are captured; samples are never serialized.
- Recovery resolves UUIDs before metadata/TP hydration and plot mounting. Rename
  and named restore remap current test, selections, TP filter keys and saved Time
  Y contexts. Same-name replacement, missing/trash sources and ambiguous identity
  are skipped with visible explanations. Guarded metadata and TP reads reject a
  name whose ID changed after bootstrap with 409; legacy API reads remain valid.
- Changed data/schema resets saved axis ranges; analysis uses current data. A TP
  whose ID now describes a changed interval is skipped. Hidden selections,
  filters, original overlays, notes visibility, layout and valid ranges survive.
  First-selected order survives delayed metadata; busy-source references retain
  their last known sample and TP revisions while pending.
- Unresolved references pause autosave and retain the original stored workspace.
  Retry uses that snapshot after a restore or repair; Continue accepts the
  recovered subset. A missing active test requires an explicit choice, instead
  of selecting an arbitrary replacement. An empty workspace can still open the
  first ready test normally.
- Legacy name-only sessions pause recovery and autosave. Users may explicitly
  reconnect by name (the UI states the identity cannot be verified) or discard
  just those references. Retry/reconnect use the existing navigation draft guard
  so canceling from Edit preserves unsaved notes.
- Saved variable slots stay positional. Missing Y/X variables render an
  unavailable card with visible selectors; no invalid plot/API/export is mounted
  for that slot. Neighboring filters and original-display settings do not move.
  Saved axes are not replaced while a relevant schema is still loading.
- Recovery notices are expandable; review-required notices open automatically.
  Their buttons/selects are keyboard accessible. With a notice visible, the
  analysis pane scrolls its controls and keeps a usable plot viewport at desktop
  zoom instead of shrinking controls over the plots.

## Verification

- `backend\.venv\Scripts\python.exe -m pytest backend/tests -q`:
  **406 passed /312 subcases**, including eight new source tests. Covers concurrent
  idempotent migration, existing-file hashes, rename/trash/named restore and
  byte-identical same-name reupload with a different ID, notes/components,
  independent TP changes, real schema rebuild, malformed/read-only/duplicate
  identities, busy nonblocking behavior and guarded metadata/TP hydration.
- Frontend `npm.cmd run build` and `npm.cmd run lint`: passed. Existing bundle-size
  and Starlette deprecation warnings remain non-failing.
- `python -X utf8 -u scripts/verify_session_recovery.py`: passed, real isolated
  backend 8220/frontend 3220, temporary Chromium profile. Twelve pure resolver
  cases run against the actual served TypeScript module. Real API/browser cases
  cover delayed first-source metadata, busy/reload, two-source hidden selection,
  filters/overlay/notes/ranges, rename, trash/replacement/named restore and Retry,
  changed TP bounds, canceled Edit draft discard, schema rebuild, unavailable
  variable correction, legacy reconnect/discard, catalog 503/Retry and duplicate
  IDs. Keyboard, maximize/restore, 1100px resizing, actual 125%/150% browser zoom,
  control clipping and hit-testing pass. The final 150% screenshot was reviewed.
- All 22 fixture source files are unchanged by ordinary recovery; the 11 beta
  files remain unchanged through the lifecycle/edit scenarios. Deliberate changes
  are confined to alpha/restored, replacement/duplicate fixtures and a temporary
  busy-status simulation restored byte-for-byte. No page errors; console errors
  are only the deliberately injected 503s. Lazy TP cache files are excluded from
  browser hashes, as in earlier milestones.
- `python -X utf8 -u scripts/verify_trash.py`: full existing lifecycle, actual
  Windows locked-file partial deletion/retry, snapshot Delete all, failure,
  keyboard/resize/zoom and empty-library reload/restore suite passed. Nine restored
  source/annotation/identity files and 20 active files are unchanged. Identity
  migration is completed before checksum baselines for new uploads in this fixture.
- `scripts/verify_time_y_zoom.py` and `scripts/verify_rendering.py` passed against
  Vite on 3231, owned by the existing `verify_data_quality.servers` context with
  isolated backend 8231. Their mocked catalogs now include deterministic source
  IDs; old mock gaps for existing annotations/components/trash endpoints were
  filled. Original geometry, zoom/pan/reset, saved ranges, layout/maximize,
  keyboard/reduced motion, tooltip and scatter-artifact assertions remain intact.
- `git diff --check`: passed. No commit, dependency change or subagent was used.

Ignored evidence is in `data/verification/session-recovery/`, `trash/`,
`time-y-zoom/`, `rendering/` and `session-plot-regressions/`. Owned server children
and temporary profiles/datasets were cleaned up. The generic skill server helper
was tried first; the final plot regression run uses the project's direct-child
server helper and temporary roots. Do not treat initial mock-handler failures as
application regressions.

## Limits and Phase 8b handoff

- Revisions detect ordinary local changes; they are not full sample-file hashes,
  immutable history or cross-machine reproducibility guarantees. External edits
  that preserve size/mtime and schema can escape detection. Keep the ID file when
  moving a dataset; manually copying it into another active test creates ambiguity.
- Identity guards currently cover recovery/bootstrap metadata and TP hydration.
  This is not continuous cross-tab synchronization or an ID-based rewrite of all
  DSP/export endpoints. Reload after external data or lifecycle changes. Existing
  loaded-reference snapshots are not advanced by background catalog refreshes;
  explicit invalidation refreshes them along with the data. Unverified new sources
  save null IDs, which are rejected on subsequent recovery.
- Catalog cost is proportional to library and TP metadata size, with no pagination
  or benchmark for very large libraries. Automatic browser storage remains subject
  to the existing localStorage quota/access behavior. Browser coverage is Windows
  Chromium only. Phase 8a did not add a named-session store or portable file.
- Phase 8b builds on these contracts with explicit file save/reopen and Spectrum/XY
  range persistence. See [saved-session verification](SAVED_SESSIONS_VERIFICATION.md)
  for the current file format, application path, coverage and limitations.
- Keep Phase 9 menus, Phase 10 component totals and Phase 11 polish pending.
  Trash retention is unchanged. Native calculations, export methods, component
  totals policy, cold TP cache race and comma-containing Time column limitations
  remain as documented by earlier milestones.
