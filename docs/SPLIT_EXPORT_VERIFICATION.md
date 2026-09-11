# Phase 5 — identifiable split-data exports

Completed 2026-09-10 on `feature/resumable-multipart-upload` (baseline `873c80c`).

## Scope and behavior

Uploads previously offered only the original uploaded CSV. It now also offers
**Split CSV**, a compact on-demand list of saved test points, each with its own
download. Loading, empty and failed lists have explicit messages and Retry;
closing and reopening fetches current definitions. Escape returns keyboard focus
to the disclosure button. Original CSV still returns the exact uploaded bytes.

Every TP CSV uses `GET /api/tests/{name}/testpoints/{id}/export` and the shared
`store.stream_csv` Arrow writer. This also upgrades existing Analyze TP downloads
that already use that endpoint. The generated **`test_point_id`** is the tool's TP
definition ID, not the value of an acquisition column used for auto-splitting.
IDs are scoped to the source test, identified by the filename.

- Saved filename: `{test}_tp{id}.csv`.
- Draft filename: `{test}_tp{id}_draft.csv`.
- All stored signal columns by default; optional `cols` selects source columns.
  Stored time stays first; generated `test_point_id` is appended last and is
  present even with a column subset. Units/time origin remain those of the stored
  data; TP CSV time is not shifted to zero like the overlay plot's time axis.
- A source column named `test_point_id` is retained under `source_test_point_id`,
  adding another `source_` prefix until the name is unique in the **full source
  schema**. This mapping stays stable for column subsets. If the time column has
  the reserved name, its renamed version still comes first. No source data or
  metadata is renamed on disk; generic full-test/window exports retain their schema.
- Full resolution from `data.parquet`, including prior stored edits/derived
  columns, missing cells and non-finite values. No plot reduction, temporary
  filtering, relative-time shift, interpolation or six-decimal plot rounding.
- TP IDs are serialized as CSV strings containing their exact integer spelling;
  common CSV readers may infer them as integers. The backend also preserves IDs
  outside Arrow int64, though the existing browser ID model remains JS numbers.

## Boundaries and drafts

The previous Split download used the generic inclusive time-window endpoint,
which could include the next TP's boundary sample. Both download entry points
now use exact **half-open `[start_idx, end_idx)`** rows. Saved indices are
authoritative; legacy time-only/open-ended points use the existing shared
`store._testpoint_bounds` fallback (next strictly later TP or dataset end).

Unsaved Split edits/new points still export without saving. Split passes both
`start_idx` and `end_idx` query parameters; supplying only one is rejected. The
new `indexTestPoints` helper is shared with Save, preserving its
`Math.round((seconds - t_start) * fs_hz)` behavior. An open draft ends at the next
strictly later draft TP's start, or `n_rows`. Row bounds clamp to the dataset;
empty/reversed ranges fail, and an empty draft has a disabled download link.
When the editor is dirty, all its row downloads are marked as drafts, since
changes to neighboring TPs can change an open end. Downloads do not persist them.

Each download concerns one TP: overlapping definitions yield separate files and
samples outside TPs remain available in original/full-test exports. No combined
membership policy or batch export was introduced.

The endpoint preflights errors before attachment headers, then re-resolves
metadata/bounds under the streaming body's `data_read` lock. A concurrent save
between preflight and streaming therefore cannot apply stale saved bounds.
Per-test locks, native read slots and the existing 65,536-row Arrow batches remain.
Busy/error tests cannot start TP exports; generic/full/raw endpoints retain their
existing behavior.

## Verification

Commands from repository root unless stated otherwise:

| Check | Result |
|---|---|
| `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_export.py -q` | 20 passed, including 9 new tests / 15 subcases |
| `backend\.venv\Scripts\python.exe -m pytest backend\tests -q` | 249 passed / 108 subcases |
| `npm.cmd run build` in `frontend` | Passed after final layout fix |
| `npm.cmd run lint` in `frontend` | Passed after final layout fix |
| `python -X utf8 -u scripts\verify_split_exports.py` | Passed after final layout fix |
| `git diff --check` | Passed |

Backend regressions cover nonsequential/zero/negative IDs; saved-index authority;
adjacent, legacy and open-ended points; new/edited draft exports without writes;
clamping/invalid inputs; missing/busy/failed tests; column selection/deduplication;
source-ID and time-column collisions; large integer IDs; preflight/save races;
and a **140,006-row** CSV crossing Arrow batch and Parquet row-group boundaries.
The latter verifies one header, all sample values at full precision, null, NaN
and both infinities. Existing raw/generic exports and stats-cache tests pass.

The live harness uploads isolated fixtures through the real resumable API, saves
definitions through the API, then exercises real UI links and browser downloads:

- Byte-identical saved CSVs from Uploads and Split; exact original upload bytes.
- Actual files, suggested filenames, ID values, time rows, source-column collision
  preservation, missing samples and tiny-signal precision.
- Unsaved edited/new TP exports, next-TP/data-end open ranges, no persistence from
  download, and byte-identical draft-versus-saved contents after Save.
- Split wheel zoom / Shift-drag pan do not change exported TP bounds.
- Loading failure/Retry, no saved points, fresh definitions after Save, empty-draft
  guard, keyboard Enter download and Escape/focus restoration, long TP names.
- Both sections at 1100px width and actual 125% / 150% Chromium zoom via
  `chrome.tabs.setZoom`. Geometry asserts action containment. Screenshots at 150%
  were visually reviewed, including the scrolled Split table and Uploads list.
- No unexpected browser console/page errors; injected HTTP 503 errors are expected.

The initial Uploads geometry check exposed its old 900px table minimum exceeding
the 892px history pane at 150% zoom. The minimum is now 860px, preserving readable
columns and existing horizontal keyboard scrolling at narrower widths. The final
regression confirms all row buttons fit at the tested widths/zoom levels.

Harness logs, downloaded CSVs, results and screenshots are ignored under
`data/verification/split-exports/`. It checks ports 3110/8110 are free, owns and
stops only its two child servers, uses Python 3.13 for backend/native work, and
cleans temporary datasets/profile. User datasets/settings were not changed.

## Limits and next milestone

- Native browser-managed downloads remain; app-managed export progress,
  cancellation, metadata sidecars and filtered-data exports belong to Phase 6.
  If a test disappears/becomes invalid after attachment headers, streaming can
  fail; it does not substitute another TP or silently use stale saved bounds.
- Full maximum-size/multi-GB download, disconnect cancellation and Firefox/Safari
  were not tested. Existing dependency deprecation/bundle-size warnings are non-failing.
- Arrow writes lowercase `nan`/`inf`; Polars CSV inference may classify such a
  column as text. Regression reads use the known numeric source dtype. This is
  existing serialization, not missing-value replacement.
- Existing legacy time-only sample conversion and JS numeric ID limits remain.

Next: **Phase 6a — audit despiking scope and display original/filtered traces
together**, then single-plot exports. Keep the Phase 2 spectral prerequisites
in `FFT_VERIFICATION.md` ahead of spectral export implementation.
