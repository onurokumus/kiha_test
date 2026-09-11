# Phase 6c — selected multi-plot time-series export

Subsequent [Phase 6g](ANALYSIS_METADATA_VERIFICATION.md) adds default metadata ZIPs,
and [Phase 6h](EXPORT_PROGRESS_VERIFICATION.md) adds progress and cooperative
cancellation. Those reports supersede delivery/cancellation limitations below;
the numerical contract and recorded milestone evidence remain applicable.

2026-09-10, branch `feature/resumable-multipart-upload`, baseline `873c80c`.
Existing user and Phase 1–6b changes remain uncommitted and preserved.
Implementation and verification are complete.

## Scope and decisions

Test points and Full test grids expose **Export selected plots**. Select mounted
grid slots, choose a **2 × 2** or **3 × 3** layout, and choose original, filtered,
or both independently for each CSV. Duplicate variables are distinct slots and
retain their own filters. Selections are packed in ascending original grid
order; a preview shows placement and empty cells. Exceeding layout capacity
blocks download with an explanation; nothing is silently dropped.

CSV downloads one ZIP containing one full-resolution CSV per selected slot.
Separate files preserve independent sources, filters, relative time origins and
window scopes without joining, interpolating or inventing shared samples.
Names such as `01_slot-2_<source>_<variable>_test-points_both.csv` record packed
position, original grid slot and the existing single-plot filename. PNG produces
one fixed grid, with original slot labels and complete annotations in each cell.
Its actual visible traces are independent of the CSV data choices.

The dialog reuses each mounted plot's current export builder and synchronous
capture callback. Both single and multi-plot entry points therefore apply the
same source eligibility, native legend visibility, exact bounds, current X crop,
filter readiness, processing context, warnings and units. Restore a maximized
plot before selecting multiple slots; hidden/unmounted peers are not refetched.
See [the single-plot contract](PLOT_EXPORT_VERIFICATION.md) and
[filter methods](FILTER_METHOD.md).

Native modal keyboard focus, Escape/Close, resizing and desktop zoom are
supported. Context changes abort browser delivery and close the dialog. Source
or filter failures identify the affected slot; retry uses the current context.
Closing suppresses late ZIP and PNG downloads. Preparation is shown, without
claiming that the underlying server calculation was canceled.

## Backend and image ownership

`POST /api/plot-export/bundle` accepts:

```json
{
  "layout": "2x2",
  "plots": [
    { "slot": 2, "request": { "column": "load_N", "data": "original", "sources": [{ "test": "example", "tp_id": 7 }], "filter": null, "x_range": null } }
  ]
}
```

Slots must be unique, ascending integers 1–9 and fit the selected layout.
Existing single-plot request validation remains authoritative. The entire bundle
has a limit of **128 source references** and **8,000,000 requested rows before
cropping**, counting duplicate-variable slots separately. Metadata preflight
rejects excessive work before DSP; a shared budget checks actual locked reads
again if a source grows between preflight and staging. Validation messages,
including source-reference limits, are shown in the export dialog.

Each CSV reuses the full-resolution single-export serializer and shared DSP.
It is staged in a spooled temporary file, streamed into a staged ZIP, and closed
immediately. Only a complete ZIP is sent. Late-source errors, streaming failure,
transport cancellation before headers, and successful responses close temporary
files. Browser downloads begin only after a complete Blob. No dataset writes,
metadata sidecars or new runtime dependencies are introduced.

PNG captures every selected live plot synchronously, then composes and encodes.
Cards retain native canvas pixels, full wrapped labels, source identities,
actual axes/filter settings/warnings and visible-series legends. Equal-width,
equal-height cells preserve fixed 2×2/3×3 placement and leave unused cells blank.
There is no plot refetch, live resize, silent clipping or downsampling. Source
cards are disposed before asynchronous encoding; partial capture failure cleans
all earlier cards and never downloads an incomplete image. Different unsettled
pixel densities during zoom produce an explicit retry message.

Each image is capped at **8,192 pixels per edge / 16 million pixels**; each
capture retains the **64,000-character** annotation limit. Combined captures
also total at most 16 million pixels. The owned source/output RGBA allocation
is bounded to about 128 MB during composition, excluding browser internals and
live plots. Oversized output fails explicitly.

## Verification

