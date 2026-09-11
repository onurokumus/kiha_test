# TODO

Open items below are listed in recommended implementation order. Complete each phase before moving to the next, except for independent small fixes.

## Requested component sets and telemetry (2026-09-11)

- [x] Support multiple named propeller / motor / ESC sets per test, each with its own RPM column and optional motor temperature and power columns.

User-requested feature completed ahead of independent Phase 11b. Up to 16 named
sets preserve legacy assignments, Upload/Edit and resumable uploads, independent
RPM runtime and optional finite running-sample telemetry. Explicit C/F/K and W/kW
units normalize summaries to C/W; no power type is inferred. Atomic draft saves,
column edits, test lifecycle and complete export provenance are retained. Full
backend suite (476 tests), frontend build/lint, new native two-set browser suite
and existing component/statistics regressions pass. Keyboard/window/125%/150%
zoom, HTTP-compatible IDs, invalid-settings repair and unchanged source samples
verified. See [component sets verification](docs/COMPONENT_SETS_VERIFICATION.md).

## Requested despike trace fix (2026-09-11)

- [x] Keep filtered Test points plots as one line per test point instead of separate maximum/minimum curves when adding Despike.

User-requested fix takes priority over the independent Phase 11b side panel.
Use the existing bounded Line display for TP filtering, retaining exact saved
rows, relative time, Original overlay and full-resolution exports. Full-test
Auto/Line/Min-max controls keep their existing behavior. Build/lint, all 453 backend
tests/370 subtests, extended browser overlay checks with 600/20,000-sample TPs,
and compact-controls/real export regressions pass. Desktop gestures, resize,
maximize/restore, 125%/150% zoom and unchanged fixture sources verified.
See [despike trace verification](docs/DESPIKE_TRACE_VERIFICATION.md).

## Requested UI fixes (2026-09-11)

- [x] Make Show time notes and Export selected plots icon-only with hover tooltips; make filter-dialog fields readable and styled; widen the right-side test selector; show X before Y in XY variable selection; keep overlapping-point selection above the scatter divider and adjacent plots.

User-requested polish takes priority over the independent Phase 11b side panel.
Completed with readable modal fields, shared icon controls, flexible test width,
explicit X/Y order and a body-level clustered-point menu. Build/lint, all 453
backend tests/370 subtests, focused UI/scatter checks and existing XY-time,
compact-controls, Y-axis and rendering regressions pass. Keyboard, resize,
maximize/restore and actual 125%/150% zoom verified; fixture sources unchanged.
See [UI polish verification](docs/UI_POLISH_VERIFICATION.md).

## Linux deployment regression (2026-09-11)

- [x] Fix the analysis-source/Trash API failures on the Linux Python 3.11 baseline and the ready-test count that only appears after opening Uploads.

User-reported regression handled ahead of independent Phase 11b. Portable link checks preserve symlink/junction safeguards across analysis sources, Trash and component statistics. The initial test list/count now loads independently of source verification; unavailable/loading states and retry preserve saved identities. All 453 backend tests/370 subtests, frontend build/lint and the extended isolated session-recovery browser suite pass. Actual Linux production confirmation/deployment remains outstanding; tests simulate the absent Python 3.12 API on the required Windows 3.13 runtime. See [diagnosis and verification](docs/LINUX_CATALOG_FIX.md).

## 1. Fix current UI and plot issues

Fix existing rendering problems before adding plot interactions and exports.

- [x] Fix the alignment / positioning of the "CSV" text in the Uploads section.
- [x] Fix scatter-plot hover boxes: keep all text inside the box, wrap long content, and ensure boxes appear above neighboring plots on the right instead of underneath them.
- [x] Fix shifted hover / crosshair lines after maximizing a time-series plot in the Test Points section. Verify alignment after maximizing, restoring, and resizing.
- [x] Fix shadow-like error-bar lines left behind on scatter plots after zooming or panning. Reset zoom currently does not clear the artifacts; verify redraws remove stale error bars.

Completed 2026-09-09. Regression coverage and results: [rendering verification](docs/RENDERING_VERIFICATION.md). Also fixed blank grids in short desktop panes exposed by 150% browser zoom.

