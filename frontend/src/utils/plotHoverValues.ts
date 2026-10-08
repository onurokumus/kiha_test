import type uPlot from 'uplot';

export interface PlotHoverRow {
  label: string;
  color?: string;
  values: string[];
}

export interface PlotHoverValues {
  heading: string;
  columns: string[];
  units?: string[];
  prefixes?: string[];
  rows: PlotHoverRow[];
}

export function formatHoverNumber(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.abs(value) >= 1e6 || (value !== 0 && Math.abs(value) < 1e-4)
      ? Number(value.toPrecision(6)).toExponential()
      : String(Number(value.toPrecision(6))) : '—';
}

/** Trim repeated source/processing context from the app's legend labels. Keep
 * test names in cross-test comparisons and processing names in overlays. */
export function compactHoverLabels(rows: PlotHoverRow[]): string[] {
  const parsed = rows.map(row => {
    const parts = row.label.split(' · ');
    const last = parts[parts.length - 1] ?? '';
    const processing = parts.length > 1 && /^(original|filtered)( max| min)?$/.test(last) ? parts.pop()! : '';
    const point = parts.length > 1 && !processing && /^TP -?\d+(?:\.\d+)?$/.test(last) ? parts.pop()! : '';
    if (!processing && !point) return { name: row.label, test: '', processing: '', point: '' };
    const test = parts.length > 1 ? parts.pop()! : '';
    return { name: parts.join(' · '), test, processing, point };
  });
  const oneTest = new Set(parsed.map(row => row.test).filter(Boolean)).size <= 1;
  const overlay = parsed.some(row => row.processing.startsWith('filtered'));
  return parsed.map(row => {
    const repeatedName = parsed.filter(other => other.name === row.name && other.point !== row.point).length > 0;
    const processing = overlay ? row.processing : row.processing.replace(/^original\b\s*/, '');
    return [row.name, !oneTest ? row.test : '', repeatedName ? row.point : '', processing].filter(Boolean).join(' · ');
  });
}

/** Use the same sample indices as the cursor markers, without interpolation.
 * Facets retain their independent X arrays; null samples remain unavailable. */
export function sampleHoverRows(plot: uPlot, faceted: boolean): PlotHoverRow[] {
  const rows: PlotHoverRow[] = [];
  for (let seriesIdx = 1; seriesIdx < plot.series.length; seriesIdx++) {
    const series = plot.series[seriesIdx];
    if (series.show === false) continue;
    const index = plot.cursor.idxs ? plot.cursor.idxs[seriesIdx] : faceted ? null : plot.cursor.idx;
    if (index == null || index < 0) continue;
    const data = plot.data as unknown as (number[] | [number[], number[]])[];
    const xs = faceted ? (data[seriesIdx] as [number[], number[]])[0] : data[0] as number[];
    const ys = faceted ? (data[seriesIdx] as [number[], number[]])[1] : data[seriesIdx] as number[];
    if (!Number.isFinite(xs?.[index])) continue;
    const stroke = typeof series.stroke === 'function' ? series.stroke(plot, seriesIdx) : series.stroke;
    rows.push({ label: typeof series.label === 'string' ? series.label : series.label?.textContent ?? `Series ${seriesIdx}`,
      color: typeof stroke === 'string' ? stroke : undefined,
      values: [formatHoverNumber(xs[index]), formatHoverNumber(ys?.[index])] });
  }
  return rows;
}

/** XY samples are unsorted and can share X. Find each source's nearest visible
 * pair in screen space so both the marker and readout belong to that pair. */
export function nearestXYDataIdx(plot: uPlot, seriesIdx: number): number | null {
  const { left, top } = plot.cursor;
  if (seriesIdx === 0 || left == null || top == null || left < 0 || top < 0) return null;
  const pair = (plot.data as unknown as ([number[], number[]] | null)[])[seriesIdx];
  if (!pair) return null;
  const xScale = plot.series[seriesIdx].facets?.[0]?.scale ?? 'x';
  const yScale = plot.series[seriesIdx].facets?.[1]?.scale ?? 'y';
  const xRange = plot.scales[xScale], yRange = plot.scales[yScale];
  let nearest: number | null = null, distance = Infinity;
  for (let index = 0; index < pair[0].length; index++) {
    const x = pair[0][index], y = pair[1][index];
    if (!Number.isFinite(x) || !Number.isFinite(y) || x < xRange.min! || x > xRange.max! ||
        y < yRange.min! || y > yRange.max!) continue;
    const dx = plot.valToPos(x, xScale) - left, dy = plot.valToPos(y, yScale) - top;
    const candidate = dx * dx + dy * dy;
    if (candidate < distance) { distance = candidate; nearest = index; }
  }
  return nearest;
}
