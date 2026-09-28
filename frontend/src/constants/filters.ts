// Shared DSP-filter UI state + spec builder. Filter state is PER-PLOT
// (App.plotFilters, one FilterUi per grid cell): each TimePlot/FullTestPlot
// toggles its own filter row via the ≈ header button and fetches its column's
// processed trace from its own spec. There is no shared/broadcast control.

import { FilterKind, FilterSpec } from '../types';
import { parseFiniteNumber } from '../utils/numericField';

export const FILTER_LABELS: Record<FilterKind, string> = {
  lowpass: 'Low-pass',
  highpass: 'High-pass',
  bandpass: 'Band-pass',
  bandstop: 'Band-stop',
  moving_avg: 'Moving average',
  despike: 'Despike',
  detrend: 'Detrend',
};

/** Raw text of one plot's filter inputs (an entry of App.plotFilters). */
export interface FilterUi {
  kind: '' | FilterKind;
  order: string;
  f1: string;
  f2: string;
  winS: string;
  despikeWindowMs: string;
  maxSpikeMs: string;
  threshold: string;
  absFloor: string;
  replacement: 'linear' | 'median';
}

export const DEFAULT_FILTER_UI: FilterUi = {
  kind: '',
  order: '4',
  f1: '',
  f2: '',
  winS: '1',
  despikeWindowMs: '25',
  maxSpikeMs: '10',
  threshold: '3.5',
  absFloor: '0',
  replacement: 'linear',
};

/** Valid spec, or null while 'none' is selected or params are incomplete. */
export const buildFilterSpec = (ui: FilterUi): FilterSpec | null => {
  if (!ui.kind) return null;
  if (ui.kind === 'detrend') return { kind: ui.kind };
  if (ui.kind === 'despike') {
    const windowMs = parseFiniteNumber(ui.despikeWindowMs);
    const maxSpikeMs = parseFiniteNumber(ui.maxSpikeMs);
    const threshold = parseFiniteNumber(ui.threshold);
    const absFloor = parseFiniteNumber(ui.absFloor);
    if (
      windowMs === null ||
      maxSpikeMs === null ||
      threshold === null ||
      absFloor === null ||
      windowMs <= 0 ||
      maxSpikeMs <= 0 ||
      windowMs <= 2 * maxSpikeMs ||
      threshold <= 0 ||
      absFloor < 0
    ) {
      return null;
    }
    return {
      kind: ui.kind,
      windowS: windowMs / 1000,
      maxSpikeS: maxSpikeMs / 1000,
      threshold,
      absFloor,
      replacement: ui.replacement,
    };
  }
  if (ui.kind === 'moving_avg') {
    const w = parseFiniteNumber(ui.winS);
    return w !== null && w > 0 ? { kind: ui.kind, windowS: w } : null;
  }
  const o = parseFiniteNumber(ui.order);
  const a = parseFiniteNumber(ui.f1);
  if (o === null || !Number.isInteger(o) || o < 1 || o > 10 || a === null || !(a > 0)) return null;
  if (ui.kind === 'lowpass' || ui.kind === 'highpass') return { kind: ui.kind, order: o, f1: a };
  const b = parseFiniteNumber(ui.f2);
  return b !== null && b > a ? { kind: ui.kind, order: o, f1: a, f2: b } : null;
};

/** One plot filter must be valid for every visible source that supplies its
 * variable. Unknown sample rates retain server validation; never infer a rate
 * from display-decimated trace spacing or silently clamp a cutoff. */
export function filterForSampleRates(spec: FilterSpec | null, rates: readonly (number | null | undefined)[]) {
  const known = rates.filter((rate): rate is number => typeof rate === 'number' && Number.isFinite(rate) && rate > 0);
  const fs = known.length ? Math.min(...known) : null;
  if (spec && fs !== null && ['lowpass', 'highpass', 'bandpass', 'bandstop'].includes(spec.kind)) {
    const cutoffs = spec.kind === 'bandpass' || spec.kind === 'bandstop' ? [spec.f1, spec.f2] : [spec.f1];
    if (cutoffs.some(cutoff => typeof cutoff === 'number' && cutoff >= fs / 2)) return { fs, spec: null };
  }
  return { fs, spec };
}
