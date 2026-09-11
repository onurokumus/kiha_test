# Persistent time annotations — Phase 7b

Verified 2026-09-10 on Windows, Python3.13 backend and isolated desktop Chromium.

## Storage and time identity

- Each test supports200 annotations with stable UUIDs and nonblank plain text
  up to2,000 Unicode code points each. Times are finite, nonnegative seconds;
  interval end exceeds start. Text shares test-note validation and newline
  normalization. HTML-looking text is literal.
- `GET /api/tests/{name}/annotations` returns version, test name, revision,
  current data bounds and items. Legacy tests return empty revision0 without a
  write. `PUT` replaces the list with `expected_revision` and
  `expected_data_bounds`. Test writer locks and atomic JSON replacement serialize
  saves. Stale/busy/corrupt-file cases fail explicitly; unchanged lists are no-ops.
  Annotation reads take test locks without native reader slots.
- `tests/<name>/annotations.json` stores format version1,
  `time_basis: stored_elapsed_seconds`, revision and items, without embedding the
  test name. It stays separate from scientific data, metadata and upload identity.
- Full-test plots use stored seconds. TP plots subtract the exact loaded
  `analysis.source_centers.first_time_s`, never the rounded TP descriptor,
  `time_origin_s` or a sample-rate estimate. Legacy trace responses lacking
  source centers do not offer a falsely positioned annotation source.
- New/moved notes must fit current `t_start` through `t_start + duration_s`.
  Trims retain original annotation timestamps. Outside/partly outside notes are
  flagged and remain readable, text-editable and deletable. Rebuild, rename and
  existing trash/restore carry the file unchanged. Future stable-ID/conflict-safe
  trash behavior is not implemented by this milestone.

## UI and exports

- Compact Notes dialog on each Time/Full test plot: source selection, manual
  marker/interval times, current-view center/interval shortcuts, complete saved
  note text, CRUD, failed-draft retry and unsaved source/reload/close guards.
  Delete has an inline confirmation; conflicts retain the draft until explicit
  reload. Native modal focus trapping/restoration and before-unload protection
  prevent accidental loss during edits/writes.
- Preview labels use12 significant digits. Editor values, drawing and metadata
  retain full precision. Canvas dashed lines, subtle interval shading and short
  A1/A2 tags follow zoom/pan/filter display. Three collision-avoiding tag lanes
  suppress crowded labels while keeping lines and the full text in the dialog.
- TP source colors distinguish repeated tags. Hidden/unplotted sources and hidden
  legend series do not draw notes. Projection clips to current data, loaded TP
  and visible X range without inventing boundaries at clipped edges.
- Shared Show time notes and the dialog checkbox use the same global state,
  persisted by automatic analysis sessions; legacy sessions default to visible.
  Phase8 explicit session management and Phase9 context menus remain future.
- Single/multi PNG captures contain visible canvas annotations and full note
  text. PNG sidecars snapshot UUID/test/source/stored times/text/tag/color and
  projected positions. Hidden notes are omitted with visibility recorded.
  Loading/failed annotation reads gate PNG until reload or explicit hide.
  Existing numerical CSV content is unchanged.

## Checks and evidence

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
# in frontend
npm.cmd run build
npm.cmd run lint
# from root; global Python runs only stdlib/Playwright
python -X utf8 -u scripts/verify_annotations.py
python -X utf8 -u scripts/verify_plot_exports.py
git diff --check
```

- **376 backend tests /285 subcases passed**, including six new tests /21
  subcases: legacy no-write reads, CRUD/no-op, source preservation, concurrent
  writers/stale revisions, changed trim bounds, outside-data edits, schema/
  rename/current trash-restore, text/time/UUID/interval validation, duplicate IDs,
  200/201-item and2,000-character limits, busy/missing tests, disk failure/retry
  and corrupt-file protection. Build/lint and diff whitespace checks passed.
- **Real-data annotation browser suite passed.** Three isolated uploads;
  source-specific CRUD, keyboard/input validation/Unicode, injected503 save/read
  failures, actual409 conflict, retry/reload/unsaved guards. Native canvas drawing
  is observed and compared with `uPlot.valToPos`: actual TP origin is
  `0.10000000000002274` s although its descriptor says `0.075` s. Full, relative,
  filtered, legend hiding, saved visibility, local wheel/middle-pan and reset
  are covered. Five real PNG ZIPs cover TP, filtered TP, full, combined and hidden
  annotations; native decoding, rendered text and sidecar snapshots checked.
- Desktop1440/1100px,125%/150%, maximize/restore, modal centering/bounds and focus
  checked. PNG and150% dialog visually reviewed. The global margin reset initially
  prevented native centering; explicit `margin:auto` resolves it.
- All **21 monitored raw/Parquet/pyramid/testpoint/manifest files** and unrelated
  metadata remained unchanged during annotation operations. A separate deliberate
  fixture trim verified byte-identical note-file retention, clipped intervals,
  outside-data labels and text correction without another source rewrite.
  No page errors; only deliberate503/409 console errors. No unexpected writes.
- **Existing native Time export suite passed**: numerical original/filtered/both
  CSV oracles, sample clipping/Y independence, hidden sources, independent
  filters, pending/failure/retry, Full Line/envelope CSV/PNG, nine-slot desktop
  controls,1100px/125%/150% and keyboard/maximize/restore regression checks.
- Temporary fixtures/profile removed; only owned3190/8190 and3130/8130 servers
  stopped. Evidence ignored under `data/verification/annotations/` and
  `data/verification/plot-exports/`. Existing non-failing bundle-size, Starlette
  deprecation and Git CRLF/config-permission warnings remain. No dependencies added.

## Limits and continuity

- No authorship/history/live synchronization/automatic conflict merge. Refresh
  via Notes. Spectrum/XY notes and context-menu creation are out of scope.
- Dense notes can hit existing PNG text/dimension/package limits; export fails
  explicitly. Hide notes or narrow the view when needed. Max item count is API
  tested; worst-case rendering/resource use is unbenchmarked. Chromium desktop
  tested; Firefox/Safari untested.
- Tests still use folder/name identity. Future stable-ID trash work must carry
  `annotations.json` and preserve annotation UUIDs.
- Browser harness must select sources/series by identity, not asynchronous
  selection-restoration order. Use combobox/textbox roles for wrapped labels and
  wait for native redraw before observing canvas calls. Public trim retains at
  least1 second. Fixture TP stats caches are warmed serially to avoid the
  pre-existing Windows cold-cache replacement race.

Next: Phase7c component registry and upload/existing-test propeller/motor/ESC
associations. Preserve statistics invalidation requirements; runtime/RPM totals
and deleted-test contribution policy belong to Phase10.
