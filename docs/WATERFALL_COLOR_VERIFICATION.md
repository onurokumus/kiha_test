# Waterfall color-limit verification

The requested controls edit the minimum and maximum of each waterfall plot's
shared color scale. Verification uses two uploaded synthetic sources with
different 84/85 Hz tone amplitudes, a separate reference variable and an
all-zero variable. Both source maps in a slot use the same limits.

## Command and isolation

```powershell
python scripts\verify_waterfall_color.py
```

The verifier uses installed Python/Playwright for browser and standard-library
operations. The `verify_data_quality.servers` helper runs the native backend
with `backend/.venv/Scripts/python.exe` (Python 3.13) and Vite on isolated ports
8352/3352. It refuses occupied ports, terminates only its own child processes,
and removes its temporary dataset, browser profile and zoom extension on exit.
The webapp-testing helper's `--help` was checked first; the repository helper
supplies the required native runtime and existing dataset isolation.

Only the browser-served test module exposes its existing uPlot instance,
computed color ticks and bitmap pixel reader. No production source is
instrumented. HTTP responses are actual backend FFT results. Every exported
CSV cell is compared against the displayed source grid using the existing
detail verifier's reference checker.

## Acceptance coverage

- Legacy sessions without saved limits use Auto: zero through the shared
  maximum in linear mode, or six decades below the shared maximum in log mode.
  Manual limits apply atomically to both source maps. Bitmap pixels below and
  above the limits use the endpoint colors.
- Enter in the maximum field applies both values and retains keyboard focus.
  Empty, inverted, equal, negative linear and nonfinite drafts show accessible
  validation feedback and leave the committed colors and axes unchanged.
  Keyboard Auto restores the active mode and retains button focus.
- Color edits and linear/log toggles do not fetch another FFT grid. Frequency
  and elapsed-time scales remain unchanged. A manual color range survives
  both-axis refinement, maximize/restore and reload with the saved viewport.
- Linear and log limits persist independently. Negative log bounds are valid;
  Auto clears only the active mode. All-zero magnitude maps to the minimum
  color in both modes.
- The narrow range `1` through `1.00001` retains distinct minimum, midpoint and
  maximum tick labels, exact visible limits and exact PNG provenance.
- Three slots retain independent variable ranges. Changing a restored slot's
  column does not apply the previous variable's limits. Two slots displaying
  the same variable retain different ranges through reload.
- A real session JSON download includes every slot's column and both saved
  modes. Opening that file after changing the active limits restores all three
  slots and both linear/log settings through the normal session UI.
- CSV downloads before and after manual color changes are byte-identical;
  every magnitude and source/frame/cell boundary matches the loaded grid.
  PNG sidecars record the exact manual range, linear/log transform and
  `color_range_mode: manual` for both source facets. Images show those limits.
- Closing Analysis details and immediately opening Export through the keyboard
  works. The controls remain visible at 1100 px desktop width and actual
  125%/150% browser zoom, with preserved ranges/axes and no horizontal overflow.

## Results and evidence

Passed 2026-09-14: all nine browser check groups, five actual export packages
(two CSV and three PNG), a session file Save/Open round trip, all 14 fixture
source files unchanged, and no browser page errors. Owned server/profile/data
cleanup completed. No product failure remains in this suite.

Frontend build/lint and all 26 helper tests pass (eight new color/session/tick
cases). All 489 backend tests pass on Python 3.13.14 in 48.95 seconds, with only
the two existing dependency deprecations. Build retains its existing bundle-size
notice. Independent code review and whitespace checks pass.

The existing `python scripts\verify_waterfall_detail.py` also passes all eight
groups unmodified on stable final source. Two earlier runs dismissed the export
menu immediately after closing Analysis details. The exact pre-dialog hover,
close and keyboard-export sequence passed twice in an isolated diagnostic,
followed by 30 repeated cycles and the complete stable regression. The cause
was not reproduced; concurrent edits during earlier runs are a possible but
unconfirmed contributor. Details-close focus avoids scrolling, and no broader
menu implementation change was needed.

Evidence is under ignored `data/verification/waterfall-color/`:

- `linear-manual.png`, `narrow-range.png`, `per-slot-limits.png`: visible controls
  and plot scale states.
- `auto-grid.zip`, `manual-grid.zip`: unchanged linear-magnitude CSV data and
  source/method metadata.
- `manual-linear-image.*`, `manual-log-image.*`, `narrow-range-image.*`: exported
  PNGs, ZIP packages and extracted JSON sidecars with exact color provenance.
- `color-session.json`: the actual downloaded session used by Open session.
- `desktop-1100-1.png`, `desktop-1600-1.25.png`, `desktop-1600-1.5.png`: resized
  and maximized plots at actual browser zoom.
- `results.json`, `backend.log`, `frontend.log`: check results, export requests,
  loaded-grid request parameters, source hash count and server evidence.

The manual-log export and 150% desktop screenshot were visually reviewed:
readable controls, source captions, axes and color values; no overlapping or
clipped color controls. One intermediate rerun overlapped an in-progress
helper edit and failed on a temporarily unavailable import. The final run uses
the completed source and passes; that intermediate screenshot is not evidence
of a remaining product defect.
