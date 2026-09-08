import { TpStat } from '../types';

export type ScatterErrorRange = [number, number];

export interface ScatterRangePoint {
  x: number;
  y: number;
  xError?: ScatterErrorRange;
  yError?: ScatterErrorRange;
}

export interface ScatterRangeVisibility {
  horizontal: boolean;
  vertical: boolean;
}

export interface ScatterExtents {
  xMin: number;
  xMax: number;
  yMin: number;
  yMax: number;
}

/** Recharts expects distances from the mean, not absolute endpoints. */
export function tpStatErrorRange(stat: TpStat | undefined): ScatterErrorRange | undefined {
  const mean = stat?.mean;
  const minimum = stat?.min;
  const maximum = stat?.max;
  if (
    typeof mean !== 'number' ||
    !Number.isFinite(mean) ||
    typeof minimum !== 'number' ||
    !Number.isFinite(minimum) ||
    typeof maximum !== 'number' ||
    !Number.isFinite(maximum)
  ) {
    return undefined;
  }

  // Normalizing the endpoints keeps malformed or slightly rounded source
  // statistics from producing negative error distances.
  const lower = Math.min(mean, minimum, maximum);
  const upper = Math.max(mean, minimum, maximum);
  const lowerDistance = mean - lower;
  const upperDistance = upper - mean;
  if (!Number.isFinite(lowerDistance) || !Number.isFinite(upperDistance)) return undefined;
  return [lowerDistance, upperDistance];
}

/** Find the visible chart extrema, including only enabled min/max ranges. */
export function scatterExtents(
  points: readonly ScatterRangePoint[],
  visibility: ScatterRangeVisibility
): ScatterExtents | null {
  if (points.length === 0) return null;

  let xMin = points[0].x;
  let xMax = points[0].x;
  let yMin = points[0].y;
  let yMax = points[0].y;

  for (let index = 0; index < points.length; index += 1) {
    const point = points[index];
    const xLow = visibility.horizontal && point.xError ? point.x - point.xError[0] : point.x;
    const xHigh = visibility.horizontal && point.xError ? point.x + point.xError[1] : point.x;
    const yLow = visibility.vertical && point.yError ? point.y - point.yError[0] : point.y;
    const yHigh = visibility.vertical && point.yError ? point.y + point.yError[1] : point.y;

    if (xLow < xMin) xMin = xLow;
    if (xHigh > xMax) xMax = xHigh;
    if (yLow < yMin) yMin = yLow;
    if (yHigh > yMax) yMax = yHigh;
  }

  return { xMin, xMax, yMin, yMax };
}
