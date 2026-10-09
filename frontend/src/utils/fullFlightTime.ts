import type { DataWindow, WindowDisplayMode } from '../types';
import type { FullFlightSource } from './fullFlightComparison';
import { FLIGHT_VARIABLE_DASHES } from './fullFlightComparison';

/** Flight supplies hue; variable supplies line pattern in every comparison. */
export const FULL_FLIGHT_VARIABLE_DASHES: readonly number[][] = FLIGHT_VARIABLE_DASHES;

export interface FullFlightQuery {
  t0: number | null;
  t1: number | null;
  px: number;
  display: WindowDisplayMode;
}

export interface FullFlightWindow {
  source: FullFlightSource;
  columns: string[];
  window: DataWindow;
  query: FullFlightQuery;
}

export interface FullFlightTimeTrace {
  test: string;
  column: string;
  label: string;
  color: string;
  dash: number[];
  kind: 'original' | 'filtered';
  subdued: boolean;
  edge?: 'max' | 'min';
  band?: string;
  t: number[];
  y: (number | null)[];
}

/** Reject malformed windows before data from separate reads can be combined. */
export function windowColumnsAlign(window: DataWindow, keys: readonly string[]) {
  return keys.every(key => {
    const values = window.series[key];
    if (!values) return false;
    if (window.mode === 'raw') return Array.isArray(values) && values.length === window.t.length;
    const envelope = values as { min: (number | null)[]; max: (number | null)[] };
    return Array.isArray(envelope.min) && Array.isArray(envelope.max) &&
      envelope.min.length === window.t.length && envelope.max.length === window.t.length;
  });
}

export function windowsAlign(original: DataWindow, filtered: DataWindow, keys: readonly string[]) {
  return original.mode === filtered.mode && original.level === filtered.level &&
    original.i0 === filtered.i0 && original.i1 === filtered.i1 &&
    original.t.length === filtered.t.length && original.t.every((time, index) => time === filtered.t[index]) &&
    [original, filtered].every(window => windowColumnsAlign(window, keys));
}

/** Keep each flight's native samples; shifting time never resamples values. */
export function flightWindowTraces(entry: FullFlightWindow, window: DataWindow, columns: readonly string[],
  kind: 'original' | 'filtered', subdued: boolean): FullFlightTimeTrace[] {
  const keep = window.t.flatMap((time, index) => time !== null && Number.isFinite(time) ? [index] : []);
  const t = keep.map(index => window.t[index]! + entry.source.timeOffset);
  return entry.columns.flatMap(column => {
    const index = columns.indexOf(column);
    if (index < 0) return [];
    const base = { test: entry.source.test, column, color: entry.source.color,
      dash: FULL_FLIGHT_VARIABLE_DASHES[index % FULL_FLIGHT_VARIABLE_DASHES.length], kind, subdued, t };
    const label = `${entry.source.test} · ${column} · ${kind}`;
    if (window.mode === 'raw') {
      const values = window.series[column];
      return [{ ...base, label, y: keep.map(i => values[i]) }];
    }
    const envelope = window.series[column];
    const band = JSON.stringify([entry.source.test, column, kind]);
    return (['max', 'min'] as const).map(edge => ({ ...base, label: `${label} ${edge}`, edge, band,
      y: keep.map(i => envelope[edge][i]) }));
  });
}
