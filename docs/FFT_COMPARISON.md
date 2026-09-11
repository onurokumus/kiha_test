# FFT comparison guide

Updated 2026-09-10 for Phase 6d, method metadata version `kiha-spectrum-v2`. The FFT/Welch estimators retain their previous normalization, windows and preprocessing. Saved TP selection, display-bin coordinates and analysis metadata now follow the contract below. Current implementation and verification are tracked in [Spectrum analysis verification](SPECTRUM_ANALYSIS_VERIFICATION.md); [FFT_VERIFICATION.md](FFT_VERIFICATION.md) preserves the original Phase 2 audit and its historical defect characterizations.

**Compare identical stored samples, sample rate and estimator first.** Spectrum uses full-resolution `data.parquet`, independently of the time-plot filters. TP requests use saved half-open row bounds; Full-test requests retain nominal-time conversion. Display reduction keeps each bucket's actual maximizing frequency, but discards other bins and is unsuitable for PSD integration.

## Implementation and versions

Start at [backend/app/dsp.py](../backend/app/dsp.py), function `spectrum_samples`. It returns a `SpectrumResult` containing the complete frequency/magnitude arrays and analysis metadata. `spectrum` builds the reduced display payload from that result. [main.py](../backend/app/main.py) exposes `GET /api/tests/{name}/spectrum`; [SpectrumPlot.tsx](../frontend/src/components/plots/SpectrumPlot.tsx) calls it through [fetchSpectrum](../frontend/src/services/api.ts). FFT computation stays on the backend; the browser renders with uPlot and exposes each trace's metadata through **Analysis**.

| Component | Installed version | Role |
| --- | --- | --- |
| Python | 3.13.14, Windows x64 | Project backend environment |
| NumPy | 2.4.2 | Float64 arrays, interpolation, real FFT and frequency bins |
| SciPy | 1.17.1 | Mean removal and Welch PSD |
| Polars / PyArrow | 1.42.1 / 24.0.0 | Stored samples and CSV/Parquet processing |
| FastAPI | 0.136.1 | Spectrum endpoint |
| React / uPlot | 18.3.1 / 1.6.32 | Spectrum controls and rendering |

