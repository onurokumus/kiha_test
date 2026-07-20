# Remaining work — PTT (ptt/)

Open items distilled from `possible_bugs.md` after the 2026-07-17 fix passes
(first through eighth) and the 2026-07-18/07-19 passes (ninth onward). This is
the live worklist; `possible_bugs.md` keeps the full findings text and the
per-pass "Fixed" history. File references are relative to `ptt/`. Ordered by
decreasing severity within each section.

**§1 Likely bugs — ALL CLEARED** (1.1–1.20). Nothing open in §1.

Cleared across the 2026-07-18/07-19 passes (see the Fixed blocks in
`possible_bugs.md`): 2.3, 2.4, 2.5, 2.6, 2.7, 2.9, 3.2, 3.5, 3.6, 3.7, 3.8, 4.3,
4.4, 4.5, 4.7, 4.8, 4.11, 4.12, 4.13, 4.14, 5.1, 5.3, 5.4, 5.5, 5.6, 5.10, 6.1,
6.5, 6.6, 6.7, 6.10, 6.11; 6.3 backend half done (frontend copies remain).
**§2 Performance — now down to 2.8 only. §3 Documentation mismatches — ALL
CLEARED.**

---

## 2. Performance bottlenecks

*(2.4 uPlot in-place `setData` via `syncPlot` + 2.5 clustering grid-hash + 2.6
`read_testpoint_trace` reduceat-vectorization — all FIXED 2026-07-19, fourteenth
pass; see `possible_bugs.md`. §2 is now down to 2.8 below.)*

### 2.8 Full-column collects in split helpers
`split.autosplit` collects the whole time + ID columns (~120 MB for a 1 h test)
and `id_candidates` collects all columns at stride. Acceptable today; both could
push column selection down / stream.

---

## 3. Documentation / comment mismatches — ALL CLEARED (2026-07-18)

3.2 (NaN policy), 3.5 (time-column vs assumed `fs`), 3.6 (serving rule + the
5000/6000 numbers), 3.7 (multi-series / x-link toggle), 3.8 (launch command),
3.10 (FMS tooltip comment), 3.11 (`TpTraceResponse` fields) all corrected. The
readers still convert idx↔time via the assumed uniform `fs` — that's now
described accurately in MVP.md §12 (a per-sample time-accurate reader is filed
under §7 features, not a doc mismatch).

---

## 4. Maintainability / infrastructure

### 4.1 No CI; no backend lint/type tooling; frontend lint gate not enforced
Backend now has pytest (71 tests) but nothing runs it automatically; no
ruff/flake8/mypy. Frontend `npm run lint` exists but is not a gate and currently
reports pre-existing issues (e.g. an `e as any` in `MainScatterPlot`, a
`useMemo` dep warning). A GitHub Action running pytest + `npm run build` + eslint
(+ ruff) is the cheapest way to keep future sessions honest.

### 4.2 (partial) status-before-lock invariant reinforced but not structural
Every mutating endpoint now 409s on busy status before its lock
(`_reject_if_busy`, 1.12/1.5), but nothing *enforces* it — a future endpoint
could forget. A `require_status(name, "ready")` + a `guarded_write(name)` that
asserts the check would make it structural.

### 4.6 Two parallel styling systems
`App.css` utility classes (`.btn`/`.input`/`.panel`/`.badge`) coexist with
duplicated inline-style objects (`constants/styles.ts` + ad-hoc inline styles).
Same colors/paddings in ≥3 places; theming needs a repo-wide hunt.

### 4.9 `stop.bat` / `stop.sh` kill by port
Both kill whatever listens on 8000/3000 — possibly an unrelated dev process. A
pidfile written by `start.*` would be safer.

### 4.10 `start.bat` has no preflight checks
`start.sh` verifies the venv + `node_modules` and prints remediation; `start.bat`
launches minimized windows that die instantly on a fresh clone, error hidden.

---

## 5. Dead / unused code (verify each before removing)

