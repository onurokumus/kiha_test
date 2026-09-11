# Phase 6d — Spectrum scientific prerequisites for export

2026-09-10, branch `feature/resumable-multipart-upload`, baseline `873c80c`.
Existing user and Phase 1–6c working-tree changes remain preserved and uncommitted.
Implementation and verification are complete.

## Corrected behavior

- TP Spectrum requests now send `tp_id`, using the same authoritative half-open
  saved rows as TP CSV export and time-plot filtering. Legacy time-only and open
  TP definitions use the shared resolver. A TP with saved `[32,160)` now analyzes
  128 rows, even if its time labels disagree. The compatible Full-test request
  `[0.25,1.25]` at 128 Hz still selects 129 rows by nominal-time conversion.
- Responses report exact row bounds, actual first/last stored sample times,
  finite/missing signal counts and optional finite/missing RPM counts. Unknown
  TP IDs return 404; combining `tp_id` with time bounds returns 400. The frontend
  rejects a response that does not confirm the requested TP ID, preventing an
  older server that ignores `tp_id` from showing a full-test spectrum as a TP.
- Reduced spectra pair each bucket maximum with its actual native frequency
  and bin index. The final partial bucket follows the same rule; first-bin ties
  are deterministic. Frequencies no longer receive four-decimal rounding.
  A 128.125 Hz tone retains its 128.125 Hz label, correcting the old 127.875 Hz
  bucket-start label. This does not improve the native frequency resolution.
- `spectrum_samples` returns complete unrounded/unreduced arrays and metadata;
  the existing `spectrum` endpoint wrapper only handles display reduction.
  Future spectral CSV must use this complete result rather than display maxima.
- Every successful trace retains its method, scope, quality and reduction
  metadata. **Analysis** opens a keyboard-accessible native dialog with per-TP
  or Full-test details. The compact header surfaces missing values and reduction.
  Partial/all failures retain source identity and support Retry; changed source,
  interval, estimator or RPM context cannot expose a stale result as current.

## Method and units

FFT normalization and Welch settings are unchanged. Welch's former library
defaults are now explicit: periodic Hann, 50% floor overlap, no padding,
per-segment constant detrend, one-sided density and arithmetic mean. Both
estimators still interpolate missing values by row index with held endpoints
and remove the overall mean. Time-plot filters remain independent.

FFT axes say **Magnitude (U)**. Welch axes say **PSD (U²/Hz)** on both Hz and
order views; `U` denotes the stored signal's unit. Order changes only X using
each trace's mean absolute RPM. No angular resampling or per-order density
rescaling was introduced. Details distinguish this mean-speed reference from
order tracking and explain that Log Y is log10 of the numerical value, not dB.