Backend package versions match [requirements.txt](../backend/requirements.txt). SciPy defaults were checked against the installed implementation and [tagged v1.17.1 source](https://github.com/scipy/scipy/blob/v1.17.1/scipy/signal/_spectral_py.py), with the [1.17.0 Welch reference](https://docs.scipy.org/doc/scipy-1.17.0/reference/generated/scipy.signal.welch.html) as readable documentation. Do not substitute defaults from an arbitrary library version.

The API defaults to `mode=fft`. With no saved preferences/session, the UI defaults to FFT, Test points source, Hz and linear Y; restored settings can change those choices.

## Input, rate and interval

[ingest.py](../backend/app/ingest.py) keeps numeric signals as Float64. Numeric time is assumed to be **seconds**, regardless of its column name; clock strings `[HH:]MM:SS[.fraction]` are parsed into seconds. Measured time becomes elapsed time by subtracting its first value, retained as `source_time_origin_s`.

Nominal sample rate comes from the **whole imported time column**, not the selected spectrum interval. In `_measured_timing`, let `d` be consecutive time differences, `p` the positive differences, and

```text
q = max(quantile(p, 0.25), median(p) - 3 * median(abs(p - median(p))))
epsilon = max(abs(q) * 1e-9, float64_epsilon * 32)
dt = mean(d[d <= 1.5*q + epsilon])
fs_hz = 1 / dt
```

Zero differences remain in that mean to accommodate coarse timestamps; rows are not deduplicated. Larger differences imply missing intervals: `max(2, rint(d/dt)) - 1` signal-NaN rows are inserted between the surrounding timestamps, with half-open row ranges stored in `time_gap_ranges`. Expansion beyond 5,000,000 inserted rows is rejected.

Auto import with no detected time column, or measured time that is unparseable, nonfinite, backward or wholly constant, falls back to generated `i/fs` time. Explicit generated-time import does the same. Its rate is the upload setting, default **2048 Hz**. Generated time cannot discover missing source rows. Measured timestamps can retain jitter/quantization; Spectrum nevertheless uses uniform row spacing at `meta.fs_hz`, without timestamp resampling or an uneven-sampling estimator. Inspect `time_source`, `time_quantized`, `jitter_warning` and gap metadata. Legacy imports retain their saved rate until reimported.

The selected samples depend on the source:

| Source/control | Samples used |
| --- | --- |
| Spectrum → Full test | Active test's Full-test time range (`fullRange`); absent range means the whole stored test |
| Spectrum → Test points | Separate request with `tp_id` for each visible selected TP in its own test; `store.testpoint_range` resolves the same saved half-open rows used by TP CSV export |
| Relative time zoom in Test points | Does not shorten a TP spectrum |
| Drag/wheel/pan within Spectrum | Changes the frequency display only; no recomputation at finer frequency resolution |

**Saved TP requests:** `tp_id` is authoritative and cannot be combined with `t0` or `t1`. Saved indices define `[i0,i1)`, with the end row excluded. The shared resolver also supports legacy time-only definitions and open ends; an open end resolves to the next TP start or data end. It clamps bounds to available rows and rejects an empty interval. Spectrum uses the saved definition, not unsaved Split edits or a relative-time display zoom.

**Full-test requests:** [store.window_bounds](../backend/app/store.py) converts requested seconds to rows using nominal `fs` and `s = meta.t_start` (normally zero, possibly nonzero after trimming). Defaults/clamps are `lo=max(t0,s)` and `hi=min(t1,s+n_rows/fs)`, with absent limits using the corresponding data bound:

```text
i0 = max(0, int((lo - s) * fs))
i1 = max(i0, min(n_rows, ceil((hi - s) * fs) + 1))
N = i1 - i0; samples = data.parquet[i0:i1]
```

The lower edge rounds down; the upper edge includes the nominal sample at or immediately after the requested end. Floating-point rounding can affect endpoints. For example, at 128 Hz, a Full-test request `[0.25,1.25]` still selects rows `[32,161)`, **129 samples**. A saved TP with indices `[32,160)` now gives **128 samples** for both `spectrum?tp_id=…` and saved TP export. Using a TP's displayed times as a Full-test request is not an equivalent substitute.

Both paths report exact `i0`, exclusive `i1`, and actual first/last stored sample timestamps as `time_start_s`/`time_end_s`. These timestamps describe the selected rows; they do not change Full-test row selection or the assumed uniform spacing. Match actual `N` and rows, not just labels or duration. FFT record duration is `N/fs`; the first-to-last span of uniform sample timestamps is `(N-1)/fs`, and measured timestamps may differ.

## Preprocessing and actual calculation

Spectrum reads the current working Parquet slice directly, without pyramid/plot decimation. Persisted edits, NaN fills and materialized equations therefore affect its input; the original uploaded CSV may differ. Record `nan_policy` and `derived_variables` provenance. Despike, Butterworth, moving-average and detrend settings in the **time-plot filter controls do not feed Spectrum**.

A range intersecting a recorded acquisition gap is rejected. Otherwise `_interp_nan` treats NaN, null and ±infinity as missing, interpolates interior values by **sample index**, and holds the nearest finite endpoint at either end. It does not drop rows or impose a maximum missing run length. Fewer than two finite samples fails with `range is all NaN`, even if one finite sample exists. These endpoint rules follow [NumPy interp](https://numpy.org/doc/2.4/reference/generated/numpy.interp.html). Explicit persisted fill edits clear recorded gap boundaries; legacy files without them cannot trigger this gap rejection.

The API requires **16 ≤ N ≤ 8,000,000**. Missing-data/range errors return HTTP 400; an unknown TP returns 404. A successful response includes original `finite_count` and `nan_count` for every trace, before repair. The latter includes all nonfinite values, not only IEEE NaN. Analysis details expose the counts separately for Full-test and each TP trace. Zero missing count does not establish that acquisition was complete. The code also rejects a nonpositive/nonfinite sample rate, nonfinite requested bounds or boundary timestamps, a row-count mismatch with metadata, and nonfinite estimator output.

Verbatim calculation excerpt from `dsp.spectrum_samples` (outer indentation removed; `frame` is the selected Parquet slice and `fs` is metadata Hz):

```python
v = frame[col].to_numpy().astype(np.float64)
clean, mask = _interp_nan(v)
nan_count = int(v.size - mask.sum())
if clean is None:
    raise ValueError("range is all NaN")
clean = signal.detrend(clean, type="constant")  # drop DC so it can't dwarf peaks

extra = {}
if mode == "welch":
    seg = int(min(max(nperseg, 64), clean.size))
    overlap = seg // 2
    # Spell out the existing SciPy defaults to keep the method reproducible
    # across library upgrades; periodic Hann is distinct from symmetric Hann.
    window = signal.get_window("hann", seg, fftbins=True)
    freqs, mag = signal.welch(
        clean, fs=fs, window=window, nperseg=seg, noverlap=overlap,
        nfft=seg, detrend="constant", return_onesided=True,
        scaling="density", average="mean")
    extra["nperseg"] = seg
    segment_count = 1 + (clean.size - seg) // (seg - overlap)
    used_samples = seg + (segment_count - 1) * (seg - overlap)
else:
    mag = np.abs(np.fft.rfft(clean)) * 2 / clean.size
    mag[0] /= 2
    if clean.size % 2 == 0:
        mag[-1] /= 2
    freqs = np.fft.rfftfreq(clean.size, d=1.0 / fs)
    seg, overlap, segment_count, used_samples = None, None, 1, clean.size
if not np.isfinite(mag).all():
    raise ValueError("spectrum contains nonfinite results; check signal magnitude")
```

Both estimators first subtract the interpolated slice's mean; this is constant detrending, not linear detrending. See [SciPy detrend](https://docs.scipy.org/doc/scipy-1.17.0/reference/generated/scipy.signal.detrend.html).

### FFT magnitude

For the cleaned, centered samples `x[j]`, the code implements

```text
X[k] = sum(j=0..N-1, x[j] * exp(-2*pi*i*j*k/N))
f[k] = k*fs/N, k=0..floor(N/2); delta_f = fs/N
A[k] = c[k]*abs(X[k])/N
c[k] = 1 at DC and (only for even N) Nyquist; 2 elsewhere
```

The forward FFT is unscaled by NumPy; the application supplies the factors above. There is no taper (equivalent to a rectangular window), zero-padding, power-of-two rounding, coherent-gain correction, or sub-bin peak fitting. FFT length is exactly `N`. For odd `N`, the final positive-frequency bin is below Nyquist and remains doubled. See [NumPy normalization](https://numpy.org/doc/2.4/reference/routines.fft.html#normalization), [rfft](https://numpy.org/doc/2.4/reference/generated/numpy.fft.rfft.html) and [rfftfreq](https://numpy.org/doc/2.4/reference/generated/numpy.fft.rfftfreq.html).

If input units are `U`, magnitude units are `U`. An isolated sinusoid centered on a non-DC/non-Nyquist bin reports its **peak amplitude**, not RMS or peak-to-peak. Its RMS is peak/√2. Off-bin tones leak into neighboring bins and can have a smaller largest-bin amplitude; bin spacing alone is not a guarantee of resolving nearby tones. See the [SciPy spectral-analysis guide](https://docs.scipy.org/doc/scipy-1.17.0/tutorial/signal.html#spectral-analysis).

### Welch PSD

The API/UI default `nperseg` is **4096**, overriding SciPy's standalone default. The browser does not expose/send this setting; an API caller can supply it. Effective segment length is `L=min(max(nperseg,64),N)`, including `L=N<64` for short valid ranges. The response reports effective `nperseg`.

The application explicitly pins the previous SciPy 1.17.1 defaults: periodic Hann window, overlap `floor(L/2)`, `nfft=L` (no zero-padding), constant detrending of each segment, one-sided density scaling and arithmetic mean averaging. With hop `H=L-floor(L/2)`, there are `K=1+floor((N-L)/H)` complete segments, covering `L+(K-1)*H` input samples. The remaining `(N-L) mod H` trailing samples are unused. The tagged [Welch/CSD implementation](https://github.com/scipy/scipy/blob/v1.17.1/scipy/signal/_spectral_py.py) confirms segment selection and averaging. Metadata reports these effective settings and counts.

```text
w[j] = 0.5 - 0.5*cos(2*pi*j/L), j=0..L-1
Z_r[k] = DFT((segment_r - mean(segment_r)) * w)[k]
P[k] = mean(r=0..K-1, c[k]*abs(Z_r[k])^2 / (fs*sum(w^2)))
delta_f = fs/L
```

Here `c[k]` has the same endpoint exceptions, using length `L`. The periodic window differs from a symmetric Hann's `L-1` denominator. See [Hann window options](https://docs.scipy.org/doc/scipy-1.17.0/reference/generated/scipy.signal.windows.hann.html). PSD has units **U²/Hz**; it is not the FFT amplitude, its square, or an RMS-amplitude spectrum. Integrating unreduced PSD over Hz estimates mean-square power; windowing and segment detrending matter. See [Welch scaling](https://docs.scipy.org/doc/scipy-1.17.0/reference/generated/scipy.signal.welch.html).

## Response reduction, units and RPM

After either estimator, `dsp.spectrum` caps the response at **4000 points**. For `B` original bins, bucket width is `F=max(1,ceil(B/4000))`. Each nonoverlapping group returns its **maximum** magnitude/PSD and that maximum's **actual native frequency and bin index**. The final shorter group follows the same rule; the first native bin wins an exact tie. With `F=1`, all bins are returned. Frequencies are serialized without the old four-decimal rounding. This is display reduction after the full-rate transform, not improved frequency resolution.

For example, `N=16384`, `fs=2048` gives 8193 native FFT bins, 0.125 Hz spacing and `F=3`. A bin-centered 128.125 Hz, amplitude-3 tone retains **128.125 Hz, amplitude 3** in the reduced payload. The Phase 2 report's old 127.875 Hz label describes the corrected bucket-coordinate defect. Native `peak` metadata is calculated before reduction and describes the largest linear bin across the complete analysis interval, even after the user zooms the display; it is not an interpolated peak estimate.

Reduced coordinates remain ordered but are not generally uniformly spaced. Frequency zoom cannot recover the discarded bins, and reduced Welch PSD is not suitable for numerical integration. Use the complete uniformly spaced arrays from `spectrum_samples` or an independent calculation on identical samples for that purpose. `max_bins` remains an internal Python argument, **not an HTTP query option**. This milestone does not add spectral CSV/image downloads or an HTTP switch for unreduced arrays.

The API always returns Hz. With **Per rev**, the backend calculates mean/min/max of **finite absolute RPM samples in the identical selected row interval**, including zero-speed samples; it does not interpolate RPM or exclude rows where the analyzed signal was missing. It reports `rpm_finite_count` and `rpm_nan_count` separately. No finite RPM values or a nonpositive/nonfinite mean fails. The frontend computes `order = Hz*60/mean_RPM` separately for each trace. This is a fixed mean-speed reference, with no angular resampling, variable-speed order tracking or blade-count multiplier. Confirm that the chosen column is actually RPM. For comparison, [MathWorks orderspectrum](https://www.mathworks.com/help/signal/ref/orderspectrum.html) explicitly resamples at constant phase; that is a different procedure from the conversion here.

Only X changes: Welch Y remains **U²/Hz** on both Hz and order axes, with explicit units in the plot and Analysis details. FFT magnitude remains **U**. `U` is a placeholder for the stored variable/equation's physical unit; the application does not infer a calibrated unit from its name. Log scale displays `log10` of the numerical magnitude or PSD in those units, excluding nonpositive values; it does not calculate dB.

For clarity, a density **per order** would be a different numerical representation. Let `r=mean_RPM/60` and `q=f/r`; preserving integrated power requires `S_q(q)=r*S_f(r*q)` because `df=r*dq`. This is a change-of-variables derivation from the [PSD power-integral definition](https://docs.scipy.org/doc/scipy-1.17.0/tutorial/signal.html#spectral-analysis), **not an implemented rescaling**. Integrating the unchanged per-Hz Y values directly over order would give the wrong power. Large RPM variation can also smear a mean-referenced spectrum; min/max RPM and counts describe the reference but do not correct it.

## Analysis metadata and limits

The response preserves existing `mode`, `col`, `fs_hz`, `n_samples`, `nan_count`, `freqs`, `mag`, effective Welch `nperseg` and optional RPM statistics, and adds the following auditable context:

| Fields | Meaning |
| --- | --- |
| `i0`, `i1`, `tp_id` | Actual half-open source rows and saved TP ID, or `null` for Full-test selection |
| `time_start_s`, `time_end_s` | Actual first/last stored sample centers, in normalized stored seconds |
| `finite_count`, `nan_count` | Original finite/nonfinite signal counts before interpolation; sum is `n_samples` |
| `method.version`, `source`, `prefilter`, `sampling` | `kiha-spectrum-v2`, `stored`, `none`, `metadata_fs`; current stored signal, no time-plot filter, nominal metadata rate |
| `method.missing_values`, `detrend` | `linear_by_index_hold_edges`, `constant`; repair policy and global mean removal |
| `method.window`, `nfft`, `bin_spacing_hz`, `onesided`, `scaling`, `units` | Actual rectangular FFT or periodic-Hann Welch settings; native spacing; peak-amplitude U or density U²/Hz |
| `method.nperseg`, `noverlap`, `average`, `segment_count`, `used_samples`, `trailing_samples` | Effective Welch segmentation; FFT has null segment length/overlap/average, one transform, all N samples used |
| `peak.frequency_hz`, `magnitude`, `bin_index` | Largest unreduced linear bin, first bin winning ties; complete interval, regardless of display zoom |
| `bin_indices` | Native integer bin index corresponding to each returned frequency/magnitude pair |
| `reduction.method`, `factor`, `n_bins_original`, `n_bins_returned` | `none` or `max-bin`, bucket size and before/after counts |
| `quality.known_gap_count`, `gap_metadata_available` | Successful results have zero intersecting recorded gaps; availability means a saved gap-range list exists, not proof of complete acquisition |
| `quality.time_source`, `time_quantized`, `jitter_warning` | Existing import timing provenance/warnings, or unavailable values for legacy metadata |
| `rpm_col`, `mean_rpm`, `min_rpm`, `max_rpm`, `rpm_finite_count`, `rpm_nan_count` | Optional finite-absolute RPM reference over the same source rows |

These fields explain calculation and display choices; they are not a data-integrity hash, instrument calibration, complete edit history, or missing-data uncertainty estimate. Preserve source/Parquet identity and import/equation metadata separately when comparing results. Missing runs can still be long, unknown lost rows cannot be recovered, and jitter is not resampled. A successful zero-gap result cannot certify an uninterrupted acquisition when gap metadata is absent or generated time was used. Analysis details remain available per successful trace alongside separately identified failed traces, rather than folding a failed TP into another trace's metadata.

## Reproducible comparison checklist

1. **Match data identity.** Record test name, TP ID (if used), source filename/hash, current Parquet hash, `meta.json`, equations/edits and software versions. Same filename is insufficient after edits.
2. **Capture the actual request.** In desktop browser DevTools → Network, save the `/spectrum` request URL and JSON response for one trace. Record `col`, `mode`, `tp_id` or Full-test `t0/t1`, optional `rpm_col`, and the source mode. Retain exact source rows/timestamps, sample/rate/missing counts, full `method`, `quality`, `peak`, `reduction`, `bin_indices` and optional RPM fields.
3. **Exchange identical samples.** For `tp_id`, request `/api/tests/{name}/testpoints/{tp_id}/export` without draft index overrides. For Full-test, request `/api/tests/{name}/export` with the same `t0/t1`. In both cases `cols` selects the signal plus RPM if needed, and time is added automatically. Preserve decimal precision; account for the TP export's generated `test_point_id` column. Do not substitute `/raw` or a reduced plot response. Verify first/last row, sample count, units, rate source and missing-data policy. Keep the test unchanged between reads; individual read locks do not make separate requests one immutable snapshot.
4. **Match preprocessing and method.** Reproduce index interpolation, mean removal, FFT length/window/scaling or every Welch setting above. Compare full arrays before display reduction. For exact bin comparisons, compute locally on those rows; the HTTP endpoint cannot disable reduction. Shortening the interval to avoid reduction changes the method input.
5. **Separate display effects.** First compare linear Hz results, using `bin_indices` to compare retained maxima with the complete native array. The current payload does not round frequencies to four decimals. Then compare RPM normalization and log display. Check time/rate units, included endpoints, off-bin leakage, reduction and varying RPM before attributing discrepancies to the FFT library.
6. **Run the independent fixtures.** From the repository root:

   ```powershell
   backend\.venv\Scripts\python.exe -m pytest backend/tests/test_spectrum_method.py backend/tests/test_spectrum_rpm.py backend/tests/test_time_gaps.py backend/tests/test_ingest_dialect.py -q
   ```

   [Method tests](../backend/tests/test_spectrum_method.py) create isolated synthetic datasets. At 2048 Hz with 4096 samples, `3*sin(2*pi*128*t)+7` yields FFT peak 3 with DC removed; default Welch with 8192 samples yields peak 6 U²/Hz and integrated power 4.5 U². Further cases cover odd/even endpoints, direct-DFT references, missing values, TP boundaries and display reduction. See [current verification evidence](SPECTRUM_ANALYSIS_VERIFICATION.md) for executed checks and remaining limits; the [original audit](FFT_VERIFICATION.md) records the old TP/reduction behavior for historical comparison.
