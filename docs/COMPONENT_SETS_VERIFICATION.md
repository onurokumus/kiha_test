# Multiple component sets and operating telemetry

Completed 2026-09-11 on `feature/resumable-multipart-upload`. User-requested
milestone ahead of independent Phase 11b. Existing despike changes were preserved;
no dependencies, commit, deployment, or changes to user datasets.

## Delivered behavior

- Up to 16 named propeller/motor/ESC sets in Upload and Edit. Each set retains its
  own stable ID and hardware, plus independent RPM, motor temperature and power
  mappings selected after import. Component creation reuses the existing registry.
- Explicit C/F/K and W/kW source units; summaries use C/W. Running remains finite
  RPM > 0 outside acquisition gaps. Optional metrics use independent finite masks
  and measured-second weighting; missing telemetry never reduces RPM runtime.
- Motor temperature belongs to the motor aggregate; the selected power signal
  describes operating conditions for each hardware component in the set. Source
  details retain column names/units and measured/missing coverage. Power type is
  selected by the user through the source column, with no electrical/mechanical
  inference. This does not calculate energy.
- Legacy single-set files open without a migration write. Explicit empty lists
  stay empty. Atomic revisions guard canonical saves and concurrent legacy edits;
  stale saves retain the draft. Valid explicit replacement repairs invalid saved
  settings; reading them and saving unrelated notes preserves the raw settings.
- Local/server-only upload resume, failed-ingest status, list/search/Trash,
  column rename/drop, sample edits and export provenance retain every set.
  Existing first-set fields remain read projections. Old assignment writers are
  rejected after canonical migration rather than discarding extra sets.

Method and API details: [Component statistics method](COMPONENT_STATISTICS_METHOD.md).

## Automated verification

From repository root:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -p no:cacheprovider
python -X utf8 -u scripts/verify_component_sets.py
python -X utf8 -u scripts/verify_components.py --frontend-port 3200 --backend-port 8200
python -X utf8 -u scripts/verify_component_statistics.py --frontend-port 3290 --backend-port 8290
```

From `frontend`: `npm.cmd run build` and `npm.cmd run lint`.

- Full backend suite: 476 passed. Existing two dependency deprecations only.
  The cache provider is disabled for the known ignored `.pytest_cache` permission
  issue. Backend native data operations run in the required Python 3.13 venv.
- Frontend build/lint pass; only the existing bundle-size notice. Whitespace
  check passes. No new runtime packages.
- `test_component_sets.py` adds 14 tests, including canonical/legacy/empty
  behavior, atomic concurrent/stale/disk-failed saves, validation, immutable upload
  identity, resume without registry access, failed ingestion, source-byte
  preservation, column changes, trash/restore, real export metadata, migration
  races, long column names and guarded corruption repair.
- `test_component_stats.py` adds nine tests for independent channels, finite
  telemetry masks, C/F/K and W/kW conversion, different sample-rate weighting,
  zero versus missing values, source-local optional channel failures, cache
  invalidation/reassignment, copied datasets versus legitimate sets, corrupt
  duplicate assignments and bounded projected Parquet reads.

## Native browser evidence

The new suite owns temporary servers at 3196/8196, a temporary backend dataset
and an isolated Chromium profile. Global Python runs only Playwright/stdlib;
the server helper launches Python 3.13 for native reads. No pre-existing server,
browser profile, or user data is used. All owned children and fixtures clean up.
Evidence is in ignored `data/verification/component-sets` (logs, screenshots,
`results.json`).

The two-shaft fixture has 80 rows at approximately 10 Hz:

| Set | Running seconds | RPM | Motor mean | Power mean | Measured telemetry rows |
| --- | ---: | ---: | ---: | ---: | --- |
| Left drive | 4 | 1200 | 30 C | 200 W | Temperature 40; power 40 |
| Right drive | 6 | 2400 | 58 C | 600 W | Temperature 50; power 40 |

Left temperature is supplied as Fahrenheit, power as kW; right uses C and W,
with distinct temperature/power gaps. API results match the independent expected
values and the table displays the corresponding metrics and coverage.

Verified real two-set Upload/Pause/reload/reselect/resume in both local and
server-only recovery; per-set hardware/mapping/unit persistence; editing while a
component creation response is delayed; unsaved-navigation guard; 503 save retry;
409 conflict/reload; duplicate hardware prevention; set removal/recalculation;
statistics failure/retry; exclusion on trash and restoration of both sets.

The suite disables `Crypto.randomUUID` to simulate its absence on the deployed
plain-HTTP origin. New sets still receive distinct valid v4 UUIDs using
`getRandomValues`; this avoids the localhost secure-context exception concealing
the production issue. See [MDN Web Crypto](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/getRandomValues).
Actual deployment remains outside this local verification.

Three malformed metadata fixtures (`null`, `[null]`, missing set fields) verify
Uploads and Edit remain usable, display explicit invalid settings, preserve raw
settings through notes-only saves, and allow an explicit saved replacement.

Keyboard focus, desktop windows at 1440/1100 pixels, actual browser zoom at
125%/150%, control wrapping and horizontally scrollable statistics regions pass.
Screenshots inspected. No page errors or duplicate-key diagnostics. Eighteen
source files (raw CSV, Parquet/pyramids and manifests) remain unchanged throughout
the new suite, including lifecycle and metadata-corruption checks.

Existing component and component-statistics suites were adapted to canonical
revision/selector semantics without removing their checks. Both pass, preserving
36 and 24 source files respectively. They additionally cover registry errors,
batch uploads, RPM population SD/range oracles, data trim/rename/drop, gap history,
permanent deletion, busy/unmounted requests and desktop zoom.

## Review findings and practical limits

Independent review found and resolved legacy-to-canonical lost updates, delayed
component creation overwriting newer draft edits, plain-HTTP UUID generation,
malformed settings crashing list/editor views, and invalid telemetry from one
source suppressing another source's valid measurements.

Browser harness corrections: selects are located by accessible combobox name,
not label text that includes option contents; after external API changes reload
the app before expecting the editor's cached metadata to update. There is no
automatic multi-client synchronization. The user can explicitly reload metadata
or refresh statistics. Existing legacy browser suites now use the set-scoped
controls and canonical revision patches after UI migration.

No component-statistics CSV export or lifetime ledger was added. Generic signal
plots/exports continue to work; their provenance now includes all sets. The next
backlog milestone remains Phase 11b, the collapsible variable/filter side panel.
