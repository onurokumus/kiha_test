# Scatter filter verification

Completed 2026-09-28 on `feature/resumable-multipart-upload`.

## Scope and diagnosis

User-requested debugging of PTT scatter filters takes priority over independent
Phase 11b. Mouse clicks on the portaled parameter column menu reached the drawer's
outside-pointer handler. DOM containment excluded the portal, so the drawer
unmounted the option before its click could select it. React pointer capture now
recognizes events belonging to the drawer, including its portal, without stopping
global propagation or swallowing the first outside click. Nested Escape respects
handled events and preserves focus restoration.

The audit also fixed:

- Searched test-parent checkbox state now uses the same visible child set as its action.
- Add expands Parameters; Clear is available for unbounded draft rows too.
- Valid numeric bounds commit immediately and survive Escape, outside click,
  Close, trigger toggle and section collapse. Intermediate exponent input remains
  editable; unfinished invalid numbers retain the existing clear-on-blur behavior.
- Reversed intervals display an accessible error and consistently exclude points
  in every mode, including when statistics are still loading.
- Retained unavailable parameter columns remain named in the selector and can be replaced.
- Duplicate summary chips have distinct React keys.

The former Any sample mode is labeled **Range overlap**. It has always tested the
TP minimum-to-maximum interval, not exact sample membership. Saved sessions retain
`mode: 'any'` and valid-range calculation behavior is unchanged. The selector tooltip,
type comment and MVP document now describe that behavior accurately.

Existing provisional inclusion during unloaded statistics is retained. Loading and
partial-error messages explicitly mention parameter filters; the shared Retry
handler is unchanged. Backend aggregate precision and computation are unchanged.

## Verification

Commands from repository root unless noted:

- `npm.cmd run build` in frontend: pass; existing bundle-size notice only.
- `npm.cmd run lint` in frontend: pass.
- `node --test frontend/tests/*.test.mjs`: **40 pass**, including eight new
  real-hook tests with known aggregates across two tests, differing schemas,
  null/missing statistics, inclusive/equal/open/reversed bounds, combined TP and
  label criteria, multiple parameter rows and deduplicated requested columns.
- `backend\.venv\Scripts\python.exe -m pytest backend\tests -p no:cacheprovider -q`:
  **497 tests / 477 subtests pass** in 49.27 seconds, with two existing dependency
  deprecations; native Python 3.13 environment.
- `python scripts/verify_scatter_filters.py --url http://127.0.0.1:3361`:
  **six browser groups pass**, zero browser errors. Real clicks and key events
  cover column/search/clear, all modes and point counts, adding/removing/intersecting
  rows, test/TP/label searches, parent subset toggling, every drawer close path,
  exponent/negative input, nested Escape/Tab/typeahead, autosave/reload/Clear,
  unavailable-column recovery, 1100px resize and actual 125%/150% browser zoom.
- `python scripts/verify_scatter_point_menu.py --url http://127.0.0.1:3361`:
  existing regression passes, including selection/search/scrolling, first outside
  click, keyboard/focus, maximize/restore, resize and actual 125%/150% zoom.
- Independent source review and `git diff --check`: pass. The 150% screenshot was
  visually inspected. Browser evidence is ignored under `data/verification/scatter-filters/`
  and `data/verification/scatter-point-menu/`.

The browser suites use fresh isolated profiles and GET-only mocked API fixtures;
no user datasets or browser profile are changed. Backend regressions use isolated
fixtures. No live deployed-instance verification or deployment was performed.

For the browser suites, start Vite in frontend with:
`npm.cmd run dev -- --host 127.0.0.1 --port 3361 --strictPort --open false`.
The tests require the already-installed Python Playwright/full Chromium runtime.
