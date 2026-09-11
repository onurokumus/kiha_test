# Linux catalog and startup-count regression

Completed locally 2026-09-11 on `feature/resumable-multipart-upload`, based on
`23a866a`. The explicit production bug report takes priority over Phase 11b.

## Diagnosis and acceptance

The user reports `/ptt/api/analysis-sources` returning 500, zero ready tests on
initial Analyze, and 19 only after opening Uploads. The screenshot also shows
an unavailable Trash list. The deployment guide targets Linux Python 3.11.

`analysis_sources`, `trash`, test deletion and component statistics called
`Path.is_junction()` unconditionally. That method was
[added in Python 3.12](https://docs.python.org/3.12/library/pathlib.html#pathlib.Path.is_junction).
On 3.11 the uncaught `AttributeError` makes populated catalogs fail while
`/api/tests` still succeeds. Frontend bootstrap used `Promise.all` and discarded
that successful list when source verification failed. Uploads fetched the list
independently, causing the reported count change.

Acceptance: populated endpoints work without the 3.12 method; symlink/junction
protections remain; the ready count loads independently; unavailable data is
not displayed as zero; source failure and retry preserve saved-session identity.

This code-level diagnosis is reproduced locally. No production traceback or
direct Linux server access was supplied, so actual server confirmation and
deployment remain outstanding.

## Changes

- `backend/app/paths.py` uses `lstat`, symlink mode and the Windows mount-point
  reparse tag. It handles missing paths without hiding permissions/I/O failures.
  The [Windows tag is available since Python 3.8](https://docs.python.org/3.11/library/os.html#os.stat_result.st_reparse_tag).
  All affected API paths share the helper and retain resolved containment checks.
- App bootstrap publishes `/tests` immediately and separately waits for the
  identity catalog. Header states distinguish loading, unavailable and ready
  counts. Source-check errors identify the failure and expose Retry.
- Metadata/session hydration and autosave remain paused until identities verify;
  failure never reconnects saved tests by name or overwrites saved sessions.
- Deployment checks now exercise populated source/trash catalogs in addition
  to health. The deployment guide includes diagnostic commands and remediation.

No dependency updates, source-data conversion, identity replacement or production
data edits are part of this fix.

## Verification

Commands from the repository root, except build/lint inside `frontend`:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider
npm.cmd run build
npm.cmd run lint
python scripts/verify_session_recovery.py --frontend-port 3144 --backend-port 8144
git diff --check
```

- Before the fix, all four new populated API regressions returned 500 with the
  missing-method condition. The successful `/tests` read confirmed the split
  failure before `/analysis-sources` failed.
- Final backend: **453 passed, 370 subtests** on Windows Python 3.13.14; two
  existing dependency deprecation warnings. Six new tests cover absent 3.12
  APIs, ready/busy catalogs, guarded metadata/TP reads, actual component use,
  legacy Trash migration, delete/restore/permanent-delete, sample-byte
  preservation, POSIX links, Windows junction tags, missing paths and I/O errors.
  Python 3.11 method absence is simulated on 3.13; no native 3.11/Linux run.
- Frontend build and lint pass. Existing bundle-size notice only.
- Extended real Chromium session-recovery suite passes: held/500 source catalog
  shows the correct initial count before Uploads, no unverified metadata loads,
  Uploads/Retry works, list outage shows unavailable, and failed checks preserve
  storage. Existing rename/replacement/trash/duplicate-ID/legacy/busy-source,
  filters/order, keyboard, maximize, 1100px and 125%/150% zoom regressions pass.
  Zero page errors; console errors are injected 500/503 responses only.
- Isolated fixture directories and owned servers are cleaned. Eleven unchanged
  beta fixture files are byte-verified; screenshot visually inspected. Evidence
  remains ignored under `data/verification/session-recovery/`.
- Independent backend review found no issues; final whitespace review passes.

## Deployment follow-through

The user requested committing and pushing this verified fix to
`origin/feature/resumable-multipart-upload`. Deploy the corrected backend and rebuilt frontend using
[the deployment guide](../deployment_guide.md#5-diagnosing-analysis-source-500s-and-an-incorrect-ready-count),
restart `ptt-backend`, and hard-refresh. Check both direct and proxied
`analysis-sources`, populated Trash, and the ready count before opening Uploads.
If the server traceback differs, investigate that traceback separately.

After production confirmation, the next feature backlog remains Phase 11b.
