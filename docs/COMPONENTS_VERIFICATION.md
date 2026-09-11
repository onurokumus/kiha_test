# Phase 7c — component registry and test associations

Completed 2026-09-10 on `feature/resumable-multipart-upload` (baseline `873c80c`).
Existing uncommitted Phase 1–7b work and user files were preserved. No dependencies
were added and no commit was created.

## Scope and acceptance

Users can select an existing propeller, electric motor and ESC, create a reusable
component of each type during upload, and assign/correct/clear those associations
on existing tests in Edit. Selection survives batch upload, local resume,
server-only recovery and ingestion failure. Failed metadata saves retain drafts;
stale association writes require explicit reload. Existing test text, annotations
and scientific data are preserved.

The upload-selector TODO is complete. The combined correction/statistics TODO
remains unchecked: its correction behavior is implemented, but runtime/RPM totals
and caches do not exist. Phase 7 explicitly requires deletion policy before
totals, and Phase 10 defines running samples, missing RPM and deleted-test
contributions. An association revision supports future invalidation; it is not
statistics calculation. The next dependency-ready milestone is the trash lifecycle.

## Persistence and API contract

- The shared `components.json`, beside the tests directory, contains version 1
  and a list of typed UUID records with names and creation timestamps. Back up
  this registry together with test folders. GET `/api/components` on legacy data
  returns an empty list without writing a file. Invalid existing registry content
  returns 409 and is never silently replaced.
- POST `/api/components` creates a propeller, motor or ESC. Names use Unicode NFC,
  trimmed outer whitespace and 1–120 code points, with control/invalid-Unicode
  validation. Names are single-line without embedded tabs. Matching normalized,
  case-insensitive names within the same type reuse the existing UUID, including
  concurrent creation and lost-response retries. Different types remain distinct.
  Limits are 1,000 records per type and an 8 MiB registry read bound.
- Registry writes use atomic JSON replacement under an independent lock. Registry
  operations never acquire test/catalog locks. Test writers may acquire a registry
  read lock for validation; keep that direction one-way. The existing single-process
  server requirement and test-read-lock-before-native-slot rule remain.
- Test `meta.json` stores optional `components` with `propeller`, `motor`, `esc`
  UUID-or-null entries, plus `components_revision`. Legacy absent bindings mean
  unassigned and revision zero. Free-form `user_meta` fields are not migrated or
  reinterpreted, even if named motor or components.
- Upload initialization accepts these bindings, validates IDs/types before name
  reservation, and records canonical IDs as immutable session identity. Receiving,
  ingestion and error status retain them. Repeating initialization with changed
  bindings returns 409. Missing legacy manifest/client/browser fields mean empty
  bindings. Existing valid sessions can resume even if the registry is unavailable;
  later edits never change their original upload manifest.
- PATCH `/api/tests/{name}/meta` changes only supplied top-level fields. A supplied
  `components` object replaces the complete binding map; omitted kinds within it
  become null. It requires `expected_components_revision`. Changed bindings and
  the incremented revision commit atomically in the same metadata write. No-op
  bindings leave the revision unchanged. Stale/missing revisions return 409, invalid
  references return 422, and neither can partially apply accompanying text changes.
- Test-list responses prefer current metadata over initial upload status. Rebuild,
  trim, rename and existing trash/restore retain IDs and revisions. Missing registry
  references remain visible as unavailable IDs rather than being silently cleared.
- Shared analysis provenance snapshots `component_ids` and
  `component_association_revision` from the loaded/executed source metadata, without
  a registry reread. Legacy missing records are null. Numerical CSV content is
  unchanged; component names and complete assignment history are not exported.

## UI and continuity

- Shared keyboard-accessible native selectors offer Unassigned, existing items
  and Add new. Creation validates names, retains failed drafts, supports retry and
  restores focus. Adding immediately saves a reusable registry record; the UI
  explains this before creation. Binding it to a test remains a separate upload/save.