## 2. Document and verify the FFT method

Document the current method early so the other team can compare results while feature development continues.

- [x] Write a short, source-verified FFT comparison document for the team. Include the actual FFT code snippet (starting with `backend/app/dsp.py`), libraries and versions, parameters and defaults, equations, sample-rate derivation, selected time interval, preprocessing / filtering, detrending, windowing, FFT length, frequency-bin spacing, amplitude normalization, units, and handling of missing samples or time gaps. Distinguish FFT magnitude from Welch PSD and explain any RPM / per-revolution frequency conversion. Include a reproducible comparison checklist so another team using the same data can investigate different reported frequencies.

Completed 2026-09-09. [Team comparison guide](docs/FFT_COMPARISON.md) and [verification evidence](docs/FFT_VERIFICATION.md): 17 new method tests; all 217 backend tests, frontend build/lint and documentation checks pass. Existing reduction, TP-boundary and spectrum-metadata limitations are documented for separate work before spectral exports; analysis behavior was not changed.

## 3. Surface data-quality issues

Make input problems visible before users interpret plots or export analysis results.

- [x] Add a compact data-quality summary for uploaded tests: time gaps, duplicate or backward timestamps, and missing values, with affected columns / intervals where available. Reuse existing validation and distinguish warnings from conditions that prevent analysis.

Completed 2026-09-09. [Data-quality verification](docs/DATA_QUALITY_VERIFICATION.md): compact Uploads status/details, preserved source timestamp findings, current missing/infinite counts and gap intervals, explicit legacy/generated-time limits, and existing analysis restrictions. All 231 backend tests, frontend build/lint, and live isolated Chromium upload/edit/failure/keyboard/resize/125%/150% zoom checks pass. No automatic legacy rescan or analysis-method changes.

## 4. Improve test-point analysis

Establish plot behavior before building export around it.

- [x] Enable Y-axis zoom in test-point plots against time, with a clear way to reset the axis range.
- [x] Test-point statistics: show the selected variable's mean without cluttering the screen (for example, in a compact plot subtitle or details tooltip). Clarify whether standard deviation is also needed; this carries forward the earlier mean / standard deviation TODO.

Phase 4a completed 2026-09-09. [Y-axis verification](docs/TIME_Y_ZOOM_VERIFICATION.md): per-plot Alt+wheel / Alt+drag, keyboard-accessible precise bounds and Reset Y, shared reset, and compatible session autosave. Independent ranges survive maximize/restore, layout changes, resize and reload; variable/visible-TP context prevents applying the wrong range. All 231 backend tests, frontend build/lint, extended isolated Chromium checks at actual 125%/150% zoom, and the earlier rendering regression suite pass. Statistics remain the next feature boundary.

Phase 4b completed 2026-09-10. [Statistics verification](docs/TP_STATISTICS_VERIFICATION.md): user-confirmed compact header means and population SD in per-TP details, using complete original full-resolution finite samples with counts/bounds and explicit source labels while filtering. Shared cache/retry supports legacy responses, missing/partial/error states and lazy cache upgrade. All 240 backend tests, frontend build/lint, live isolated statistics checks and existing rendering/Y-axis suites pass, including repeated resize/maximize/keyboard/125%/150% browser zoom. Also fixed the shared tooltip update loop exposed by stress checks. Phase 5 is next.

## 5. Make split-data exports identifiable

Use a consistent test-point ID column across both download entry points.

- [x] Include a test-point ID column when downloading split test data from Uploads.
- [x] Include a test-point ID column when downloading data from the Split section, and include the test-point ID in the downloaded filename.

Phase 5 completed 2026-09-10. [Split-export verification](docs/SPLIT_EXPORT_VERIFICATION.md): on-demand saved-TP CSV downloads in Uploads, shared `test_point_id` column with source-name collision preservation, and exact half-open saved/draft exports from Split with TP IDs in filenames. Unsaved/new/open-ended TP downloads match Save without persisting changes; original/full exports remain compatible. All 249 backend tests, frontend build/lint and isolated real Chromium downloads/keyboard/resize/125%/150% zoom checks pass. Fixed Uploads action clipping at 150% zoom. Phase 6a (despiking audit and original/filtered overlay) is next.

