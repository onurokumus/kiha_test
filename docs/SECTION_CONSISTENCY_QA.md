# Companion-section consistency

Updated 2026-09-28. Working application: `work_v2/kiha_test`, branch
`codex/ptt-feature-light`. This follows the current Analyze controls, including
the recent compact toolbar and stable selection-panel work.

## Changes and preservation

| Section | Presentation changes | Preserved behavior |
| --- | --- | --- |
| Split | Compact sticky test/action toolbar, stable draft status, Files menu, smaller plot headers, shared gesture help and tighter test-point rows. | Nine linked plots, individual variables/reset/remove, boundary dragging, keyboard point fields, manual definitions, auto-split Preview/Apply/Save, discard guards, exact CSV and JSON import/export. |
| Edit | Sticky Save/Reset controls, side-by-side description/findings, compact source summary and visible draft indicators on closed sections. | Metadata revisions, component sets/telemetry, custom metadata, missing-value treatment, trim, formula recipes/preview/apply/rebuild, column rename/remove, confirmations and busy/draft guards. |
| Uploads | Smaller import area, stable time-basis fields, optional metadata disclosure, compact history/status rows, Clear filters, and Trash after history. | Actual resumable CSV transfer, uploader and description, component assignments, pause/reselect/resume, server-only recovery, cancel/retry, search/status filters, quality details, all downloads and test lifecycle actions. |
| Components | Aligned search/type/reset controls, quieter policy disclosure, compact numerical tables, sticky table headers and tighter source rows. | Positive-RPM counting, weighted statistics, optional temperature/power, source coverage/warnings, component selection/ranges, filtering, refresh/error retry and direct test editing. |
| Settings | Sticky Save/Revert, More menu, paired primary sections and labeled XY cells. | Draft Save/Revert/reset, JSON import/export, shared-default confirmation, scatter/datasheet settings, nine plot defaults and XY pairs, spectrum/clustering/upload-rate preferences. |

Components and Uploads component-edit links explicitly open and focus the
component-set disclosure. Ordinary Edit navigation retains the compact overview.
The same existing edit and navigation handlers perform the actions.

Scientific processing, source formats, numerical values, API contracts, upload
protocols and retention policy are unchanged. Sample-rate rounding applies only
to the compact summary; full values remain available in its tooltip. All current
Analyze work and other pre-existing uncommitted changes are retained.

## Verification

Build/lint and 44 frontend helper tests pass. Browser checks use disposable
profiles; mutating workflows use isolated temporary datasets and the project's
Python 3.13 backend, or explicitly intercepted browser responses. The existing
user library is only read by the production-preview checks.

| Suite | Evidence |
| --- | --- |
| Split multi-plot, auto-split and focused controls | 19 groups: linked gestures, nine plots, boundary edits, native CSV, JSON import/export, draft Save/Discard, stale/error recovery, keyboard, desktop resize and actual 125%/150% zoom. Fixture source hashes preserved. |
| Upload data quality and component lifecycle | Real uploads, malformed-time/quality reporting, pause/resume after reload, server-only resume, multi-file component assignments, validation/failure/retry, metadata revisions and lifecycle. Component suite preserved 36 fixture source files. |
| Focused Uploads | Metadata disclosure draft retention, incomplete-component/rate validation, stable time fields, saved description/component/sample rate, filter reset, real trash/restore and desktop/zoom checks. |
| Component statistics | Four native groups: explicit RPM, weighted runtime/mean/SD/ranges, metadata conflict/retry, assignment recalculation, statistics failure/unmount, trash/restore/delete, source-column repair and desktop keyboard/zoom. Initial 24 fixture source files unchanged before explicit fixture lifecycle/rebuild operations. |
| Edit and Settings | Eleven groups: JSON draft round trips, local Save/reload, absent-signal preferences, invalid import/rate, shared-default cancel/failure/retry, all nine XY pairs, notes/custom metadata/equation/cleanup/column drafts, guards/reset, sticky actions and short-window menu access/dismissal. Server writes intercepted. |
| Analyze regression | All 16 established control groups pass: source/view/method changes, nested options, selection/export, stable panel geometry, resize and actual browser zoom. |
| Production integration | 52 checks in `verify_section_consistency.py`: five pages, narrow/short desktop windows, actual 125%/150% zoom, every menu action and primary-action reachability, keyboard dismissal, direct component editing and filter reset. No JavaScript errors or API writes. |

Short-window review found the new Split and Settings popovers could clip their
last actions at 960 × 400 and 150% zoom. Their final viewport-fitting behavior and
outside dismissal are included in the integration checks. Split's toolbar scrolls
normally at viewport heights of 400px or less so Auto-split remains reachable;
isolated Files/JSON/keyboard checks additionally pass at 629 × 210 CSS pixels.
Final production bundle: `index-Cd2hOOzV.js`.

## Reproduction

From `frontend`: `npm run build`, `npm run lint`, and
`node --test tests/*.test.mjs`.

From the repository root, with the `/ptt/` preview running:

```powershell
python -X utf8 scripts/verify_section_consistency.py
python -X utf8 scripts/verify_analysis_controls.py
python -X utf8 scripts/verify_edit_settings_polish.py
```

Isolated native suites own their temporary servers and datasets. Choose free
frontend/backend ports when running them concurrently:

```powershell
python -X utf8 scripts/verify_split_multi_plots.py --frontend-port 3311 --backend-port 8311
python -X utf8 scripts/verify_multi_variable_autosplit.py --frontend-port 3312 --backend-port 8312
python -X utf8 scripts/verify_data_quality.py --frontend-port 3313 --backend-port 8313
python -X utf8 scripts/verify_components.py --frontend-port 3314 --backend-port 8314
python -X utf8 scripts/verify_component_statistics.py --frontend-port 3315 --backend-port 8315
python -X utf8 scripts/verify_upload_polish.py --frontend-port 3316 --backend-port 8316
```

The existing Split/Components suite locator updates match the passing adapted
runs; assertions are retained. The component-sets staging helper received the
same disclosure-opening update and was syntax checked; its full independent
suite was not rerun for this presentation change.

Screenshots and reports are in `%TEMP%/ptt-section-consistency`,
`%TEMP%/ptt-edit-settings-polish`, `%TEMP%/ptt-upload-polish`,
`%TEMP%/ptt-analysis-controls`, and ignored `data/verification/` suite folders.
The existing Vite bundle-size advisory remains. No production deployment or
offline package rebuild is part of this work.