- One binding map applies to an upload batch. The next batch starts unassigned.
  Upload history shows a compact association link using the existing Edit handler;
  full names are available in its title, and search includes component names.
- Edit includes assignments in dirty/reset/navigation guards and submits only
  changed fields. Unfinished component creation blocks upload/metadata save until
  completed or canceled. Failed saves retain drafts. Reload saved metadata asks
  before discarding unsaved edits and restores the authoritative metadata/revision.
- Catalog reads have error/retry states; missing bound IDs are explicitly flagged.
  Catalog state is refreshed on view mounting, not live-synchronized across clients.

## Verification

| Command | Result |
| --- | --- |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests -q` | 384 passed, 301 subcases |
| `npm.cmd run build` in `frontend` | Passed |
| `npm.cmd run lint` in `frontend` | Passed |
| `python -X utf8 -u scripts/verify_components.py` | Passed |
| `python -X utf8 -u scripts/verify_test_notes.py` | Passed |
| `git diff --check` | Passed |

Eight new backend tests /16 subcases in `backend/tests/test_components.py` cover
normalization/type identity, concurrent idempotence, full-length emoji names,
validation/limits/corrupt registry/disk failure, real upload and failed ingestion,
immutable/legacy resume, invalid-reference name reservation, partial atomic saves,
no-ops/stale/concurrent revisions, busy/failure retry, rebuild/trim/rename/current
trash-restore, missing registry IDs and explicit clearing. An actual plot-export
ZIP verifies current IDs/revision in its sidecar; an earlier loaded source snapshot
retains its earlier associations. Registry capacity is tested with a reduced limit,
not a 1,000-item performance benchmark. The full existing backend suite also passes.

The component browser harness uses six isolated tests and real CSV transfers,
durable chunks, ingestion and metadata requests. It checks existing/new selection,
creation failure/retry, local-record and server-only resume, duplicate-name reuse,
two-file batch bindings, blank later setup, legacy assignment/correction/clear,
failed saves, a real remote 409 conflict, explicit reload and navigation guards.
Missing registry references and list-read failure/retry are tested separately.

Desktop checks cover 1440px and 1100px windows, actual Chromium 125%/150% zoom,
keyboard focus, long new-component names and compact history summaries. Edit,
history and upload setup screenshots at 150% were visually reviewed. All 36
monitored raw/Parquet/pyramid/test-point/upload-manifest files remain unchanged;
unrelated metadata and existing legacy annotations are byte-identical. No page
errors or unexpected writes occurred; console errors were only induced 503s and
the expected conflict. Evidence is ignored at `data/verification/components/`.
Owned 3200/8200 servers stopped and temporary fixtures/profile were removed.

The existing notes browser suite also passes after integration, including real
batch/local/server-only resume, edit/failure/retry/reset/clear/legacy/keyboard and
1100px/125%/150% checks. It independently preserves 36 source files. Its owned
3180/8180 servers stopped and temporary fixtures/profile were removed.

## Limits and next milestone

- No component rename, deletion, merge, authorship/history, automatic conflict
  merge or live catalog synchronization. Registry records survive test deletion
  and unassignment, including records created before an upload is canceled.
- No runtime/RPM totals or cache exists yet. Future statistics must account for
  current associations, sample/source changes and the defined deletion policy;
  association revision alone is not a complete statistics cache key.
- Chromium desktop was tested; Firefox/Safari and maximum registry performance
  were not. Existing non-failing bundle-size/Starlette deprecation and Git
  CRLF/configuration-permission warnings remain.
- Existing name-based trash and limited retention are unchanged. Next:
  **Phase 7d — trash bin with stable deleted-test IDs, individual/all permanent
  deletion and conflict-safe restore**, retaining notes, annotations and component
  IDs. Audit current purge and name-conflict behavior before replacing it. Preserve
  the unfinished statistics clause for Phase 10.
