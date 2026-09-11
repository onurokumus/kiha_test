# Phase 3 data-quality verification

Completed 2026-09-09 on `feature/resumable-multipart-upload`. Phase 3 is complete; no backlog reordering. Existing user changes and Phases 1-2 remain uncommitted and were preserved.

## Behavior and acceptance

Uploads now has a compact quality disclosure in each test row. It distinguishes detected warnings, incomplete checks, processing, failed analysis readiness, and a completed check with no findings. Warning counts group findings by type, not by affected sample count. Analyze and original-CSV actions retain their existing handlers and availability.

The disclosure opens three sections:

- **Current working data:** missing/null/NaN and infinite-cell counts for each affected signal, current inserted-gap row intervals, nominal time bounds, and the saved fill policy. Columns with fewer than two finite samples identify the existing spectrum restriction. Counts refresh after trims, fills, column changes and materialized equations.
- **Source timestamps at upload:** original column and row count, adjacent equal/backward timestamp counts, invalid/missing timestamps, source gap boundaries, coarse-clock and timing warnings, and skipped non-numeric columns. Source findings remain unchanged after working-data edits.
- **Analysis availability:** ready tests stay available; spectra cannot cross existing known gaps or use a column with fewer than two finite samples. Generated-time results require the actual upload rate. Failed uploads/rebuilds show their existing error and remain unavailable through the existing UI controls.

Details support keyboard activation, visible focus, Escape/Close with focus return, scrollable affected-column tables, desktop resizing and actual browser zoom. Loading errors have a retry action. Closing/switching a disclosure aborts its metadata request; the catalog's opaque metadata revision refreshes an open disclosure even when consecutive edits share the same second-resolution `edited_at` value.

## Data contract and method continuity

| Field / path | Meaning |
| --- | --- |
| `meta.source_time_quality.version = 1` | Immutable import diagnostic snapshot, captured before elapsed-time normalization or generated-time fallback |
| `checked`, `column`, `n_rows`, `axis_reason` | Distinguishes measured time, invalid-source fallback, deliberately generated time and absence of a detected source time column |
| `duplicate_steps`, `backward_steps` | Counts adjacent **finite** equal/decreasing timestamp pairs across the entire source column; examples identify the later row and both source times |
| `invalid_timestamps` | Missing, unparseable or nonfinite source timestamps, with example row numbers; comparisons do not bridge invalid rows |
| `gap_count`, `gap_examples` | Uses the existing `_measured_timing` result; null means the clock was unusable and gap detection did not run successfully |
| `meta.nan_counts` | Existing current null/NaN counts, including inserted gap rows; semantics unchanged |
| `meta.inf_counts` | Separate current positive/negative infinity counts, collected during the existing pyramid pass and rebuilt after edits |
| `GET /api/tests`: `data_quality` | Small metadata-only warning-category list and partial-check flag; lifecycle readiness remains authoritative |
| `GET /api/tests`: `quality_revision` | Opaque metadata file revision string used for refreshing expanded details; not persisted analysis provenance |
| `GET /api/tests/{name}` | Existing full-metadata endpoint supplies details; frontend `fetchMeta` now accepts an optional AbortSignal |

Source data rows are one-based parsed records, excluding the header, rather than physical CSV line numbers. Source seconds retain the original clock/epoch origin. Counts are exact; at most eight examples per source issue are persisted. Working gap indices retain the existing zero-based half-open `[start, end)` convention. Displayed working time bounds are explicitly nominal (`t_start + index / fs_hz`), not claimed as re-read measured timestamps.

No dataset migration, raw-CSV rescan, extra bulk-data read on polling, new dependency, new endpoint, or change to analysis normalization/filtering was introduced. Ingest now gives a clear error for a CSV with no numeric signal columns; that input previously failed during pyramid construction. Existing generated-time fallback, gap expansion, gap clearing on fill, sample-rate estimation and numerical methods remain intact.

The entire `dsp.py` byte fingerprint still matches the Phase 2 audit. AST comparisons against baseline `873c80c` passed for `_measured_timing`, `_time_seconds_expr`, `_insert_missing_rows`, `window_bounds`, `_testpoint_bounds`, `bucket_minmax`, and `_updated_gap_ranges`. All Phase 2 numerical characterization tests passed again. Historical ingest/store file fingerprints in [FFT_VERIFICATION.md](FFT_VERIFICATION.md) describe the earlier audit snapshot; those files now also contain these diagnostics/catalog additions.

