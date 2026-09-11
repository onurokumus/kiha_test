import { SelectedTestPoint } from '../types';

export type AxisRange = [number, number];
export interface SavedTimeYRange {
  context: string;
  range: AxisRange;
}

/** Explicit uPlot scales bypass auto-range safeguards. Reject non-finite,
 * reversed and near-degenerate bounds before tick generation can stall. */
export function validAxisRange(value: unknown): value is AxisRange {
  if (!Array.isArray(value) || value.length !== 2) return false;
  const [min, max] = value;
  if (typeof min !== 'number' || typeof max !== 'number') return false;
  const span = max - min;
  return (
    Number.isFinite(min) &&
    Number.isFinite(max) &&
    Number.isFinite(span) &&
    span > Math.max(Math.abs(min), Math.abs(max), 1e-12) * 1e-9
  );
}

/** Slot is implicit in the containing array. JSON avoids delimiter collisions;
 * sorting means selection order/color changes do not discard a valid view. */
export function timePlotContexts(
  columns: string[],
  selections: SelectedTestPoint[],
  hidden: Set<string>
): string[] {
  const points = selections
    .filter((s) => !hidden.has(s.id))
    .map((s) => JSON.stringify([s.test, s.tpId, s.tp.start_s, s.endS]))
    .sort();
  return columns.map((column) => JSON.stringify([column, points]));
}

export function normalizeTimeYRanges(value: unknown): (SavedTimeYRange | null)[] {
  return Array.from({ length: 9 }, (_, i) => {
    const item = Array.isArray(value) ? value[i] : null;
    return item && typeof item.context === 'string' && validAxisRange(item.range)
      ? { context: item.context, range: item.range }
      : null;
  });
}
