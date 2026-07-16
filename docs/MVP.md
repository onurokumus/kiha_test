# Time-Series Test Data Plotter — MVP Spec

Web-based tool for plotting and analyzing electric motor / propeller test-rig data
(thrust, power, vibration, temperature, etc.). Multiple teams will use it, including
teams that need to inspect full-resolution 2 kHz vibration data.

## 1. Data Assumptions

- Input: CSV, ~112 columns, 2048 Hz sample rate, up to ~1 hour per test.
- Scale: 1 hour = **7.37 M rows ≈ 826 M values ≈ 6.6 GB in RAM (float64), 6–10 GB as CSV**.
  This rules out "load whole CSV into browser" — see Architecture.
- Time column present, typically the first column (`time_s`). **Relative seconds** —
  no absolute timestamps / timezones in MVP.
- Units are encoded in column names (e.g. `thrust_kg`). Axis titles show the column name
  verbatim; no separate unit handling in MVP.
- One column *may* be a test-point ID column — optional, user selects it or splits manually.
- Real data will contain NaN / missing values and possibly gaps. Tool must not crash on these.

## 2. Architecture

**Client–server. All heavy work happens server-side; the browser only ever receives
downsampled or windowed slices (≤ a few thousand points per series).**

| Layer    | Choice                                        | Why |
|----------|-----------------------------------------------|-----|
| Backend  | Python, **FastAPI** + **polars/pyarrow** + **scipy** + numpy | Fast columnar range reads, mature DSP |
| Frontend | **React + Vite + TypeScript**, **uPlot** for charts | uPlot is canvas-based, ~40 KB, handles 100k+ points smoothly; Plotly/Chart.js cannot |
| Storage  | Filesystem only — **no database.** Bulk samples in Parquet, everything human-readable in JSON | Metadata must be inspectable/editable in vi/VS Code |

### Storage layout

```
data/
  tests/
    <test_name>/
      meta.json              # name, fs, columns, units, row count, NaN policy chosen, source info
      raw.csv                # original upload (kept for provenance / export; deletable)
      data.parquet           # working copy; row-grouped so time ranges can be read without full scan
      pyramid/               # precomputed min/max downsample levels (see §5)
        L16.parquet
        L256.parquet
        L4096.parquet
      testpoints.json        # test point definitions — human-editable, uploadable, downloadable
```

Rationale: CSV cannot be range-read (no fixed row width — reading rows 3.1M–3.3M requires
scanning from byte 0). Parquet row groups allow seeking straight to a time range and reading
only the needed columns. All metadata stays ASCII/JSON per requirement. CSV export is always
available (§3, §4).

### Ingest pipeline (on upload)

1. Stream-parse CSV in chunks (never fully in RAM).
2. Scan for NaN/missing values per column → report to user (§7).
3. Convert to `data.parquet` (row groups ≈ 30 s of data each).
4. Build min/max downsample pyramid (§5).
5. Write `meta.json`.

Target: 1-hour CSV ingested in ≲ 2 minutes, server RAM stays < ~2 GB throughout.

## 3. Pre-Process Features

- Upload test data (CSV). Progress indicator; ingest pipeline above.
- Choose test data from previously uploaded tests (list from `data/tests/`).
- Split test data into test points (§6).
- Save test points → `testpoints.json`.
- Upload ("read test points file") an existing `testpoints.json` from local machine.
- Export: full test data as CSV; individual test points as CSV; `testpoints.json` download.

## 4. Post-Process Features

- Choose test → choose test point(s).
- Filter test points by variable range: user picks variable, range [x, y], and an
  aggregation mode — **mean / min / max / any-sample** (any-sample = keep TP if any
  sample falls in range). All four available; per-TP aggregates come from the pyramid,
  so this is cheap.
- Plot variables vs time.
- Plot variable vs another variable (XY plot).
- Signal processing (server-side, applied per selected series on the current test point / range):
  - Butterworth low-pass / high-pass / band-pass / band-stop (order + cutoff(s) user-set)
  - Moving average (window user-set)
  - Detrend
  - FFT magnitude spectrum and Welch PSD — plotted against frequency (0 – 1024 Hz Nyquist).
    Runs on a single variable over a test point (~123k points for 60 s) — cheap, no full-file FFT.
  - Filtered series can be overlaid on the raw series.
- Multiple series in a single plot (multi-line, shared time axis, per-series y-axis scaling if units differ).
- Multiple plots simultaneously (grid layout).
- **X-axis link toggle**: button to group plots so zoom/pan is synchronized across them.

## 5. Downsampling Strategy (vibration-safe)

LTTB is *not* used — it produces pretty lines but drops extremes, which is unacceptable
for vibration data. Instead:

- **Min/max envelope per bucket (M4-style)**: for each screen-pixel bucket, keep min and max
  (plus first/last). A spike can never disappear.
- **Precomputed pyramid**: levels at ÷16, ÷256, ÷4096 stored as Parquet at ingest.
  Each level stores per-bucket min/max per column.
