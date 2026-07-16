# Frontend Improvements

Goal: user-friendly first, impressive second. All styling follows [STYLING_GUIDE.md](STYLING_GUIDE.md).

## Done

- [x] **Resilient data fetching** — read requests now use bounded timeouts and automatic
  retry with jittered backoff for network errors, rate limits, and transient 5xx responses.
  Rapid plot changes are debounced, successful charts stay visible during refresh/failure,
  panels offer an explicit retry, and a compact connection-status banner reports recovery.
  Large JSON responses are compressed; unexpected backend failures are logged and returned
  as safe structured errors with request IDs for diagnosis. Native dataframe reads are
  concurrency-gated on Windows to prevent the Python 3.14/Polars access violation found in
  `backend.log` from taking down the API process.

- [x] **In-page delete confirmation** — replaced browser `confirm()` with a themed modal
  (`ConfirmDialog.tsx`). Escape / backdrop click cancels; focus starts on *cancel* so
  Enter can't delete by accident. Danger-styled confirm button (`.btn-danger`, `#e06c75`).
- [x] **Toast notifications** — `Toasts.tsx` (`useToasts` hook + `ToastStack`). Replaces all
  `alert()` calls. Bottom-right stack, color-coded left border (info/success/error),
  auto-dismiss (4 s, errors 8 s), click to dismiss.
- [x] **Drag-and-drop CSV upload** — drop `.csv` file(s) anywhere in the window. Full-screen
  dashed-border overlay while dragging. Multiple files upload sequentially; per-file
  success/error toast. Upload button also accepts multiple files now.
- [x] **Auto-poll ingest status** — while any test is `ingesting`, the test list refreshes
  every 2 s. No more manual "refresh" clicking. Ingesting rows show a spinner; failed
  rows show the error in a tooltip.
- [x] **Loading skeletons** — shimmer placeholder (`.skeleton`) at plot height while a
  plot/spectrum/XY panel loads its first data. All loading glyphs (⟳) now actually spin.
- [x] **Series color swatches** — checked columns in the sidebar show the color the series
  will get in the next added plot.
- [x] **Crosshair sync across time plots** — uPlot `cursor.sync` (key `kiha-x`, matched by
  x value). Hover any time plot, crosshair tracks in all of them.
- [x] **Source-aware workspace persistence** — plots and shared zoom are saved as one global
  workspace (`kiha:workspace:v2`), while checked columns remain per-test. Every regular,
  spectrum, and XY plot retains its source dataset when the test-point picker switches;
  source badges make ownership explicit. Legacy per-test layouts migrate automatically,
  and rename/delete/undo update or restore only plots owned by the affected dataset.
- [x] **Plot export** — `png` button on every panel (canvas composed onto theme background);
  `csv` on time plots (visible window, envelope exports `col_max`/`col_min`) and spectra
  (freq/mag). `exportUtils.ts`.
- [x] **Command palette** — Ctrl+K (or header button). Add plot/XY/spectrum, toggle x-link,
  reset zoom, zoom to any test point, switch test, switch tab, delete test. Substring
  filter, ↑↓ + Enter, Esc closes. `CommandPalette.tsx`.
- [x] **Keyboard shortcuts** — `L` toggle x-link, `Esc` reset zoom, `↑`/`↓` switch tests
  (ignored while typing in inputs or while a dialog is open).
- [x] **Layout control** — drag the ⠿ grip in a panel header onto another panel to reorder;
  drag the strip under a panel to resize its height (150–800 px). Heights persist with
  the session state.
- [x] **Delete with undo** — DELETE now soft-deletes: the test moves to `data/trash/` and
  the success toast shows an *undo* button (10 s). Undo calls
  `POST /api/tests/{name}/restore` and also restores the saved layout. Trash entries are
  purged on later deletes once older than 1 h (`TRASH_MAX_AGE_S`).
- [x] **Test rename** — ✎ button in the sidebar opens inline edit (Enter commits, Esc/blur
  cancels). `POST /api/tests/{name}/rename?new_name=…` moves the dir and rewrites the
  embedded names in `meta.json`/`testpoints.json`; blocked while ingesting or on name
  collision. Saved layout follows the new name.
- [x] **Shareable views** — *share view* copies a URL whose hash contains the current test,
  visible time-series columns, and zoom range. Opening the link validates the referenced
  test and columns, restores a linked time plot before local session state, and degrades
  cleanly if data has since been renamed or removed. Also available in the command palette,
  with a manual-copy dialog when browser clipboard permissions are unavailable.
- [x] **Test browser polish** — compact two-line test rows now show readable durations,
  column counts, and distinct ready/ingesting/error states. Selection has a stronger visual
  anchor; rename uses explicit save/cancel controls; destructive actions are clearer; and
  refresh/upload provide better feedback with a quieter drag-and-drop hint.
- [x] **Cross-test point comparison** — the whole test-point card now toggles selection
  while a separate magnifier zooms. Statistical filters live in an expandable menu with
  validation and match counts. A test-qualified comparison set survives test switches,
  is visible beside every contributing test, and feeds both relative-time and XY plots
  using only common columns. Relative-time plots overlay every selected point from `t = 0`;
  long traces use ordered min/max reduction so spikes survive the point budget. Comparisons
  are capped at 16 series and load at most four data windows concurrently.

## Ideas (not planned yet)

- [ ] **Cursor value readout** — pinned readout of all series values at the crosshair
  position (uPlot legend already shows live values; a pinned/copyable variant).
