import type { TestMeta, TestPoint } from '../types';

/** Native auto-split/saved boundaries already identify exact rows. Only edited
 * times need nominal sample conversion (including the same ties as Save). */
export function indexTestPoints(points: TestPoint[], meta: TestMeta): TestPoint[] {
  const origin = meta.t_start ?? 0;
  const nativeIndex = (value: number | null | undefined, fallback: number) =>
    value != null && Number.isSafeInteger(value) && value >= 0 && value <= meta.n_rows
      ? value : fallback;
  return [...points].sort((a, b) => a.start_s - b.start_s).map((tp) => ({
    ...tp,
    start_idx: nativeIndex(tp.start_idx, Math.round((tp.start_s - origin) * meta.fs_hz)),
    end_idx: tp.end_s !== null
      ? nativeIndex(tp.end_idx, Math.round((tp.end_s - origin) * meta.fs_hz)) : null,
  }));
}

/** Keep the opposite edge's native row anchor when changing a boundary. */
export function patchTestPoint(point: TestPoint, patch: Partial<TestPoint>): TestPoint {
  return {
    ...point,
    ...patch,
    ...(patch.start_s !== undefined && patch.start_s !== point.start_s ? { start_idx: null } : {}),
    ...(patch.end_s !== undefined && patch.end_s !== point.end_s ? { end_idx: null } : {}),
  };
}

/** Input is indexTestPoints output. Open ends stop at the next strictly later
 *  TP or n_rows, matching the saved backend resolver; no inclusive +1. */
export function draftTestPointRange(tp: TestPoint, points: TestPoint[], nRows: number) {
  const next = points.find((point) => point.start_s > tp.start_s);
  const start = tp.start_idx;
  const end = tp.end_idx ?? next?.start_idx ?? nRows;
  if (start == null || !Number.isSafeInteger(start) || !Number.isSafeInteger(end)) return null;
  const start_idx = Math.min(nRows, Math.max(0, start));
  const end_idx = Math.min(nRows, Math.max(0, end));
  return end_idx > start_idx ? { start_idx, end_idx } : null;
}
