# Export progress and cancellation — Phase 6h

Completed and verified: 2026-09-10. Full backend, build/lint, real long-export
lifecycle checks and all metadata/file-only browser regressions pass.

## User behavior

Single and selected Time, Spectrum and XY export dialogs now show the current
operation and a **Cancel export** button. Download/selection controls stay
disabled during a run. Progress includes the current grid slot, source and a
stage-local row/bin/byte count when measurable. Native calculation and PNG
encoding stages use an indeterminate indicator, without an invented overall
percentage or time estimate. The progress area remains visible when scrolling.

The shared dialog lifecycle distinguishes running, canceling, canceled,
completed and failed. Cancel waits for cleanup acknowledgement, suppresses the
download and restores the initiating action's keyboard focus. Close, Escape,
context changes and unmount also abort the operation. A previous operation's
late response cannot replace a newer run's feedback. Completion means the file
was sent to the browser; the application cannot confirm saving it to disk.

All current numerical methods, source scopes, CSV choices, metadata sidecars,
file-only options and staged all-or-error attachments are preserved. Legacy
API clients can continue calling the existing export endpoints without a token.

## Server lifecycle and cleanup

- `POST /api/export-progress` registers a random 128-bit token. The binary
  export request includes it in `X-Export-ID`; GET on the token reports status
  and renews its lease; DELETE requests cancellation. Each token can start
  one request. Cancel-before-start is terminal and prevents that request.
- Trackers are process-local status only: no persistent export history, queued
  background jobs, database or retained download artifacts. Existing POST and
  response scopes continue owning their temporary files.
- At most eight active/pending trackers and 64 total records. The polling lease
  is 45 seconds, terminal records expire after 120 seconds, and lazy pruning
  also runs on lookup/registration. Lost polling requests cancellation; an
  uninterruptible native block remains tracked until its next checkpoint.
  Restart loses trackers and callers must retry. This follows the application's
  existing single-process server requirement.
- Cooperative checks run while waiting for read locks/native slots, between
  native Parquet batches, source/slot boundaries, filter columns/gap segments
  and despike replacement runs, before/after native spectrum work, hashing,
  and each 256 KiB ZIP copy. Read-lock-before-slot ordering is unchanged.
- Cancellation raises before attachment headers during staging and unwinds
  generators, read locks and input/output spools. During response transfer,
  cancellation stops iteration; the browser never offers an incomplete Blob.
  Client disconnects request cancellation even for callers without trackers.
- Image uploads are bounded and checked as chunks arrive. A stalled receive
  also checks cancellation every 150 ms without abandoning a spool write. The disconnect
  monitor never reads the ASGI receive channel concurrently with image upload.
  An interrupted upload is canceled, with its spool closed, rather than logged
  as an unexpected server failure. Shielded worker completion prevents a
  canceled request task from abandoning a worker that still owns resources.

## Browser work

One shared AbortSignal connects controls, polling, transfer and native canvas
work. Cancellation uses a separate bounded keepalive request, so aborting the
binary request does not abort cancellation itself. The client waits up to 12
seconds for terminal server status (individual requests are timeout-bounded).
If acknowledgement is unavailable, feedback says the download was canceled
and server cleanup is not yet confirmed. Disconnect detection and lease checks
continue on the server.

PNG capture freezes all selected native canvases and their loaded provenance
synchronously. Composition then yields between cells, using only owned frozen
captures, so Cancel works without mixing snapshots. Each path disposes every
captured/composed canvas on completion, cancellation or failure. File-only PNG
never creates a server tracker; metadata packaging uses the same tracked path
as numerical downloads. Received bytes are buffered before offering a file;
compressed Content-Length is not misrepresented as a decompressed byte total.