The source-verified [FFT comparison guide](FFT_COMPARISON.md) now describes the
current implementation and contains the exact estimator excerpt. Primary
references were checked against [NumPy 2.4 FFT normalization](https://numpy.org/doc/2.4/reference/routines.fft.html#normalization),
[real FFT bins](https://numpy.org/doc/2.4/reference/generated/numpy.fft.rfftfreq.html),
[SciPy 1.17 Welch](https://docs.scipy.org/doc/scipy-1.17.0/reference/generated/scipy.signal.welch.html)
and the [installed-version SciPy source](https://github.com/scipy/scipy/blob/v1.17.1/scipy/signal/_spectral_py.py).
The guide explicitly labels the change-of-variables derivation for a hypothetical
per-order density as an explanation, not an implemented conversion. The
[Phase 2 report](FFT_VERIFICATION.md) retains its historical fingerprints and
old defect characterizations without presenting them as current behavior.

`method.version = kiha-spectrum-v2` identifies the current contract. Metadata
records effective FFT length/bin spacing/window/scaling/units; Welch segment
length/overlap/count/used/trailing samples; the unreduced global peak; original
and returned bin counts; repair policy and existing timing/gap provenance.
Native peak values describe the complete analyzed interval in linear units,
even when the plot is frequency-zoomed or logarithmic. Reduced PSD maxima must
not be integrated as though they were every uniformly spaced bin.

## Verification

| Check | Result |
| --- | --- |
| `backend\.venv\Scripts\python.exe -m pytest backend\tests -q` | **308 passed / 191 subcases** |
| `npm.cmd run build` in frontend | Passed |
| `npm.cmd run lint` in frontend | Passed |
| `python -X utf8 -u scripts/verify_spectrum_analysis.py` | Passed: 6 API cases, 16 native reference cases, 145 browser Spectrum requests |
| Estimator excerpt against current `dsp.py` | Exact match after removing outer indentation |
| Runtime/library versions | Python 3.13.14; NumPy 2.4.2; SciPy 1.17.1; Polars 1.42.1; PyArrow 24.0.0; FastAPI 0.136.1 |

Eleven new backend tests and three updated defect characterizations verify exact
TP bounds, inconsistent saved time labels, actual stored timestamps, legacy/open
TPs, finite/missing and timing metadata, invalid scopes, true peak-bin positions,
narrow frequency spacing, deterministic ties/partial buckets, unchanged Welch
power, exact TP RPM counts, and source hashes/mtimes. Existing direct-DFT and
analytic tone tests still check amplitude normalization, odd/even endpoints,
off-bin leakage, interpolation, segment detrending/window/scaling and gap guards.
The entire backend suite includes the earlier time-plot and export tests.

The isolated browser harness uses real uploads and the Python 3.13 native
runtime for full-array numerical references. It compares the reusable complete
result with an independent NumPy/SciPy calculation, then verifies each displayed
frequency/value by native bin index. It checks the actual uPlot arrays, not just
labels or screenshots. Coverage includes:

- Six direct API cases and complete/reduced FFT/Welch references, exact TP sample
  identities and non-grid Full-test time bounds. The long TP uses 16,384 samples
  with 16,380 finite/4 missing; the short TP uses 8,192 with 8,190 finite/2 missing.
- Per-trace sample/method/reduction/quality details, the real 128.125 Hz retained
  FFT peak, frequency-only client zoom, unchanged PSD Y values under order X,
  and log10 values/axis units.
- Partial/all failures and Retry; held old estimator responses cannot replace a
  new result. Missing TP identity is rejected and Retry succeeds. Optional older
  metadata is shown as unavailable without inventing bounds or reduction.
- Full/TP modes, keyboard modal entry/Escape/focus, maximize/restore, 1100px
  desktop windows and actual 125%/150% browser zoom. Actual screenshots of details
  and expanded plots were visually reviewed for readable units/legends and
  unclipped content within the scrollable native modal.
- Actual Full-test time-plot X zoom followed by Spectrum sends the correct
  nondefault interval: requested `[1.1886373121869784,14.682387312186979]` seconds
  resolves to `[2434,30071)`, **27,637 samples**, with actual sample centers
  `1.1884765625..14.6826171875`. Native/reference arrays and the visible Analysis
  details match. This demonstrates the documented compatible Full endpoint
  convention rather than substituting requested times for actual sample times.
- **13 fixture source files unchanged**, zero unexpected browser writes/errors.

Evidence is ignored under `data/verification/spectrum-analysis/`: `results.json`,
15 screenshots and owned server logs. Temporary fixtures/browser profiles isolate
checks from user data. The harness owns ports 3150/8150 and stops its child
servers on completion; both ports were confirmed clear after the final run.
Harness syntax, whitespace and documentation-link checks pass. Existing dependency deprecations, large frontend bundle
and Windows Git permission/line-ending warnings are non-failing.

## Limits and next milestone

- Spectrum still assumes uniform row spacing at the stored nominal sample rate.
  It reports actual boundary times and existing jitter/quantization flags; it
  does not resample timestamps or add an uneven-sampling estimator.
- Recorded acquisition gaps still reject the selected interval. Legacy absent
  gap metadata or generated time cannot prove acquisition continuity; no timing
  rescan is added. Missing values may still span long runs; uncertainty is not
  estimated. Fewer than two finite signal samples remains an error.
- Full-test time-to-row conversion is intentionally compatible, including its
  endpoint convention. Use returned actual rows/times when comparing results.
- Method metadata is not an immutable source revision, complete edit-history
  sidecar or instrument calibration. Separate reads can observe later source edits.
- Shared native results are available internally; the HTTP display endpoint
  retains its 4000-point cap. No spectral download or unlimited-JSON option was
  added. Chromium desktop is verified; other browsers and maximum-size data are
  untested. The separate cold statistics-cache race is unchanged; fixtures warm
  statistics sequentially as in earlier milestones.

Next: **Phase 6e — Spectrum single and selected multi-plot CSV/PNG export**, using
the complete spectral result and actual analysis/display metadata. Keep the
existing time-series export contract intact. XY export follows as a separate
slice; metadata sidecars and cooperative progress/cancellation remain open.
