# Direct analysis controls verification

2026-09-27. Follow-up to the feature-branch migration in [REWORK_QA.md](REWORK_QA.md).

## Changes

- Scatter hover belongs to an actual point hit and dismisses on leaving, clicking,
  wheel zoom, panning, Escape, resize, scroll or window blur. Removed the extra
  scatter heading; retained values, cluster selection and datasheet inspection.
- Plot titles directly open searchable signal selectors, including before selecting
  points. Keyboard focus returns to the replacement title when a chart remounts.
  TP plots retain one variable; existing Full-test comparisons retain up to six.
- Source and View are separate. FFT, PSD and Waterfall are visible choices;
  secondary controls are in Options. Switching views preserves the chosen source.
- All selected chips remain visible, with hide/remove/CSV/Clear. A shared test name
  appears once; mixed-test chips identify their own tests. Capacity remains 20.
- Shared numeric fields retain raw drafts and full-precision values, select all on
  entry, show units and small inline warnings, and use pale-red invalid styling.
  A second click in the focused field permits normal caret editing.
- Invalid numeric/filter drafts cannot become default or zero-valued requests.
  Known sample rates guard Nyquist across visible eligible sources; no cutoff is
  clamped. Waterfall invalid limits cannot be applied. Existing explicit saves,
  TP blur/Enter commits, Escape rollback and source files remain protected.
- Essential source/statistic scope remains visible; secondary explanations are
  under Details. Display formatting does not change analysis values.

No backend algorithm, source dataset, dependency, session schema or export format
was changed. No authentication, runtime internet dependency or deployment package
was added.

## Checks

- Frontend `npm run build`, `npm run lint`: pass.
- `node --test tests/*.test.mjs`: 41 pass (numeric parsing, filter sample-rate
  guards, existing session/plot/precision helpers).
- Python 3.13 `pytest backend/tests -q`: 497 tests and 477 subtests pass;
  two inherited dependency deprecations.
- Direct-analysis browser suite: 9 groups pass. Includes 20 chips, mixed tests,
  source/view/method combinations, keyboard title focus, saved-session file
  Save/Open/reload, actual 16,000-row CSV and PNG packages, 1100/1440 widths and
  actual browser zoom at 125%/150%. Eighteen fixture files unchanged.
- Numeric browser suite: 8 groups pass. Covers invalid/partial/scientific drafts,
  selection/caret behavior, request guards including Nyquist, precise axis bounds,
  time notes, TP boundary commits/draft JSON, autosplit, trim, settings/upload rates
  and Waterfall color limits. Eight fixture source files unchanged.
- Scatter browser suite: 9 groups pass. Leave/blank/Escape/wheel/click/resize,
  blur-event dismissal, real pan and real cluster-menu behavior, with hover
  returning on re-entry. All fixture files unchanged outside an explicit temporary
  duplicate TP used to exercise clustering. Blur was dispatched in headless
  Chromium after a second tab opened; native desktop window focus was not claimed.
- Final built-route smoke: all six pages, local logo/font, visible controls,
  wheel-hover dismissal, 1100px resize and K11C0 roundtrip pass. No page/HTTP errors
  or external requests; 26 non-cache dataset files unchanged. Final destination
  build/lint, all 41 helpers and Git whitespace check also pass.
- Browser suites report no page errors. Backend/upload/edit tests use isolated
  temporary data and disposable profiles, never the working datasets.

## Evidence and reproduction

Evidence is under `%TEMP%/codex-ptt-interaction-qa`:
`final/direct_analysis`, `final/scatter_hover`, `numeric/numeric_fields`, and
`production` for the final built-route check.

The local `run_suite.py` runs the corresponding modules in `adapted/` against
temporary Python 3.13 backends and Vite servers. Example commands:

```powershell
python "$env:TEMP/codex-ptt-interaction-qa/run_suite.py" direct_analysis --tag final
python "$env:TEMP/codex-ptt-interaction-qa/run_suite.py" scatter_hover --frontend-port 3362 --backend-port 8362 --tag final
python "$env:TEMP/codex-ptt-interaction-qa/run_suite.py" numeric_fields --frontend-port 3363 --backend-port 8363 --tag numeric
```

Final installation uses a SHA-256 baseline to reject intervening source edits,
backs up overwritten files, copies changed files only and verifies dataset hashes.
Record: `D:/okumus/work/.ptt-rework-stage/interaction-install-report.json`.
The target remains `D:/okumus/work_v2/kiha_test` on `codex/ptt-feature-light`.
Offline Linux deployment and server load testing remain separate work.
