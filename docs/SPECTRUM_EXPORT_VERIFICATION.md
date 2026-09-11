# Spectrum exports — Phase 6e

Subsequent [Phase 6g](ANALYSIS_METADATA_VERIFICATION.md) adds default metadata ZIPs,
and [Phase 6h](EXPORT_PROGRESS_VERIFICATION.md) adds progress and cooperative
cancellation. Those reports supersede delivery/cancellation limitations below;
the numerical contract and recorded milestone evidence remain applicable.

Completed 2026-09-10. **322 backend tests /207 subcases**, frontend build/lint,
real Spectrum downloads and both existing time-export browser suites pass.

## User-visible scope

Spectrum has **Export** per plot and **Export selected plots** above the grid.
The latter produces one CSV per selected slot in a ZIP, or a combined 2x2/3x3
PNG. Slots are packed in ascending original grid order, retaining slot numbers;
duplicate variables remain independent plots. Unused PNG cells stay empty.
Restore a maximized plot before choosing multiple slots.

There is one spectral numerical result per estimator. Time-domain Original /
Filtered choices do not appear in Spectrum. FFT/Welch uses current stored data,
including previous saved edits/equations, without temporary time-plot filters.
Hidden application TPs and hidden uPlot legend traces are excluded. An incomplete
or failed spectrum blocks exports; hiding a legend does not conceal a failed
request. All-hidden legends fail clearly. PNG also requires drawable values;
CSV retains zero values even under Log Y.

## Numerical contract

`POST /api/spectrum-export` accepts:

```json
{
  "kind": "spectrum",
  "column": "signal_N",
  "mode": "welch",
  "axis": "per_rev",
  "method_version": "kiha-spectrum-v2",
  "sources": [{
    "test": "example",
    "tp_id": 7,
    "expected_i0": 1024,
    "expected_i1": 17408,
    "expected_fs_hz": 2048,
    "nperseg": 4096,
    "rpm_col": "rpm",
    "expected_mean_rpm": 1800
  }],
  "x_range": null
}
```

For Full source, omit `tp_id` and supply the loaded `t0`/`t1` (null means full
test). A Full request has exactly one source. Saved TP selection uses authoritative
half-open rows; Full retains the existing nominal-time interval convention.
Required expected row bounds, fs and method version prevent exporting under
stale scope/settings. Order also verifies the displayed mean absolute RPM.
Older responses without the required context can still be viewed; CSV asks for
a current backend instead of inventing missing bounds/settings.

`POST /api/spectrum-export/bundle` takes `{layout, plots: [{slot, request}]}`.
Each request is the single-plot payload. Layout is `2x2` or `3x3`; selected
one-based slots are unique, ascending, in 1–9 and within layout capacity.

CSV uses `dsp.spectrum_samples`, never reduced HTTP JSON. Native DC/last bins
are included in the default view even when they did not win a display bucket.
The current default padded display range means **all bins**. An explicit
frequency X zoom/pan includes native bin centers inside its bounds, with only
float64 arithmetic tolerance. Estimation always runs over the complete loaded
time interval before frequency cropping; Y display limits never remove rows.
Double-click resets the frequency crop.

Each source is a separate block of native bins. Different sample rates, lengths,
frequency spacings and RPM references are never silently joined/interpolated.
Each row records source test, saved TP ID (blank for Full), exact source i0/i1,
actual first/last sample times, fs, N, finite/missing counts, estimator, displayed
X axis and optional crop bounds, actual `method_*` and `quality_*` fields.
Native `bin_index` and `frequency_hz` precede the sole plotted signal column:
`<column> [amplitude U]` or `<column> [PSD U^2/Hz]`. No unplotted signals appear.

Order adds `order_cycles_per_rev = frequency_hz * 60 / mean_rpm`, plus the RPM
column identity/statistics/counts. Each source has its own mean. Only X changes:
Welch remains per-Hz density, U²/Hz. There is no angular resampling, order
tracking or per-order density conversion. Log Y is a display-only log10; CSV
values are always linear, including zeros. Nonfinite estimator/axis results
are rejected. Scientific methods/normalization and primary references remain
in [FFT comparison](FFT_COMPARISON.md); no estimator changes were introduced.

## Images, atomic delivery and resources

The mounted registry shares the same single/multi request builders and PNG
capture handlers. PNG copies the current native uPlot canvas, including current
X/Y ranges, log10 display, colors and visible legend; it does not redraw from
CSV. Wrapped annotations include source rows/times/fs/counts, full method,
reduction, RPM and timing metadata. Display reduction is explicitly recorded;
a PNG is an image of the loaded display, not a full-resolution spectral array.

