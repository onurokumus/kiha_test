# Saved analysis sessions — Phase 8b

Updated 2026-09-10. Continuation of [source recovery](SESSION_RECOVERY_VERIFICATION.md).
Scientific calculations, data storage and export methods are unchanged.

## User workflow and compatibility

The visible **Sessions** header button opens a keyboard-accessible native dialog.
**Save session file** downloads a named `.ptt-session.json` from the live analysis
state, independently of the automatic-recovery debounce. The file contains a
format identifier, version, display name, save time and version-1 analysis settings.
It contains source UUID/revision references, not source samples or metadata backups.

**Session file** reads and validates a file, then previews the recovered mode,
layout/maximized slot, linked tests, selected/hidden points, variables and any
compatibility notices. Reading or closing the preview leaves the current analysis
and its automatic recovery intact. Open checks the library again: a changed result
requires reviewing the new preview and opening again. Missing/replaced sources
require the explicitly labeled **Open with available sources** action. Legacy
version-1 name-only settings require explicit reconnection by name first.

Invalid JSON, unknown format/versions, invalid shapes/ranges/filter kinds, excessive
array lengths and files over 2 MB are rejected visibly. Read/network errors permit
retry; a new file or Close invalidates outstanding responses. Existing Edit/Split
draft guards run before entering the session dialog. Opening replaces the working
analysis and enables automatic recovery of the accepted view once hydrated.

No server/browser named-session library, overwrite policy, retention or dependencies
were added. Users manage downloaded files through their browser/filesystem. Library
datasets must retain their UUIDs; re-uploading the same CSV creates a distinct source.

## Persisted state and range rules

Saved settings cover the current test; ordered TP selections, colors and hidden
state; scatter variables, filters, zoom, clustering, datasheet/error-bar visibility;
per-slot variables, filters and original overlays; time-note visibility; layout,
panel ratio/collapse and maximized slot; Time X/per-slot Y and Full time interval;
Spectrum estimator, source, Hz/order/RPM and Log Y; XY source and per-slot variables.

Spectrum now retains each slot's X range, and XY retains each slot's X/Y ranges.
Wheel, box zoom and pan share the same scale-capture path. Ranges survive resize,
maximize/restore, layout and mode changes, automatic recovery and explicit reopen.
**Reset axes** and double-click return to automatic ranges. Full-time and Spectrum
Y axes retain their existing automatic behavior; neither exposes independent Y zoom.

Range context includes dataset UUID/sample revision, source mode and interval/TP
bounds, variables, and relevant spectral estimator/axis/RPM settings. It excludes
names, colors, geometry and Spectrum Log Y (only X is saved). A mismatched context
uses automatic ranges; its last manual range can return with the matching context.
There is one retained context per slot/mode, not a history. Source recovery clears
all saved ranges for changed/missing sources or changed TP selections. Renaming the
same source preserves ranges. Missing variables keep their original slots/settings.

uPlot scale work is flushed while programmatic data/size/range updates are muted;
automatic initialization cannot overwrite saved ranges. Opening remounts the grid
and invalidates cached and outstanding metadata/trace loads. The existing recovery
path and explicit Open share one application routine. Autosave and file download
share one state-capture routine. Missing saved RPM choices survive partial metadata
loading instead of being silently replaced by the first available RPM variable.

## Verification

Commands from the repository root (build/lint run in `frontend`):

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests
npm.cmd run build
npm.cmd run lint
python -X utf8 -u scripts/verify_saved_sessions.py
python -X utf8 -u scripts/verify_session_recovery.py
python -X utf8 -u scripts/verify_spectrum_exports.py --frontend-port 3260 --backend-port 8260
python -X utf8 -u scripts/verify_xy_exports.py --frontend-port 3250 --backend-port 8250
```

The existing Time Y and rendering scripts ran together against an owned isolated
server on 3231/8231 using `verify_data_quality.servers`; see their reports for checks.
The new script uses 3240/8240 and the recovery script uses 3220/8220. All use temporary
datasets and browser profiles, with only owned child servers stopped afterward.
Native reads use the project Python 3.13 environment; global Python runs Playwright.

- Backend: **406 tests passed**, with the two existing dependency deprecation warnings.
- Frontend: build and lint passed. The existing Vite bundle-size warning remains.
- Recovery: 12 resolver cases plus real lifecycle, source changes, delayed metadata,
  busy states, failure/retry, legacy reconnection, missing-variable and desktop checks.
- Time Y/rendering: independent ranges, keyboard/mouse, filtering, context isolation,
  nine slots, crosshair alignment, scatter redraw/tooltips and actual 125%/150% zoom.
- Spectrum exports: **36 native CSV entries and 13 PNGs**, with **20 source files
  unchanged**; axis crops/reset, method/visibility/partial/stale/failure paths pass.
- XY exports: **42 native CSV entries and 18 PNGs**, with **14 source files unchanged**;
  both-axis crops, tiny values, mixed grids, layout, failure and desktop checks pass.
- Saved sessions: **12 real downloads** (Time, Spectrum, maximized Spectrum, XY,
  nine-slot XY, Welch/order/Log Y, Full Time/Spectrum/XY, and three keyboard downloads).
  File/reload/mode/layout/maximize/range checks pass, including hidden colors,
  scatter filters/zoom, original overlays, annotation/display options and saved RPM.
  An injected localStorage quota failure proves explicit Save captures the live
  ranges even when automatic recovery remains stale. Invalid JSON/version/size/
  structure/range/filter, file-read and source-network failures, retry, Close and
  legacy reconnection pass. Real rename-between-preview-and-Open requires a new
  review; trash/replacement/restore, changed TP bounds and a real variable edit are
  handled. Edit Cancel preserves its draft; Discard enters the session dialog.
  Keyboard Save/Escape/focus, resize/maximize and actual 125%/150% zoom pass, with
  screenshots visually inspected. **22 source files unchanged** before deliberate
  lifecycle edits, **11 independent beta files unchanged** throughout, no page errors.
  Results/downloads/screenshots: `data/verification/saved-sessions/`.

Test-harness lessons: use explicit different ports when running export suites
together (their defaults overlap); discard a mixed-port fixture run. Wait for the
specific slot's plot when a filtered slot loads after its neighbors. Do not edit
App code during a browser suite: hot reload intentionally reruns bootstrap effects
and can invalidate assertions about the accepted workspace. A recovery rerun passed
after one rapid-navigation run logged a canceled metadata GET.
The new desktop suite caught and verified a fix for restoring focus before closing
the native modal; Close now removes its inert state before focusing the trigger.

## Limits and continuity

- Session files reproduce configuration against the current available library,
  not historical samples, equations, annotations, component metadata or revisions.
  Revision tokens are conservative local change tokens, not full-file hashes.
- LocalStorage can be unavailable or full; its existing best-effort behavior remains
  (quota failure tested). Explicit downloads work independently. Browser download location/retention is
  managed by the user; the application reports download initiation, not disk success.
- Native per-chart legend toggles remain transient. Persistent source visibility
  uses the selected-point controls. Hover/cursor state and open editor/menu drafts
  are not session settings.
- Source checks occur at recovery/open and guarded metadata/TP reads. This is not
  continuous multi-tab synchronization or universal UUID checks on every DSP API.
- Tested on Windows Chromium at desktop sizes and real browser zoom. Other browsers,
  unusually large libraries/session files and download-policy restrictions are not
  exhaustively exercised. Existing cold TP-statistics cache and comma-named Time
  column limitations remain separate.
- Phase 9 plot context menus are next. Component totals/association-driven
  recalculation remain Phase 10 and require explicit statistical/deletion policy.
