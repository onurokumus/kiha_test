// Shared DSP-filter UI state + spec builder. Filter state is PER-PLOT
// (App.plotFilters, one FilterUi per grid cell): each TimePlot/FullTestPlot
// toggles its own filter row via the ≈ header button and fetches its column's
// processed trace from its own spec. There is no shared/broadcast control.

import { FilterKind, FilterSpec } from '../types';

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
    const windowMs = Number(ui.despikeWindowMs);
    const maxSpikeMs = Number(ui.maxSpikeMs);
    const threshold = Number(ui.threshold);
    const absFloor = Number(ui.absFloor);
    if (
      !Number.isFinite(windowMs) ||
      !Number.isFinite(maxSpikeMs) ||
      !Number.isFinite(threshold) ||
      !Number.isFinite(absFloor) ||
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
    const w = Number(ui.winS);
    return w > 0 ? { kind: ui.kind, windowS: w } : null;
  }
  const o = Math.min(Math.max(Math.round(Number(ui.order) || 4), 1), 10);
  const a = Number(ui.f1);
  if (!(a > 0)) return null;
  if (ui.kind === 'lowpass' || ui.kind === 'highpass') return { kind: ui.kind, order: o, f1: a };
  const b = Number(ui.f2);
  return b > a ? { kind: ui.kind, order: o, f1: a, f2: b } : null;
};
