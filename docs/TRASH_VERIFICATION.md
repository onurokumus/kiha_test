# Phase 7d — stable-ID trash and conflict-safe restore

Completed 2026-09-10 on `feature/resumable-multipart-upload`, baseline `873c80c`.
The existing dirty Phase 1–7c tree and user files were preserved. No dependency,
commit, deployment or subagent work was added.

## Scope and retention

Uploads now lists trash from the server across navigation and reload, including
tests deleted through Edit and pre-existing legacy trash folders. Repeated names
coexist with distinct UUIDs, deletion times and source context. Users can restore
under an available name, permanently delete an individual entry, or delete the
confirmed list. Notes, time annotations and component UUID references remain with
each stored dataset. Trash also remains usable when no active tests are left.

**The existing retention policy is preserved:** trash older than one hour is
eligible for permanent removal on the next active-test deletion. Listing or
reloading alone does not expire entries. The UI exposes this policy. An optional
preference question offered manual retention, but no change was requested; elapsed
time was not treated as authorization to change retention. `TRASH_MAX_AGE_S` is
still 3600; the implementation also handles None for explicit manual retention.
The separate incomplete-upload cancellation/stale-session policy is unchanged.

The two trash TODOs are complete. The earlier combined association/statistics
TODO remains unchecked for its Phase 10 statistics clause. No component runtime,
RPM totals or deleted-test contribution calculation was introduced.

## Storage, API and concurrency

- Each trash occurrence is `trash/<UUID>/entry.json` beside `data/`, which holds
  the complete original test folder. The wrapper records version, ID, original
  name, deletion timestamp, estimated legacy-time flag and lifecycle state.
  Metadata is written before the atomic source-directory move. New UUID folders
  must not exist; an older same-name test is never replaced.
- Legacy `trash/<name>/` folders are wrapped on list/legacy restore/expiry access.
  Their directory mtime supplies the estimated deletion timestamp, preserving the
  prior expiry clock. Inner files are not rewritten during migration. Subsequent
  lists retain the assigned UUID. Invalid or missing wrapper metadata keeps an
  identifiable unavailable entry when its data exists, rather than resetting it
  or permitting restore. Resolved paths and links/junctions are checked before
  structural changes; recursive permanent deletion stays within its entry.
- GET `/api/trash` returns entries and `retention_seconds`. DELETE
  `/api/tests/{name}` now returns `trash_id` as well as its existing fields.
  Receiving, ingesting and rebuilding tests reject soft deletion before waiting
  for a writer lock, then are rechecked inside it. Existing failed tests can move
  to trash. Soft deletion still waits for active readers and evicts the old lock
  while holding the catalog lock.
- POST `/api/trash/{id}/restore` takes `{name}`; omitted/null name uses the original.
  Names are 1–200 ASCII letters/digits/dots/underscores/hyphens, require an
  alphanumeric character, and reject reserved device names/trailing dots.
  Existing destinations return 409 before changing either dataset. Filesystem
  name semantics protect case-only conflicts on Windows as well.
- Restore parses all name-bearing JSON first, updates only `meta.name`,
  `testpoints.test` and `.upload/manifest.name` when necessary, then atomically
  publishes the folder under its destination. Other metadata, sample files,
  annotation IDs/times/text, component bindings/revisions and upload receipts are
  preserved. Name-bearing documents roll back on ordinary write/move failure;
  malformed documents are retained and explicitly rejected. A retry rewrites all
  required names. Successful restore invalidates the destination's client cache.
- Legacy POST `/api/tests/{name}/restore` remains compatible when exactly one
  copy matches; ambiguous names return 409 and require a UUID. The new UI always
  uses UUIDs. A stale/missing restore ID returns 404, never another same-name copy.
- DELETE `/api/trash/{id}` removes one entry; DELETE `/api/trash` takes explicit
  `{ids:[...]}` and returns `deleted_ids` plus per-ID failures. Duplicate IDs are
  deduplicated, already-absent IDs are safe deletion replays, and a batch is limited
  to 10,000 supplied IDs. The client captures the snapshot before confirmation;
  entries created while the dialog is open are excluded.
- Permanent deletion atomically records `state: deleting` before removing data.
  A locked file/disk failure therefore cannot leave a partially removed dataset
  offered for restore. Retry finishes removal; successful entries are reported
  separately from failures. Reusable component registry records and active tests
  are not removed by permanent trash deletion.
- All trash operations use the existing catalog writer lock, including migration,
  listing, restore and permanent deletion. Active source/destination operations
  also use test locks. No native read slot is acquired. Preserve the single-process
  backend and test-read-lock-before-native-slot rules.

