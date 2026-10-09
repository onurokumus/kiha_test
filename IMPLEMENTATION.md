# Implementation handoff

## Git checkpoint: preprocessing, appearance and auto-split (2026-10-09)

User explicitly requested committing all pending changes and pushing them on
codex/ptt-ui-rework-2026-09-28, starting from a5c16ec. This checkpoint includes
Uploads preprocessing and bulk selection, compact plot-appearance controls,
auto-split tolerance/custom value exclusion, and their tests/documentation.
All requested implementation is complete; verification for each is recorded
below and in its linked report. No deployment is included. Next independent
milestone remains Phase 11b.

## Complete: preprocessing bulk filter controls (2026-10-09)

User follow-up adds parameter checkboxes, global Select all, filtered-list
Select visible and explicit bulk Apply/Remove. Individual Configure buttons
remain available; checked targets survive search and individual inspection.
Bulk changes use a separate draft, never change unselected parameters, and
block Save until applied or discarded. Time is excluded. Scope: dialog TSX/CSS
and isolated browser coverage only; existing uncommitted preprocessing,
appearance and Split work remain intact. Arbitrary parameter names, including
Object prototype property names, safely retain individual settings and sparse
missing-sample counts. Changing the target set marks differing settings pending;
Apply/Remove clones settings only to current signal targets.

Build/lint and ten preprocessing helpers pass. Nine isolated browser groups
pass against index-B-wmAcm6.js, including exact 34-filter POST content, hidden
targets, per-parameter independence, validation/discard, special column names,
keyboard focus and both themes at actual 100/125/150% zoom. Screenshots and an
independent code review pass. No browser errors or user-data access; mocked
source fixture unchanged. Owned preview stopped. Existing Vite size advisory
remains. Backend tests were not rerun for this frontend-only follow-up.

Entry points: PreprocessDialog TSX/CSS and scripts/verify_preprocess_bulk.py.
Usage, commands and evidence: docs/PREPROCESSING.md. No remaining bulk work or
deployment. Included in the combined Git checkpoint above. Next independent
milestone remains Phase 11b's collapsible variable/filter panel.

## Complete: Auto-split custom value exclusion (2026-10-09)

Explicit request ahead of independent Phase 11b; concurrent upload preprocessing
and plot-appearance changes in this working tree are preserved. Added an optional
"Exclude a value" checkbox and finite numeric input beside "Exclude zero values".
Exact matches in any selected variable break/exclude runs. Zero remains an
independent option; missing, zero and custom counts are disjoint, in that order.
Settings persist per test with older preferences defaulting to disabled.

Acceptance: native exact signed/decimal matching, unchanged gaps/boundaries,
invalid-input rejection, preview invalidation, draft-only Apply and ordinary
Save/CSV, legacy preferences and desktop keyboard/theme/resize/zoom checks.
Acceptance complete. Production build/lint and 13 Split/export/Y-range helpers
pass; focused backend 33 tests/67 subtests and full suite 532 tests/515 subtests
pass. Seven new native browser groups and all nine existing auto-split groups
pass, including negative/decimal/exponent values, overlapping exclusions,
held stale responses, legacy/per-test/reload preferences, draft/saved CSV and
both themes at 1100px and actual 125/150% zoom. Screenshots visually reviewed;
the adjacent duration field now stays compact on one line. Ten new-suite and
15 existing-suite CSV/Parquet/pyramid source files remain unchanged. No unexpected
browser errors; owned servers/profile/fixtures cleaned up. Independent scoped
review and whitespace checks pass. Existing Vite size advisory and one backend
dependency deprecation remain. Build/native checks required approved Windows
sandbox escalation; the browser harness now waits for the enabled editor.

Entry points: AutoSplitPanel TSX/CSS, types/index.ts, split.py, the preview request
and route in main.py, test_split.py and verify_autosplit_excluded_value.py.
Details, commands and evidence: docs/MULTI_VARIABLE_AUTOSPLIT_VERIFICATION.md.
Branch: codex/ptt-ui-rework-2026-09-28; included in the combined Git checkpoint
above. No remaining scope, deployment or user-data changes.
Next independent milestone remains Phase 11b's collapsible variable/filter panel.

## Complete: plot appearance controls (2026-10-09)

Compact follow-up complete: reduced Appearance to inline Line/Scatter fields
and an icon-only reset. Removed the repeated heading, preview graphics and
helper copy; full accessible names, percentage units, range tooltips and inline
validation remain. TP Time/XY popovers now use 360px rather than 520px; Full Time
and Spectrum keep the space required by their other options. Build/lint and six
focused browser groups pass (index-D3ukhO0b.js): both themes at 100/150% zoom,
inline layout, keyboard/reset/validation and working Full/Spectrum controls.
Screenshots reviewed; no page errors, data writes or changed source hashes.
Owned servers stopped. No changes to plot rendering, persistence or numeric
handlers; earlier numerical/export tests were not rerun for this styling pass.
Evidence: docs/PLOT_APPEARANCE_VERIFICATION.md. No remaining compacting work;
next independent milestone remains Phase 11b.

Follow-up complete: replaced both sliders with shared NumericField percentage
text inputs (50–300%, including decimals). Valid edits apply immediately;
empty/invalid drafts leave the current appearance intact and restore the last
valid value on blur/Enter. Removed the former 25% step rounding, preserving
existing saved settings. Build/lint, 13 appearance/numeric helpers and six native
browser groups pass: exact decimal geometry, invalid drafts, keyboard, blur/
Enter/Escape, reset/reload and both themes at 1050px/150% zoom with maximize/
restore. Screenshots visually reviewed; no page errors, dataset writes or sample
hash changes. Owned servers stopped. Current bundle index-edHYMV-Z.js. Details
and evidence: docs/PLOT_APPEARANCE_VERIFICATION.md. No remaining follow-up work;
next independent milestone remains Phase 11b.

Explicit user request on codex/ptt-ui-rework-2026-09-28, ahead of independent
Phase 11b. Shared Options > Appearance now offers line thickness and scatter
size (50–300% of existing defaults), immediate updates, browser persistence and
Reset appearance in every analysis mode. Time, Full test/comparison, Spectrum
and Split lines plus XY/overview/reference markers use the preference. In-place
uPlot plugins retain axes, sample arrays and hidden series. Relative original/
filtered widths remain distinct; cluster membership and default sizes remain.

Fixed the existing frozen SVG radius animation exposed by live size changes;
hover uses CSS transitions and PNG snapshots retain the configured base radius.
PNG line/point geometry matches the active plot. Browser preferences use
ptt.plot-appearance.v1 with validated defaults and safe storage failure behavior.

Verification: final build/lint, all 133 frontend helpers before the animation
fix, 16 focused appearance/export helpers after it, and 21 native browser groups
pass. Coverage includes live line/point geometry, multi-flight, Split, selection/
hover, reload/reset, unchanged data/axes, native PNG exports, keyboard and both
themes at 1/4/9/maximized layouts and actual 100/125/150% zoom. No page errors or
dataset writes; original/copied sample hashes unchanged. Screenshots visually
reviewed. Owned test servers stopped. Final bundle index-CxKuzIBn.js; the existing
Vite size advisory remains. Backend tests were not rerun for this frontend-only
request. Windows build/browser setup required approved sandbox escalation.

Entry points: PlotAppearanceControls, plotAppearance/usePlotAppearance,
uplotAppearance/uplotPointAppearance, plot components and PNG/scatter export.
Reproduction/evidence: docs/PLOT_APPEARANCE_VERIFICATION.md. No work remains for
this request; next independent milestone is Phase 11b's collapsible variable/
filter controls. Included in the combined Git checkpoint above, along with the
separately verified upload preprocessing and auto-split changes.

## Complete: Uploads preprocessing (2026-10-09)

Explicit user request ahead of independent Phase 11b, starting clean at a5c16ec
on codex/ptt-ui-rework-2026-09-28. Scope: per-test Pre-process dialog with searchable
parameters, full-resolution filters and saved settings, validation and background
status; original data remains untouched and output is a separately named test
with its own Parquet/pyramid/TPs/raw upload and source provenance. Existing plot
filters apply independently on either source. Preprocessed copies open their
saved recipe rather than silently applying filters twice.

Acceptance: full native sample correctness, source preservation, atomic publish
and failure recovery, source identity/revision checks, independent exports and
downstream analysis, keyboard focus, light/dark desktop sizing and browser zoom.
Backend, dialog and Uploads integration are complete. Full combined-tree
backend suite passes 531 tests/511 subtests; frontend build/lint and 133 helpers
pass. Sixteen isolated native browser groups pass, including complete SciPy
parity, unchanged source hashes, explicit downloads, plot-filter layering,
light/dark, real 100/125/150% zoom and dense short-desktop keyboard/scrolling.
Real short-gap worker failure/retry and lost-response recovery also pass;
failed copies publish no samples or broken original download. Saved copies
preserve original raw CSV and component associations while component totals
exclude them. Retained acquisition gaps remain hard boundaries after fill edits.
All seven filter kinds, source revision guards and concurrency checks pass.
Final reviewed frontend bundle: index-Bg1W2U1i.js.
Existing bundle-size advisory and dependency warning remain. Scope/method and
commands: docs/PREPROCESSING.md; browser evidence: host temp
ptt-preprocess-verification/{results,followup-results,lifecycle-results}.json.
Owned servers stopped; user datasets/servers untouched. Restart an existing
backend to load new routes. Included in the combined Git checkpoint above;
no deployment performed. Concurrent plot-
appearance and Split changes are preserved; this task owns only preprocessing.
No remaining work here; next independent milestone remains Phase 11b.

## Git checkpoint (2026-10-09)

The user requested committing and pushing all pending changes on
codex/ptt-ui-rework-2026-09-28, starting from 1d61084. This checkpoint contains
multi-flight Full test Time/XY comparison, source-aware exports, persistent
selection/alignment, the polished flight toolbar and time basis inside Align,
plus tests and verification documentation. Final build/lint pass; implementation
verification includes 115 frontend helpers, 505 backend tests/469 subtests,
26 comparison/filter browser groups and focused toolbar/modal checks below.
No implementation work remains in these requests. Next independent milestone:
Phase 11b's collapsible variable/filter controls.