- Full Python 3.13 backend suite: **297 passed, 174 subcases**.
- Export module: **37 tests / 46 subcases**, including 15 new bundle tests.
  Covers 2×2/3×3 entry names/order, byte parity with single exports, independent
  filters/duplicate variables/crops, strict request validation, aggregate limits
  including growth after preflight, late failure, read-only sources and cleanup.
- Frontend build and lint passed after final integration and the restore fix.
- Existing `verify_plot_exports.py`: **19 CSV / 9 PNG** real downloads passed.
  Focused `verify_plot_export_ui.py` also passed modal focus/centering and two
  hidden-legend PNG regressions after the shared capture refactor.
- Isolated PNG helper QA passed real opaque output, sorted slot labels, empty
  cells, partial-capture cleanup, delayed Close suppression and oversize errors.
- Existing Y-axis and rendering suites passed against isolated 3160/8160 servers
  after the readiness fix: X/Y gestures, session ranges, resize/maximize/restore,
  crosshair alignment, scatter interactions and 125%/150% desktop zoom. The owned
  servers and temporary fixtures were cleaned up.
- `python -X utf8 -u scripts/verify_multi_plot_exports.py`: passed **13 ZIP
  downloads / 61 CSV entries and 6 combined PNGs**, using real temporary uploads,
  full-rate Python 3.13 oracles and isolated Chromium. Every CSV is numerically
  compared with source/DSP output and byte-compared with the single-plot endpoint.
  ZIP CRC/schema/entry names/order are checked. PNGs are decoded for dimensions,
  opacity, occupied panels and intentionally empty cells; canvas instrumentation
  checks complete context and unscaled copied panels. Actual PNGs and dialogs
  were visually reviewed.
- The live run covers four/nine-slot exports, noncontiguous selection packed in
  grid order, duplicate-variable independent filters, per-slot original/filtered/
  both choices, Full envelope processing context and explicit X cropping,
  layout capacity, maximize/restore, keyboard/Escape/focus, 1100px windows and
  actual 125%/150% browser zoom. Late DSP failure yields no partial ZIP; retry
  succeeds. A FastAPI-style 422 response verifies readable limit details.
  Closing during held ZIP delivery or delayed PNG encoding yields no late file.
- **21 source/raw/pyramid/metadata/TP files unchanged**, no unexpected browser
  writes/errors. `git diff --check` and harness syntax checks pass.

Evidence is ignored under `data/verification/multi-plot-exports/` (`results.json`,
ZIPs, PNGs, dialog screenshots and server logs); regression logs are under
`data/verification/multi-plot-regressions/`. The 3150/8150 multi-export,
3130/8130 single-export, 3140/8140 focused UI and 3160/8160 regression servers
were owned by these checks and stopped. Fixtures and browser profiles were
temporary; user datasets/profiles were not used. Existing dependency,
bundle-size and Windows Git permission/CRLF warnings remain non-failing.

Live nine-slot verification found a pre-existing restore race: early metadata
from a narrower test compacted saved plot columns while per-slot filters stayed
at their indices. A narrow metadata/hydration readiness guard and a deterministic
delayed-metadata browser regression passed because independent slot
meaning is necessary for this milestone. Broader stale-column/session policy
remains separate.

## Limits and remaining scope

- CSV snapshots are sequential per test and per slot; there is no atomic
  cross-slot/cross-test revision. Bounds guards cannot detect same-shape sample
  edits after plotting. CSV reads current stored data; PNG captures loaded data.
- Server filtering and ZIP compression do not yet support cooperative progress
  or cancellation. Browser Blob buffering and full-rate processing remain;
  maximum-size performance is untested.
- PNG retains displayed reduction/precision. Chromium desktop is the verified
  target; Firefox/Safari and maximum-size exports are untested.
- The pre-existing Windows cold statistics-cache replacement race is separate.
  Fixtures warm statistics sequentially as documented in the 6b report.
- Spectrum/XY export, scientific Spectrum prerequisites, metadata sidecars and
  long-export progress/cancellation remain open. The combined export TODO stays
  open until the remaining plot modes are handled; 6c completes its selected
  multi-plot time-series slice.

Next milestone: **Phase 6d — Spectrum scientific prerequisites for export**.
Reconcile saved TP half-open intervals and actual time, preserve true peak-bin
frequencies, expose reduction/method metadata and missing counts, clarify PSD
units on frequency/order axes, and establish a shared unreduced result path.
Verify numerical behavior and primary method references before extending exports
to Spectrum/XY. This follows the earliest open export requirement; metadata
sidecars and cooperative progress/cancellation remain subsequent work.
