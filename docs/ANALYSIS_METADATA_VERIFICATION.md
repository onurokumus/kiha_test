# Analysis provenance sidecars — Phase 6g

Completed and verified: 2026-09-10. Full backend, frontend build/lint and live
isolated Chromium metadata/file-only export suites pass.

Subsequent [Phase 6h](EXPORT_PROGRESS_VERIFICATION.md) adds shared progress and
cooperative cancellation while preserving this provenance/file contract.

## Delivery and compatibility

Single and selected Time, Spectrum and XY export dialogs enable **Include
analysis metadata (ZIP)** by default. The archive contains the requested CSV
or PNG and one UTF-8 `analysis.json`. Multi-plot CSV ZIPs contain the existing
per-slot CSVs and one manifest covering every selected slot. Multi-plot images
contain one composed PNG and a record for each selected plot in grid order.
The option is keyboard accessible and can be unchecked for file-only delivery.
Single/multi handlers, numerical column choices and canvas composition are shared.

Numerical endpoints accept optional `include_metadata` (default false for older
API callers); the bundle's top-level flag controls its manifest. No additional
source mutation or persisted export history is introduced. CSV staging, source
locks/native read slots, row budgets, response cleanup and Close suppression
are reused. Metadata errors fail the entire package before attachment headers.

Archives use Python's standard [ZIP writer](https://docs.python.org/3.13/library/zipfile.html),
with UTF-8 member names where needed, and are closed before delivery. The JSON
records schema `kiha-analysis-v1`, creation time in UTC, format, optional layout,
and ordered plot records. CSV records identify each filename and its SHA-256;
PNG records identify its filename, SHA-256, byte size and pixel dimensions.
These hashes bind the sidecar to the exported file, not to an immutable source
dataset revision.

## Numerical provenance

CSV metadata is collected by the same writer that computes/serializes the
data, while the corresponding source lock is held. It is not reconstructed
from display JSON or from a later metadata read. Every source records:

- Test name and saved TP ID (a string, or null for Full), requested nominal
  time bounds, resolved half-open source rows and the interval convention.
- Time-column identity, sample rate, stored start/duration/row count where
  recorded, source time origin/kind, gap metadata and last edit timestamp.
- Selected variables, with explicit unknown units because this store has no
  authoritative per-column unit catalog. Signal names remain exact.
- Current retained equations for selected variables and their transitive
  dependencies, including missing dependency markers and engine/version.
  Unrelated equations are omitted. Legacy absence is `not_recorded`.
- Python/NumPy/SciPy/Polars/PyArrow versions and file-size/mtime observations.
  Mtime is a string to preserve nanosecond integer precision in JSON clients.
- Actual output row count, and first/last emitted sample indices/time centers
  where applicable. Zero-row sources remain represented in the manifest.

There is no complete stored edit log. Overwritten/dropped equations, prior
replacement inputs, fills, trims and other past operations cannot be invented.
`edit_history_status` states this explicitly. Current formula text is not a
promise that the original raw upload plus that text can replay every past edit.
Dependency cycles in legacy records terminate safely when collecting metadata.

Time CSV records the executed original/filtered/both choice, actual crop and
per-source processing result. Original processing has `filter: null`. Filters
include the method version `kiha-time-filter-v1`, resolved parameters, actual
sample windows, full processing rows (including Full envelope shoulders),
gap policy and warnings/repair counts. Moving average records rounded sample
width, nearest boundary handling and zero origin; Butterworth records clamped
order, cutoffs in Hz and forward/backward SOS filtering with SciPy's default
padding; detrend records linear type; despike records resolved odd window,
maximum run size, threshold, MAD scale, floor and replacement mode. Existing
algorithms are unchanged; see [filter method](FILTER_METHOD.md),
[SciPy SOS filtering](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.sosfiltfilt.html)
and [uniform filter semantics](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.uniform_filter1d.html).
Exported row crops happen after complete interval processing.

Spectrum records the actual `spectrum_samples` metadata, including the resolved
FFT/Welch method, N, finite/missing counts, effective segment/overlap/FFT sizes,
window/detrend/normalization/units, timing quality and optional RPM reference.
The requested segment size is retained separately from the actual one. Native
bin count/range after crop is recorded. Order remaps X only; CSV stays linear
and Welch density stays per Hz. See [Spectrum contract](SPECTRUM_EXPORT_VERIFICATION.md).

XY records both exact variable identities, finite/omitted pair counts, both-axis
crop, source/emitted row centers and `kiha-xy-v2`. It still uses original stored
pairs in source order, without interpolation or temporary time filters. See
[XY contract](XY_EXPORT_VERIFICATION.md).

## Image provenance

Plot responses now carry source/equation context alongside the actual loaded
values. Original TP traces retain this per column; Full windows, filtered
results, Spectrum and XY retain it on their loaded response. PNG capture copies
that context synchronously with the canvas, axis ranges, visible legend and
original/filtered series roles. It excludes hidden sources and large numerical
display arrays. Display reduction, method/quality details, logarithmic display
and order transformations remain explicit. The CSV radio choice does not change
the PNG's displayed traces or provenance.