## Complete: time basis inside Align (2026-10-09)

Moved the time-basis selector from the toolbar into Align above the offset rows.
Time basis and offsets are drafted together; Apply commits both, while Cancel or
Escape discards both. SearchableSelect portals inside its nearest native dialog
so the menu stays in the modal's top layer/focus scope. Updated the comparison
browser verifier for the new flow. Build/lint and five focused browser groups
pass: popup mouse/keyboard operation within the native modal, nested Escape,
draft cancellation, combined Apply, native plotted time values, reload, and
compact light/dark 100/150% zoom. Screenshots visually reviewed. No page errors,
dataset writes or changed fingerprints; owned servers stopped. Final bundle
index-DFd70Mnb.js; existing size advisory remains. No backend/helper suite rerun
for this control change. Evidence: ptt-flight-toolbar/basis-report.json in the
host temp directory; details in docs/FULL_FLIGHT_COMPARISON_VERIFICATION.md.
Included in the user-requested complete Git checkpoint above. No remaining work in this request;
next independent milestone remains Phase 11b.

## Complete: flight toolbar styling (2026-10-09)

Latest copy refinement: shortened the time-basis choice to "Elapsed time" and
narrowed its selector to 126px. Updated browser-verifier label expectations.
Build/lint pass (index-Bi8SUViK.js); browser/backend suites were not rerun for
this label/width-only follow-up.

Polished Flights/Align/selected-flight styling in FlightSelection TSX/CSS and
SelectedPointsPanel. The picker now sits beside selected-flight chips in one
comparison toolbar; a count badge replaces the duplicate heading. Consistent
32px controls, restrained flight colors, offset badges and visible hide/remove
icons clarify actions. Alignment/Fit/export wrap as a group in narrow desktop
panes; long chip lists scroll without displacing tools. Align now closes its
modal before returning keyboard focus to the opener; its layout is centered with
clear spacing between its explanation and input rows. Analysis/state/backend
behavior and the prior uncommitted comparison implementation are preserved.

Build/lint and 12 focused browser groups pass: both themes, 1500/1050px desktop
windows, actual 100/125/150% zoom, picker/Align bounds, keyboard selection,
hide/show/remove, Cancel/Apply/Escape/focus, and 12 long names with horizontal
scrolling/keyboard reachability. Screenshots independently reviewed. No page
errors, dataset writes or changed source fingerprints; owned servers stopped.
Final centered-dialog checks also pass at 1050px/100% and 150% with 12 long names.
Final bundle index-D98E8fWr.js; existing size advisory remains. Backend/helper
suites were not rerun for this styling/focus change. Details and evidence:
docs/FULL_FLIGHT_COMPARISON_VERIFICATION.md. No remaining work in this request;
included in the complete Git checkpoint above. Next remains independent Phase 11b.

## Complete: multiple full-flight comparison (2026-10-09)

Implemented the approved shared flight picker for Full test Time and XY, starting
clean at 1d61084 on codex/ptt-ui-rework-2026-09-28. Flights is now a searchable
multi-select with persistent colors/visibility, stored or elapsed time, optional
per-flight shifts and Fit all flights. Time preserves multiple variables through
line patterns, lighter originals, independent filters/envelopes and variable
visibility controls. XY keeps native paired arrays and shifts only actual time
axes. CSV/PNG include source identities and alignment provenance. Native samples
and calculations remain unchanged; no joining or resampling across flights.

Missing signals and source/filter failures retain valid traces with coverage
details and Retry. Adding/hiding flights preserves zoom, including the first
transition from legacy single-flight state; alignment changes reset obsolete
crops. Quiet reload follows durable source IDs through rename and skips replaced
sources. Per-variable legend visibility remains transient. Spectrum/Waterfall
retain their single active test and separate native time crop.

Verification complete: production build/lint, all 115 frontend helpers, 505
backend tests/469 subtests, 17 native comparison browser groups and nine focused
Time filter/envelope groups pass. Desktop coverage includes both themes,
1/4/9/maximized layouts, resize, actual 100/125/150% zoom, keyboard selection,
hover/zoom, reload, missing schemas, retry and native CSV/PNG parity. All visible
canvases are awaited before layout/hover assertions; screenshots visually reviewed.
No page errors, dataset writes or changed source fingerprints. Owned servers
stopped; user servers unchanged. Final bundle index-DtOnfjLR.js; existing bundle
size advisory and two backend dependency warnings remain. Native test/build
processes required approved Windows sandbox escalation. Browser harness selector
and pending-canvas timing errors were corrected before the final successful run.

Entry points: FlightSelection/SearchableSelect, App/TimeSeriesGrid,
MultiFlightTimePlot/FullTestVariables, XYPlot, comparison/time/XY helpers,
analysisSession/sessionSources, backend plot_export/xy_export, and their tests.
Commands, contracts and evidence: docs/FULL_FLIGHT_COMPARISON_VERIFICATION.md.
No implementation work remains in this request; included in the user-requested
complete Git checkpoint above. Next independent milestone: Phase 11b's collapsible
variable/filter controls.

## Complete Git checkpoint (2026-10-08)

The user requested committing and pushing all pending changes on
codex/ptt-ui-rework-2026-09-28, starting from 3e0b26d. This checkpoint includes
compact hover values for all plot types, synchronized Current/All/None modes,
the themed Values dropdown, Sessions removal, regression coverage and docs.
The final combined frontend passes build/lint, 96 helpers and 43 native
plot-values browser groups; Sessions removal additionally passed 13 groups,
eight reload groups and nine rename groups. Details are in the reports below.
No implementation work remains in these requests. The next independent
milestone is Phase 11b's collapsible variable/filter controls.

## Plot Values selector (2026-10-08)

Completed explicit compact hover / three-way / styling follow-ups on
codex/ptt-ui-rework-2026-09-28, ahead of Phase 11b. Values uses the existing
styled SearchableSelect: Current shows only the hovered plot, All shows every
visible plot, and None hides compact boxes. Its trigger sizes to the complete
Values + mode label with a chevron; the themed popup has three checked/hoverable
rows. Search/footer/redundant tooltip are omitted only for Values; other shared
selectors preserve their defaults. This fixes Current clipping in the supplied
native-control screenshot. ptt.plot-values.v1 remembers choices and migrates
old true/false to All/None; invalid/blocked storage safely defaults to All.

The coordinator shares actual coordinates for matching axes, relative positions
for unlike XY variables and actual nearest samples; no analysis/data/scale changes.
Mode changes cancel pending frames and stale boxes. Waterfall can use its source
label area to retain Hz/time/U in tiny nine-slot/150% canvases; scrolled-out facets
stay hidden. Existing expanded legends and analysis/export handlers remain.
Acceptance complete: all modes/layouts/maximize, full labels/styled menu, pointer
and keyboard choice/focus/dismissal/reload, preference migration, compact desktop
resize/zoom and unaffected scientific values, axes/data/gestures/exports.

Verification: build/lint, all 96 helpers and 43 copied-demo native Chromium groups
pass. Current/All/None cover Time, Full line/envelope/comparisons, FFT/PSD/log/order,
XY and both Waterfall sources in 1/4/9/maximized views. Menu checks cover all three
checked choices, no search/footer or clipped labels, Escape/outside/Tab/focus,
both themes and actual 100/125/150% zoom; 1000x750 resize passes. The verifier
waits for listbox focus before keyboard navigation/commit. None/Current reload,
legacy/invalid preferences, retained uPlot instances/axes/data, drag and PNG/canvas
parity pass. No page errors, dataset writes or changed original/copied fingerprints.
Owned servers stopped; user servers unchanged. Menu screenshots visually reviewed.
Final bundle index-B-cpAS3P.js; existing size advisory remains. Backend tests were
not rerun for frontend-only changes. No remaining scope; details/commands in
docs/PLOT_HOVER_VALUES_VERIFICATION.md.

Entry points: SelectedPointsPanel TSX/CSS, SearchableSelect's optional footer,
App, TimeSeriesGrid, PlotHoverContext, plotHoverGroup and hover tests/verifier.
Included alongside Sessions removal in the user-requested complete Git checkpoint.
Next independent milestone: Phase 11b collapsible variable/filter controls.

## Sessions removal (2026-10-08)

Completed the explicit request on codex/ptt-ui-rework-2026-09-28 ahead of
independent Phase 11b.
Started at 3e0b26d with the hover plot/helpers/tests/script and these two handoff
files already modified. The concurrent hover work is preserved and now records
its own completed verification below; this request did not edit those files.

Removed the Sessions header slot, Save/Open dialog, file parser/downloader,
recovery notice/review/reconnect UI and CSS. Browser persistence remains quiet:
dataset IDs and TP revisions still gate restoration; skipped references no longer
pause autosave, and an unavailable active test falls back to the first ready test.
Network/catalog failure still retains the saved workspace and supports Retry.
Name-only legacy references are skipped; no automatic name matching is introduced.
Resumable upload sessions remain essential transfer state and are unchanged.

Acceptance: no Sessions control/dialog/recovery notices for clean, compatible,
renamed, changed, missing, replacement or legacy state; valid choices/selections
survive reload, stale crops reset, autosave proceeds, network failure/retry keeps
saved data, analysis/export and desktop keyboard/resize/zoom remain usable.
Entry points: App.tsx, Header.tsx, App.css, services/sessionSources.ts. Existing
Full-test/Waterfall persistence tests now cover browser state; file-only import
validation cases retired with the feature. The two dedicated session UI scripts
were retired; six existing browser scripts now use quiet persistence or assert
absence instead of invoking the removed dialog. Historical session reports are
marked superseded. All seven changed/new browser scripts pass syntax checks.

