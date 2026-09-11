# Time-series filtering and despiking

Audited 2026-09-10 against `backend/app/dsp.py`, `/filter` in `main.py`, bounds/reduction in `store.py`, persistent edits in `edit.py`, and the per-plot controls. Runtime: Python **3.13.14**, NumPy **2.4.2**, SciPy **1.17.1**, Polars **1.42.1**. Official library references below confirm the primitives; the application-specific detector and interval rules come from the code and regression tests.

## What changes, and what is saved

- **Original** means current full-resolution `data.parquet`, including previously saved trims, missing-value edits and derived variables. It is not necessarily the original uploaded CSV.
- **Filtered** means a temporary result from `GET /api/tests/{name}/filter`. Filtering reads current Parquet under the existing data-read lock; it does not replace samples, rebuild a pyramid, change TP definitions, or write filter settings into the dataset. There is no persistent despike operation.
- The Edit view can persist column changes, trims, missing-value interpolation/zero-fill and formula results through the staged rebuild. Explicit fill policies also clear known acquisition-gap boundaries; subsequent filters use that edited dataset. `raw.csv` remains the separate original upload.
- The time plot has one filter kind/settings per plot slot, shared between its Test points and Full test presentations. The overlay choice changes display only. TP statistics, scatter, Spectrum and existing CSV downloads still use stored samples; time-plot filters are not a preprocessing chain for those calculations. Spectral processing is documented separately in [FFT_COMPARISON.md](FFT_COMPARISON.md).

## Despike method and defaults

The detector is a **Hampel-style rolling residual method**. For the finite working vector `x`, with centered reflected windows of odd length `W`, it computes:

```text
m[i] = median_filter(x, W, mode="reflect")[i]
d[i] = abs(x[i] - m[i])
r[i] = median_filter(d, W, mode="reflect")[i]
candidate[i] = d[i] > max(abs_floor, threshold * 1.4826 * r[i])
```

`r[i]` is a median of residuals from each sample's own local median. It can differ from the textbook window MAD, `median_j(abs(x[j] - m[i]))`, which uses one common center within window `i`. Reproducing a standard Hampel implementation can therefore give different detections. The `1.4826` factor approximates the normal-distribution MAD scale conversion; it does not make this residual statistic a calibrated noise estimate. [SciPy MAD definition and normal scaling](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.median_abs_deviation.html).

| Control / API parameter | Default | Meaning |
| --- | --- | --- |
| Context / `window_s` | 25 ms / 0.025 s | `W = max(3, round(window_s * fs))`, then add 1 if even. Python rounding applies. |
| Max event / `max_spike_s` | 10 ms / 0.010 s | Maximum consecutive **candidate** run: `M = max(1, floor(max_spike_s * fs + 1e-12))`. |
| MAD threshold / `threshold` | 3.5 | Dimensionless multiplier of scaled rolling residual dispersion. |
| Absolute floor / `abs_floor` | 0 | Minimum absolute departure in the selected variable's native units. |
| Replacement / `replacement` | `linear` | Straight bridge between the immediate non-candidate shoulders; `median` uses the local median. |

At 2048 Hz these defaults resolve to **51 context samples and at most 20 candidate samples**. The backend requires `W > 2*M`, `W <= 100001`, and `W` shorter than the processed continuous segment. The UI additionally requires context milliseconds greater than twice maximum-event milliseconds. Sub-sample duration requests still allow one sample because `M` is bounded below by 1.

Candidates are grouped into consecutive runs. Runs of length at most `M` are replaced; longer candidate runs are left unchanged. This bounds detected runs, not every possible physical event: a long event may contain shorter candidate pieces, and multiple nearby spikes can defeat the clean-majority assumption. A zero residual dispersion plus zero absolute floor marks any positive departure as a candidate. Use the overlay to judge whether meaningful transients are being removed.

The linear replacement uses both shoulders from the original working vector; it is a single pass. At a selected-range or acquisition-gap edge, a run lacks two shoulders and falls back to its local median. Reflection repeats only values within that continuous segment, with half-sample symmetry. [SciPy median-filter edge behavior](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.median_filter.html).

`replacement_counts` counts repaired originally finite samples within the requested range. `spike_event_counts` counts contiguous runs of that repaired mask, after restoring missing positions; a missing position can split one candidate run into two counted events. Counts are calculated before display reduction and are not permanent edits or a confidence measure.

## Other filters and units

