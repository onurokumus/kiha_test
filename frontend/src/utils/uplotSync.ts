import uPlot from 'uplot';

/**
 * In-place uPlot updater (perf 2.4). The plot components used to
 * `destroy()` + `new uPlot()` on EVERY data change — with 9 linked plots
 * refetching per zoom/pan step that is a storm of canvas teardown, plugin
 * re-init and layout thrash. Instead we keep the instance alive and feed new
 * data through `setData`, rebuilding only when the *structure* changes.
 *
 * `structKey` must encode everything that a `setData` cannot change in place:
 * the series definitions (count, labels, stroke roles, bands/facets), the pixel
 * size, and the expanded state. Same key as last time → reuse via `setData`;
 * different key → destroy + recreate.
 *
 * Gotchas baked in:
 * - The instance outlives individual renders, so any callback the opts capture
 *   (pan/zoom commit, setSelect) MUST be read through a "latest" ref by the
 *   caller — a reused instance still holds the closures from its build.
 * - `makeOpts` is a thunk so the (comparatively expensive) opts object +
 *   plugin array are only built on a real rebuild, never on a plain data tick.
 * - `onCreate` runs ONLY on a rebuild (e.g. the legend-height `setSize`
 *   shrink, which must not re-run on every data update); `onUpdate` runs ONLY
 *   on the reuse path (e.g. re-applying an explicit zoom via `setScale`).
 */
export interface SyncPlotArgs {
  plotRef: { current: uPlot | null };
  structKeyRef: { current: string };
  el: HTMLElement;
  structKey: string;
  makeOpts: () => uPlot.Options;
  data: uPlot.AlignedData;
  /**
   * `resetScales` passed to `setData` on the REUSE path (default true → scales
   * auto-range to the new data). Set false when the caller re-applies an
   * explicit scale in `onUpdate`, so the data swap doesn't auto-range and cause
   * a visible flicker before the scale is restored. Ignored on a fresh build,
   * where the scales come from `makeOpts`.
   */
  resetScales?: boolean;
  onCreate?: (u: uPlot) => void;
  onUpdate?: (u: uPlot) => void;
}

export function syncPlot(a: SyncPlotArgs): void {
  const existing = a.plotRef.current;
  if (existing && a.structKeyRef.current === a.structKey) {
    existing.setData(a.data, a.resetScales ?? true);
    a.onUpdate?.(existing);
    return;
  }
  existing?.destroy();
  const u = new uPlot(a.makeOpts(), a.data, a.el);
  a.plotRef.current = u;
  a.structKeyRef.current = a.structKey;
  a.onCreate?.(u);
}

/** Destroy + clear a plot ref (the no-data path and on unmount). Resetting the
 *  struct key forces a fresh build when data next arrives. */
export function clearPlot(
  plotRef: { current: uPlot | null },
  structKeyRef?: { current: string }
): void {
  plotRef.current?.destroy();
  plotRef.current = null;
  if (structKeyRef) structKeyRef.current = '';
}