Native library calls already executing cannot be safely killed. Cancellation
waits for the current call, then prevents further processing/serialization.
Browser `toBlob` similarly has a result callback and no abort argument; its
result is discarded after cancellation. See the authoritative
[AnyIO thread cancellation guidance](https://anyio.readthedocs.io/en/stable/threads.html#reacting-to-cancellation-in-worker-threads)
and [canvas encoding API](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toBlob).
No filtering, FFT, normalization, units or crop formulas changed in this phase.

## Verification

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests
python -X utf8 -u scripts/verify_export_progress.py
python -X utf8 -u scripts/verify_analysis_metadata.py
python -X utf8 -u scripts/verify_xy_exports.py --frontend-port 3160 --backend-port 8160
python -X utf8 -u scripts/verify_spectrum_exports.py --frontend-port 3170 --backend-port 8170
python -X utf8 -u scripts/verify_plot_exports.py --frontend-port 3160 --backend-port 8160
python -X utf8 -u scripts/verify_multi_plot_exports.py --frontend-port 3170 --backend-port 8170
# frontend directory:
npm.cmd run build
npm.cmd run lint
```

New backend coverage verifies lifecycle/reuse, early cancellation, real batch
and native-block cancellation, later-slot suppression, ZIP/image spool cleanup,
writer/slot waits, disconnects, interrupted/stalled uploads, limits/leases/expiry and
the disconnect-monitor shutdown race. Existing numerical/sidecar suites retain
their native value and hash oracles.

The new browser harness ingests an isolated **700,000-row** fixture using Python
3.13. It observes running row/bin progress and cancels real **2.8M-row requested
multi-plot work** in Time, Spectrum and XY, confirms five server cancellations
including Close and reload, and verifies no corresponding download. A retry
completes a 700K-row CSV with correct first/last indices, row count and SHA-256
sidecar. Cancellation during a real throttled response discards received partial
bytes. Browser-only barriers hold a real PNG encoding callback and slow
composition yields to verify both cancellation stages deterministically.
Failure/retry/completed states, keyboard Cancel/Escape/focus, desktop1100px and
actual125%/150% zoom pass. No browser page errors or unexpected source writes;
all seven source/raw/pyramid/meta/TP files remain unchanged.

- Backend: **361 passed /245 subcases**, including **11 new lifecycle tests /
  2 subcases**. Final focused export checks also pass. Build/lint pass; existing
  non-failing bundle-size and dependency deprecation warnings remain.
- Metadata suite: **24 ZIP packages /28 CSV entries /13 packaged PNGs**, plus
  three file-only PNGs. Equation edit-after-load, source/hash/settings parity,
  nine slots, failure/Retry/Close and desktop controls remain verified.
- File-only suites: XY **42 CSV entries /18 PNGs /14 native references**;
  Spectrum **36 CSV entries /13 PNGs /20 native cases**; Time single **19 CSVs /
  9 PNGs**; Time multi **13 ZIPs /61 CSV entries /6 PNGs**. Numerical values,
  crop/visibility, filters, duplicate variables, native FFT/order/PSD, precision,
  partial/stale/failure guards, maximize/restore, keyboard and zoom checks pass.
- Progress and Cancel at 150% zoom were visually reviewed. Long-export7,
  metadata14, XY14, Spectrum20 and Time21 fixture files remain unchanged outside
  the metadata suite's explicit test edit. All owned servers stopped and all
  temporary datasets/profiles removed. No dependencies changed.

Evidence is ignored under `data/verification/export-progress/` and the existing
export directories. Harness corrections: whitelist lifecycle control requests
as exports, wait for canceled server state/canvases after reload instead of an
abandoned request's network-idle state, and retain exact endpoint matching in
request observers. Complete reruns pass. The disconnect-probe shutdown race
found during implementation has a targeted regression test.

## Limits and continuity

Cancellation is cooperative, with latency bounded by the current native read,
calculation, compression write or canvas capture/encode block. Extremely large
single native calls can outlast the client acknowledgement window. No ETA,
restart-resume, cross-process registry or background download queue is added.
Existing size/row/canvas/metadata limits remain. Peak-limit memory/disk capacity
is unmeasured; the real 700K-row fixture is below those limits. Chromium desktop
is verified; Firefox/Safari are untested. Existing cold statistics-cache races
and comma-containing Time column-name limitations remain separate.

Next milestone: Phase 7a, upload descriptions and editable test notes.
