// Shared DSP-filter UI state + spec builder. The filter controls live in the
// mode bar (SelectedPointsPanel) and apply to ALL full-test plots at once;
// each FullTestPlot fetches its own column's overlay from the shared spec.

import { FilterKind, FilterSpec } from '../types';

export const FILTER_LABELS: Record<FilterKind, string> = {
  lowpass: 'low-pass',
  highpass: 'high-pass',
  bandpass: 'band-pass',
  bandstop: 'band-stop',
  moving_avg: 'moving avg',
  detrend: 'detrend',
};

/** Raw text of the filter inputs in the mode bar (App state). */
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
