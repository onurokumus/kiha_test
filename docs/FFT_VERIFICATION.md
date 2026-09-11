# Phase 2 FFT method verification

Completed 2026-09-09. Deliverable: [FFT comparison guide](FFT_COMPARISON.md). This milestone adds documentation and isolated verification tests; it does not change the production method, API, UI, dependencies or stored datasets.

**Historical Phase 2 record.** The observations, fingerprints, test counts and defect examples below describe the 2026-09-09 baseline, not the current application. Phase 6d subsequently corrects TP row selection and reduced peak coordinates and adds analysis metadata/units; its implementation and verification are in [Spectrum analysis verification](SPECTRUM_ANALYSIS_VERIFICATION.md). The [comparison guide](FFT_COMPARISON.md) now describes that current contract. The numerical FFT/Welch normalization remains unchanged.

## Code and source audit

Baseline commit: `873c80c0ab908bb69e88012f5baf7f73ab276170` on `feature/resumable-multipart-upload`. Existing Phase 1 frontend changes remained uncommitted and were preserved. At the Phase 2 boundary, the following production paths matched that commit:

| Entry point | Verified behavior |
| --- | --- |
| `backend/app/dsp.py:spectrum` | Raw Parquet slice; missing-value repair; constant detrend; FFT/Welch; payload maxima; RPM statistics; response rounding |
| `backend/app/dsp.py:_interp_nan`, `_known_gap_slices` | Finite-mask interpolation with held endpoints; rejection based on recorded acquisition gap ranges |
| `backend/app/ingest.py:_measured_timing`, `_ingest_csv` | Actual nominal-rate inference, measured/generated time, normalization, inserted missing rows, metadata |
| `backend/app/store.py:window_bounds`, `_testpoint_bounds`, `bucket_minmax` | Nominal-time endpoint rounding; saved TP half-open bounds; spectral payload grouping |
| `backend/app/edit.py:_rebuild`, `_updated_gap_ranges` | Current stored data reflects fills, trims and equations; fills clear gap boundaries; saved rate is retained |
| `backend/app/main.py:api_spectrum`, `api_export`, `api_export_testpoint` | Exposed parameters/defaults, validation errors, export row semantics |
| `frontend/src/App.tsx`, `TimeSeriesGrid.tsx`, `services/api.ts` | Full-test range versus TP bounds; filter settings absent from Spectrum requests; no exposed `nperseg`/`max_bins` controls |
| `SpectrumPlot.tsx`, `SelectedPointsPanel.tsx`, `constants/settings.ts` | Estimator, Hz/order, log10, defaults/preferences; frequency-only zoom; TP `nan_count` omission |

The literal FFT/Welch excerpt in the guide was checked against `dsp.py` after restoring its outer indentation. SHA-256 of the inspected file bytes at the Phase 2 boundary (line endings matter):

```text
backend/app/dsp.py
6a573e52580e1754432879b9d197b27865696167eaefb09a9a607f9e4d16eed9
backend/app/ingest.py
113e88a11102fd2e60035788044791636d3fdada1ef8612ec921d69a9a9badf8
backend/app/store.py
ff42e7df8450b8fd46c7d53391b7db6f105d7b5a67c4682ad8959f626c4b16cf
```

Phase 3 subsequently added import diagnostics and catalog fields to ingest/store. At that checkpoint the DSP fingerprint and audited timing/bounds/reduction methods remained unchanged, and the full numerical suite passed again. See [data-quality verification](DATA_QUALITY_VERIFICATION.md) for those additions. Later milestones modify these files; the hashes above retain only the original audit snapshot.

Python and package versions in the guide were read from `backend/.venv` using `sys.version` and `importlib.metadata.version`; pins were compared to `backend/requirements.txt`. Frontend rendering versions came from installed package manifests. `inspect.signature(signal.welch)` and `inspect.getsource(scipy.signal._spectral_py.csd)` confirmed the periodic Hann defaults and complete-segment selection.

