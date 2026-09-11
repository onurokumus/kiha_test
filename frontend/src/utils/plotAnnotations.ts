import uPlot from 'uplot';
import type { Trace } from '../types';

export interface TimeAnnotation { id: string; start_s: number; end_s: number | null; text: string }
/** Readable labels only; editor values, projection and sidecars keep full precision. */
export const annotationTimeLabel = (value: number) => Number(value.toPrecision(12)).toString();
export interface AnnotationDocument {
  version: 1; test: string; revision: number; time_basis: 'stored_elapsed_seconds';
  data_bounds: [number, number]; annotations: TimeAnnotation[];
}
export interface AnnotationSource {
  key: string; test: string; label: string; origin: number;
  /** Loaded TP bounds in stored time; null uses the test's current bounds. */
  bounds: [number, number] | null; color: string;
  seriesIndices: number[];
}
export interface PlotAnnotation extends TimeAnnotation {
  test: string; source: string; tag: string; color: string;
  displayStart: number; displayEnd: number | null;
  startClipped: boolean; endClipped: boolean;
}

/** Never guess a TP origin from rounded user-entered start_s or sample rate. */
export function traceTimeBounds(trace: Trace | undefined): [number, number] | null {
  const centers = trace?.analysis?.source_centers as Record<string, unknown> | undefined;
  const start = centers?.first_time_s, end = centers?.last_time_s;
  return typeof start === 'number' && typeof end === 'number' &&
    Number.isFinite(start) && Number.isFinite(end) && end >= start ? [start, end] : null;
}

export function projectAnnotations(source: AnnotationSource, document: AnnotationDocument): PlotAnnotation[] {
  const min = Math.max(document.data_bounds[0], source.bounds?.[0] ?? -Infinity);
  const max = Math.min(document.data_bounds[1], source.bounds?.[1] ?? Infinity);
  return document.annotations.flatMap((item, index) => {
    if (item.start_s > max || (item.end_s ?? item.start_s) < min || max < min) return [];
    return [{ ...item, test: source.test, source: source.label, tag: `A${index + 1}`, color: source.color,
      displayStart: Math.max(min, item.start_s) - source.origin,
      displayEnd: item.end_s === null ? null : Math.min(max, item.end_s) - source.origin,
      startClipped: item.start_s < min, endClipped: item.end_s !== null && item.end_s > max }];
  });
}

/** Canvas rendering keeps annotation lines/bands aligned during local wheel/pan
 * and includes them in existing single/multi PNG captures. Text lives in the
 * dialog; short tags avoid covering signals with long observations. */
export function annotationsPlugin(getItems: (plot: uPlot) => readonly PlotAnnotation[]): uPlot.Plugin {
  return { hooks: { draw: (u) => {
    const min = u.scales.x.min, max = u.scales.x.max;
    if (min == null || max == null || max <= min) return;
    const { ctx, bbox } = u;
    const ratio = uPlot.pxRatio;
    const laneEnds = [-Infinity, -Infinity, -Infinity];
    let count = 0;
    ctx.save();
    ctx.beginPath(); ctx.rect(bbox.left, bbox.top, bbox.width, bbox.height); ctx.clip();
    for (const item of getItems(u)) {
      if (item.displayStart > max || (item.displayEnd ?? item.displayStart) < min) continue;
      const x = u.valToPos(Math.max(min, item.displayStart), 'x', true);
      const right = u.valToPos(Math.min(max, item.displayEnd ?? item.displayStart), 'x', true);
      if (![x, right].every(Number.isFinite)) continue;
      count++;
      ctx.strokeStyle = item.color; ctx.fillStyle = item.color; ctx.lineWidth = ratio;
      if (item.displayEnd !== null) {
        ctx.globalAlpha = .08; ctx.fillRect(x, bbox.top, right - x, bbox.height);
      }
      ctx.globalAlpha = .85; ctx.setLineDash([3 * ratio, 3 * ratio]);
      ctx.beginPath();
      // A clipped interval shades to the edge but never invents a boundary line.
      if (!item.startClipped && item.displayStart >= min) { ctx.moveTo(x, bbox.top); ctx.lineTo(x, bbox.top + bbox.height); }
      if (!item.endClipped && item.displayEnd !== null && item.displayEnd <= max) {
        ctx.moveTo(right, bbox.top); ctx.lineTo(right, bbox.top + bbox.height);
      }
      ctx.stroke(); ctx.setLineDash([]);
      ctx.font = `${10 * ratio}px "Segoe UI", sans-serif`;
      const width = ctx.measureText(item.tag).width + 8 * ratio;
      const left = Math.max(bbox.left, Math.min(x + 3 * ratio, bbox.left + bbox.width - width));
      const lane = laneEnds.findIndex((end, index) => end + 3 * ratio <= left && (index + 1) * 16 * ratio < bbox.height);
      if (lane >= 0) {
        const y = bbox.top + (lane * 16 + 2) * ratio;
        ctx.globalAlpha = .95; ctx.fillStyle = '#252526'; ctx.fillRect(left, y, width, 14 * ratio);
        ctx.fillStyle = item.color; ctx.textBaseline = 'top'; ctx.fillText(item.tag, left + 4 * ratio, y + ratio);
        laneEnds[lane] = left + width;
      }
    }
    ctx.restore();
    u.root.dataset.annotationCount = String(count);
  } } };
}

export function annotationImageDetails(items: readonly PlotAnnotation[], visible: boolean): string[] {
  return !visible ? ['Time annotations hidden.'] : items.length
    ? ['Time annotations (stored test seconds; clipped to the plotted source):',
      ...items.map((item) => `${item.source} · ${item.tag} · ${annotationTimeLabel(item.start_s)}${item.end_s === null ? '' : `–${annotationTimeLabel(item.end_s)}`} s: ${item.text}`)]
    : ['No time annotations in the plotted source.'];
}