Verification complete: build/lint and all 88 current helper tests pass (two
file-import-only cases retired from the earlier 90-test suite). The new isolated
GET-only suite passes 13 groups: clean/compatible/renamed/changed/missing/replaced/
legacy/ambiguous startup, autosave/reload, connection failure/retry, maximize/
restore, keyboard menus/focus, CSV, 960x500, dark theme and real 125%/150% zoom.
Existing staggered reload (eight groups) and Uploads rename (nine groups) pass;
rename mutations are simulated. No application errors or user dataset writes.
Screenshots visually reviewed; git diff --check passes. Native/backend tests
were not rerun for this frontend-only removal. Older adapted native browser
suites were syntax-checked, not rerun. Details: docs/SESSIONS_REMOVAL_VERIFICATION.md.
Final bundle: index-CD9J9749.js; existing Vite bundle-size advisory remains.
Owned preview stopped after verification; pre-existing servers were untouched.
No remaining work for this request; included in the complete Git checkpoint.
Next independent backlog milestone remains Phase 11b (collapsible controls panel).

## Hover values in normal-size plots (2026-10-08)

Explicit current request, ahead of independent Phase 11b, on
codex/ptt-ui-rework-2026-09-28 in D:/okumus/work_v2/kiha_test. Started from
clean 3e0b26d. Shared body-level hover boxes are implemented for collapsed Time,
Full test, Spectrum and XY cards, plus Waterfall at either size. No canvas space
is reserved. Expanded legends remain. Faceted traces use independent actual
sample indices; XY resolves the nearest visible pair in screen space, including
unsorted/repeated X. Full envelopes and Spectrum log units remain explicit.
Missing values display an em dash. Mouse-only active ownership prevents synced
plots from opening multiple boxes. Drag, key, scroll, resize and destruction
dismiss the card. Extremely large comparisons show a remaining-trace count.

User follow-up: the supplied Test_ID box was too large/text-heavy. Removed all
visible headings and column headers; retained those labels for accessibility.
One row per trace now uses a colored marker, short TP name, X/time and value.
Repeated same-test names and unneeded Original text are removed; cross-test,
overlay, min/max, log and axis identities remain. Numbers use six significant
digits/scientific notation. Waterfall shows only Hz, elapsed seconds and linear
U (max where reduced); source-frame provenance remains in data/exports. The
supplied case is approximately 140x30 CSS pixels, verified in a native browser.

Acceptance: readable and accurate values on every small right-side plot;
multiple traces/variables, nulls, FFT/PSD/log/order, XY and waterfall semantics;
no clipped boxes at nine-slot desktop sizes or actual 125%/150% zoom; preserve
maximize/restore, zoom/pan, keyboard controls, exports and source data.
Verification complete for this request: build/lint, all 90 helpers (six new)
and 24 copied-demo browser groups pass, including four/nine-card views, actual
125%/150% zoom, both themes, resize, gestures, maximize/restore and real PNG.
No application errors/dataset writes; original datasets and copied CSV/Parquet
sources unchanged. Owned Python 3.13/Vite servers stopped; existing user servers
were untouched. Details/commands: docs/PLOT_HOVER_VALUES_VERIFICATION.md.
Entry points: utils/plotHoverValues.ts, utils/uplotHover.ts/css, five plot
components, frontend/tests/plotHoverValues.test.mjs and the browser verifier.

Concurrent work appeared afterward in App.tsx/App.css/Header.tsx/sessionSources
and deleted SessionControls/sessionFiles. Those changes are preserved. The
temporary session-recovery type mismatch was resolved by that work; the final
shared build passes (index-CD9J9749.js). Latest focused hover tests and lint pass.
No hover implementation work remains; next independent milestone is Phase 11b.
Included in the user-requested complete Git checkpoint above.

## Distinct Full test variable colors (2026-09-30)

Current user request, ahead of independent Phase 11b. Target remains
D:/okumus/work_v2/kiha_test, codex/ptt-ui-rework-2026-09-28. The working tree was
clean at ff810fd before this work. This fix is included in the user-requested
Full test color Git checkpoint.

The preferred source-column color generator could collide: source indices 6/287
produce the same hex, while 6/13 and the demo electrical_power_w/ambient_temp_c
are nearly identical. FullTestPlot now resolves the ordered comparison against
all assigned colors, checking Oklab separation in both light and dark rendering.
The first trace keeps its source hue; later traces keep theirs when separated,
otherwise choosing the most separated color from a bounded non-blue palette.
Broader chroma supports six simultaneous traces. Existing original/filtered
opacity/dash and envelope styling remain on the same variable hue.

FullTestPlot shares resolved colors with FullTestVariables; canvas, header,
hover/popup legend, dropdown previews and PNG provenance use the same assignment.
Add previews use the existing comparison; primary previews use the new first
variable. Appending cannot recolor earlier traces. Removing/replacing variables
may release preferred hues for later ones; source colors are preferences rather
than a global uniqueness promise. Identical ordered variables/catalog reproduce
the same colors on reload/session reopen. No session schema, scientific-data,
backend, export format, theme or test-point selection changes.

Verification complete: frontend build/lint and all 84 helper tests pass
(seven new tests). Tests cover all 130,816 pairs in a 512-column catalog at
minimum 0.12 Oklab distance in both themes, exact/near collisions, six-variable
adversarial/random groups, append/re-add/reopen determinism and missing keys.
Independent palette probe: 50,000 six-key groups had minimum 0.09657; target
separation is 0.12 with maximum available separation at higher counts. Every
fallback has at least 3:1 contrast against white; 512-column dropdown previews
average about 1.4 ms. Coordinates cache per catalog; TimeSeriesGrid memoizes the
catalog by source columns so unrelated updates retain caches. No dependencies added.

Nine isolated browser groups pass with native Python 3.13 and copied demo data:
canvas/header/legend/dropdown agreement; six variables and stable append;
primary replacement; reload and actual Save/Open; original/filtered line and
envelope in both themes; maximize/restore and actual 125%/150% zoom; real PNG
colors/metadata from dark mode. No app errors; source file hashes unchanged.
Independent source review found no blockers. Detailed commands and evidence:
FULL_TEST_VARIABLES_VERIFICATION.md. Owned backend/Vite test servers stopped.

Entry points: utils/fullTestVariables.ts; plots/FullTestPlot.tsx and
FullTestVariables.tsx and TimeSeriesGrid.tsx; frontend/tests/fullTestVariables.test.mjs;
scripts/verify_full_test_colors.py. Method source:
https://bottosson.github.io/posts/oklab/ (public-domain conversion).

No implementation or verification work remains for this request.
Build: index-DfOopB_m.js; existing Vite bundle-size advisory remains.
Next independent backlog milestone remains Phase 11b.

## Linux data relocation (2026-09-29)

Completed the explicit user request ahead of independent Phase 11b in
D:/okumus/work_v2/kiha_test, codex/ptt-ui-rework-2026-09-28. This Git checkpoint also includes the completed tooltip cleanup below. No server
deployment, data copy, or service change was performed.

backend/app/config.py now defaults Linux to /data/ptt/data while code remains
/progs2/ptt. KIHA_DATA_DIR continues to take precedence; Windows/macOS retain
repository data/. Audited all runtime storage: tests, resumable uploads, trash,
component registry, formula recipes and server defaults derive from this root.
No frontend or per-feature path changes are necessary.

Updated deployment_guide.md, README.md and docs/UI_REWORK.md. Production service
instructions explicitly set the new root and mount dependency. Migration uses
stopped-backend cp -a of the entire root (including hidden upload state), ptt
ownership and a service drop-in. Timestamp preservation matters because saved
analysis-session sample revisions include data.parquet mtime_ns. Existing
EnvironmentFile overrides must be updated too. The original data is retained.

Verification: three isolated startup-configuration tests cover Linux default,
Windows/macOS defaults and environment precedence, including tests/trash child
paths. Full backend suite: 495 passed / 461 subtests, two existing deprecations.
The checkout venv lacks pytest and compatible TestClient dependencies; its
focused unittest configuration checks pass. Full suite uses the existing
D:/okumus/kiha_test/backend/.venv/Scripts/python.exe (Python 3.13.14, matching
pinned dependencies), PYTHONPATH=D:/okumus/work_v2/kiha_test/backend,
KIHA_DATA_DIR=D:/okumus/work/.ptt-data-directory/test-data,
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, with:
python -B -m pytest backend/tests -p no:cacheprovider -q.
Independent review and git diff --check pass. No frontend changes in this step.

Next operator step: deploy code, let active imports/rebuilds finish, stop the
backend, copy /progs2/ptt/data/. into /data/ptt/data/, apply the documented service
configuration and restart/check the expected test catalog. Actual Linux mount
permissions and service execution were not tested from this Windows workspace.
Next independent implementation milestone remains Phase 11b.

## Hover-tooltip cleanup (2026-09-29)

Current user request, ahead of independent Phase 11b. Authoritative checkout:
D:/okumus/work_v2/kiha_test, codex/ptt-ui-rework-2026-09-28. Included in the same Git checkpoint as the data relocation above. No deployment was performed; the working tree was clean before this request.

Removed the XY canvas gesture tooltip from the supplied photo (instructions
remain in XY Details), repeated dropdown option labels/descriptions, plot slot
numbers and Split Zoom hints. Full-name, icon, disabled-reason, scientific-value,
colored legend and scatter-data tooltips remain. Dropdown, Options and plot-action
triggers use data-tooltip and suppress hints while open.

FeedbackProvider distinguishes pointer focus from keyboard focus. Click/drag,
keyboard activation, Escape, scroll, resize, window blur and hidden tabs dismiss
help. Escape focus restoration stays quiet until fresh keyboard navigation.
A scoped active-target observer clears removed/hidden/changed controls and
guards against old-target mutations cancelling the next hint. Empty native-title
sentinels preserve React title updates/removals without restoring stale text.

Acceptance: reproduce the stuck pointer-focus hint, verify dismissal and useful
keyboard/data hints, keep XY gestures/details and maximize/restore working, and
check desktop resizing plus actual 125%/150% browser zoom.

Verification complete: frontend build/lint, all 77 helper tests, 17 focused
browser groups and six theme-tooltip groups pass. Actual 125%/150% browser zoom,
960/1100px desktop widths, maximize/restore, single-Escape dialog closure,
dynamic title changes/removal, ARIA cleanup, scatter data and colored legends
are covered. No JavaScript errors or API writes; independent final review found
no blockers. The old build reproduced both the clicked-control and XY canvas
focus persistence bugs. Tests use isolated
copies of ptt_demo_run_a/b under D:/okumus/work/.ptt-tooltip-fix/data; all browser
API writes are blocked. Browser-only Python/Playwright uses the existing global
runtime; the backend uses its required Python 3.13 environment. Original data
is unchanged. Updated verify_dark_mode.py expectations reflect activation
dismissal; new verify_hover_tooltips.py covers the shared lifecycle and plots.