The image endpoint `POST /api/plot-image-export` packages the captured PNG and
its JSON snapshot; it never refetches source metadata or reruns analysis. Thus,
a source edited after loading can produce an old loaded PNG and a new CSV, each
with its own corresponding equation/settings record. The manifest identifies
its origin as `loaded_browser_snapshot_no_source_reread`. This is provenance
for a local client capture, not a signed server attestation of browser pixels.
Legacy responses remain exportable with unavailable source context marked.

Wire format is one UTF-8 JSON line followed by PNG bytes, streamed into a
bounded temporary spool. The endpoint validates schema/format, slot count/order,
required source context and PNG signature/dimensions before staging the archive.
The filename is cleaned independently from full JSON labels. Size limits are
2 MiB JSON, 64 MiB encoded PNG, 8192px per edge and 16M output pixels; existing
PNG capture/layout limits remain. Oversize metadata fails rather than truncating
equations. Browser Close/unmount suppresses delayed downloads and temporary files
close on failure/disconnect. Phase 6h adds cancellation between compression
chunks; the current native write still finishes before cleanup.

## Verification

Run from the repository root:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests
python -X utf8 -u scripts/verify_analysis_metadata.py
python -X utf8 -u scripts/verify_xy_exports.py --frontend-port 3160 --backend-port 8160
python -X utf8 -u scripts/verify_spectrum_exports.py --frontend-port 3160 --backend-port 8160
python -X utf8 -u scripts/verify_plot_exports.py --frontend-port 3170 --backend-port 8170
python -X utf8 -u scripts/verify_multi_plot_exports.py --frontend-port 3170 --backend-port 8170
# frontend directory:
npm.cmd run build
npm.cmd run lint
```

Eleven new backend tests /9 subcases check file hashes and CSV rows/crops,
resolved moving-average/Welch sizes, both-axis XY counts, same-variable data,
per-slot settings, transitive/legacy/cyclic equations and missing dependencies,
snapshot timing, file-only compatibility, sidecar size failure/resource cleanup,
PNG packaging without source rereads and invalid/oversized image requests.

The browser harness uploads isolated fixtures and creates real materialized
derived columns through `/edit`. It checks actual downloaded archive contents,
source/row counts, file hashes, PNG pixels and captured metadata. A deliberate
later fixture edit distinguishes loaded PNG equations from newly executed CSV
equations. Existing native numerical suites run with the explicit file-only
choice, retaining their independent value/bin/pair oracles and failure checks.

- Full backend suite: **350 passed /243 subcases**; frontend build/lint pass.
  Existing non-failing dependency deprecation and bundle-size warnings remain.
- Metadata harness: **24 ZIP packages, 28 CSV entries and 13 packaged PNGs**,
  plus three file-only PNGs. All source counts, sample bounds, effective Welch
  settings, transitive equations and SHA-256 values match their downloaded files.
- Single TP original/filtered/both and Full, Welch, XY single/2x2/nine-slot
  metadata packages pass. The PNG remains filtered when the CSV radio selects
  original; copied image metadata exactly matches the submitted capture.
- A real materialized-equation edit after loading produces the old equation in
  the PNG and the newly executed equation in the CSV. Invalid image metadata
  returns 400 with no attachment; Retry succeeds and Close suppresses a held
  successful response. Malformed JSON/Unicode and oversized payloads fail cleanly.
- Native desktop 1100px, actual 125%/150% browser zoom, keyboard activation,
  metadata/file-only toggling, Escape/focus restoration and dialog fit pass.
  The 150% dialog, single exported PNG and nine-slot PNG were visually reviewed.
- File-only regressions: XY **42 CSV entries /18 PNGs /14 native references**;
  Spectrum **36 CSV entries /13 PNGs /20 native cases**; Time single **19 CSVs
  /9 PNGs**; Time multi **13 ZIPs /61 CSV entries /6 PNGs**. They retain their
  numerical/crop/visibility/failure/Close and resize/maximize/keyboard checks.
- All 14 metadata fixture files are unchanged both before the deliberate edit
  and after that edit finishes. No unexpected browser errors or writes. The
  existing suites also confirm XY14, Spectrum20 and Time21 unchanged files.
  Isolated fixtures/profiles were removed and owned servers stopped. Evidence
  remains ignored under `data/verification/analysis-metadata/` and the existing
  `{xy,spectrum,plot,multi-plot}-exports/` directories.

Harness fixes during verification: wait for every native canvas after changing
mode; use the existing combined-image button label; allow the intentionally
failed image upload time to finish; keep dialog screenshots separate from the
same-named exported PNG. A complete rerun passes after these harness corrections.

## Limits and continuity

No immutable source revision, global cross-source/slot snapshot or complete
historical replay is added. File observations are diagnostic; hashes cover the
exported file only. Units absent from the source catalog stay unknown. Metadata
adds bounded response/ZIP overhead; worst-case throughput/memory/disk capacity
is unmeasured. Images with sidecars require the connected backend; file-only
PNG remains client-side. Chromium desktop is tested; Firefox/Safari are untested.

Existing cold statistics-cache replacement races remain separate; fixtures warm
the cache serially. A mixed-mode fixture exposed another existing limitation:
the time-trace API splits comma-separated column names, so a comma inside a name
cannot be addressed there. XY's exact `y_col` path remains verified; broader
column-list compatibility requires a separate API change.

Phase 6h progress/cancellation is now implemented; see its linked verification
report and the current root handoff for the next milestone.
