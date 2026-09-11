# Multi-variable auto-split verification

Date: 2026-09-11. Scope: reviewable proposals for splitting at changes in any
selected ID/state variable, then explicitly applying the proposal to the current
TP draft and saving through the existing workflow.

## Isolated browser verification

```powershell
python scripts/verify_multi_variable_autosplit.py --frontend-port 3340 --backend-port 8340
```

Native Python Playwright runs against owned Vite and Python 3.13 backend
processes using the existing `verify_data_quality.servers` helper. Two real CSV
uploads, saved TP definitions, a Chromium profile and a browser-zoom extension
are temporary. Existing user data, settings and listeners are not used.
Ignored screenshots, downloads, results and logs are written to
`data/verification/multi-variable-autosplit/`.

The alpha fixture contains a run ID and a separate state variable. It includes
changes in either variable, repeated tuples after interruptions, zero values in
either selected variable, missing state values and runs below/exactly at the
minimum duration. The beta fixture has a disjoint numeric schema to exercise
context changes and manual selection when candidate suggestions fail.

At 10 Hz, selecting both alpha variables with zero exclusion and a one-second
minimum must produce exactly `[5,15)`, `[15,25)`, `[32,42)`, `[47,57)`, and
`[62,72)` in native row indices. Four missing samples, ten zero-valued samples
and two short runs are excluded. These expectations are independent of plot
display reduction and are checked against the live structured proposal API.

Coverage includes keyboard open/add/pick/remove/preview/apply/close, proposal
table and counts, read-only preview and Apply-before-Save persistence, existing
draft invalidation, exact draft/saved CSV parity, zero/minimum controls, empty
and failure states, manual variables after candidate failure, candidate Retry,
and disjoint test switching. Controlled response holds exercise rule changes
and Close before an old response is released. Desktop checks use a 1100px
window and actual `chrome.tabs.setZoom` at 125%/150%, verified through changed
devicePixelRatio. Source CSV/Parquet/pyramid files are fingerprinted throughout.

Proposal duration is checked as native sample count divided by sample rate, and
the table explains that distinction from source start/end timestamps. The suite
compares displayed source-time boundaries numerically, allowing floating-point
representation at the last boundary (`7.199999999999999` represents 7.2 seconds),
while enforcing exact native indices and byte-identical saved/draft CSV content.

## Results

The complete Chromium suite **passed**: six assertion groups, 16 browser preview
requests, zero unexpected console/page errors, and all 10 original
CSV/Parquet/pyramid files unchanged. The fixture's `testpoints.json` and API
definition were unchanged after Preview and Apply; only the explicit Save wrote
the five proposed definitions. Temporary uploads/profile/extension were removed
and both owned servers stopped normally.

Screenshots reviewed at 1100px and actual 150% browser zoom show readable rules,
exclusion counts, proposal values and the Apply warning/button, with normal
vertical scrolling for the larger desktop zoom. Evidence is in the ignored
output directory: `results.json`, `preview-1100-1.png`,
`preview-1440-1.25.png`, `preview-1440-1.5.png`, `draft-tp1.csv`, `saved-tp1.csv`
and owned server logs.

Related final checks:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests -q  # 436 passed, 354 subtests
cd frontend
npm.cmd run build
npm.cmd run lint
node --test tests/testPointExport.test.mjs tests/splitPlotPreferences.test.mjs tests/visibleYRange.test.mjs  # 13 passed
cd ..
python -m py_compile scripts/verify_multi_variable_autosplit.py
python scripts/verify_split_exports.py --frontend-port 3110 --backend-port 8110
python scripts/verify_split_multi_plots.py --frontend-port 3320 --backend-port 8320
```

Build and lint pass. Existing backend dependency deprecations and the Vite
bundle-size notice remain. The Split export and multi-plot suites both pass
against the final integration, including old saved/draft/open-ended CSV behavior
and nine linked plots. All 13 frontend helper tests pass; three new cases cover
native indices on uneven timestamps, edited-edge invalidation, and open ends.
Independent code review and scoped diff checks found no material issues.

The new preview endpoint accepts up to nine unique variables and rejects more
than 1,000 resulting points without returning a partial proposal. The legacy
single-variable endpoint is unchanged. Preview cancellation aborts the client
request and suppresses late results; an in-flight native server read may finish.
Source timestamps must be finite and ordered, with distinct retained start/end
boundaries. New structured JSON column names support commas; the pre-existing
comma-delimited Time/Split plot-display limitation remains outside this work.

The first new-browser run corrected a harness string comparison for the final
floating-point source-time boundary. No production defect was found by the
final browser suite. Tests intentionally cover exact value changes, without
claiming threshold, tolerance or steady-state detection.
