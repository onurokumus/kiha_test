// Shared DSP-filter UI state + spec builder. Filter state is PER-PLOT
// (App.plotFilters, one FilterUi per grid cell): each TimePlot/FullTestPlot
// toggles its own filter row via the ≈ header button and fetches its column's
// overlay from its own spec. There is no shared/broadcast filter control.

import { FilterKind, FilterSpec } from '../types';

export const FILTER_LABELS: Record<FilterKind, string> = {
  lowpass: 'low-pass',
  highpass: 'high-pass',
  bandpass: 'band-pass',
  bandstop: 'band-stop',
  moving_avg: 'moving avg',
  detrend: 'detrend',
};

/** Raw text of one plot's filter inputs (an entry of App.plotFilters). */
export interface FilterUi {
  kind: '' | FilterKind;
  order: string;
  f1: string;
  f2: string;
  winS: string;
}

export const DEFAULT_FILTER_UI: FilterUi = {
  kind: '',
  order: '4',
  f1: '',
  f2: '',
  winS: '1',
};

/** Valid spec, or null while 'none' is selected or params are incomplete. */
export const buildFilterSpec = (ui: FilterUi): FilterSpec | null => {
  if (!ui.kind) return null;
  if (ui.kind === 'detrend') return { kind: ui.kind };
  if (ui.kind === 'moving_avg') {
    const w = Number(ui.winS);
    return w > 0 ? { kind: ui.kind, windowS: w } : null;
  }
  const o = Math.min(Math.max(Math.round(Number(ui.order) || 4), 1), 10);
  const a = Number(ui.f1);
  if (!(a > 0)) return null;
  if (ui.kind === 'lowpass' || ui.kind === 'highpass')
    return { kind: ui.kind, order: o, f1: a };
  const b = Number(ui.f2);
  return b > a ? { kind: ui.kind, order: o, f1: a, f2: b } : null;
};
