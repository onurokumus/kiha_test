import uPlot from 'uplot';

type FacetedSeriesData = [
  readonly number[],
  readonly (number | null | undefined)[],
];

/**
 * Resolve the sample nearest the cursor for uPlot mode-2 series whose x data
 * is sorted. uPlot cannot infer this from faceted data because every series
 * owns a different x array (the aligned-mode cursor index is undefined).
 */
export function sortedFacetedDataIdx(
  u: uPlot,
  seriesIdx: number
): number | null {
  if (seriesIdx === 0 || u.cursor.left == null || u.cursor.left < 0) return null;

  const pair = (
    u.data as unknown as readonly (FacetedSeriesData | null)[]
  )[seriesIdx];
  const xs = pair?.[0];
  if (!xs?.length) return null;

  const xScale = u.series[seriesIdx]?.facets?.[0]?.scale ?? 'x';
  const target = u.posToVal(u.cursor.left, xScale);
  const first = xs[0];
  const last = xs[xs.length - 1];
  if (!Number.isFinite(target) || target < first || target > last) return null;

  let low = 0;
  let high = xs.length - 1;
  while (low < high) {
    const mid = low + Math.floor((high - low) / 2);
    if (xs[mid] < target) low = mid + 1;
    else high = mid;
  }

  if (low === 0) return 0;
  const left = low - 1;
  return target - xs[left] <= xs[low] - target ? left : low;
}

/** Read and display the y value belonging to a mode-2 legend index. */
export function facetedSeriesValue(
  u: uPlot,
  _rawValue: number,
  seriesIdx: number,
  dataIdx: number | null
): string | number {
  if (dataIdx == null) return '—';
  const pair = (
    u.data as unknown as readonly (FacetedSeriesData | null)[]
  )[seriesIdx];
  const value = pair?.[1]?.[dataIdx];
  return typeof value === 'number' && Number.isFinite(value) ? value : '—';
}

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