- Butterworth low/high/band-pass/stop use `scipy.signal.butter(order, cutoffs, fs=meta.fs_hz, output="sos")`, then `sosfiltfilt`. Cutoffs are Hz and must lie strictly inside `(0, fs/2)`; band cutoffs must increase. UI/API default order is 4 and the backend clamps it to 1–10. The order is the design order; band designs double that order. [SciPy Butterworth design](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.butter.html).
- Forward/backward filtering has zero phase in its ideal interior response, doubles the designed filter order and squares its amplitude response. Consequently the design's −3 dB critical point is approximately −6 dB after both passes; the code does not compensate it. `sosfiltfilt` uses its default odd endpoint extension and padding. The application also rejects lengths `<= 3*(2*n_sos_sections+1)`, slightly more conservatively than SciPy's odd-order padding adjustment. Edge transients remain possible. [Forward/backward behavior](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.filtfilt.html), [SOS padding](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.sosfiltfilt.html).
- Moving average uses `uniform_filter1d`, window `max(1, round(window_s*fs))`, nearest-value endpoint extension and default `origin=0`. UI default is 1 s; the API requires a positive window. Windows at least as long as the continuous segment fail. Even windows follow SciPy's discrete placement and can have a half-sample centering asymmetry. [SciPy uniform-filter placement](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.uniform_filter1d.html).
- Detrend subtracts a linear least-squares fit versus sample index within each continuous segment. It has no tunable parameters, and differs from Spectrum's constant/mean detrend. [SciPy detrend](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.detrend.html).

All output values retain the input variable's units; this path does not integrate, differentiate, convert engineering units, or resample the signal. DSP uses the selected test's `meta.fs_hz`, including when overlaying TPs from different tests. It assumes uniformly spaced samples for calculations even when the stored timestamps contain small timing jitter.

## Missing samples, gaps and processing edges

