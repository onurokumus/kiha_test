# High-detail waterfall verification

User request: retain much finer waterfall detail, especially between 0 and
200 Hz. Verification uses synthetic fixtures in a temporary dataset and an
isolated Chromium profile; it does not read or modify user datasets.

## Browser command and isolation

```powershell
python scripts\verify_waterfall_detail.py
```

The runner uses installed Python/Playwright for browser and standard-library
operations only. The existing `verify_data_quality.servers` helper starts the
native backend with `backend/.venv/Scripts/python.exe` (Python 3.13.14) and Vite
on ports 8351/3351, refuses occupied ports and terminates only its own children.
It removes the temporary dataset, browser profile and zoom extension on exit.
The webapp-testing `with_server.py --help` was checked first; the repository
helper is needed to preserve the existing dataset isolation and Python runtime.

Evidence is written to ignored `data/verification/waterfall-detail/`. The script
exposes the existing uPlot instance only in the served browser test module to
inspect actual scales; production source is not instrumented or modified.

## Acceptance coverage

- Two 24-second, 2048 Hz uploaded sources contain 84 Hz and 85 Hz sinusoidal
  tones with different source amplitudes. Fresh waterfall settings select
  0.25 Hz, 0–200 Hz and 75% overlap. Every frequency bin in that band remains
  separate, with 8192-sample windows and a valley between the two native peaks.
- 0.5 Hz and 0.1 Hz choices produce two- and ten-second windows; 0.1 Hz uses
  20480 samples without zero padding. Settings survive reload.
- The full 0–1024 Hz domain is reduced when needed. Zoom to 80–90 Hz and
  6–14 seconds triggers a new request that restores individual bins, with
  the two sources sharing the viewport. CSV magnitude and every time/frequency
  cell boundary are compared with the actual displayed source responses.
- Drag/wheel zoom, Shift-drag pan, keyboard Home/reset actions, maximize,
  restore and saved viewport reload retain correct shared axes.
- Manual 256-sample windows produce more than 512 time frames. Zoom to
  10–11 seconds restores native frames while retaining the original interval's
  frame origins. Legacy sessions without the new options restore manual/full
  mode and their old three-element waterfall context and saved X/Y viewport.
- One failed source disables export until Retry. A one-second TP gives an
  explicit error for the four-second window. A delayed request released after
  switching estimators cannot relabel or overwrite the new plot. Returning from
  FFT/Welch to waterfall defaults to the active full test; Selected TPs is explicit.
- A viewport entirely above Nyquist (2000–2100 Hz) shows an explicit empty-cell
  message and disables both CSV and PNG. Keyboard Home restores the source
  domain and both maps.
- Actual single CSV/PNG and selected two-plot CSV ZIP/combined PNG downloads
  include v2 method provenance. Every selected CSV cell is checked against its
  corresponding loaded plot grid; images are saved for visual inspection.
- Desktop width 1100 px and actual browser zoom 125%/150% retain visible
  controls and usable canvases. Source file hashes and browser page errors are
  checked before successful completion.

## Results

Passed 2026-09-11: all eight browser check groups, six real export packages
(five CSV entries and two PNG images), all 14 source files unchanged, and no
browser page errors. The final run includes legacy viewport migration and the
out-of-domain reset path. The server/profile/fixture helper completed cleanup.

`results.json` records the completed checks, four CSV POST payloads, loaded GET
parameters/method/grid reduction metadata, hash count and browser error list.
The exported CSV checks compare every numeric cell and source frame timestamp
against the corresponding actual loaded response, rather than an independently
recomputed or fabricated UI dataset.

Evidence files under `data/verification/waterfall-detail/`:

- `default-025hz.png` and `zoom-84-85hz.png`: individual 0.25 Hz bins and the
  separately visible 84/85 Hz tones after frequency/time refinement.
- `default-grid.zip`, `zoom-grid.zip`, `time-refined-grid.zip`: single-plot
  grids with `analysis.json`, including frequency and time refinement.
- `zoom-image.zip` / `zoom-image.png`, `selected-grids.zip`, and
  `selected-images.zip` / `selected-images.png`: image and selected-plot exports.
- `short-interval-error.png`, `empty-viewport.png`: actionable error/empty states.
- `desktop-1100-1.png`, `desktop-1600-1.25.png`,
  `desktop-1600-1.5.png`: resized and maximized desktop maps at real browser zoom.
- `backend.log`, `frontend.log`, `results.json`: server and machine-readable evidence.

The default, refined-tone and 150% desktop screenshots were visually reviewed:
readable controls and axes, two distinct tones in the refined view, no clipped
maps or overlapping controls. Intermediate harness issues were source timestamp
normalization assumptions, missing source identities when manually replacing a
saved fixture selection, and a route glob that did not capture bundle POSTs;
these were corrected in the verifier. No product failure remained.