- **Serving rule** per plot request (viewport time range + pixel width):
  - If raw points in viewport ≤ ~5,000 → serve **raw full-resolution 2048 Hz data**.
    (This is how the "we need 2 kHz" teams get true samples: zoom in.)
  - Otherwise → serve from the coarsest pyramid level that still gives ≥ ~2 points/pixel,
    rendered as min/max envelope.
- Zoom/pan triggers a new windowed request; target < 300 ms round trip.
- XY plots (var vs var): naive stride decimation is acceptable for MVP; revisit later.

## 6. Data Split — Test Points

A **test point** is a named segment of a test:

```json
{
  "version": 1,
  "test": "demo_60s",
  "source_file": "demo_60s.csv",
  "fs_hz": 2048,
  "test_points": [
    {
      "id": 1,
      "name": "TP-01",
      "label": "throttle-40pct",
      "start_s": 4.0,
      "end_s": 12.0,          // null allowed — see below
      "start_idx": 8192,
      "end_idx": 24576,
      "notes": ""
    }
  ]
}
```

- Test points are **index/time ranges into the parent test — data is never copied**
  (no GB duplication). Export materializes a CSV on demand.
- `end_s` optional: if null, the end is the start of the next test point, or end of data.
- Split UI: plot selected variable(s) vs time, user places split points.
  **Drag handles: one for start, one for end (end optional).** Zoom/pan (§5) works here.
- Optional ID column: if one column is a test-point ID, user selects it and the tool
  auto-generates test points from its value transitions. Tool may suggest candidate columns
  (integer-valued, low cardinality, step-wise). User confirms; manual split always available.
- `testpoints.json` is human-editable and can be uploaded/downloaded.

## 7. NaN / Missing Data Policy

- Never crash. On ingest, scan and report per-column NaN/missing counts and locations.
- User chooses handling (per column or globally):
  1. Keep as gaps (plot lines break at NaN) — default
  2. Drop rows containing NaN
  3. Zero-fill
  4. Linear interpolate
- Chosen policy recorded in `meta.json`. Filters/FFT warn if the input range contains NaN.

## 8. Dummy Data

`generate_dummy_data.py` (repo root) generates realistic rig data: throttle-step test points,
rpm/thrust/torque/electrical chain with physical coupling, shaft-synchronous vibration
harmonics (amplitude ~ rpm²), blade-pass acoustic, first-order thermal lags, battery cells,
strain gauges, thermocouples, spares — 112 columns at 2048 Hz. Includes deliberate NaN blocks
(`temp_bearing_c`, `airspeed_ms`, `strain_07_ue`) and a `tp_id` column, plus ground-truth
`*.testpoints.json` in the exact §6 schema.

- `dummy_data/demo_60s.*` — 60 s, 122,880 rows, 97 MB. For functionality development.
- `dummy_data/perf_1h.*` — 1 h, 7,372,800 rows, ~6 GB. For performance/lag testing.

```
python generate_dummy_data.py --duration 60   --name demo_60s
python generate_dummy_data.py --duration 3600 --name perf_1h
```

## 9. Performance Targets

| Operation | Target |
|---|---|
| Ingest 1 h CSV (parse + Parquet + pyramid) | < 2 min |
| First plot of full 1 h test | < 2 s |
| Zoom/pan plot update | < 300 ms |
| FFT / filter on a 60 s test point | < 1 s |
| Server steady-state RAM | < 2 GB |
| Browser per-plot payload | ≤ ~5k points/series |

## 10. Out of Scope (MVP)

- Authentication / user accounts / multi-tenancy
- Database (filesystem + JSON only)
- Live streaming from the rig
- Report generation

## 11. Resolved Decisions

- Units: encoded in column names (`thrust_kg`); axis title = column name, no unit logic.
- Time: relative seconds, no absolute timestamps.
- TP range filter: all aggregation modes offered (mean / min / max / any-sample).

## 12. Open Problems / Known Risks

- **Non-uniform time / jitter**: idx↔time mapping assumes uniform 2048 Hz. Real rig data may
  have jitter or gaps in the time column. Mitigation: derive time from the time column, not
  from row index; ingest warns if Δt deviates > 1% from nominal.
- **NaN policy change after ingest**: switching policy (e.g. gaps → interpolate) requires
  rebuilding `data.parquet` + pyramid. Acceptable (re-run pipeline), but UI must warn it takes time.
- **XY plot downsampling**: naive stride decimation can alias/mislead (e.g. hide hysteresis
  loops). Accepted for MVP; density/heatmap mode is the proper fix later.
- **Filter edge transients**: Butterworth on a test point slice has edge effects. Use
  `filtfilt` with padding; if the TP touches data boundaries, warn.
- **Upload name collisions**: same test name uploaded twice → suffix or prompt user.
- **Concurrent users**: unknown count. Single-process FastAPI fine for a handful; revisit
  worker count if many teams hit it simultaneously. Not an MVP design change.
