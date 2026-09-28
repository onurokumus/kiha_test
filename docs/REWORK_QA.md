# PTT feature-branch rework QA

Verified 2026-09-27. Source baseline: `feature/resumable-multipart-upload` at `1f0793380d0bad43f88e2c4d384ad05f7a0421e2`.

## Result

- **497 backend tests + 477 subtests passed** in 52.12 seconds. Only two existing Starlette/httpx deprecation warnings.
- **32 frontend helper tests passed**, with `npm ci`, production build and lint passing.
- **10 isolated browser suites passed: 63 workflow check groups**, covering actual imports, edits, calculations, downloads and recovery paths.
- **8 final production checks passed** through `http://127.0.0.1:8087/ptt/`, with no browser errors, failed responses or external requests.
- No blocking regression found. No application-source edits were made by QA.

Workflow groups contain multiple assertions; they are not an additional count of unit tests.

## Browser coverage

| Suite | Groups | What was exercised |
|---|---:|---|
| Component sets | 6 | Actual multipart Import CSV staging/upload; **Pause → reload → file reselect → Resume**; server-only recovery after clearing browser resume records; two hardware sets, telemetry conversions, runtime/statistics, stale-revision conflicts, duplicate prevention, failure/retry, trash/restore and malformed-settings repair. |
| Full-test variables | 9 | Six variables in a plot, search/keyboard, colors/removal, native envelope and Line data, filtered/original pairs, absent variables, real CSV/PNG/metadata and multi-plot ZIP, session round trips, zoom/maximize and filter retry. |
| Filter overlay | 10 | Exact TP time/row alignment for 600/20,000 samples, Despike, independent original overlay, hidden selections, independent Y/linked X zoom, nine slots, partial/all failure, retry/stale-response guards and Full Line/envelope alignment. |
| Analysis metadata | 3 | **24 real export packages** across TP, Full, Welch and XY; exact row counts, hashes, transitive equations, PNG loaded provenance versus CSV executed provenance, nine-slot bundles, file-only toggle, failure/retry/cancel. |
| Saved sessions | 5 | Real Save/Open JSON, colors/hidden/filter/overlay/notes/scatter settings, independent mode/slot axes, malformed input rejection, rename/trash/restore/changed-TP recovery, draft guards, network retry and close. |
| Waterfall detail | 8 | Distinct 84/85 Hz tones, 0.1/0.25/0.5 Hz bins, refinement/bands, native CSV and PNG provenance, pan/zoom/reset/maximize, session/legacy migration, partial failures, short and empty intervals. |
| Waterfall color | 9 | Shared automatic/manual range, invalid/narrow/log/linear limits, unchanged numerical CSV, truthful exported PNG, no FFT refetch for color-only edits, independent slots, zero sources, Save/Open/reload. |
| Multivariable auto-split | 9 | Exact constant-tuple/zero/missing/minimum rules, read-only preview, draft-only Apply, persisted Save, half-open native CSV boundaries, stale/error recovery, continuously changing samples and 1 Hz defaults. |
| Edit/settings | 3 | Read-only formula preview, saved recipe, materialization, applied-equation editing and dependent recalculation; notes/custom metadata; draft navigation, Revert, actual JSON export/import, Save/reload, reset, shared defaults and malformed-import guard. |
| Visual/capacity | 1 | All **20 unique non-blue selected colors**, navy unselected scatter points, all six pages and 1024/1280/1600/1920 px layouts without horizontal overflow. |

An independent numerical assertion verified all 40 native fixture rows after editing a saved formula: `double_signal = 3*signal` and dependent `shifted = 3*signal+1`. Established suites also tested 1100 px windows and actual 125%/150% browser zoom, keyboard operation, dialogs and maximize/restore. Resumable-upload checks disabled `crypto.randomUUID` to exercise plain-HTTP compatibility.

## Data preservation and production integration

Compatibility checks used copies of only the earlier-main `ptt_demo_run_a` and `ptt_demo_run_b`. Each retained 8,192 rows and four TPs; metadata, windows, TP traces/statistics, source catalog and native CSV exports worked. All **18 original demo files** remained byte-identical.

During the final checkout migration, all **25 existing dataset files** were identical. On the new backend's first reads, **23 original non-cache files stayed unchanged**; the two `tp_stats.json` caches were rebuilt to feature schema v2, and three `source_identity.json` files were added as designed. No original CSV, Parquet, metadata or TP definition was changed by the migration verification.

The final production build loaded local font/logo, restored three selected points and four plots, rendered all six pages, resized to 1100 px and completed K11C0 → PTT → K11C0 navigation. The production report records zero browser errors, external requests or failed responses.

## Review evidence

- [Production Analyze preview](rework-qa/production-analyze.png)
- [Twenty selected points at 1280 px](rework-qa/twenty-selections.png)
- [Actual exported full-test PNG](rework-qa/exported-full-test.png)
- [Populated component statistics at 1100 px](rework-qa/component-statistics.png)
- [Browser regression results](rework-qa/regression-results.json)
- [Final production results](rework-qa/production-results.json)

Detailed logs, scripts, downloaded files and secondary-page screenshots remain under `C:/Users/onuro/AppData/Local/Temp/codex-ptt-feature-qa`. `FEATURE_REWORK_QA.md` there has the full coverage narrative. `run_suite.py` and `adapt_harnesses.py` reproduce the isolated suites; test-only copies update old source-identity seeds, Plot actions selectors, disclosure clicks and the `Save test points` label. Early HMR-overlapped attempts were discarded; all final results above passed on stable source.

## Scope and cleanup

All destructive browser operations used unique temporary datasets/profiles; original data remained read-only. Python 3.13 used the already installed backend dependencies. No internet dependency was added. QA-owned ports 3356/3357/8356/8357/8358 are stopped; regular preview 8087/backend 8000 remain running.

Verification was on Windows development services. Offline Linux deployment, sustained 20/100-user load and multi-gigabyte endurance have not been tested. No offline ZIP was generated.