Commands: npm run build; npm run lint; node --test tests/*.test.mjs (frontend);
python -B -X utf8 scripts/verify_hover_tooltips.py;
python -B -X utf8 scripts/verify_dark_mode.py --tooltip-only (repository root).
Reports and before/after screenshots: %TEMP%/ptt-hover-tooltips and
%TEMP%/ptt-dark-mode. The 150% screenshot was visually inspected.

Built preview: index-cneQXkXM.js. Existing Vite bundle-size advisory remains.
No remaining work for this request. Temporary test servers stopped after QA.
Next independent backlog milestone remains Phase 11b.

## Complete Git checkpoint (2026-09-29)

The user requested committing and pushing all remaining changes to
origin/codex/ptt-ui-rework-2026-09-28. This checkpoint includes the Uploads
inline test-name editor, rename/recovery integration, styles and browser test.
The dark-mode and scatter work was already committed and pushed as efe6d28.
Earlier local/uncommitted status notes below describe the implementation stage.

Fresh verification: frontend build/lint and all 77 helper tests pass; all nine
isolated Uploads rename browser groups pass against the built frontend,
including failure/retry, state recovery and actual 125%/150% browser zoom.
All browser API writes were simulated. Independent review found no blockers;
git diff --check passes. No deployment or user dataset changes. No feature work
remains in this checkpoint; the next independent backlog milestone is Phase 11b.

## Scatter grid and navigation (2026-09-28)

Explicit user request takes priority over independent Phase 11b. Target remains
D:/okumus/work_v2/kiha_test, codex/ptt-ui-rework-2026-09-28. Changes are local,
alongside the completed dark mode and independent Uploads rename work.

Implemented a solid adaptive engineering grid with major/minor lines and a
stronger zero reference, separate light/dark palette tokens, round tick spacing,
and step-aware precision. Deep-zoom labels stay distinct; X endpoint labels align
inward and the Y gutter separates values from the axis title. Explicit shared
Recharts axis sizes fix the old mismatch between the rendered data rectangle
and wheel/pan/clustering calculations.

The filter button/count and fixed Pan, Box zoom, +/-, fit/reset controls share
one compact row. Export and reset actions now live in the existing More popup;
separate Export/ellipsis buttons are removed. Right-click and Shift+F10 preserve
the same plot actions. Navigation is portaled into the filter summary row,
keeping drag state local and the filter drawer outside the clipped canvas.
More closes before export opens and receives focus when the dialog closes.
Controls stay aligned with active filters and at narrow desktop widths.
Removed the visible Drag to pan/Drag to zoom hint at user request; tooltips
and accessible keyboard guidance remain. Build/lint pass for this text removal. Default drag pans; Shift-drag
or Box zoom draws a rectangle; middle-drag pans in either mode. Scroll zooms
smoothly at the pointer; opposite wheel input reverses without drift. Ctrl/Meta
wheel remains browser zoom. Focused canvas shortcuts: arrows pan, +/- zoom,
Home fits, P/Z choose mode, Escape cancels. Background double-click also fits.

Pointer capture begins after a real drag; release flushes the final frame.
Escape, blur, resize and lost capture cancel cleanly. Point and cluster clicks
remain selectable, with fresh callbacks after filtering coincident points.
Portal/interactive targets cannot start a canvas gesture. Pointer focus is
explicitly marked so a drag cannot inherit the keyboard-only canvas outline;
Tab and subsequent keyboard actions restore visible focus. Saved views and
selections keep their existing session model. PNG exports normalize custom grid
strokes and axes to the light palette; CSV retains the same source rows.

Entry points: MainScatterPlot/ScatterGrid and MainScatterPlot.module.css;
useScatterNavigation/useMainPlotZoom; scatterGeometry, scatterTicks and
scatterViewport helpers; AxisControls/App wiring; scatterExport light snapshot.

Verification:
- Frontend build/lint and all 77 helper tests pass (17 new numeric cases).
- Existing scatter CSV/PNG exports and point-menu regression pass, including
  actual 125%/150% browser zoom for the menu, with no API writes/browser errors.
- Independent browser review passes 11 interaction groups: portal multi-select,
  final-frame pan, outside capture, cancel/blur/lost capture/resize, point-start
  drags, box zoom and keyboard. Filtered coincident-point regression also passes.
- Light/dark screenshots and 1100px deep-zoom axis labels visually inspected.
- python -X utf8 scripts/verify_scatter_navigation.py: all 24 browser groups
  pass across both themes, 960/1100px and actual 125%/150% browser zoom. Tests
  measure rendered axes, pointer anchoring/inverse wheel, fast final-frame pan,
  capture outside the plot, all box directions, cancel, keyboard and point clicks.
- python -X utf8 scripts/verify_plot_theme.py: seven plot groups and 14 real PNG
  downloads pass again before the final tick-spacing refinement. Light/dark exports are byte-identical,
  including the custom scatter grid after a real pan with a selected point.
  View, selection and dark rendering survive success and forced capture failure.
  A focused PNG pair after the final refinement also passes byte parity and
  saved-state preservation. Narrow plots now refine an overly coarse step before
  falling back to endpoint labels; the 150% view retains round 25/50/75 ticks.
- No application errors/API writes. Reports and screenshots: %TEMP%/
  ptt-scatter-navigation, ptt-scatter-readonly-audit, ptt-scatter-exports and
  ptt-plot-theme. Final 150% browser-zoom screenshot visually inspected.

Follow-up verification for the compact toolbar and mouse-focus repair:
- Frontend build/lint pass. Independent focus QA passes 14 cases across themes.
- New scripts/verify_scatter_toolbar.py passes 19 groups covering one row with
  active filters/Clear, More export/reset, real CSV/PNG downloads, focus return,
  context menus, pointer-vs-keyboard outlines, 960/1100px and actual 125%/150% zoom.
- Updated export/navigation helpers use More as the visible export entry point.
  All 24 navigation groups and 17 normal/browser-zoom export downloads pass.
  The shared plot-theme suite passes all seven groups and 14 PNG downloads,
  including More focus return and hidden context-menu focus fallback.
- More popup and active-filter 150% screenshot visually inspected; reports in
  %TEMP%/ptt-scatter-toolbar and ptt-scatter-focus. No dataset writes.

Preview: http://127.0.0.1:8087/ptt/, index-DRhFlxft.js. No commit/push/deployment.
No remaining work for this request. Next independent backlog milestone: Phase 11b.

## Dark mode switch (2026-09-28)

Completed the explicit user request ahead of Phase 11b, on
codex/ptt-ui-rework-2026-09-28 in D:/okumus/work_v2/kiha_test. The compact
sun/moon header switch persists ptt.theme.v1 locally. First paint uses the saved
choice or system theme; explicit choice wins over later OS changes and syncs
between tabs. Blocked storage still allows switching within the current tab.
Theme remains independent of analysis sessions and server defaults.

theme.css defines dark semantic tokens; existing light values remain fallbacks
throughout all sections, controls, popovers and dialogs. Header wrapping accounts
for the added switch. Its accessible switch state communicates on/off; a stable
tooltip avoids the feedback provider restoring stale dynamic title text. The
existing logo already supports dark backgrounds. Native controls, placeholders,
focus, status colors and translucent drag/range overlays follow the palette.

usePlotTheme redraws existing uPlot instances without rebuilding charts,
refetching data, resetting zoom or changing saved selections/colors. Canvas axes
resolve live tokens, while dark series colors receive a presentation-only hue
preserving brightness lift. SVG scatter and variable legends also adapt.
PNG exports retain the original light palette: per-plot synchronous light
capture restores the dark canvas in finally; scatter normalizes its detached
SVG including nested text/paths. Data, scientific settings and provenance are
unchanged. Files: hooks/useTheme.ts, public/theme.js, theme.css, Header/main;
constants/uplotTheme.ts, utils/usePlotTheme.ts, plotPngExport/scatterExport;
section/control/plot styles retain their existing layout and light fallbacks.

Verification:
- Frontend build/lint and all 60 helper tests pass; existing bundle advisory.
- python -X utf8 scripts/verify_dark_mode.py: 11 browser groups pass, covering
  six sections, pickers/Options/filters/session/export/confirmation dialogs,
  exact saved selection/zoom preservation, reload, OS changes, cross-tab sync,
  blocked storage and dark startup with the app bundle/API blocked.
  287 enabled UI text/placeholder samples have minimum contrast 4.823:1.
  Header has no overflow/overlap at 960/1100/1241/1300/1301px or actual 150% zoom.
  Its --tooltip-only follow-up verifies mouse, Space, Enter, Escape and focus.
- python -X utf8 scripts/verify_plot_theme.py: six Analyze modes plus Split pass.
  Fourteen actual PNG downloads are byte-identical between light/dark themes,
  including scatter datasheet and range overlays. Canvas identity, saved ranges
  and requests remain unchanged; successful and forced-failed captures restore
  the dark plot. No application errors/API writes in either browser suite.
- Independent review corrected contrast, opaque Split overlays and SVG export
  descendant colors. Dark pages/charts and exported PNGs visually inspected.
  Evidence: %TEMP%/ptt-dark-mode and %TEMP%/ptt-plot-theme. Existing browser-only
  Python/Playwright runtime used; no native scientific libraries or data writes.

Built preview: http://127.0.0.1:8087/ptt/, index--iILnIwo.js. Changes remain
local/uncommitted alongside the independent Uploads rename work; no push or
deployment. No remaining dark-mode work. Next independent milestone: Phase 11b.

## Inline test rename in Uploads (2026-09-28)

Completed the explicit user request ahead of the independent Phase 11b backlog.
Target: D:/okumus/work_v2/kiha_test, codex/ptt-ui-rework-2026-09-28. Changes are
local/uncommitted; the rebuilt preview at http://127.0.0.1:8087/ptt/ serves
index-BDI_zM6m.js. No deployment or user dataset changes.

A compact pencil beside each history test name opens an inline editor with
Save name/Cancel rename, Enter/Escape, selected input text and focus return.
Trimmed names follow the existing rename API character rules; blank, unchanged
and duplicate names cannot submit. Server errors retain the draft for retry.
Busy tests and concurrent row actions cannot rename. Search stays unchanged;
if the renamed row leaves its results, focus returns to history search.

TestNameEditor.tsx reuses UploadView.module.css; UploadView wires status and row
focus. App.tsx reuses renameTest and identity-aware recovery, capturing the live
workspace before POST. Active/inactive renames preserve TP selections, hidden
state/colors, filters, axes and zoom, and remap datasheet settings. Recovery
failures retry separately from a completed rename. Navigation guards and list
response epochs prevent concurrent changes/stale polls from replacing recovery;
statistics and trace loaders wait for verified source recovery.

Verification:
- Frontend build/lint and all 60 helper tests pass (existing bundle-size advisory).
- Python 3.13 backend suite: 492 pass / 456 subtests, two existing deprecations.
- python -X utf8 scripts/verify_upload_rename.py: nine groups pass, including
  mouse/keyboard save/cancel, failures/retry, duplicate submissions, delayed stale
  list responses, current/inactive tests, filtered history focus, page reload,
  recovery outage, datasheet rename, 1100px and actual 125%/150% browser zoom.
  All API mutations are simulated; only expected injected 409/503 console errors.
- python -X utf8 scripts/verify_scatter_reload.py: all existing reload checks pass.
- Independent review resolved navigation/focus and stale-list races. The 150%
  screenshot was visually inspected; evidence: %TEMP%/ptt-upload-rename.
- git diff --check passes. Next independent backlog milestone remains Phase 11b.

## Scatter-filter fixes integrated into Codex rework (2026-09-28)

Corrects the earlier branch split: filter behavior was committed separately as
88f9483 on feature/resumable-multipart-upload, while scatter CSV/PNG export,
compact PNGs and section scrolling were already on this rework branch in c0b5121.
The authoritative UI checkout for this request is D:/okumus/work_v2/kiha_test,
branch codex/ptt-ui-rework-2026-09-28. This correction is committed and pushed to
that branch; the older checkout is only a source reference.

Adapted the filter fixes to the rework's shared NumericField, funnel control and
light styling. Portaled column menus remain open for mouse selection; searched
parent checkboxes reflect the visible subset; Add opens Parameters, Clear removes
draft rows, and saved unavailable columns remain identifiable. Valid numeric edits
commit immediately so closing the drawer cannot discard them. Raw sign, decimal
and exponent spelling is retained while typing. Reversed bounds show an accessible
error and match no points, including while statistics are pending. The legacy
'any' mode is accurately labeled Range overlap. CSV/PNG exports are unchanged.

Verification on the corrected rework:
- Frontend build and lint pass; existing bundle-size advisory remains.
- node --test frontend/tests/*.test.mjs: all 60 pass, including eight filter tests.
- python -X utf8 scripts/verify_scatter_filters.py: all six browser groups pass,
  covering mouse/keyboard, numeric ranges and immediate close, tree/labels,
  persistence, missing columns, desktop resize and actual 125%/150% browser zoom.
- python -X utf8 scripts/verify_scatter_exports.py: all 13 CSV/PNG downloads pass,
  including filtered exports, viewport changes, encoding failure and cancellation.
- Independent source review and git diff --check pass. The 150% browser capture
  was visually inspected. Browser fixtures allow GET only; no API writes/errors.

The preview http://127.0.0.1:8087/ptt/ was rebuilt (index-BdEq7vh2.js). Filter
screenshots/results are in ignored data/verification/scatter-filters; export
evidence is in %TEMP%/ptt-scatter-exports. No backend or deployment changes.
Earlier checkpoint sections below retain their historical validation counts.

## GitHub checkpoint (2026-09-28)

This checkpoint includes all pending UI, compact PNG, scatter CSV/PNG and section
scrolling changes at the user's request. Publication target is the existing
codex/ptt-ui-rework-2026-09-28 branch of onurokumus/kiha_test. Validation results
below remain applicable; historical references to uncommitted work describe
status before this checkpoint. No deployment is part of this request.


## Scatter CSV and PNG export (2026-09-28)

Completed the explicit user request ahead of the independent Phase 11 backlog.
The scatter has a visible Export control and the same action in its three-dot /
right-click menu. The existing keyboard-accessible export dialog and task status
are reused; other plots keep their existing metadata ZIP defaults. Scatter
exports download standalone files locally with no backend packaging request.

CSV contains one row per filtered original test point, irrespective of zoom or
visual clustering, plus separately identified enabled datasheet rows. Columns
include source/point identity, name/label, time bounds, selection, axis names,
X/Y and min/max. Source aggregate extrema are retained directly, avoiding
subtractive rounding. Values match supplied scatter statistics (the backend's
existing aggregate rounding is unchanged); CSV adds no numeric rounding. UTF-8
BOM and CSV escaping retain Unicode, quotes and embedded line breaks. Numeric
point IDs remain unquoted; unsafe integer IDs fail explicitly rather than
silently emitting a rounded identifier. Decimal IDs remain supported.

PNG freezes the current native SVG before asynchronous font/encoding work and
embeds the bundled font. Axes, zoom/pan, colors, clusters, range bars and enabled
datasheet line remain; hover effects/cursor and controls are excluded. Output is
opaque white at 2x display dimensions, bounded to 8192px edges / 16M pixels.
Canvas and temporary SVG URLs are released; cancellation prevents download.
Loading/error guards include enabled datasheet metadata; empty comparisons
cannot export, while datasheet-only views can.

Entry points: MainScatterPlot.tsx, App.tsx and types/index.ts; new
utils/scatterExport.ts. PlotExportControls.tsx adds optional metadata support
and PNG copy for this client export, preserving prior compact-PNG work.

Verification on codex/ptt-ui-rework-2026-09-28:
- Frontend build/lint pass; existing bundle-size advisory remains.
- node --test frontend/tests/*.test.mjs: 52 pass (eight new export tests).
- D:/okumus/kiha_test/backend/.venv/Scripts/python.exe -m pytest backend/tests
  -p no:cacheprovider -q: 492 pass / 456 subtests, 45.11s; two existing dependency
  deprecations. The active checkout venv lacks pytest, so its existing neighboring
  Python 3.13 test environment was used with this checkout's tests.
- python -X utf8 scripts/verify_scatter_exports.py: 13 CSV/PNG downloads pass;
  exact fixture values/IDs/escaping, filters, reference-only/empty views,
  clustering, wheel zoom/pan, keyboard/menu focus, 1100x720 and 960x500,
  opaque decoded PNG pixels/colors, encoding failure/retry and cancellation.
- The same script --zoom-only: four downloads at actual 125%/150% browser zoom,
  modal fit and focus return pass. No JavaScript errors or API writes.
- Independent review resolved metadata loading, extrema precision and unsafe-ID
  issues. Exported PNG visually inspected: clean axes, colors and range bars.
Evidence: %TEMP%/ptt-scatter-exports (CSVs/PNGs, captured SVGs and JSON reports).
Browser tests use isolated GET-only mocks and profiles; user datasets unchanged.

Built local preview http://127.0.0.1:8087/ptt/ includes these changes
(index-SXCLMbWf.js). Changes are local/uncommitted alongside independent work;
no deployment or push. This request is complete. Next independent backlog item
remains Phase 11b; no additional scatter export work is required.


## Section scrolling fix (2026-09-28)

User-requested scrolling repair takes priority over the independent Phase 11
backlog. Chromium's scrolling fieldset accepted wheel events over child content
but ignored events over its own padding and flex gaps. Reproduced with every
Edit disclosure expanded: an 800px wheel moved content by 800px, while the same
wheel over left/right padding or a fieldset gap left scrollTop at zero.
Adding min-height:0 or display:block did not repair the fieldset hit-testing.

Edit now uses a bounded outer div as the sole page scroll owner. The inner
fieldset retains native disabled/busy behavior, natural section heights and the
sticky Save toolbar. All formerly dead page areas scroll, with identical expanded
content height, at 1440x800 and 960x400. Split explicitly has min-height:0 and
allows ordinary wheel events over its plots to scroll the page. Shift+wheel zooms
time and Alt+wheel zooms Y; Details documents these gestures. Analyze keeps its
existing wheel zoom. No backend processing or dataset changes.

Build/lint, 44 frontend helper tests, all 11 existing Edit/Settings browser groups
(including sticky actions, draft guards, menus, keyboard and actual 125%/150%
zoom), live-library before/after wheel probes and independent scoped review pass.
All 50 focused checks now pass with
`python -X utf8 scripts/verify_section_scrolling.py`. They use an isolated browser,
existing library GETs and a guard against all API writes. Actual wheel input covers
Edit margins, fieldset gaps, inputs, nested column-table boundary chaining and
bottom-control reachability; expanded Settings/Components; overflowing Uploads;
Split scrolling plus Shift/Alt zoom; and unchanged Analyze wheel zoom. Cases cover
1440x800 and 960x400 content viewports plus actual 125%/150% browser zoom. Analyze's
shared wheel handler is checked in a single plot with the scatter collapsed so
short-window control wrapping does not exhaust its trace area; this is not a new
Analyze grid-sizing test. No JavaScript errors or API writes. Evidence and reviewed
screenshots: `%TEMP%/ptt-section-scrolling` (report.json and four Edit captures).

The built local preview at http://127.0.0.1:8087/ptt/ serves index-DvqQFut7.js with
the repair. The existing Vite bundle-size advisory remains. No deployment or
backend/data changes. Changes remain uncommitted on
codex/ptt-ui-rework-2026-09-28 alongside independent PNG-export work. This request
is complete; the independent Phase 11 backlog is the next separate milestone.

## Compact PNG exports (2026-09-28)

Completed the user's request ahead of the independent Phase 11 backlog. Shared
PNG capture now draws only a small title, the unscaled native plot and a compact
wrapping legend. It no longer paints source/settings paragraphs, full-precision
axis summaries or Visible traces headings. Source/filter/method/axis context is
unchanged in optional analysis.json. Full-test titles retain the test name.
Identically styled, visible min/max edges of actual envelope bands share one
legend item; hidden edges remain independent. Waterfall has no synthetic line
legend and its native colorbar identifies U or log10(U). Combined 2x2/3x3 exports
use tight white gutters without layout/slot captions; original slots remain in
metadata and intentionally unused fixed-grid cells stay blank.

Changed entry points: utils/plotPngExport.ts, FullTestPlot/WaterfallPlot and the
two export-dialog descriptions. No data/DSP/backend changes. Build/lint and all
44 existing frontend helper tests pass; only the existing Vite bundle advisory
remains. The built local preview includes these changes. Nine real ZIP/PNG
downloads cover TP Time, Full envelope, FFT, PSD, XY, linear/log Waterfall and
2x2/3x3 layouts; decoded PNGs, absent report text, retained metadata and slot
ordering pass with no page errors or dataset writes. Reproduction:
python -X utf8 scripts/verify_compact_png_exports.py
(default preview http://127.0.0.1:8087/ptt/, override PTT_PREVIEW_URL).
Evidence: %TEMP%/ptt-compact-png-exports; separate file-only single/2x2 PNGs:
%TEMP%/ptt-compact-png-preview. The script uses the existing demo read-only in
an isolated browser context and permits only image packaging/progress writes.
Historical export helpers now verify native chart/source/axis identity instead
of removed report text; their four complete older suites were not rerun.

Changes remain uncommitted on codex/ptt-ui-rework-2026-09-28 alongside independent
work, with no deployment or push. This request is complete; next independent
milestone remains Phase 11b.

## Scatter reload race (2026-09-28)

Reload previously cleared source identities with metadata. A fast test with no
test points could replace the selected axes while the matching test's metadata
and source catalog were still pending, leaving No comparable points. An isolated
Vite transform reinstating the original reload reproduced both changed axes and
lost selections; shared source was never reverted.

Manual reload now snapshots the live workspace and reuses identity-aware recovery:
fetch the list/catalog before invalidation, restore axes and unchanged selections,
and reject stale responses. Unresolved recovery references survive retries.
Metadata finally cleanup is generation-guarded, and pending scatter metadata
shows loading instead of a premature empty state. Recovered current axis/plot
choices remain fixed, as when reopening a saved workspace.

Build/lint, 44 frontend tests and `python -X utf8 scripts/verify_scatter_reload.py`
pass. Eight mocked browser checks cover staggered schemas/catalogs, repeated
reload, late obsolete statistics, live axis changes, and catalog failure/retry
without overwriting the saved workspace. Five read-only real-library reloads retained the
eight scatter points (seven rendered point/cluster marks) and selected TP with
no page errors or API writes. Evidence: `data/verification/scatter-reload` and
`%TEMP%/ptt-scatter-reload-live.png`. Built local preview serves the fix; existing
Vite bundle-size advisory remains. No backend or dataset edits. Changes remain
uncommitted alongside the independent consistency work below.

## Consistency across sections (2026-09-28, complete)

Split, Edit, Uploads, Components and Settings now follow Analyze's compact
controls and quieter hierarchy. Split has a stable action toolbar/Files menu,
smaller plot headers and shared help. Edit keeps Save/Reset reachable and shows
draft indicators on closed sections. Uploads uses stable setup fields, optional
metadata, compact history and Trash below it. Components has aligned filters,
tighter numeric/source tables and direct editing. Settings groups primary
preferences and moves import/export/default actions into More.

Existing handlers, safeguards and processing are retained. App passes an explicit
component-section destination from Components/Uploads so those links reveal and
focus the intended editor. Menus fit short zoomed windows; Split's toolbar scrolls
normally at viewport heights of 400px or less so it cannot cover editing controls.
Presentation-only sample-rate rounding retains exact hover values.

Final build/lint and scoped whitespace checks pass; final bundle is
`index-Cd2hOOzV.js`. All 44 frontend helper tests, 52 production integration checks,
16 Analyze control groups, 19 isolated Split groups, four native component
statistics groups, 11 Edit/Settings groups, and upload quality/component/resume
and focused Uploads suites pass. Tests cover actual 125%/150% browser zoom,
desktop windows down to 960x400, keyboard/menu actions, real isolated saves and
CSV/JSON downloads, failures/retry, guards, and unchanged fixture sources.
No production-preview API writes or JavaScript errors. Canonical browser suite
selectors were updated without weakening assertions; new integration/Edit/
Upload helpers are checked in. Details, commands, evidence and verification
limits: [SECTION_CONSISTENCY_QA](docs/SECTION_CONSISTENCY_QA.md).

Rebuilt preview: `http://127.0.0.1:8087/ptt/`. No backend processing change, user
dataset mutation, deployment or offline-package rebuild. Existing Vite bundle-size
advisory remains. Changes are uncommitted alongside earlier work on
`codex/ptt-feature-light`; the independent Phase 11 backlog is unchanged.

## Viewport-fitting plot grids (2026-09-28)

The requested 1x1, 2x2 and 3x3 layouts now divide the available viewport with
zero-minimum grid tracks and no grid scrolling or narrow-pane reflow. The scatter
remains beside the grid at desktop widths; its divider keeps horizontal mouse
and keyboard resizing. Short-window spacing preserves the current stable analysis
controls while making room for traces at browser zoom. Recovery messages no
longer force the plot pane into a scrolling minimum height.

uPlot uses fixed 11px tick/title fonts, measured numeric gutters, compact X
gutters and explicit padding. `utils/uplotAxisTitle.ts` fits Spectrum/XY/Waterfall
titles to the whole canvas, wraps long titles and preserves complete labels for
exports. Single-source Waterfall facets flex to their cells; existing multiple
source comparisons retain internal scrolling. Concurrent color popovers and
stable-toolbar changes are preserved.

Build/lint and scoped whitespace checks pass. All 62 desktop grid-fit checks pass
on the final `index-DBLnTTWQ.js` bundle, including glyph bounds, title/tick overlap,
long X/Y labels, nonblank drag/wheel/reset, resize, maximize/restore and splitter
controls. Actual XY PNG export retains full axis titles in both image and metadata.
No JavaScript errors or dataset writes. Actual 125%/150% browser zoom passes for
nine TP/Full-test plots with the scatter open; single,
quad and nine Waterfall checks pass at 1600/1100/800px. Reproduction:
`python scripts/verify_plot_grid_fit.py`, plus
`python D:/okumus/work/.ptt-rework-stage/check_grid_browser_zoom.py` and
`python D:/okumus/work/.ptt-rework-stage/check_waterfall_fit.py`.
Evidence: `%TEMP%/ptt-plot-grid-fit`, `%TEMP%/ptt-grid-browser-zoom`,
`%TEMP%/ptt-waterfall-fit` and `%TEMP%/ptt-grid-fit-export`.
The rebuilt local `/ptt/` preview serves this completed request. Existing Vite
bundle-size advisory remains. Work is uncommitted alongside the other current
changes; no backend data changes. Independent Phase 11 backlog remains next.

## Stable analysis controls (2026-09-28)

The latest alignment correction replaces independently wrapping controls with
two coherent groups: source/view/test on the left; Reset, Spectrum methods,
Options and detail/layout on the right. Above 900px of panel content width they
share one row, including the user's exact 960px and 1115px panels. Narrower panes
use fixed rows, preserving panel height across modes and selection counts. The
test selector gets its own row only at 510px content width or below; the left
group's measured 497px footprint otherwise fits comfortably in a 544px pane.
Toolbar controls share a 32px height. The test field is 160px with full-name
hover/focus, no visible Test label, and a matching divider after the view buttons.

Absent methods, reset and detail content consume no space. A real detail badge
such as `1:16` sits 6px before the layout buttons; Options and layout have a
subtle separator. Reset uses a named icon button and its existing handler.
Unavailable views keep a disabled Options icon. Opening/closing settings never
moves its trigger or the panel. The popover also observes its containing group's
size so an arriving detail badge cannot leave the popup misaligned.

Clear and the export portal now share the selection heading's baseline. Empty
selection guidance occupies the left chip area; Clear is disabled at zero.
The fixed horizontal chip strip stays 38px high and never wraps; the full tray
is 70px (68px in short desktop windows). Long shared test names truncate while
the selection count remains on one line. Both portal targets stay mounted.
Settings, source/view, selection, export and analysis handlers remain intact.

Build/lint and 17 read-only browser groups pass with
`python -X utf8 scripts/verify_analysis_controls.py`: both sources/all views and
methods, exact 960px/1115px first-row alignment, no overlapping controls or
phantom detail gaps, identical 8/1/0-chip panel/tray bounds, real long-name
tooltip, stationary open/close trigger, nested RPM search/two-stage Escape,
Tab traversal, Waterfall settings, test switching, export focus/maximize,
narrow panes, actual 125%/150% zoom and all popup fields at 960x400. No JavaScript
errors or API writes. Updated panel screenshots were visually reviewed.
Final targeted checks on the rebuilt correction also pass immediately either
side of the 900px/510px breakpoints, with Reset visible, real Time detail, and
stationary Options. At 307px content width, a 20/20 count remains on one line
beside a truncated long shared test name and Clear/export. The actual 544px
minimum pane now needs only two toolbar rows. See final-boundary-report.json.
Evidence: `%TEMP%/ptt-analysis-controls`; existing browser-only Python/Playwright
runtime, without loading native scientific-data libraries.

The rebuilt local preview http://127.0.0.1:8087/ptt/ serves this correction.
Existing Vite bundle-size advisory remains. No backend or session-schema changes.
This checkpoint targets `codex/ptt-ui-rework-2026-09-28`. Independent Uploads
rename changes and the Phase 11b backlog remain outside this alignment checkpoint.

## Compact waterfall color controls (2026-09-28)

Waterfall Min/Max/Apply/Auto now open from a Color button beside the plot title;
cards at 400px or below use a color-scale icon, matching Time statistics density.
The popup is portaled outside the plot scroll area, clamped to the viewport and
keyboard accessible, with Escape/Close focus return and outside dismissal.
Manual mode is highlighted. Exact values, validation, per-slot linear/log limits,
draft preservation and existing Apply/Auto handlers remain intact. Missing-sample
warnings retain their status row. The removed controls row frees plot space.

Frontend build/lint, scoped whitespace and 13 waterfall session helper tests pass.
Six read-only browser groups cover validation, Apply/Auto/Enter, draft retention,
keyboard/outside dismissal, reload, maximize/restore, 1100px/800px windows,
nine cards and actual 125%/150% browser zoom. No page errors or API writes.
Evidence: `%TEMP%/ptt-color-popover`; reproduction:
`python -X utf8 D:/okumus/work/.ptt-rework-stage/check_color_popover.py`.
The existing waterfall color browser suite now opens the portal by its trigger
and unique panel ID; syntax checked, its full export suite was not rerun.
Existing Vite bundle-size advisory remains. Built `/ptt/` preview serves the change.
Work is uncommitted alongside concurrent axis/layout edits on
`codex/ptt-feature-light`; independent Phase 11 backlog remains next.

## Spectrum and XY scope-text removal (2026-09-28)

Removed the shared Original data / complete TP banner and its row from
TimeSeriesGrid, including full-test/selected-interval variants and Spectrum's
Waterfall mode. Removed unused banner styles. Analysis details, missing-data
warnings and export provenance retain their existing behavior.

Frontend build/lint and scoped whitespace checks pass. Read-only browser checks
confirm the rebuilt preview has no banner or empty row in XY, FFT and PSD,
including full-test sources, maximize/restore and a narrower desktop viewport.
No page errors or API writes. Evidence: `%TEMP%/ptt-spectrum-xy-scope-removal.json`.
The existing bundle-size advisory remains. Changes are uncommitted alongside
prior work; this request is complete and the independent Phase 11 backlog remains.

## Shared display-detail indicator (2026-09-28)

Full-test display detail appears once as `1:16` (or the actual loaded level),
beside the shared layout controls. It uses the existing controls row; plots have
no resolution footer or extra row. The hover/focus tooltip distinguishes min/max
groups from sampled lines and states that CSV uses full-resolution samples.
Different visible levels show Mixed with per-variable detail in the tooltip.
Stable per-slot registrations report actual displayed responses and clean up on
unmount; density/maximize selects the relevant slots. A missing/empty plot hides
the aggregate until every available displayed slot has data. Original + filtered
and filtered status labels are removed from TP and Full-test headers; filter
warnings, overlay controls, legends and export provenance are preserved.

Build/lint and seven read-only browser groups pass: one shared indicator/no plot
footers, reclaimed canvas height, 800px/1100px, nine plots, maximize/restore,
keyboard tooltip, envelope/line/full-detail zoom, filtered-only/TP overlay,
browser-only mixed-level fixture and real test switching. No page errors or API
writes. Evidence: `%TEMP%/ptt-shared-plot-detail`; reproduction:
`python D:/okumus/work/.ptt-rework-stage/check_plot_detail_footer.py`.
The existing full-test comparison suite's layout assertion/documentation now
uses the shared indicator. Existing bundle-size advisory remains; the built
preview serves the completed request. Independent Phase 11 backlog remains next.

## Scatter control styling (2026-09-28)

Follow-up: More's SVG now sits 1px lower to align with Manrope's visible text,
which sits below its line-box center. Only `.moreGlyph` changes. Build/lint and
scoped whitespace checks pass; browser checks cover 1600/960px windows, actual
125%/150% zoom and click/Enter/Escape menu behavior. No page errors or API writes.
Evidence: `%TEMP%/ptt-more-alignment`. The rebuilt local preview serves the fix.

Removed the extra boxes around the axis row and filter summary. X/Y fields now
share a quiet background, 34px height and one outer focus/open outline; embedded
pickers no longer add an inner hover frame. More uses an aligned SVG ellipsis.
Filters has a 16px funnel SVG, a quiet resting state and a tinted open/active state;
the summary uses a single lower divider. Narrow desktop panes retain compact
controls and wrap without stretching Filters into a full-width button.

Frontend build/lint and scoped whitespace checks pass. Read-only Chromium checks
cover axis search/change, keyboard focus/Escape, More, filter selection/badges/Clear,
1600/1100/960px windows, minimum pane width and actual 125%/150% zoom. Screenshots
were visually reviewed; no JavaScript errors or API writes. Evidence:
`%TEMP%/ptt-scatter-controls`; helper:
`D:/okumus/work/.ptt-rework-stage/check_scatter_controls.py`.
Existing Vite bundle-size advisory remains. Changes are uncommitted alongside
other work in `work_v2/kiha_test`; the rebuilt `/ptt/` preview serves this completed
request. Independent Phase 11 backlog remains next.

## Compact variable legend (2026-09-28)

Full-test legend tooltips now show colored strokes and variable names only.
Popup entries use one row each; Original/Filtered labels and dashed/solid samples
appear only when both traces are shown. Single-mode rows use an unlabeled swatch.
The footer is shortened to Shared Y axis. Names truncate in popup rows with the
full name available on hover; tooltip entries wrap when needed. PageTooltip's
optional legend metadata uses the existing positioning and keyboard lifecycle,
falls back to ordinary text, and dismisses when focus moves into the popup.

Build/lint and six read-only browser groups pass: original/filtered/overlay,
matching colors, one-row layout, keyboard/Escape, 800px/1100px widths, six variables,
removal and ordinary tooltip regression. No page errors or API writes. Evidence:
`%TEMP%/ptt-compact-legend`; reproduction:
`python D:/okumus/work/.ptt-rework-stage/check_compact_legend.py`.
Existing bundle-size advisory remains. The built local preview serves this change;
this request is complete and the independent Phase 11 backlog remains next.

## Export button placement (2026-09-28)

Export selected plots now sits at the right of the selected-point chips, directly
below Clear. A stable tray target receives the existing grid-owned export control
through a React portal, preserving its registry and dialog. The separate export
row is removed; Spectrum/XY scope text was removed in the subsequent request above. The empty TP tray reserves
no button space, while Full test still exports without selected points.

Build/lint and scoped whitespace checks pass. Read-only Chromium checks cover
eight wrapping chips at 1600/1100/960px, keyboard/click dialog opening, Escape/Close
focus return, maximize/restore availability, Time/Spectrum/XY switching, and
empty/full-test states. No JavaScript errors or API writes; screenshots visually
checked. Evidence: `%TEMP%/ptt-export-placement`; helper:
`D:/okumus/work/.ptt-rework-stage/check_export_placement.py`.
Existing Vite bundle-size advisory remains. Work remains uncommitted in the
authoritative `work_v2/kiha_test` alongside prior/concurrent changes. The rebuilt
`/ptt/` preview serves this completed request; independent Phase 11 remains next.

## Time notes removed (2026-09-28)

Removed the unused time-notes feature across TP and Full-test plots: toolbar toggle,
marker/interval/manage menu items, dialog, canvas overlays, hooks/API requests,
PNG text/provenance and export availability dependency, session visibility state,
and backend GET/PUT routes/storage module. Source files already present on disk
are not deleted or interpreted; ordinary test descriptions/findings remain.
Legacy sessions ignore the retired field and new saves omit it.

Build/lint and frontend helper tests pass (44 checks in the current workspace).
Backend Python 3.13 suite: 492 tests / 456 subtests pass; two existing dependency
deprecations. Existing Vite bundle-size advisory remains. Focused browser checks
cover TP/Full-test keyboard/right-click menus, single/combined PNG ZIP exports,
1100px resizing, maximize/restore, old session file import, save and reload, with
no page errors, retired API requests or dataset-write requests. Reproduction:
`python scripts/verify_time_notes_removal.py`; evidence:
`%TEMP%/ptt-time-notes-removal`. Backend tests use the existing dependencies via
`PYTHONPATH=D:/okumus/kiha_test/backend/.venv/Lib/site-packages` with
`backend/.venv/Scripts/python.exe -m pytest backend/tests`.

Local backend restarted (launcher PID 14340); live OpenAPI confirms no annotation
endpoints. The rebuilt `/ptt/` preview serves the change. Obsolete annotation
tests were removed and related browser suites updated. Other concurrent work
and datasets are preserved; no deployment or Git checkpoint was requested.
This request is complete; the independent Phase 11 backlog remains next.

Updated: 2026-09-28. Selected test-point tooltips now show only the point name,
test and optional label; the Click to hide/show suffix is removed in
`frontend/src/components/controls/SelectedPointsPanel.tsx`. Visibility actions and
accessible names remain intact. Frontend build/lint pass; this text-only change
was checked in source without another browser run. Existing bundle-size advisory
remains. Independent Phase 11 backlog is unchanged; prior work is recorded below.

## Current milestone

The user-requested interaction pass is installed in `work_v2/kiha_test` and
verified through the final built `/ptt/` route. The local preview serves it now.

Changes: fixed scatter hover dismissal and removed its heading; plot titles now
select signals directly, including empty-workspace configuration; Source/View
and FFT/PSD/Waterfall are clear separate controls; selected chips remain visible;
shared numeric fields select all on entry and show small inline warnings; secondary
help is under Details. TP plots keep one variable; Full-test comparisons keep six.
Capacity remains 20 and selected colors remain distinct from blue overview points.

Full precision, session schemas, exports, explicit save guards and backend methods
are preserved. Invalid filter drafts, known Nyquist violations and invalid Waterfall
color limits cannot launch processing or commit limits. Unknown sample rates still
use server validation. Native focus stays on a signal selector after chart remount.

## Source and preservation

Authoritative destination: `D:/okumus/work_v2/kiha_test`, branch
`codex/ptt-feature-light`, based on feature/resumable-multipart-upload commit
`1f0793380d0bad43f88e2c4d384ad05f7a0421e2`. Work remains uncommitted.
Current stage: `D:/okumus/work/.ptt-interaction-rework`.
Do not copy older `.ptt-feature-rework` or `.ptt-main-rework-backup` over this work.

Changed-file install protects intervening edits using
`D:/okumus/work/.ptt-rework-stage/interaction-baseline.json`; overwritten files
are backed up and dataset hashes verified. Installation/backup record:
`D:/okumus/work/.ptt-rework-stage/interaction-install-report.json`.
Original repositories, final data, Python environment and dependency versions
are preserved. No GitHub push, ZIP or production deployment was requested.
Earlier main UI remains in stash `3bbdbd2cec371d2db63a98dee4e1817b85281459`.

## Verification

- Frontend build/lint pass; all 41 frontend helper tests pass.
- Python 3.13 backend: 497 tests / 477 subtests pass; two dependency deprecations.
- Isolated browser suites: 9 direct-analysis, 9 scatter-hover, 8 numeric groups.
  Exports/session reopening, keyboard focus, source/view combinations, 20 chips,
  actual 125%/150% zoom, invalid-input guards and fixture preservation pass.
- Final destination build/lint, 41 helpers and Git whitespace check pass.
- Final built `/ptt/` smoke: all six pages, scatter dismissal, Source/View/method
  controls, local logo/font, 1100px resize and K11C0 roundtrip pass. No page/HTTP
  errors or external requests; 26 non-cache dataset files unchanged.
- Details and local reproduction: [INTERACTION_QA](docs/INTERACTION_QA.md).
  Evidence: `%TEMP%/codex-ptt-interaction-qa`.
- Preceding feature migration evidence remains in [REWORK_QA](docs/REWORK_QA.md).

Inherited Vite large-bundle advisory remains. Dependencies were not upgraded.

## Runtime and next step

Preview: `http://127.0.0.1:8087/ptt/`; same-origin `/ptt/api/`, local backend8000,
K11C0 return link, local Manrope and the shared K11C0 rotor favicon/logo. No backend code change
requires a restart. The project uses `npm run build` (no build:rota script).

This requested milestone is complete. Independent Phase11 backlog remains unchanged. Offline Linux deployment
and 20/100-user load testing have not been performed. Deployment notes remain in
`../PTT.md`; runtime has no internet/GPT/auth dependency.

## Subsequent requested visual polish

Browse full test has white text in normal/hover states. Source/View/Layout
visible captions were removed while accessible group names remain. The browser
title is PTT · Propeller Test Tool (HTML entity prevents mojibake). Header and
favicon now share a local copy of toolsPage/assets/favicon.svg, replacing the
previous PTT image. Original asset remains available; no external asset dependency.

Header now uses one 64px row for brand, navigation and actions above 1240px.
Narrower desktop widths retain all controls in two rows; checked at 1600, 1440,
1241, 1100, 960 and 800px with no overflow. Build/lint, navigation and Sessions
checks pass. Local backend was found stopped and restarted (PID21296); all three
ready tests load again. Header preview: %TEMP%/ptt-single-row-header.png.

Shared control contrast audit: fixed the legacy primary-button text override
centrally, including hover and disabled states; removed the Browse-only exception.
Primary/import/upload actions share white text on the accent background. Improved
muted selection controls, Auto-split toggle and warning/danger confirmation hovers.
Build/lint and Git whitespace checks pass. Disposable-browser audit covers all six
pages, searchable dropdown selections/hover, native filter select, invalid/valid
numeric fields, axis and session dialogs, shared action variants and the actual
Try again state via browser-only API failure interception. All 42 checked states
pass; rendered enabled control text meets 4.5:1 in sampled states. No page errors
or data writes. Evidence: %TEMP%/ptt-control-colors/report.json; helper:
D:/okumus/work/.ptt-rework-stage/check_control_colors.py.

Plot-copy simplification initially consolidated scope above the grid for
XY/Spectrum/Waterfall; that banner is now removed by the request above. Mean captions
open statistics directly; filtered plots still say Original mean(s). Routine per-plot
scope/Details rows are removed; Analysis details stays in each plot menu and
missing-sample warnings retain direct Details access. Incompatible signals use a
quiet one-line state without the waveform or repeated names; complete reasons
remain accessible and in XY Details. Slots, signal pickers, retries and export
provenance are preserved. Build/lint and whitespace checks pass; 15 focused
browser checks cover quad/maximized scopes, keyboard focus, filter context,
analysis details, incompatible data and omitted-row warnings at 1600/1100 widths.
No browser errors or data-write requests. Evidence: %TEMP%/ptt-plot-repetition;
helper: D:/okumus/work/.ptt-rework-stage/check_plot_repetition.py.

Statistics display polish: the quiet Mean label, tabular value and disclosure
chevron now sit at the right of the signal title row, before the plot actions.
Only Time TP headers opt into this layout; other summaries keep their placement.
Cards at 400px or below use a statistics icon with the rounded mean in its compact tooltip
and the same popup, keeping the title row compact. Identical means appear once;
ranges use a spaced dash, while distinct means that round to the same value show
an approximation marker. Captions and popup means/population SD use four
significant digits with trailing zeros removed; small nonzero values retain
scientific notation. Full precision stays available on popup cell hover. The header tooltip now shows
only the rounded caption (including Original/partial context when applicable). Sample counts,
row bounds, time precision and all calculations are unchanged. Popup numeric
columns align to the right. Build/lint and whitespace checks pass; focused browser
checks verify real demo means/SD, exact titles, keyboard opening/closing, equal
and near-equal values, tiny signed numbers/zero, and unclipped desktop captions.
Evidence: %TEMP%/ptt-statistic-display. Follow-up header build/lint and browser
checks pass for four/nine plots at 1600/1100px, including same-row alignment,
unclipped values, signal picking and keyboard statistics. Compact statistics
controls also preserve tooltip and popup access with the scatter pane open. Latest screenshots:
%TEMP%/ptt-statistic-header.

Requested TP toolbar text removal (2026-09-28): removed the Statistics / original
data / complete TP banner from TimeSeriesGrid. Build/lint pass; read-only browser
check against the rebuilt /ptt/ preview confirms four TP plots and statistics
controls remain visible with no shared scope banner. Existing bundle-size advisory
remains. This request is complete; independent Phase 11 backlog is unchanged.


## Selected-point left-drag zoom (2026-09-28)

Ordinary left-drag now zooms X, Y or both in TP Time plots, using a 10 CSS pixel
threshold on each axis. X remains linked; only the target plot receives fixed Y
bounds through its existing saved range state. The shared pan/zoom plugin owns
this gesture for TP plots so synchronized uPlot mouse events cannot apply Y to
other variables. Y is committed before X to prevent automatic fitting from
undoing the selection. Existing Alt+drag/wheel Y, Shift/middle X pan retain their existing behavior. Other plot modes are covered
by the extension below. Escape, blur, resize/destruction and
reset clean up the selection. Double-click resets both axes; Reset Y still fits
only Y. The Y-axis Details text describes these gestures.

Build/lint and scoped whitespace checks pass. Nine read-only browser checks cover
diagonal/vertical/horizontal/reverse drags, exact plotted Y bounds, per-slot state,
maximize/resize/reload, double-click reset, Alt-drag, Escape/tiny gestures,
Shift/middle pan and right-drag exclusion. No JavaScript errors or API writes.
Evidence: %TEMP%/ptt-left-drag-zoom; reproduction helper:
D:/okumus/work/.ptt-rework-stage/check_left_drag_zoom.py.


## Left-drag zoom across non-scatter plots (2026-09-28)

Full test, FFT/PSD, Waterfall, XY and Split previews now use the same 10px-per-axis
left-drag selection as TP Time: horizontal X, vertical Y, diagonal both. Full/Split
X stays linked and each plot owns its Y. The main Recharts scatter keeps its
existing wheel zoom and left-drag pan. Split labels/edge handles still edit only
the unsaved TP draft. Escape/right-click exclusions and double-click reset remain.

Full-test Y ranges persist in the optional `plotViewports.full` session slots;
older sessions normalize to automatic Y. FFT/PSD and Waterfall distinguish manual
Y from automatic fitting, so X changes/refetches cannot discard the vertical crop.
Spectrum linear/log switches preserve X and reset incompatible Y bounds; old
X-only viewport contexts still restore. Split Y survives refetch/resize, and any
Split reset clears all preview Y axes. Split viewport state remains local to its
editing view, as before. XY retains its two-axis wheel/pan behavior.

Build/lint and 44 frontend tests pass, including new full-range session validation
and legacy compatibility. Read-only browser checks cover all modes with actual
rendered bounds, vertical/horizontal/reverse box drag, reset, resize/reload,
linear/log transitions, Escape and right click. Split handle edits remained in the
disposable browser draft. Scatter wheel/pan behavior was checked unchanged.
No JavaScript errors or data-write requests. A transient backend-unavailable
response interrupted one Waterfall reload check; the backend recovered and the
rerun passed. No backend changes. Evidence and reproduction:
%TEMP%/ptt-all-plot-zoom and D:/okumus/work/.ptt-rework-stage/check_all_plot_zoom.py.


## Drag-box border snap fix (2026-09-28)

Two real-mouse reproductions exposed separate transitions: reversing back inside
an activated axis threshold, and crossing X/Y thresholds at different times at
the start of a drag. Latching activation fixed the first but left the initial
full-height/full-width flash. `uplotPanZoom.ts` now draws the ordinary preview
from the anchor to the pointer immediately, independently of commit thresholds.
Pure horizontal/vertical drags have a one-pixel visual thickness, clamped inward
at plot edges. Unmoved/cancelled/released selections have zero area. Alt-Y keeps
its full-width preview; the original axis thresholds/latches, actual-coordinate
range calculation and finite/nonzero guards still govern commits. Calculations,
linked axes and scatter behavior are unchanged.

Build/lint and all 44 frontend tests pass. `check_zoom_start.py` passes 28 browser
groups across TP Time, Full test, FFT, PSD, Waterfall, XY and Split: first-pixel
and unequal threshold starts in both directions, reversals, Alt-Y, visible thin
horizontal/vertical drags, tiny noncommitting gestures, Escape, actual final
ranges and selection cleanup. No JavaScript errors or API writes. Evidence:
%TEMP%/ptt-zoom-start (before.json records the original 213px-to-11px height snap);
helper: D:/okumus/work/.ptt-rework-stage/check_zoom_start.py. Existing earlier
reset/resize/reload/scatter checks are recorded under the non-scatter extension.
The stopped local preview/backend were restarted for testing (ports 8087/8000).
This requested fix is complete; the independent Phase 11 backlog is unchanged.