## Desktop UI

The compact Trash control expands a server-backed list with deletion date/time,
short ID (full ID in title), source/uploader, description and assigned components.
Soft deletion expands it immediately. Refresh trash recovers read errors and
discovers changes from another client; view mounting also refreshes it.

Restore opens an inline named form with keyboard focus and validation. Conflicts
and failed requests retain the proposed name. Permanent single/all deletion uses
the existing keyboard-accessible confirmation dialog and explains irreversibility.
Partial results show success counts and an actionable retry; damaged entries have
Restore disabled. Errors avoid displaying internal filesystem paths. Controls
disable during requests, and focus returns to the trash control after completion.

## Verification

| Command | Result |
| --- | --- |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests -q` | 398 passed, 312 subcases |
| `npm.cmd run build` in `frontend` | Passed |
| `npm.cmd run lint` in `frontend` | Passed |
| `python -X utf8 -u scripts/verify_trash.py` | Passed |
| `python -X utf8 -u scripts/verify_components.py` | Passed |
| `git diff --check` | Passed |

Fourteen new backend tests /11 subcases cover repeated names, UUID persistence,
legacy migration/time preservation, real ingestion and restored upload recovery,
notes/annotations/components and name rewrites, snapshot batch deletion, concurrent
restore/delete, readers, busy/invalid/reserved names, corrupt/missing metadata,
bookkeeping/metadata/move failures, partial deletion/retry and expiry behavior.
Existing lifecycle/upload tests now assert UUID storage rather than obsolete
name-based paths; their behavioral requirements remain intact.

The real Chromium suite uses isolated CSV uploads, including repeated names with
different data. It verifies reload and legacy migration, Uploads/Edit deletion,
503 read/restore/delete retry, real 409 name conflict, named restore, canceled
confirmations, single deletion and snapshot Delete all. A real open Windows CSV
handle forces a partial filesystem deletion; the UI disables restore, retains the
failed entry and completes a later retry. A newly trashed test created during
confirmation survives that batch. Deleting every active test, reloading the empty
library and restoring all three original names also succeeds.

Desktop geometry and keyboard checks cover 1440px/1100px windows and actual
125%/150% Chromium zoom, long restore names, focus and scroll access. The 150%
restore form and partial-deletion screenshots were visually reviewed. Eight
restored sample/annotation/status/receipt files and 18 files in active datasets
remain byte-identical; name-bearing JSON changes are verified separately.
`tp_stats.json` is excluded from browser checksums because ordinary analysis
loading can populate that pre-existing lazy cache. Backend move/failure checks
compare all fixture files. The reusable registry is byte-identical throughout.

The existing component suite also passes: real local/server-only upload resume,
batch selection, legacy reassignment/clear, conflicts/failures/reload and desktop
zoom/keyboard; its 36 monitored source files remain unchanged. There are no page
errors. Trash console errors are only the deliberate 503s and expected 409.
Owned 3210/8210 and 3200/8200 servers stopped and temporary fixtures/profiles were
removed. Ignored evidence is under `data/verification/trash/` and
`data/verification/components/`. Existing bundle-size/Starlette deprecation and
Git CRLF/configuration-permission warnings remain non-failing.

## Limits and next milestone

- Retention remains one hour on the next test deletion. The new trash is not an
  indefinite backup. Large trash libraries and the 10,000-ID batch boundary are
  not performance-benchmarked; no pagination, progress or cancellation is added.
- Structural operations are serialized in one server process. Restore handles
  normal I/O failures; power loss during multi-file name rewriting is not a fully
  journaled transaction. Retrying a retained trash copy reapplies required names.
  Empty bookkeeping from interrupted preparation/cleanup may remain on disk;
  stored wrappers without a dataset are omitted from the list. A lost successful
  restore response can require list refresh instead of an idempotent restore replay.
- Trash UUIDs identify deletion occurrences, not permanent identities of active
  tests. Component and annotation UUIDs are preserved, but automatic analysis
  sessions still have their existing name-based references. No active dataset
  identity migration or automatic session remapping is claimed here.
- Chromium on Windows was tested; Firefox/Safari and native Linux filesystem
  behavior were not. Receiving-upload cancellation remains a separate operation.
- Next: **Phase 8 — explicit save/reopen of analysis sessions**. Audit current
  autosave before adding controls; cover source/TP selections, variables, filters,
  layout, axes, original overlays and annotation visibility. Handle deleted,
  restored, renamed and replaced same-name sources without silently choosing a
  different dataset. Phase 10 statistics remain pending.