(5.1, 5.3, 5.4, 5.5, 5.6, 5.10 removed in the ninth pass — 2026-07-18.)

- **5.9** Unreachable selection colors — `constants/colors.ts` has 10 but
  `MAX_SELECTED_POINTS = 6`; indices 6–9 unreachable, and #10 (`#569cd6`) would
  collide with the unselected-point blue if the cap were raised. Left as-is:
  the extra entries are harmless headroom, but the #10/blue collision is worth
  a comment before the cap is ever raised.

---

## 6. Duplicate code that could be commonized

### 6.3 (partial) test-point end resolution still duplicated on the frontend
Backend half done (ninth pass): `store._compute_tp_stats` now calls
`_testpoint_bounds` instead of its own inline copy, so the scatter aggregate and
the CSV export / TP trace resolve identical row ranges. Still duplicated on the
frontend: `SplitPlot.effectiveEnd` and `App.handleScatterToggle` each re-derive
"explicit idx → time×fs → next TP's start → end of data"; they should share one
helper.

### 6.4 Four plot components repeat the same four blocks
`TimePlot`/`FullTestPlot`/`SpectrumPlot`/`XYPlot` each copy: the ResizeObserver
box-tracking effect, the post-create legend-height `setSize` shrink, the ~30-line
edit-mode column `<select>`, and the floating collapsed-cell filter overlay +
expanded filter row. A `usePlotBox()` hook, a `shrinkForLegend(u, box)` util, and
shared `<PlotHeader>` / `<PlotFilterOverlay>` would delete ~300 lines.

### 6.8 Two near-identical color palettes
`constants/colors.ts` (`COLORS`) and `constants/uplotTheme.ts`
(`PALETTE`/`colorFor`) share 7 entries in different orders; consolidate.

### 6.9 `buttonStyle`/`SelectStyle` duplicate `.btn`/`.input`
`constants/styles.ts` re-declares in JS what `App.css` utility classes define
(see 4.6). Pick one mechanism.

### 6.12 SpectrumPlot / XYPlot fetch scaffolding
The `dead`-flag + AbortController + 100 ms timer + `tpFingerprint` + per-TP
`Promise.all` with eligibility filtering is structurally identical in both. A
`useTpSourcedFetch` hook would collapse them.

---

## 7. Possible improvements & features (not bugs)

**Data robustness** (CSV dialect sniffing, full-file jitter scan) — DONE (1.1 / 1.14).

**Analysis features (propeller-specific):**
- Derived columns (user expressions: `P_mech = torque*rpm*2π/60`, `eff`, `g/W`).
- Spectrogram / waterfall (STFT over time) for vibration columns.
- RPM-order overlays on spectra (1P/2P/blade-pass cursor lines from an RPM column).
- Cursor measurements (A/B markers: Δt, Δy, min/mean/max/RMS of the visible window).
- Test comparison mode (color scatter by test; overlay same column across two tests).
- Density/heatmap XY mode (server-side 2D histogram — stride hides hysteresis loops).

**UX polish:**
- Persist analysis sessions (selection/axes/filters/plot configs/zoom → JSON).
- PNG export per plot (uPlot `toDataURL`, Recharts svg serialize); later a report.
- Keyboard shortcuts (1–9 expand cell, Esc close, ←/→ pan, R reset).
- Wheel-zoom/pan in SplitPlot (only uPlot view without `xPanZoomPlugin`); snap TP
  handles to integer sample times.
- Surface `meta.time_source == "generated"` as a banner in Analyze (the flag
  already exists from the synthetic-time-axis work).
- Sortable TP-table view (open follow-up from the tp_stats work).

**Infrastructure:**
- CI (see 4.1); codify the headless-Edge check as a Playwright smoke test.
- WebSocket/SSE status channel to replace the 2 s poll (removes 2.9).
- Upload hardening: size cap + first-KB content sniff (4.12); chunked/resumable
  uploads for multi-GB files over flaky links.