Time and Spectrum share `stage_export`, `stage_bundle`, aggregate row budgets,
path/column/readiness checks, ordered test read locks/native read slots,
spooled temporary files, ZIP serialization and disconnect-safe cleanup. All
selected sources/entries finish before attachment headers; failure returns
JSON with the failing slot where applicable, never a partial downloadable ZIP.
The browser receives a complete Blob before initiating a download. Close,
unmount or changed context aborts the browser request/suppresses late delivery.
Native estimation/ZIP work is not cooperatively cancellable in this milestone.

Limits remain 128 source references and 8M requested source samples per request
or bundle, counted before frequency crop and repeated for repeated slots.
Preflight resolves every selected plot before native DSP; actual locked work
revalidates the aggregate budget. CSV spools to disk above 2 MiB. PNG limits:
8192px per edge, 16M output pixels, 16M aggregate input captures, 64K annotation
characters per card. Layouts use a common DPR and preserve current canvas scale;
over-limit captures fail rather than dropping provenance or reducing resolution.

## Verification and limits

Run from the repository root:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests
python -X utf8 -u scripts/verify_spectrum_exports.py
python -X utf8 -u scripts/verify_multi_plot_exports.py --frontend-port 3160 --backend-port 8160
# frontend directory:
npm.cmd run build
npm.cmd run lint
```

The new backend tests use independent NumPy/SciPy reference arrays, irregular
saved TP labels versus authoritative rows, missing/interpolated endpoints,
odd Welch segments/trailing samples, unequal grids/rates, large TP IDs, zero
values, native crop boundaries, Full intervals, stale settings, gaps, invalid
requests, row budgets and late-failure temporary-file cleanup. Browser checks
use actual downloaded CSVs/ZIPs/PNGs, isolated uploaded fixtures and a separate
profile. Native calculations run only in backend/.venv Python3.13; global
Python uses stdlib/Playwright. Evidence is ignored under
`data/verification/spectrum-exports/`.

Final evidence:

- Fourteen new backend tests /16 subcases; time/Spectrum shared staging tests
  remain green. Order overflow rejects infinite coordinates explicitly.
- Spectrum: **10 single CSVs +8 ZIPs (26 CSV entries) =36 CSV entries**, compared
  bin-by-bin with **20 independent native reference cases**. Exact identity,
  rows/times, metadata, crop and linear amplitude/density values all match.
- **13 actual PNG downloads**, including 2x2, 3x3, all nine slots, log/order,
  hidden legend, frequency zoom and maximized desktop views. Native-decoded
  pixels, canvas annotations, packed slot positions and empty cells checked;
  representative actual single/combined PNGs visually reviewed.
- Different tests at 2048/1024 Hz and differing TP lengths, missing endpoints,
  correct default DC/last bins, frequency zoom/reset/**Shift-drag pan**, independent
  per-slot crops, order RPM references and byte-identical CSV under Log Y pass.
- Real Full time-plot X zoom feeds non-null Full Spectrum bounds; downloaded
  bins match the resulting restricted source interval, with no frequency crop.
- Late source failure returns JSON/no attachment; Retry succeeds. Closing a
  held ZIP suppresses delivery. Partial spectra and missing legacy method context
  disable the relevant export; all-hidden legends fail explicitly.
- Desktop 1100px, actual 125%/150% browser zoom, single/multi native dialogs,
  keyboard/Escape/focus, maximize guard and restore pass. All 20 fixture source
  files unchanged; no unexpected browser errors or writes.
- Existing time export suites: **19 CSVs /9 PNGs** (single) and **13 ZIPs /
  61 CSV entries /6 PNGs** (multi). Original/filtered/both, crop/overlay, native
  DSP settings, failure/Retry/Close and desktop interaction regressions pass.
- Owned 3150/8150 and 3160/8160 servers stopped; temporary datasets/profiles
  removed. Build size and dependency deprecation warnings remain non-failing.

CSV reads current stored values; PNG captures loaded values. No immutable
dataset revision or global cross-test/cross-slot snapshot is provided. Guards
do not detect same-shape value edits that retain the checked settings. Metadata
columns are not the deferred complete provenance sidecar (equations/history).
Repeated metadata increases CSV size; maximum-size throughput, peak memory and
disk capacity have not been measured. Source samples remain uniformly spaced
at metadata fs; timing/gap limitations from Phase6d persist. Existing Windows
cold statistics-cache replacement race is not fixed; fixtures warm it serially.
Chromium desktop is covered; Firefox/Safari are untested. XY export is the next
milestone, followed by metadata sidecars and long-export progress/cancellation.
