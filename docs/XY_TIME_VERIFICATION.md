# XY time-variable verification

Completed 2026-09-11 for the user's request to add time to XY plots.

## Behavior

- XY's X and Y pickers now include actual time-column names from source metadata,
  including measured, custom-named and generated time. Both axes may use time.
- Selected-TP mode includes columns from selected tests and the active test;
  each trace still requires both columns in its own source. Full-test mode uses
  only the active test's columns. An unavailable saved axis keeps its slot and
  offers the existing replacement picker.
- Settings' XY defaults include time; session picks and normal plot defaults
  retain their existing precedence. Signal-only Time/Spectrum/scatter lists and
  shared grid slot choices remain unchanged.
- Time values are stored seconds, including the source's existing origin.
  A later test point is not reset to zero. Imported measured and generated axes
  retain the existing ingestion normalization; this change adds no conversion.
- Existing XY backend, filtering rules, native export handlers, saved-session
  format and API contracts are reused without modification.

## Checks

From the repository root:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests -q
python scripts/verify_xy_time.py
```

From `frontend`: `npm.cmd run build` and `npm.cmd run lint`.

- Backend: **421 tests and 331 subtests passed**. The existing time-axis CSV
  regression also checks exact display timestamps on either/both axes and
  preserved saved TP bounds. Two existing dependency deprecations remain.
- Frontend build/lint pass; the existing Vite bundle-size warning remains.
- Real Chromium uses an isolated profile and three temporary uploaded tests:
  measured `clock`, measured `elapsed`, and generated `generated_s`.
- Browser checks cover time as X/Y/both, exact later-TP seconds, cross-test
  column eligibility, full-test data, settings-only default seeding, keyboard
  picks, session reload, Settings save/apply, and recovery after changing to a
  test with a different time-column name. A non-XY picker still excludes time.
- Eight downloaded metadata ZIPs contain six CSVs and two PNGs. CSV axis time
  values match `time_s`; exports cover TP, Full, generated time, same-axis time,
  and an explicit XY zoom crop. PNG bytes and metadata packaging are checked.
- Wheel zoom, Shift-drag pan, keyboard reset, maximize/restore, a 1100px desktop
  window and actual 125%/150% browser zoom pass. Screenshots at 150% and 1100px
  were visually inspected: chart and controls remain readable and contained.
- No browser page errors; all 21 fixture source files unchanged. Owned servers
  stop and temporary data/profile are removed at the end.

Evidence is under ignored `data/verification/xy-time/`, including `results.json`,
export ZIPs, screenshots and server logs. The first verification run used an
outdated test selector for the reset menu; correcting it to `Reset axes` allowed
the complete suite to pass. No production fix was needed for that failure.

The earlier Split multi-plot verification is separate and remains pending.