Primary references are linked next to their claims in the guide. NumPy 2.4 documentation, SciPy 1.17.0 documentation and the exact [SciPy v1.17.1 source](https://github.com/scipy/scipy/blob/v1.17.1/scipy/signal/_spectral_py.py) were read on 2026-09-09. SciPy 1.17.1 documentation URLs failed to load; the version-tagged source and installed implementation resolved that gap. Current unversioned SciPy documentation identifies itself as 1.18.0 and was not used to infer this environment's defaults.

## Reproduction and measured results

From the repository root, using the required Python 3.13 backend environment:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_spectrum_method.py -q
backend\.venv\Scripts\python.exe -m pytest backend/tests
```

The new [17 tests](../backend/tests/test_spectrum_method.py) use `DataDirTestCase`, patching data/trash paths to temporary directories. FastAPI TestClient exercises the real endpoint without starting a server. Synthetic Parquet fixtures do not need pyramid files; even spectra above time-plot display thresholds read full resolution. Analytic tones and a direct DFT (without NumPy/SciPy FFT calls in the reference calculation) provide numerical oracles.

| Case | Observed/expected result |
| --- | --- |
| FFT, 4096 samples at 2048 Hz | 0.5 Hz bins; 128 Hz sine amplitude 3; 320 Hz cosine amplitude 0.8; offset 7 removed to <1e-12 DC |
| Even/odd lengths | Nyquist cosine amplitude 2.5 is not doubled; final positive bin of a 101-sample record retains amplitude 2.5; no padding |
| Off-bin 10.25 Hz cosine | Rectangular-window direct DFT agrees (`rtol=1e-11`, `atol=1e-12`); largest bin is 10 Hz with amplitude below 3 |
| NaN/null/±infinity | Six missing values interpolate/hold to the explicitly constructed reference; direct DFT agrees; linear trend remains |
| Default Welch, 8192 samples at 2048 Hz | `L=4096`, 0.5 Hz bins; amplitude-3 sine gives 6 U²/Hz at 128 Hz, 1.5 in adjacent bins, integrated power 4.5 U² |
| Welch segmentation | Four 64-sample segments at starts 0/32/64/96 agree with direct-DFT periodograms (`rtol=1e-10`, `atol=1e-12`); 21 trailing samples unused |
| Welch clamping | Requested -5/20 → 64, 65 → 65, 4096 → available 128; a valid 16-sample range uses 16 |
| Nonzero origin/off-grid time bounds | At `t_start=10`, 128 Hz, `[10.253,10.498]` selects `[32,65)`; spectrum agrees with those 33 samples; data-bound clamping works |
| TP spectrum versus CSV | At 128 Hz, TP `[32,160)` exports 128 rows; Spectrum `[0.25,1.25]` uses 129 rows and `delta_f=128/129` |
| FFT payload reduction | 16,384 samples, 2048 Hz, 128.125 Hz tone → 2731 points with peak labelled 127.875 Hz; internal uncapped result has 8193 bins and peak 128.125 Hz; amplitude 3 retained |
| Welch payload reduction | `L=8192`, 2048 Hz, 128.25 Hz tone → 128.0 Hz label; unreduced PSD sums to 4.5 U², treating reduced maxima as ordinary bins incorrectly gives 7.5 U² |
| Time-plot filter independence | A 50 Hz low-pass suppresses a 256 Hz tone in the filtered response; subsequent Spectrum response remains identical |
| Gap/sample/RPM errors | Both estimators reject recorded gaps; too-short, over-limit, <2 finite signal samples, missing RPM and zero mean RPM report HTTP 400 |
| RPM | Finite absolute values, including zeros, yield mean 1440, min 0, max 2400; 96 Hz corresponds to order 4; backend frequency/magnitude arrays remain unchanged |

Existing `test_ingest_dialect.py` and `test_time_gaps.py` cover clock parsing, normal-rate inference through dropouts, quantization, backward-time fallback, generated-time limitations, inserted NaNs and fill/trim compatibility. `test_spectrum_rpm.py` also checks RPM over an exact subrange. All ran in the full suite.

## Required checks and limits

- Targeted method suite: **17 passed**, 12 subtests passed (1.80 s).
- Full backend suite: **217 passed** (17.14 s), up from the 200-test Phase 1 baseline.
- `npm.cmd run build` in `frontend`: **PASS**. Existing non-failing >500 kB bundle warning remains.
- `npm.cmd run lint` in `frontend`: **PASS**.
- `git diff --check`: **PASS**. Git's existing line-ending/global-ignore notices are environmental, not check failures.
- Backend runs retain the two existing Starlette/httpx and AnyIO deprecation warnings. No new test failures or warnings were introduced.
- Guide excerpt and relative document links: verified. Documentation cross-check covered every Phase 2 TODO requirement.

No browser interaction was changed, so no new browser run was needed; source tracing establishes how controls build requests. [Phase 1 browser evidence](RENDERING_VERIFICATION.md) remains available. These are deterministic synthetic-data checks, not a comparison against the other team's real data/results or instrument calibration. No maximum-size acquisition was allocated: the over-limit test temporarily lowers the guard to verify rejection safely.

## Deferred behavior changes

These were the Phase 2 findings and proposed prerequisites for spectral exports. The old TP and reduction examples above are retained as defect characterizations, not as expected current behavior:

- Retain the maximizing bin's actual frequency and expose reduction/method metadata; use unreduced PSD for numerical integration.
- Reconcile TP spectra with saved half-open row bounds so plot/export comparisons use identical samples.
- Surface missing-value counts for each TP spectrum, and label density units correctly when frequency is shown as order.

At the original checkpoint, the next ordered milestone was Phase 3: compact data-quality summaries, reusing existing ingest metadata and distinguishing warnings from conditions that prevent analysis.

## Phase 6d supersession

Phase 6d addresses the three prerequisites above without changing the FFT/Welch estimators. `spectrum_samples` now shares a full-resolution result with source/method/quality metadata. TP Spectrum requests use `tp_id` and the same saved half-open rows as TP CSV; Full-test nominal-time selection remains unchanged and reports actual row/time bounds. Reduced payloads retain each maximum's true native frequency and index without four-decimal rounding, while separate peak metadata describes the complete unreduced result. Per-trace Analysis details expose original missing counts, effective settings and timing limitations. Order remaps only frequency, with Welch PSD explicitly remaining in U²/Hz.

See [Spectrum analysis verification](SPECTRUM_ANALYSIS_VERIFICATION.md) for current acceptance evidence and limitations. Spectrum downloads remain a subsequent milestone; this historical report does not establish their completion or replace the current verification results.