## 6. Build plot export and filtered-data support

First verify and display the original / filtered data paths, then reuse them for exports. Implement single-plot export before extending it to multi-plot layouts.

Proposed prerequisites for spectral exports from the Phase 2 audit: preserve actual peak frequencies and expose reduction/method metadata; reconcile Spectrum intervals with saved half-open TP rows; surface per-TP missing-value counts and explicit PSD units on order axes. See [deferred behavior changes](docs/FFT_VERIFICATION.md#deferred-behavior-changes). These findings do not reorder Phases 3–5.

- [x] Verify the current scope of despiking and add a toggle to display original and filtered data together, with clearly distinguishable traces.

Phase 6a completed 2026-09-10. [Method and scope](docs/FILTER_METHOD.md), [overlay verification](docs/FILTER_OVERLAY_VERIFICATION.md): persisted per-plot Original toggle in TP and Full test modes, thin dashed originals and prominent filtered traces, independent TP facets, no display-only re-filter, and explicit loading/partial/fallback/Retry states. TP filters now use exact saved half-open rows and actual time origin. All 260 backend tests, frontend build/lint, real isolated Chromium data/style/failure/persistence/keyboard/resize/maximize/125%/150% zoom checks and existing Y-axis/rendering suites pass. Filtering remains temporary; existing statistics/CSV exports remain original. Phase 6b single-plot CSV/image export is next.

- [x] Add a plot export button with CSV data and image export options. Support a single plot or a user-selected multi-plot layout, including 2x2 and 3x3. Limit exported data columns to the selected / plotted variables and the time or axis identifiers needed to interpret them.

Phase 6b completed 2026-09-10. [Plot-export verification](docs/PLOT_EXPORT_VERIFICATION.md): single TP/Full test plot CSV and PNG, explicit original/filtered/both CSV choice, full-resolution shared DSP and exact source/TP/row/time identifiers. Unzoomed CSV exports complete source rows; X zoom crops actual sample centers after processing. PNG captures current traces/axes with complete source/settings/legend. All 282 backend tests, build/lint, 19 real CSV/9 PNG browser downloads, focused modal/hidden-legend checks and existing Y/rendering suites pass. Known pre-existing Windows cold statistics-cache race is documented; fixtures are prewarmed. Multi-plot/Spectrum, sidecars and long-export progress/cancellation remain; this combined checkbox stays open. Next: Phase 6c selected multi-plot time-series export (2x2/3x3).

Phase 6c completed 2026-09-10. [Multi-plot verification](docs/MULTI_PLOT_EXPORT_VERIFICATION.md): selected TP/Full time-series slots in grid order; full-resolution CSV ZIP with independent per-slot original/filtered/both choices, plus a combined 2x2/3x3 PNG. Duplicate variables retain slot identity and settings through shared 6b builders/DSP/capture paths. Aggregate limits, complete staging, failure/Retry, Close suppression, layout preview/capacity and maximize/restore are verified. Fixed a pre-existing partial-metadata restore race that moved saved columns without their slot filters. All 297 backend tests/174 subcases, build/lint, 13 real ZIPs (61 CSV entries)/6 combined PNGs, 6b exports and existing Y-axis/rendering regressions pass; 21 source files unchanged. The broad export checkbox stays open for Spectrum/XY. Next: Phase 6d scientific Spectrum prerequisites from Phase 2, then remaining plot-mode exports; metadata sidecars and cooperative progress/cancellation follow.

Phase 6d completed 2026-09-10. [Spectrum analysis verification](docs/SPECTRUM_ANALYSIS_VERIFICATION.md) and updated [FFT comparison guide](docs/FFT_COMPARISON.md): exact saved TP rows/actual sample times, reusable unreduced spectral results, true maximizing-bin frequencies without rounding, and per-trace method/reduction/missing/RPM metadata exposed through Analysis. FFT/Welch estimators remain unchanged; Welch is explicitly U²/Hz on both Hz and order axes (X remapping only). Correct TP identity is required from the server; context guards and partial/error details prevent stale or mislabeled results. All 308 backend tests/191 subcases, build/lint, six real API cases and 16 native browser reference cases pass, including explicit Full time-zoom → Spectrum bounds, retries, legacy metadata, keyboard/maximize/125%/150% checks; 13 source files unchanged. Next: Phase 6e Spectrum single/multi-plot CSV/PNG export; XY export, metadata sidecars and cooperative progress/cancellation remain subsequent slices.

Phase 6e completed 2026-09-10. [Spectrum-export verification](docs/SPECTRUM_EXPORT_VERIFICATION.md): single CSV/PNG and selected 2x2/3x3 CSV ZIP/PNG, including nine slots. Native FFT/Welch bins retain independent source grids, exact TP/Full rows and actual method/timing/RPM metadata; order maps X only, Log Y preserves linear CSV values, and frequency zoom/pan crops after estimation. Shared staging/limits, stale-context checks, visible legends and all-or-error delivery are verified. All 322 backend tests/207 subcases, build/lint, 36 real CSV entries/13 PNGs, and both time-export browser regressions pass; 20 Spectrum fixture files unchanged. The broad export checkbox remains open for XY. Next: Phase 6f XY single/multi-plot export, then provenance sidecars and cooperative progress/cancellation.

Phase 6f completed 2026-09-10. [XY-export verification](docs/XY_EXPORT_VERIFICATION.md): single CSV/PNG and selected 2x2/3x3/nine-slot CSV ZIP/PNG. Native finite X/Y pairs retain full precision, exact saved TP/loaded Full rows, both variable identities and source/sample/time identifiers; default views retain all native pairs, explicit crops use both axes, with no interpolation or temporary filtering. Display now exposes exact context/counts and supports tiny-axis wheel zoom. All 339 backend tests/234 subcases, build/lint, 42 real XY CSV entries/18 PNGs and existing Spectrum/single/multi time-export browser regressions pass; all 14 XY fixture files unchanged. Partial/stale/legacy/failure/Close guards and desktop keyboard/maximize/125%/150% checks pass. The broad plot-export checkbox is complete across Time, Spectrum and XY. Next: Phase 6g analysis provenance metadata sidecars, then Phase 6h progress/cooperative cancellation; Phase 6 remains open.

- [x] Allow exporting despiked / filtered data through the plot export controls. Make the choice between original and filtered data explicit and use the same filter settings as the displayed plot.
- [x] Include a small analysis metadata file with exports, recording source test and test-point IDs, time interval, selected variables, equations, filter settings, and applicable FFT settings / method version. Generate it from the settings actually used for the export so results can be reproduced and compared.

Phase 6g completed 2026-09-10. [Analysis metadata verification](docs/ANALYSIS_METADATA_VERIFICATION.md): single/selected Time, Spectrum and XY exports include `analysis.json` in a ZIP by default, with a file-only option. CSV records executed settings/results under source locks; PNG records loaded source/equation/method context captured with axes and visible traces. Current equations/dependencies, exact scopes/counts, runtime and file hashes are included; unavailable history/units are explicit. All 350 backend tests /243 subcases, build/lint, 24 real metadata packages (28 CSV entries /13 PNGs), and all four file-only export browser suites pass. Real edit-after-load, failure/Retry/Close, nine slots, keyboard and 1100px/125%/150% checks pass; 14 source files unchanged outside the explicit fixture edit. Next: Phase 6h progress/cooperative cancellation; Phase 6 remains open.

- [x] Add progress reporting and cancellation for long-running CSV / multi-plot image exports. Cancel the underlying work where possible and clearly distinguish completed, canceled, and failed exports.

Phase 6h completed 2026-09-10. [Export progress verification](docs/EXPORT_PROGRESS_VERIFICATION.md): shared progress and Cancel in single/multi Time, Spectrum and XY dialogs, with explicit completed/canceling/canceled/failed states and keyboard focus restoration. Cooperative checkpoints cover lock waits, source/batch reads, processing boundaries, hashing and packaging; canceled/disconnected work releases locks/spools and never offers a partial file. PNG captures freeze together before composition yields. All 361 backend tests /245 subcases, build/lint, 24 metadata packages and all four file-only browser suites pass. A real 700K-row fixture verifies 2.8M-row multi-plot cancellation, native Time/Spectrum/XY work, throttled receiving, retry, Close/reload, PNG encoding/composition, keyboard and 1100px/125%/150%; seven source files unchanged. Native calls finish their current block before cleanup. **Phase 6 is complete. Next: Phase 7a upload descriptions and editable test notes.**

## 7. Extend test metadata and deletion handling

Add metadata and annotations before extending saved sessions and context-menu actions. Settle deletion behavior before calculating component totals.

- [x] Allow a description / note when uploading a test (for example, "This is a temperature test"). Allow adding and editing findings later (for example, "This happened at the end of the test").

Phase 7a completed 2026-09-10. [Test-notes verification](docs/TEST_NOTES_VERIFICATION.md): optional upload descriptions survive local/server-only resume and ingestion; Uploads previews/search links directly to dedicated Edit description/findings fields. Partial atomic metadata writes preserve legacy descriptors and scientific data, with validation, clearing, failed-save retry and unsaved-draft guards. All 370 backend tests /264 subcases, frontend build/lint, and isolated Chromium upload/batch/resume/edit/reload/keyboard/1100px/125%/150% checks pass; 36 source files unchanged. Rename/rebuild/current trash-restore preservation is verified. No note revision history or annotation behavior yet. Next: Phase 7b persistent time markers and interval annotations.
- [x] Add persistent time markers and interval annotations to test plots (for example, "Vibration started here"). Support creating, editing, and deleting notes tied to a test and timestamp / interval, with an uncluttered way to show or hide them.

Phase 7b completed 2026-09-10. [Annotation verification](docs/ANNOTATIONS_VERIFICATION.md): revision-checked per-test markers/intervals, create/edit/delete and failed-draft guards, exact full/TP-relative canvas projection, persisted show/hide and single/multi PNG text/provenance. Trims retain original timestamps and flag out-of-data notes; rebuild/rename/current trash-restore preserve the file. All 376 backend tests /285 subcases, build/lint, isolated CRUD/conflict/failure/filtered/full/relative/PNG/keyboard/resize/maximize/125%/150% checks and existing native time-export regressions pass. 21 source files unchanged during annotation edits; separate real trim and later note correction verified. No history/live synchronization; stable-ID trash remains separate. Next: Phase 7c component registry and upload/existing-test associations.
- [x] During upload, allow separate selection of the propeller, electric motor, and ESC. Each component type should have a dropdown of existing components and an option to add a new one (for example, motor_1, motor_2, helix_1, helix_2).

Phase 7c completed 2026-09-10. [Component verification](docs/COMPONENTS_VERIFICATION.md): shared typed UUID registry, existing/new Upload/Edit selectors, local/server-only resume, failed-ingest identity retention and atomic revision-guarded reassignment. Legacy fields, notes, annotations and scientific files are preserved; export provenance snapshots association IDs/revision. All 384 backend tests /301 subcases, frontend build/lint, isolated component browser checks and the existing notes browser suite pass, including failure/conflict/reload, keyboard, 1100px/125%/150%; each browser suite preserves 36 source files. The next checkbox's assignment/correction behavior is complete, but its statistics clause remains pending for Phase 10: no component totals/cache exists, and deletion policy must be settled first. Association revisions provide a future invalidation token, not implemented statistics. Next: Phase 7d trash with stable deleted-test IDs and conflict-safe restore.
- [x] Allow assigning and correcting propeller / motor / ESC associations after upload, including for existing tests. Recalculate affected component statistics when an association changes.

The deferred recalculation clause was verified and completed in Phase 10 below (2026-09-10): current association reads update both former and replacement component totals, without caching aggregate assignments.
- [x] Add a trash bin to Uploads that lists deleted tests. Provide permanent deletion for individual tests and a "Delete all" action for the trash. Handle multiple deleted tests with the same name using stable unique IDs and enough visible context to distinguish them (such as deletion time). Preserve notes, time annotations, and component associations while tests are in the trash.
- [x] Restore tests from the trash with their data and metadata intact. Handle name conflicts with active tests without overwriting either test or breaking ID-based associations.

Phase 7d completed 2026-09-10. [Trash verification](docs/TRASH_VERIFICATION.md): persistent UUID entries preserve repeated names, legacy trash, metadata, annotations and component IDs; Uploads provides restore under an available name, permanent individual/all deletion and explicit partial-failure retry. Delete all acts only on the confirmed ID snapshot. All 398 backend tests /312 subcases, build/lint, real Chromium lifecycle/conflict/failure/locked-file/empty-library reload/keyboard/1100px/125%/150% checks and the component regression suite pass. Eight restored source/annotation files and 18 active files remain byte-identical; component regression preserves 36 source files. Existing one-hour expiry on the next test deletion is retained and displayed; no retention change was requested. Next: Phase 8 explicit save/reopen of analysis sessions. The earlier component-statistics clause remains pending for Phase 10.

## 8. Complete saved analysis sessions

Build on the existing session persistence in `frontend/src/services/analysisSession.ts`; audit what already works before adding anything. Cover the analysis state introduced in earlier phases.

- [x] Complete explicit save / reopen of analysis sessions, including selected tests / test points, variables, filter settings, plot layout, and axis ranges. Preserve original / filtered display choices and annotation visibility. Handle references to deleted tests or changed variables gracefully.

Phase 8a completed 2026-09-10. [Session-recovery verification](docs/SESSION_RECOVERY_VERIFICATION.md): durable dataset identities and conservative sample/TP revisions protect automatic recovery through rename/trash/restore and reject same-name replacements, ambiguous IDs and changed intervals. Explicit legacy reconnection, skipped-reference review/Retry and draft guards preserve the original stored workspace until accepted. Missing variables retain their slots/settings; delayed metadata preserves selection order. All 406 backend tests /312 subcases, frontend build/lint, isolated real recovery/lifecycle/failure/busy/keyboard/resize/maximize/125%/150% checks and existing trash/Y-axis/rendering regressions pass. Recovery preserves 22 source files; 11 independent fixture files remain unchanged through edit/lifecycle checks. **Next: Phase 8b explicit save/reopen controls and complete Spectrum/XY axis persistence.** The combined checkbox stays open; 8a was the prerequisite required by the name-only identity audit. Component totals remain deferred to Phase 10.

Phase 8b completed 2026-09-10. [Saved-session verification](docs/SAVED_SESSIONS_VERIFICATION.md): visible Sessions controls download named JSON files and preview/recheck source compatibility before reopening through shared recovery/draft guards. Live capture preserves selections/colors, scatter filters/options, per-slot filters/overlays, annotation visibility, layouts/maximize and Time/Spectrum/XY ranges. All 406 backend tests, frontend build/lint, 12 real session downloads, automatic recovery/Y-axis/rendering and native Spectrum/XY export regressions pass, including invalid/read/network/storage failures, Close/stale responses, real source lifecycle/edits, keyboard/1100px/125%/150%. Fixed modal focus return; 22 source files stay unchanged through ordinary save/open and 11 independent files through fixture lifecycle checks. Files contain configuration/source references, not historical samples; native legend toggles remain transient. **Phase 8 is complete. Next: Phase 9 plot context menus.** Component statistics remain Phase 10.

## 9. Add plot context-menu access

Build the menu after plot actions exist so it reuses the same actions and state.

- [x] Evaluate and implement a custom right-click menu for plot actions, including export, original / filtered overlay, reset zoom, and time-marker / interval-note creation.

Phase 9 completed 2026-09-10. [Plot-menu verification](docs/PLOT_CONTEXT_MENU_VERIFICATION.md): shared right-click/three-dot menu for Time/Full/Spectrum/XY actions and overview reset, using existing modal, overlay, range and annotation handlers. Marker/interval drafts use actual clicked/view timing and explicit source selection. All 406 backend tests, frontend build/lint, eight native CSV/PNG metadata downloads, annotation/rendering/Y-axis and native Spectrum/XY export regressions pass. Nine slots, disabled/failure/stale states, keyboard/focus, resize/maximize/reload and actual 125%/150% zoom pass; 21 source files unchanged in the menu suite. Existing visible controls remain. Split/Edit editing canvases and Phase 11 header redesign are outside this milestone. **Next: Phase 10 component statistics, starting with running/missing-RPM/deletion contribution policy.**

  Design feedback: this application targets desktop keyboard and mouse use only. Secondary actions may live exclusively in the right-click menu. Keep important actions such as export and original / filtered display discoverable; a small visible three-dot button can open the same menu without adding many toolbar buttons. Support keyboard access and keep the menu scoped to plots.

## 10. Add component statistics

This depends on component associations and a defined policy for deleted tests.

Phase 10 completed (2026-09-10). [Method and confirmed policy](docs/COMPONENT_STATISTICS_METHOD.md), [verification](docs/COMPONENT_STATISTICS_VERIFICATION.md): Components page with individual runtime minutes, duration-weighted RPM mean/population SD/min/max, operating ranges and per-test coverage. Explicit revision-guarded RPM selection in Edit; finite RPM > 0 counts as running, missing RPM/acquisition gaps are excluded, and trash/deletion removes contributions while restore adds them back. Source cache invalidation and fresh assignments cover reassignment, trim/fill/rename/drop/replacement. Acquisition gap history survives future fill edits; older filled tests with lost history are explicitly excluded. All 421 backend tests /331 subcases, build/lint, native statistics/component browser suites and rendering/Y-axis regressions pass, including keyboard/resize/maximize/125%/150%. Twenty-four source files remain unchanged through ordinary configuration/reads, with restored bytes and real data edits verified separately. The extra navigation tab exposed header clipping at 150%; wrapping now preserves Import CSV/Sessions access. Phase 7's deferred statistics clause is also complete. **Next: Phase 11a shared plot headers/toolbar polish.** No lifetime ledger or Phase 11 redesign was added.

- [x] Add a component statistics page showing accumulated runtime in minutes and RPM statistics / operating ranges across associated tests. Track each individual propeller, electric motor, and ESC separately. Define which samples count as running, how missing RPM data is handled, and whether trashed / permanently deleted tests contribute to lifetime totals before implementing totals.

## 11. Polish the interface (lower priority, after phases 1–10)

Aim for a simple, visually striking desktop engineering workspace with plots as the main focus. Refine the existing dark theme through consistent spacing, typography, alignment, and restrained color. Reuse features built above rather than implementing them again.

- [x] Standardize plot headers and the shared toolbar: show the variable name, unit, compact mean value, maximize control, and a three-dot menu. Keep core actions discoverable while reducing repeated controls.

Phase 11a completed 2026-09-11 with the user's related zoom request. Shared Time/Full/Spectrum/XY headers retain source variable/unit labels and existing scientific summaries (including original TP means), with only maximize and a three-dot action control. Export, filters, original overlay, notes, Y bounds and Spectrum details reuse existing handlers through the menu; filters open on demand even when maximized. Right-button drags no longer zoom. Time/Full/Spectrum Y fits visible finite traces with padding, while manual TP Y remains available and resets to Auto. Native Chromium checks cover all modes, nine slots, left/right/Shift/middle gestures, menu/dialog keyboard focus, resize/maximize/restore, 1100px and actual 125%/150% zoom, eight CSV/PNG metadata exports and unchanged fixture samples. Build/lint, all 421 backend tests/331 subtests and eight numerical helper tests pass. See [compact plot verification](docs/COMPACT_PLOT_CONTROLS_VERIFICATION.md) for evidence and display-data limits. **Next: Phase 11b collapsible variable/filter side panel.**

- [x] Add more line plots in the Split section for different variables.

Completed 2026-09-11 at the user's request, ahead of the independent Phase 11b side-panel redesign. Up to nine stacked variable previews have per-plot pickers/removal, independent Y axes, linked time ranges/cursors and shared TP editing. Browser-local per-test choices survive reload and reconcile unavailable columns. Native Chromium verifies keyboard use, mixed raw/envelope geometry, missing/error/Retry states, rapid context switches, nine plots, resizing and actual 125%/150% zoom; draft/Save/reset/CSV/JSON behavior remains intact and source samples unchanged. Frontend build/lint, 10 helper tests, all 421 backend tests/331 subtests and the existing Split export browser suite pass. See [Split multi-plot verification](docs/SPLIT_MULTI_PLOTS_VERIFICATION.md). Next remains Phase 11b below.

- [x] Allow time as an X or Y variable in XY plots, including saved defaults and exports.

Completed 2026-09-11 at the user's request. XY pickers and saved defaults include each source's actual measured/generated time column; TP pairs retain source seconds. Source eligibility, session restoration, settings, unavailable-axis recovery and CSV/PNG exports reuse existing handlers. All 421 backend tests/331 subtests, frontend build/lint and isolated Chromium checks pass, including keyboard, zoom/pan, maximize/restore, 1100px and actual 125%/150% zoom. Eight export packages verified; 21 fixture files unchanged. See [XY time verification](docs/XY_TIME_VERIFICATION.md). Split multi-plot verification is also complete and its suite passed again against the XY changes.

- [x] Add multi-variable auto-split with clear rules and a proposal preview before applying it to the draft.

Completed 2026-09-11 at the user's request, ahead of the independent side-panel redesign. Auto-split now supports up to nine distinct variables: any exact selected value change starts a new run, with explicit missing/zero exclusions and minimum duration (sample count / sample rate). A compact on-demand rule editor offers searchable recommendations, browser-local settings, full-resolution proposal counts/intervals and explicit draft-only Apply before Save. Empty/failure/stale previews cannot overwrite the draft; native row indices survive unchanged boundaries through Save/CSV. All 436 backend tests/354 subtests, frontend build/lint, 13 helper tests, the new controlled-race/keyboard/resize/125%/150% Chromium suite and existing Split export/multi-plot suites pass. See [multi-variable auto-split verification](docs/MULTI_VARIABLE_AUTOSPLIT_VERIFICATION.md). This method detects exact value changes; numeric threshold/tolerance conditions remain a separate follow-up.

- [x] Add a waterfall FFT time-frequency magnitude view in Spectrum, with separate comparable source maps.

User-requested milestone (2026-09-11), ahead of the independent Phase 11b side-panel redesign. Complete and verified. The user clarified that the PowerPoint is only a style reference: Waterfall opens with one active-test/variable map; multiple maps require an explicit Selected TPs choice. Method and user controls: [Waterfall FFT](docs/WATERFALL_FFT.md). Uses full-resolution windowed Hann FFT, elapsed-time/frequency heatmaps, shared per-variable source color scales, window/overlap settings, two-axis inspection, persisted sessions, and single/multi-plot CSV/PNG with metadata. All 447 backend tests/360 subtests, frontend build/lint, 13 helper tests, native waterfall checks and the current compact-controls regression suite pass. Fourteen waterfall and 21 regression source files remain unchanged. See [verification](docs/WATERFALL_VERIFICATION.md). Existing FFT/Welch behavior is preserved.

- [ ] Give plots more space by organizing variable selection and filters in a collapsible side panel. Preserve usability across desktop window sizes and maximized views.
- [ ] Show test notes, component associations, and data-quality details in an on-demand test details panel instead of displaying all metadata permanently.
- [ ] Unify colors: use one interface accent color and distinguishable plot-series colors. Keep each test point's color consistent across plots; render original data with a thinner, subdued trace and filtered data more prominently while keeping both readable.
- [ ] Refine chart presentation with subtle grid lines, readable axes, explicit units, consistent spacing, and a shared hover-box layout across plot types.
- [ ] Refine Uploads into a clean table with test name and short description together, and separate columns for duration, components, and status. Open details on row selection and group secondary actions in the three-dot / right-click menu.
- [ ] Add short, restrained transitions for panels and plot maximization. Keep zooming and data inspection immediate, respect reduced-motion preferences, and avoid animation that delays interaction.

Proposed follow-ups for chart presentation (keep within Phase 11): handle exceptionally long axis captions without clipping, and provide an accessible details view for metadata so long that a wrapped hover card exceeds the desktop viewport height.