## Verification

From the repository root:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_data_quality.py -q
backend/.venv/Scripts/python.exe -m pytest backend/tests
python -X utf8 -u scripts/verify_data_quality.py
git diff --check
```

In `frontend`:

```powershell
npm.cmd run build
npm.cmd run lint
```

| Check | Result |
| --- | --- |
| New backend/API quality suite | **14 passed, 4 subtests passed** |
| Full Python 3.13.14 backend suite | **231 passed** in 17.19 s |
| TypeScript/Vite production build | **PASS** |
| ESLint | **PASS** |
| Live isolated browser verification | **PASS** |
| Source-method continuity and final diff check | **PASS** |

Backend cases cover clean input; analyzable coarse-clock duplicates; fallback with backward, invalid and infinite timestamps; explicit generated/absent time; exact counts across the 65,536-row batch boundary; bounded examples; source-gap preservation across trim/fill/rename/drop; formulas; revisions for edits sharing a timestamp; the one-finite-sample spectrum rejection; legacy omissions without bulk reads/writes; failed/oversized-gap imports; and multipart upload/delete/restore provenance.

`scripts/verify_data_quality.py --help` documents its two optional ports (defaults 3100 and 8100). It uses the installed global Python 3.14 Playwright 1.59.0 only for browser/stdlib work. Its backend child always uses `backend/.venv/Scripts/python.exe` and `backend/run.py`, preserving the project's Windows native-read constraints. No new dependency was installed.

The harness owns temporary datasets, settings, trash and a Chromium profile. It refuses occupied ports, launches only its own server processes with isolated environment settings, and terminates those processes before removing its fixtures. It uses live API uploads plus one real UI upload. Legacy omissions, a processing state and a repeated edit display timestamp are deliberately applied only to fixture metadata; one HTTP 503 is injected to verify retry. The browser uses a temporary extension's `chrome.tabs.setZoom`, asserting device-pixel ratio, rather than CSS zoom or pinch emulation.

Browser coverage includes:

- Real file selection, upload options, multipart transfer, ingest completion and quality polling.
- Keyboard disclosure/Close/Escape and focus return; affected-column table focus.
- Long sensor names, 1100/1440px desktop windows, real 100%/125%/150% zoom and containment of the nested count table. Screenshots at 100% and 150% were visually inspected.
- Current/source gap indices and seconds; live fill/drop refresh; source findings surviving edits; refresh when successive edits share `edited_at`.
- Clean, generated, pending-to-ready, failed, sparse-signal and legacy states; large epoch timestamps retain fractional distinctions; metadata failure/retry; no unexpected page or console errors.
- All owned processes and temporary profiles/datasets cleaned up. Existing port-3000 service and user datasets were untouched. Screenshots/logs remain under ignored `data/verification/data-quality/`.

## Limits and follow-ups

- Legacy imports retain known gap/missing-value facts, but unavailable original clock diagnostics and infinity counts are labelled incomplete. Rebuilding refreshes current signal counts without manufacturing source provenance. Deliberately generated time does not audit ignored source timestamp columns or infer acquisition dropouts.
- Duplicate checks describe adjacent equal timestamps, not a global unique-timestamp inventory. Invalid neighbors are not bridged. Source examples and displayed working gap intervals are limited to the first eight; exact totals remain visible. Individual signal-missing intervals are not stored by the existing per-column count scan.
- This is diagnostic reporting, not a change to data repair, CSV inference, calibration, or FFT behavior. For example, a late infinity in an integer-inferred CSV column still produces the existing parse error. An early test fixture exposed that behavior; the count-scan fixture was corrected to use explicit floating-point values. Generated-time rate assumptions and coarse-clock precision still need engineering judgment.
- Chromium was verified; Firefox/Safari and maximum-size imports were not exercised. A batch-boundary fixture checks count continuity without allocating a full-hour dataset. No plot interactions changed in this milestone; Phase 1 maximize/restore/pan evidence remains in [RENDERING_VERIFICATION.md](RENDERING_VERIFICATION.md).
- The existing Starlette/httpx and AnyIO deprecation warnings and Vite bundle-size warning remain non-failing. React StrictMode can issue and abort an initial detail fetch: the injected failure stays active until the retry check, rather than consuming it on a single request.
- Phase 2 spectral-export prerequisites remain deferred to Phase 6. Next: Phase 4's test-point time-plot Y-axis zoom/reset, followed by compact mean/statistics presentation.
