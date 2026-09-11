import type uPlot from 'uplot';
import { AxisRange, validAxisRange } from './timePlotRanges';

type Samples = readonly (number | null | undefined)[];
export interface LineSamples { x: Samples; y: Samples }

const finite = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value);

/** Extents of the actual visible line: include sample centers and clipped
 * boundary intersections, but never join across a missing/non-finite sample.
 * Each trace owns its X array (TP and Spectrum facets need no resampling).
 * This reads the displayed arrays, including both envelope edges/overlays. */
export function visibleLineExtents(
  traces: readonly LineSamples[],
  xRange: AxisRange | null
): AxisRange | null {
  let min = Infinity;
  let max = -Infinity;
  const include = (value: number) => {
    if (!Number.isFinite(value)) return;
    min = Math.min(min, value);
    max = Math.max(max, value);
  };
  for (const { x, y } of traces) {
    const count = Math.min(x.length, y.length);
    for (let i = 0; i < count; i++) {
      const xi = x[i], yi = y[i];
      if (!finite(xi) || !finite(yi)) continue;
      if (!xRange || (xi >= xRange[0] && xi <= xRange[1])) include(yi);
      if (!xRange || i === 0) continue;
      const previousX = x[i - 1], previousY = y[i - 1];
      if (!finite(previousX) || !finite(previousY) || xi === previousX) continue;
      for (const edge of xRange) {
        if ((previousX < edge && xi > edge) || (xi < edge && previousX > edge)) {
          const fraction = (edge - previousX) / (xi - previousX);
          // Weighted terms avoid overflowing a difference between large Ys.
          include(previousY * (1 - fraction) + yi * fraction);
        }
      }
    }
  }
  return min <= max ? [min, max] : null;
}

/** Ten percent breathing room around the visible extrema. Constant/tiny
 * signals get magnitude-relative padding so uPlot's tick loop stays finite. */
export function paddedVisibleRange([min, max]: AxisRange): AxisRange {
  const span = max - min;
  const magnitude = Math.max(Math.abs(min), Math.abs(max));
  const pad = span > magnitude * 1e-9 ? span * 0.1 : magnitude === 0 ? 1 : magnitude * 1e-3;
  const range: AxisRange = [min - pad, max + pad];
  return validAxisRange(range) ? range : [min, max];
}

/** uPlot mode 2 auto-ranging reads complete facets, independent of its X
 * viewport. Read only visible, enabled line series for consistent Time/Full/
 * Spectrum fitting. Spectrum arrays are already in display units (log10/order).
 * A completely empty X window retains its last usable Y axis until data returns. */
export function visibleYRange(
  u: uPlot,
  initialMin: number | null,
  initialMax: number | null
): [number | null, number | null] {
  const minX = u.scales.x.min, maxX = u.scales.x.max;
  const xRange: AxisRange | null = finite(minX) && finite(maxX) && minX <= maxX
    ? [minX, maxX]
    : null;
  const traces: LineSamples[] = [];
  const data = u.data as unknown as (Samples | [Samples, Samples] | null)[];
  for (let i = 1; i < u.series.length; i++) {
    if (u.series[i].show === false || u.series[i].auto === false) continue;
    if (u.series[i].facets) {
      const pair = data[i] as [Samples, Samples] | null;
      if (pair) traces.push({ x: pair[0], y: pair[1] });
    } else {
      traces.push({ x: data[0] as Samples, y: data[i] as Samples });
    }
  }
  const extents = visibleLineExtents(traces, xRange);
  if (extents) return paddedVisibleRange(extents);
  const previous = [u.scales.y.min, u.scales.y.max];
  if (validAxisRange(previous)) return previous;
  return finite(initialMin) && finite(initialMax)
    ? paddedVisibleRange([initialMin, initialMax])
    : [null, null];
}

/** Range callbacks run before uPlot publishes its pending X scale. Refit in
 * the next microtask, after that scale transaction and before browser paint.
 * Scheduling from the hook also covers box zoom, double-click and restored
 * viewports, without replacing uPlot internals or committing auto Y upstream. */
export function visibleYAutoFitPlugin(isManual = () => false): uPlot.Plugin {
  let destroyed = false;
  let queued = false;
  return {
    hooks: {
      setScale: (u, axis) => {
        if (axis !== 'x' || queued || isManual()) return;
        queued = true;
        queueMicrotask(() => {
          queued = false;
          if (destroyed || isManual()) return;
          u.setScale('y', { min: null, max: null } as unknown as { min: number; max: number });
        });
      },
      destroy: () => { destroyed = true; },
    },
  };
}
