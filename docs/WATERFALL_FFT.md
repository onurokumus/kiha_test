# Waterfall FFT

The Spectrum **Estimator → Waterfall FFT** view is a time-frequency magnitude
map, matching the orientation of the supplied comparison: frequency on X,
elapsed seconds on Y (increasing upward), and amplitude in color. Entering
Waterfall FFT defaults to **one map of the active test and chosen variable**.
The supplied PowerPoint is a style reference, not a requirement for two sources;
no demonstration data is installed in the user's datasets. Optionally choose
**Selected TPs** for separate maps of the visible saved test points, including
points from different tests; **Full test** uses the active test and its current
Full-test time interval. Each grid slot uses its selected variable. Sources
with missing variables report individual errors rather than disappearing.

All sources of one variable share a color range. Different variables use their
own scales because their units can differ. Linear color spans zero to the
largest loaded magnitude. **Log color** displays log10(magnitude in U), with a
floor six decades below the shared maximum; zero maps to that floor. It is not
dB or PSD. Physical units are inherited from the signal; U means its unit.

Drag to zoom both axes, wheel to zoom frequency, Shift-drag or middle-drag to
pan frequency, and Alt-wheel/Alt-drag to adjust elapsed time. The maps in a slot
share zoom. Double-click, Home while the canvas is focused, or **… → Reset
axes** resets the view. Maximize and browser zoom preserve interaction.
Window/overlap, mode and axis ranges survive automatic and explicit sessions.

## Calculation contract (`kiha-waterfall-v1`)

- Backend `app/waterfall.py` reads the stored full-resolution Parquet column
  under the existing native read gate. Saved TP `[i0,i1)` bounds are authoritative;
  Full-test bounds use the same nominal-time resolver as ordinary Spectrum.
  Actual source bounds, sample rate and source context are retained.
- Nominal sampling rate is `meta.fs_hz`. There is no resampling, order tracking,
  padding or time-plot filtering. Known acquisition gaps reject the request.
  Finite timestamps must be nondecreasing. Missing signal samples use linear
  interpolation by row index with edge holds, as in ordinary Spectrum; fewer
  than two finite samples fail. Interpolation counts are retained.
- Default window length L = 1024 samples, overlap = 50%. Available power-of-two
  lengths range from 64 to 16384; overlap is 0, 25, 50 or 75%. A short interval
  is rejected with guidance to reduce L. No silent window adjustment occurs.
- Hop H = L × (1 − overlap/100). Complete windows start at jH, for
  j = 0 … floor((N−L)/H). Every window is independently mean-centered, multiplied
  by a periodic Hann `scipy.signal.get_window('hann', L, fftbins=True)`, and
  transformed with `numpy.fft.rfft` at length L.
- Magnitude `A[j,k] = c[k] × abs(rfft((x[j]−mean(x[j])) × w)[k]) / sum(w)`.
  `c=2` for interior bins, `c=1` for DC and Nyquist. This is a one-sided peak
  amplitude in U, not Welch density or squared STFT power. A steady, bin-centered
  sinusoid away from DC/Nyquist has its peak amplitude recovered. Window
  leakage and varying frequency/amplitude can change a measured peak.
- Bin spacing is fs/L; time step is H/fs. The nominal center of window j is
  `(jH+(L−1)/2)/fs` seconds from the selected first row. Only full windows are
  used; unused trailing samples are reported. The edge regions are not padded.
  Hover/export also retain actual source timestamps at the first and last
  window centers in each displayed time cell (midpoint of the middle samples).

This amplitude normalization follows the window-sum scaling described in the
[SciPy spectral analysis tutorial](https://docs.scipy.org/doc/scipy-1.17.0/tutorial/signal.html#spectral-analysis).
See also [SciPy ShortTimeFFT](https://docs.scipy.org/doc/scipy-1.17.0/reference/generated/scipy.signal.ShortTimeFFT.html)
for the window/hop time-frequency interpretation. This implementation uses
explicit complete windows; it does not adopt ShortTimeFFT boundary padding or
the squared-magnitude definition of its `spectrogram` convenience method.
Ordinary FFT magnitude and Welch PSD remain unchanged; see
[FFT comparison](FFT_COMPARISON.md).

## Display reduction and export

Every window is computed in batches of at most 64, without decimating the source
signal. The grid has at most 256 time cells and 512 frequency cells. Consecutive
windows/bins are grouped by ceiling(native count / display limit); a cell
contains the maximum magnitude in that group. Explicit frequency/time edges
retain the smaller first/last frequency cells and final partial groups. Time
cell edges are halfway between nominal centers. Grid reduction is a display
summary: it cannot locate a maximum more precisely than its cell bounds, and
it must not be integrated as power or treated as native spectral bins.

**… → Analysis details** shows executed settings, counts, interpolation,
trailing samples, timing quality and reduction factors. **… → Export CSV / PNG**
and **Export selected plots** use the shared export workflow, metadata sidecars,
source locks, bounds/rate checks, progress and cancellation. Single-slot PNGs
include all its sources. Selected slots support 2×2/3×3 packages.

- CSV is one row per cell intersecting the current two-axis viewport, with
  linear magnitude, variable/test/TP identity, source rows, rate, window/overlap,
  missing counts, grouping factors, frequency/time edges and source center
  timestamps. CSV recalculates the grid from current stored data. Include
  `analysis.json` (default) for full executed method/provenance. No log-color
  transformation is applied to CSV, and no native-bin export is implied.
- PNG freezes the current maps, axes, color scales and source/method details.
  Its metadata describes loaded results. Large source sets may need fewer
  selected points to fit the existing image size limits.
- Empty, loading, failed or incomplete source sets cannot export. Changing
  source/settings suppresses stale responses. Requests retain the existing
  8,000,000-sample limit; backend exports cooperate with cancellation between
  native work batches. An in-flight preview request may finish after abort.

Method parameters are independent of the ordinary FFT/Welch and Per rev
settings. Waterfall currently uses Hz only. Future extensions such as a 3D
surface, configurable windows, manual color limits or native-window CSV are
separate features.
