# Multi-variable auto-split verification

Date: 2026-09-11. Scope: reviewable proposals for intervals where every selected
ID/state variable is constant together, then explicitly applying the proposal
to the current TP draft and saving through the existing workflow.

## Constant-interval correction

The user clarified that if even one selected variable is changing, that part
must not become a test point. The existing change mask already combined all
variables: any value change ended the current tuple run. However, it accepted
one-sample runs when `min_len_s <= 1/fs`. At 1 Hz with the default one-second
minimum, a flat first variable and a continuously changing second variable
therefore produced one TP per sample.

The structured `/split/preview` endpoint now requires **at least two consecutive
samples with identical values in every selected variable**, plus the configured
minimum duration. The whole tuple must stay identical within a retained run;
different constant tuples may form adjacent TPs. A changing stretch separates
plateaus even when the same values recur later. Comparisons use full-resolution
native values with exact equality, without tolerance, smoothing or rounding.

For example, at 1 Hz, `A=[1,1,1,1,1,1]` and `B=[10,11,20,20,21,22]` keep only
rows `[2,4)`. The changing rows are excluded even with minimum duration zero.
The minimum uses the existing half-open sample duration `N/fs`. Native row
indices and source-time boundaries retain their existing Save/export meaning.

`excluded.isolated_samples` counts otherwise eligible single-sample runs.
Missing, zero and isolated sample counts are disjoint; `short_runs` now counts
only eligible runs of at least two samples below minimum duration. The UI
states the all-variable condition and shows isolated-sample exclusions.
The additive response field is optional in the frontend type for older
responses. The historical single-variable `/split/auto` endpoint remains
compatible; the current UI uses `/split/preview` for one or multiple variables.
Existing saved definitions change only after explicitly applying and saving a
new proposal.

Focused backend verification passes: **25 tests, 37 subtests**. New cases cover
ramps in either variable order, a third selected variable, 1 Hz/default duration,
zero duration, overlapping plateaus, same-value recurrence across interruptions,
singleton-only data, zero inclusion, disjoint exclusion counts and legacy API
compatibility. Proposal-limit and source-clock tests now use repeated plateaus
so they continue exercising their original validation rules.

Full backend verification passes: **481 tests, 421 subtests**, 48.59s, using
`backend/.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider`.
Only the two existing dependency deprecations remain. Frontend build, lint and
all 13 Split export/preferences/Y-range helper tests pass; the existing bundle
size notice remains. Independent production diff review found no material issue.
The extended Chromium suite passes all **nine assertion groups**, retaining the
original six and adding:

- A selected continuously changing signal at 10 Hz with minimum duration zero:
  no TPs, 67 isolated samples, existing definitions unchanged.
- A 1 Hz changing signal at untouched one-second minimum: no TPs, 26 isolated
  samples, existing definitions unchanged.
- Staggered plateaus/ramping of each variable in turn: only native row intervals
  `[4,9)`, `[13,17)`, `[20,23)`, `[23,26)` survive at default and zero minimum;
  11 isolated samples are excluded. All eight draft/saved CSVs for these four
  TPs match source values and indices, and each selected column is constant
  across every exported row. Including the original fixture, ten CSV downloads
  were verified.

Preview and Apply leave saved files unchanged; explicit Save persists only the
expected definitions. All 15 source CSV/Parquet/pyramid files stay unchanged.
No unexpected browser errors; all owned servers, fixtures, profile and extension
cleaned up. Keyboard, 1100px resizing and actual 125%/150% browser zoom pass.
Screenshots reviewed: `staggered-constant-preview.png`, `preview-1440-1.5.png`;
additional ramp/1100px/125% evidence and `results.json` are under the ignored
`data/verification/multi-variable-autosplit/` directory. The command is unchanged
from the isolated browser verification section below.

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

## Original implementation results

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
