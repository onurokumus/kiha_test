# Implementation handoff

Updated: 2026-09-11. Branch `feature/resumable-multipart-upload`; baseline
`23a866a` (Waterfall FFT is committed). Initial working tree clean, apart from
an inaccessible ignored `.pytest_cache` warning. This checkpoint contains the
verified fix for the user's requested commit/push to `origin`
(github.com/onurokumus/kiha_test). Production deployment remains outstanding.

## Current milestone / status

**Linux source-catalog failure and initial ready count: implemented and verified
locally.** Explicit user bug report takes priority over Phase 11b. Screenshot:
`/ptt/api/analysis-sources` 500, zero ready tests until opening Uploads (then 19),
and Trash unavailable. See `docs/LINUX_CATALOG_FIX.md` for diagnosis, acceptance,
commands and evidence. Actual Linux confirmation is still required.

## Implemented / decisions

- Deployment guide uses Linux Python 3.11, but ten backend checks required
  `Path.is_junction` from 3.12. Uncaught AttributeError reproduces the reported
  populated-catalog/Trash failure while `/tests` succeeds.
- New `backend/app/paths.py` checks lstat symlinks and Windows junction reparse
  tags across supported runtimes. Analysis sources, component statistics,
  deletion and Trash use it; containment and permission/I/O failures retained.
- App's initial Promise.all discarded successful `/tests` on catalog failure.
  It now publishes the list independently and awaits both outcomes for safe
  session recovery. Header distinguishes loading/unavailable/ready counts.
  Explicit source-verification error/Retry retains the workspace; metadata
  hydration/autosave wait for identity verification. No name-only fallback.
- `deployment_guide.md` now probes source/Trash catalogs as well as health and
  documents this exact 3.11 traceback/remediation. CLAUDE.md clarifies Windows
  3.13 versus Linux 3.11 and the shared portable check.

## Verification

- Four new populated API regressions reproduced 500s before the fix. Final full
  backend: **453 passed, 370 subtests** (Windows Python 3.13.14, 45.46s).
  Six new tests cover missing 3.12 API, guarded reads, ready/busy sources,
  component use, legacy Trash/lifecycle/sample preservation and link/I/O guards.
  Command: `backend\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider`.
  Cache disabled only because existing ignored `.pytest_cache` is inaccessible.
  Two existing dependency deprecations.
- Frontend `npm.cmd run build` and `npm.cmd run lint` pass (bundle notice only).
- `python scripts/verify_session_recovery.py --frontend-port 3144 --backend-port 8144`
  passes using global Python/Playwright and owned backend Python 3.13. Held/500
  catalog publishes count before Uploads, avoids unverified metadata reads,
  preserves sessions, and Retry works; failed list shows unavailable. Existing
  lifecycle/identity/legacy/busy/order/filter/keyboard/maximize/1100px/125%/150%
  checks pass. Zero page errors; only injected 500/503 console errors.
  Eleven beta fixture files unchanged, screenshot inspected. Owned servers and
  fixtures cleaned; ignored evidence in `data/verification/session-recovery/`.
- Independent backend review and final whitespace review pass. No production
  dataset modifications. Python 3.11 method absence is simulated on 3.13;
  production Python version/traceback was requested but not supplied.

## Next steps

The commit/push follow-up reuses the passing verification above; implementation
has not changed since those checks. Deploy the fix through the normal workflow:
update backend + frontend, restart `ptt-backend`, hard-refresh, verify direct and
proxied analysis-source/Trash endpoints and initial ready count on `heliweb1`.
The code-level cause is reproduced but is not yet confirmed against server logs.
No test re-upload or Python upgrade is needed for this compatibility fix.

Next feature backlog stays Phase 11b collapsible variable/filter side panel.
Prior waterfall/multi-variable auto-split/Split multi-plots/XY time behavior is
complete; preserve their existing verification documents.
