import { COLORS } from '../constants/colors';
import type { PlotViewports } from './plotViewport';

export type FlightTimeBasis = 'stored' | 'elapsed';
export interface FullFlightSelection {
  test: string;
  color: string;
  hidden: boolean;
  /** Additional display shift, in seconds, after choosing the time basis. */
  offset: number;
}
export interface FullFlightComparison {
  flights: FullFlightSelection[];
  timeBasis: FlightTimeBasis;
}
/** Native samples are never joined/interpolated. display = stored + timeOffset. */
export interface FullFlightSource {
  test: string;
  color: string;
  columns: string[];
  timeColumn: string | null;
  timeOffset: number;
  fs: number | null;
}

export function nextFlightColor(flights: readonly FullFlightSelection[]): string {
  return COLORS.find(color => !flights.some(flight => flight.color === color)) ?? COLORS[flights.length % COLORS.length];
}

export function normalizeFullFlightComparison(value: unknown): FullFlightComparison | null {
  if (!value || typeof value !== 'object' || !Array.isArray((value as FullFlightComparison).flights)) return null;
  const raw = value as FullFlightComparison;
  const flights: FullFlightSelection[] = [];
  for (const entry of raw.flights.slice(0, 20)) {
    if (!entry || typeof entry.test !== 'string' || !entry.test || flights.some(flight => flight.test === entry.test)) continue;
    flights.push({ test: entry.test, hidden: entry.hidden === true,
      color: typeof entry.color === 'string' && /^#[\da-f]{6}$/i.test(entry.color) ? entry.color : nextFlightColor(flights),
      offset: typeof entry.offset === 'number' && Number.isFinite(entry.offset) ? entry.offset : 0 });
  }
  return { flights, timeBasis: raw.timeBasis === 'stored' ? 'stored' : 'elapsed' };
}

export function flightTimeOffset(basis: FlightTimeBasis, start: number, additional = 0): number {
  return (basis === 'elapsed' ? -start : 0) + additional;
}

export function nativeFlightRange(range: [number, number] | null, offset: number): [number, number] | null {
  return range ? [range[0] - offset, range[1] - offset] : null;
}

export const FLIGHT_VARIABLE_DASHES = [[], [8, 4], [2, 3], [9, 3, 2, 3], [12, 4], [5, 3, 1, 3]];

/** Entering comparison without changing coordinates must retain existing crops. */
export function comparisonViewports(previous: PlotViewports, sourceToken: unknown[], basis: FlightTimeBasis): PlotViewports {
  const migrate = (kind: 'full' | 'xy') => previous[kind].map(viewport => {
    if (!viewport) return null;
    try {
      const context = JSON.parse(viewport.context);
      if (context[0] !== kind) return viewport;
      if (kind === 'full' && JSON.stringify(context[1]) === JSON.stringify(sourceToken)) context[1] = ['comparison', basis];
      else if (kind === 'xy' && context[3] === 'full' && Array.isArray(context[4]) &&
        JSON.stringify(context[4][0]) === JSON.stringify(sourceToken)) context[4][0] = ['comparison', basis];
      else return viewport;
      return { ...viewport, context: JSON.stringify(context) };
    } catch { return viewport; }
  });
  return { ...previous, full: migrate('full'), xy: migrate('xy') };
}
