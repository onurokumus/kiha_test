import { PLOT_INSET, PLOT_INSET_X, PLOT_INSET_Y } from '../constants/scatterGeometry';

export type ScatterViewport = [number, number, number, number];
export interface ScatterBounds { xMin: number; xMax: number; yMin: number; yMax: number }
export interface ScatterAnchor { x: number; y: number }
interface ChartRect { left: number; top: number; width: number; height: number }

// Limits are relative to the source range, so very small/large engineering
// units behave alike. The separate precision check protects offset signals.
const MIN_SPAN_RATIO = 1e-9;
const MAX_SPAN_RATIO = 1e6;

export const boundsViewport = (bounds: ScatterBounds): ScatterViewport =>
  [bounds.xMin, bounds.xMax, bounds.yMin, bounds.yMax];

export function validScatterViewport(view: readonly number[]): view is ScatterViewport {
  if (view.length !== 4 || !view.every(Number.isFinite)) return false;
  for (const index of [0, 2]) {
    const min = view[index], max = view[index + 1], span = max - min;
    const precision = Math.max(Math.abs(min), Math.abs(max)) * Number.EPSILON * 16;
    if (!Number.isFinite(span) || span <= 0 || span <= precision) return false;
  }
  return true;
}

/** Reject collapsed, overflowing or impractically distant views atomically. */
export function withinScatterLimits(view: readonly number[], baseline: ScatterViewport): view is ScatterViewport {
  if (!validScatterViewport(view) || !validScatterViewport(baseline)) return false;
  for (const index of [0, 2]) {
    const span = view[index + 1] - view[index];
    const baseSpan = baseline[index + 1] - baseline[index];
    const ratio = span / baseSpan;
    const center = view[index] + span / 2;
    const baseCenter = baseline[index] + baseSpan / 2;
    if (ratio < MIN_SPAN_RATIO || ratio > MAX_SPAN_RATIO ||
        Math.abs((center - baseCenter) / baseSpan) > MAX_SPAN_RATIO) return false;
  }
  return true;
}

/** Wheel positions over labels or outside the data rectangle are not zoom anchors. */
export function scatterPlotAnchor(clientX: number, clientY: number, rect: ChartRect): ScatterAnchor | null {
  if (![clientX, clientY, rect.left, rect.top, rect.width, rect.height].every(Number.isFinite)) return null;
  const width = rect.width - PLOT_INSET_X, height = rect.height - PLOT_INSET_Y;
  if (width <= 0 || height <= 0) return null;
  const x = (clientX - rect.left - PLOT_INSET.left) / width;
  const y = 1 - (clientY - rect.top - PLOT_INSET.top) / height;
  return x >= 0 && x <= 1 && y >= 0 && y <= 1 ? { x, y } : null;
}

/** Normalize mouse wheels and trackpads; equal opposite input is reciprocal. */
export function scatterWheelFactor(deltaY: number, deltaMode: number, plotHeight: number): number | null {
  if (!Number.isFinite(deltaY) || deltaY === 0 || !Number.isFinite(plotHeight) || plotHeight <= 0) return null;
  const unit = deltaMode === 0 ? 1 : deltaMode === 1 ? 16 : deltaMode === 2 ? plotHeight : null;
  if (unit === null) return null;
  const pixels = Math.max(-240, Math.min(240, deltaY * unit));
  return Math.exp(pixels * 0.002);
}

/** factor multiplies the span: below 1 zooms in; above 1 zooms out. */
export function zoomScatterViewport(
  view: ScatterViewport, factor: number, baseline: ScatterViewport,
  anchor: ScatterAnchor = { x: 0.5, y: 0.5 },
): ScatterViewport | null {
  if (!validScatterViewport(view) || !Number.isFinite(factor) || factor <= 0 ||
      ![anchor.x, anchor.y].every(value => Number.isFinite(value) && value >= 0 && value <= 1)) return null;
  const next = [...view] as ScatterViewport;
  for (const [index, ratio] of [[0, anchor.x], [2, anchor.y]]) {
    const span = view[index + 1] - view[index];
    const nextSpan = span * factor;
    const center = view[index] + span * ratio;
    next[index] = center - nextSpan * ratio;
    next[index + 1] = center + nextSpan * (1 - ratio);
  }
  return withinScatterLimits(next, baseline) ? next : null;
}

/** Deltas are data units, matching the existing drag-pan callback. */
export function panScatterViewport(
  view: ScatterViewport, deltaX: number, deltaY: number, baseline: ScatterViewport,
): ScatterViewport | null {
  if (!validScatterViewport(view) || !Number.isFinite(deltaX) || !Number.isFinite(deltaY)) return null;
  const next: ScatterViewport = [view[0] - deltaX, view[1] - deltaX, view[2] - deltaY, view[3] - deltaY];
  return withinScatterLimits(next, baseline) ? next : null;
}
