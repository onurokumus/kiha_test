# PTT interface rework

Updated: 2026-09-27.

The rework uses `feature/resumable-multipart-upload` at
`1f0793380d0bad43f88e2c4d384ad05f7a0421e2` as its source baseline. The older
default-branch prototype is not the feature baseline. The working application
belongs in `work_v2/kiha_test`; the original projects remain reference copies.

## Design and scope

PTT now follows the light K11C0, Aerospace Calculator and Mathematical Models
interface: locally bundled Manrope, white panels, quiet borders, compact
fields, restrained blue controls and a wide analysis workspace. The original
PTT logo is retained. Desktop keyboard and mouse use, window resizing, browser
zoom and expanded plots remain the target; no phone-specific workflow is added.

The shell keeps Analyze, Split, Edit, Uploads, Components and Settings visible.
Secondary controls and explanatory text are grouped so the working data stays
prominent. The selection limit remains **20 test points**, including selection
quiet browser restoration. This is a per-workspace selection limit, not a
server-user limit.

This remains an internal application without login, signup or userSystem.
The deployed app uses only its local frontend assets and Python API. No remote
fonts, CDN, GPT service or internet connection is required at runtime. No
offline ZIP is rebuilt as part of this UI update.

## Feature-preservation map

| Area | Preserved capabilities and presentation |
| --- | --- |
| Analyze | Multi-test/test-point selection, scatter filters and clustering, linked time inspection, expansion/reset, plot actions and exports, original/filtered traces, FFT, Welch, waterfall FFT, XY including measured/generated time, quiet browser restoration. Full-test plots retain multiple variables rather than reverting to the older single-variable implementation. |
| Uploads | CSV staging and import time-basis/sample-rate setup, uploader and description fields, component sets, resumable multipart transfers with progress/pause/resume/cancel/retry, recovery of server-only receiving sessions, search/status filters, notes/components links, quality details, original and split CSV downloads. Analyze and CSV actions remain visible; Recompute and Delete are grouped under Manage. |
| Split | Up to nine linked signal plots, per-plot selectors/removal, manual intervals and boundary dragging, multiple-variable auto-split rules and proposal preview, explicit Apply-to-draft/Save, notes/labels/open ends, JSON import/export, exact draft/saved CSV export, unsaved-change guards and load-failure protection. Primary actions use Add test point, Import JSON, Export JSON, Save test points and Discard changes. |
| Edit | Descriptions and findings remain prominent. Manage test, Component sets and telemetry, Additional metadata, Data cleanup, Derived variables, and Rename or remove columns are named disclosures. Metadata revisions/draft guards, all formula/recipe/dependency/preview/rebuild controls, missing-value treatment, trim, column changes and existing confirmations remain connected to their original handlers. |
| Components | Registry associations and component sets, explicit RPM/temperature/power telemetry, current-library runtime and operating statistics, filtering, coverage warnings, per-test contributions and editing links. The counting policy remains available on demand. |
| Settings | Personal draft/save/revert/reset/import/export, server-wide default publication and confirmation, scatter axes/datasheet defaults, nine plot defaults, independent XY pairings, view/FFT/Welch/waterfall settings, clustering and upload rate. Datasheet, XY and upload-rate settings are collapsed by default. |
| Export and provenance | Existing CSV/PNG/selected-plot packages, full-resolution data selection, analysis metadata, source/equation/filter/method provenance, progress/cancellation and failure handling remain available. |
| Lifecycle | Existing test notes, annotations, data-quality information, persistent trash IDs, conflict-safe restore and confirmed permanent deletion are retained. |

Changing presentation does not change processing formulas, numerical
normalization, upload protocols or data retention. Existing edit/rebuild and
delete confirmations continue to apply. An uploader name is provenance, not
an account or authentication mechanism.

## Runtime and transfer notes

The production frontend base is `/ptt/`; API requests use same-origin
`/ptt/api/`, which nginx forwards to the backend's `/api/`. K11C0 opens PTT at
`/ptt/`. Build with `npm ci` followed by `npm run build`; there is no
`build:rota` command. Transfer the complete `frontend/dist/` output, including
the local font, font license and original logo. Node and npm are build tools,
not production runtime requirements.

PTT requires the matching **feature-branch backend and dependencies**, not
just a replacement frontend. In particular, resumable uploads use
`POST /api/uploads`, chunk `PUT`, status `GET`, completion `POST` and cancel
`DELETE` endpoints. Follow [the offline transfer notes](../../PTT.md) and
[the backend/nginx deployment guide](../deployment_guide.md), adapting its
online installation examples for the air-gapped server.

Verified source details relevant to deployment:

- `backend/run.py` runs exactly one uvicorn worker. Locks and upload commit
  coordination are in-process; multiple workers/replicas must not share the
  data directory.
- `KIHA_DATA_DIR` selects persistent storage; Linux defaults to
  `/data/ptt/data/`, while Windows/macOS default to repository `data/`.
  Here `data/` denotes that configured root. Durable uploads live in
  `data/tests/<name>/.upload/manifest.json`, `commits/` and
  `raw.csv.uploading`. There is no separate `data/uploads/` directory in this
  implementation. Keep hidden directories when copying or backing up data.
- Verified chunk receipts determine progress. Completion publishes
  `data/tests/<name>/raw.csv` atomically before ingestion. Valid receiving
  sessions survive a backend restart; startup recovery also reconciles
  interrupted finalization/ingestion. Interrupted edit/rebuild operations are
  instead marked as errors requiring recovery from the original CSV.
- Default chunks are 16 MiB, with three browser requests in flight and 1 MiB
  allowed multipart overhead. nginx's request limit must accommodate the
  negotiated chunk size plus overhead: 17 MiB with defaults. Existing
  sessions keep their original size when the configured default changes.
- The complete CSV limit defaults to 20 GiB. Receiving sessions expire after
  seven inactive days and are cleaned on startup or new upload initiation;
  new reservations retain an advisory 1 GiB free-space allowance. These
  limits are configurable in `backend/app/config.py`. Allow additional disk
  space for multipart temporary files, raw data, Parquet, pyramids and exports.
- Reloading a browser loses permission to read its local file. Resume by
  choosing the original CSV again; already received chunks are re-hashed
  before they are skipped. Browser recovery and server-only recovery both
  retain the original import settings. The bundled `@noble/hashes` code also
  supports the existing plain-HTTP internal host without a remote service.
- Install `backend/requirements.txt`, including `python-multipart`, from a
  complete wheel set built/downloaded for the destination's Linux platform
  and Python version. Windows `.venv` and `node_modules` are not deployment
  artifacts. The project specifies Python 3.13 on Windows development and
  supports the Linux Python 3.11 deployment baseline.

## Verification status

The UI implementation, source preservation review, build/lint, 32 frontend
tests, 497 backend tests with 477 subtests, and 63 browser check groups pass.
The final build also passed checks through the combined `/ptt/` preview with
existing datasets. See [the verification report](REWORK_QA.md) for coverage and
limits. The offline Linux smoke test remains a separate deployment step.
No production deployment is performed by this work.

The deployment smoke test should cover a multi-chunk CSV transfer, pause and
resume after reload, an interrupted receiving-session restart, import time
bases, Split preview/Apply/Save, multi-variable analysis, waterfall and
provenance downloads, component data, saved-session recovery and local asset
loading through the `/ptt/` route.