Ordinary null/NaN/±infinite signal values become non-finite float64 samples. Inside each continuous segment, `_interp_nan` creates temporary linear values in **sample-index coordinates**, with constant first/last finite values at missing endpoints. Fewer than two finite samples produces an all-missing output. After filtering, every original non-finite position becomes NaN again and serializes as JSON `null`; interpolation can still influence neighboring finite outputs. [NumPy interpolation and endpoint defaults](https://numpy.org/doc/2.4/reference/generated/numpy.interp.html).

Known `time_gap_ranges` are hard boundaries: processing never bridges a dropout with interpolation, filter state, rolling statistics or replacement shoulders. Too-short segments are returned missing with `gap_segment_warning`; if every otherwise-processable segment is too short, the request fails. Legacy/generated-time datasets can lack known gap metadata, so the backend cannot infer those hard boundaries from absent rows. Gap-free short selections also fail when the filter needs more context.

Every filter request must cover at least 8 rows and no more than **8,000,000** requested rows. The entire processing slice is loaded at full rate; display reduction is not a memory-saving filter input. Median filtering with very large windows is particularly costly, and the 100001-sample validation ceiling is not a performance guarantee. `boundary_warning` currently marks only requests touching the stored dataset's ends; absence of that flag does not certify freedom from selected-range edge effects.

## Original/filtered scope and display alignment

**Saved Test point:** `/filter?tp_id=<id>` resolves the same authoritative half-open `[start_idx,end_idx)` range as original TP traces and TP CSV exports. Legacy times and open-ended points use the shared `_testpoint_bounds` fallback. It rejects simultaneous `t0`/`t1` because combining two scopes is ambiguous. It reads and filters only those TP rows, including when reduced, so neither adjacent TP samples nor globally aligned pyramid shoulders affect the result. Responses add `tp_id`, exact `i0/i1`, the actual first-sample `time_origin_s`, and `relative_t` computed before rounding. Use `relative_t` for the overlay; subtracting the returned absolute timestamps after rounding can introduce another error.

TP original traces keep ordered minimum/maximum samples at their actual times (plus endpoints). As of 2026-09-11, filtered TP plots request `display=line` so each TP remains a single chronological trace, including after adding Despike; automatic min/max reduction previously displayed two separate curves on long TPs. Line mode filters at full resolution, then stride-reduces responses longer than 8000 samples; this can omit brief extrema or missing runs from the displayed line. Original and filtered traces share interval and origin, but can have different time arrays and retained points. They must be separate time facets, not zipped together or used to compute pointwise differences from reduced arrays. Filtered TP computation uses the complete TP regardless of subsequent client-side plot zoom. The explicit API envelope option remains available and uses TP-local buckets, but the TP plot does not request it. TP CSV requests use the same Line setting and still export full-resolution results.

**Full test:** existing `/filter?t0=...&t1=...` semantics are unchanged. `window_bounds` uses a truncated lower sample index and `ceil(upper*fs)+1` as the exclusive end, clamped to the dataset. Original `/data` uses the same rule. In line mode both read that same slice. In envelope mode both use global 16/256/4096-sample pyramid buckets; filtering processes the complete bucket-aligned slice, possibly including rows just outside the requested viewport. The same timestamp and merge rules align the two responses. Zooming or changing representation can change the processing slice/context and thus edge results. Resizing alone reuses existing responses; plot width captured at the next fetch can change the reduction budget.

For both filtered paths, Auto returns full-rate samples for ranges up to `max(6000, plot_budget)` rows; larger ranges return min/max envelopes. The budget is roughly twice the plot width, bounded to 1000–8000. Forced Line can stride long responses down to at most 8000 points, **after filtering**; this may omit displayed spikes. Envelopes preserve bucket extrema, but discard within-bucket ordering and can conceal sparse missing positions. JSON plot values and time arrays round to six decimals. These reduced responses are for visualization, not full-resolution filtered exports or numerical subtraction. Full-test `nan_counts` currently describes the bucket-aligned processing slice; TP counts describe its exact saved rows.

## Single-plot CSV contract (Phase 6b)

`POST /api/plot-export` accepts `column`, `data` (`original`, `filtered`, or `both`), `sources`, the actual filter settings, and nullable `x_range`. Each source identifies `test` and either saved `tp_id` or full-test `t0`/`t1`, plus the successful plot request's `px` and `display`. Optional paired `expected_i0`/`expected_i1` reject changed source bounds with HTTP 409. Filter parameters use the API spellings `kind`, `order`, `f1`, `f2`, `window_s`, `max_spike_s`, `threshold`, `abs_floor`, and `replacement`.

`dsp.filtered_samples` is now the shared calculation before display reduction. Plot rendering reduces its result exactly as before; filtered CSV uses its unrounded full-rate arrays. Full-test exports reproduce the same bucket-aligned processing context, then remove shoulders outside requested `[i0,i1)`. Saved TP exports process the complete saved interval before any X crop. Substituting `display="line"` for a displayed envelope would change processing context and is incorrect.

With `x_range=null`, CSV includes the complete requested source rows. This is appropriate for unzoomed plots, whose reduced/rounded displayed endpoints may omit final bucket samples or round a sample's actual center. A supplied range keeps inclusive sample centers in actual time coordinates: TP-relative seconds for TPs, stored seconds for Full test. Only a small float64 arithmetic tolerance is used; samples are never rounded to six decimals before selection. Y zoom does not remove rows.

CSV columns are `source_test`, `test_point_id`, `sample_index`, `time_s`, `tp_time_s`, then `<column> [original]` and/or `<column> [filtered]` for the selected choice. Full-test rows leave TP ID/time blank. Original signal/time Arrow values retain their precision and distinguish null (blank), NaN and infinities; filtered nonfinite results are blank, matching displayed missing data. TP IDs are strings to preserve large integer identifiers. Rows group by source test in first-appearance order, then that test's requested TPs, without cross-test resampling.

Every source must succeed before attachment headers are sent. The backend stages into an automatically removed temporary file, holding one test's existing read lock/native-read slot while resolving, processing and serializing its group. Downloads then read the staged file without dataset locks; success, streaming cancellation, transport failure and preparation failure close it. Cross-test groups are sequential snapshots, not a simultaneous dataset revision. Expected bounds detect changed intervals, but not same-shape sample edits made since the plot loaded. No source files change.

The request limit is 128 sources and an aggregate 8,000,000 requested rows before X cropping. Filtering still loads one source's processing context at full rate. Server preparation has no progress or cooperative cancellation yet; canceling a browser request does not stop an active SciPy calculation. Network failure can interrupt the attachment transfer, but a later source's validation/filter failure cannot produce a partial successful CSV. Sidecar provenance and long-export progress/cancellation remain follow-up work.

## Verification

`backend/tests/test_filter_overlay.py` adds 11 tests (20 subcases) for exact/mismatched saved bounds, actual/nonzero/sub-microsecond time origins, raw/local/automatic envelopes, bounded strided lines, adjacent-TP exclusion during processing, legacy/open TPs, known gaps and short segments, missing/±infinite samples, invalid scope, and byte/mtime preservation for all seven filter kinds. Existing `test_despike.py`, `test_time_gaps.py` and `test_window_budget.py` cover detector behavior and unchanged full-test alignment. Run with the project's Python 3.13 environment:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_filter_overlay.py backend\tests\test_despike.py backend\tests\test_time_gaps.py backend\tests\test_window_budget.py -q
```

`backend/tests/test_plot_export.py` adds 22 tests / 34 subcases for every filter kind, unrounded/large/batched CSV values, exact TP and full-test context, inclusive X selection, cross-test origins/rates, IDs and column collisions, null/NaN/infinite behavior, gaps, expected bounds, strict limits, read-only staging and error/cancellation cleanup. The full backend suite after this change passed: **282 tests, 162 subcases**, with two existing dependency deprecations.

This audit does not establish a universal spike/noise classification for real rig signals. Reproducibility sidecars, multi-plot export, long-export progress/cancellation and the deferred spectral-method changes remain later Phase 6 milestones.
