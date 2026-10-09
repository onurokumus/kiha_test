import { API_BASE, getJson } from './api';
import { buildFilterSpec, DEFAULT_FILTER_UI, FILTER_LABELS, type FilterUi } from '../constants/filters';
import type { FilterKind, FilterSpec, TestMeta } from '../types';

export interface PreprocessingSource { id: string; revision: string; name: string }
export interface PreprocessingFilter {
  kind: FilterKind;
  order?: number;
  f1?: number | null;
  f2?: number | null;
  window_s?: number | null;
  max_spike_s?: number;
  threshold?: number;
  abs_floor?: number;
  replacement?: 'linear' | 'median';
}
export interface PreprocessingParameter { column: string; filter: PreprocessingFilter }
export interface PreprocessingProvenance {
  version: 1 | 2;
  mode?: 'in_place';
  source: PreprocessingSource;
  filters: PreprocessingParameter[];
  created_at: string;
  completed_at?: string;
  seconds?: number;
  method: 'whole_native_recording';
  warnings: string[];
  original_raw_available?: boolean;
}
export interface PreprocessingOperation {
  request_id: string;
  state: 'running' | 'completed' | 'failed';
  error?: string | null;
}
export interface PreprocessingProgress { stage: string; completed_columns: number; total_columns: number }
export interface PreprocessingSnapshot {
  source: PreprocessingSource;
  meta: TestMeta;
  preprocessing: PreprocessingProvenance | null;
  legacy_preprocessing?: PreprocessingProvenance | null;
  preprocessing_operation?: PreprocessingOperation | null;
  max_samples: number;
}
export interface PreprocessingRequest {
  request_id: string;
  source_id: string;
  source_revision: string;
  filters: PreprocessingParameter[];
}

/** Plain HTTP deployments support getRandomValues, but not randomUUID. */
export function newPreprocessingRequestId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function fetchPreprocessing(name: string, signal?: AbortSignal): Promise<PreprocessingSnapshot> {
  return getJson(`/tests/${encodeURIComponent(name)}/preprocess`, signal);
}

export async function applyPreprocessing(source: string, request: PreprocessingRequest): Promise<{ name: string; status: string; preprocessing_operation?: PreprocessingOperation }> {
  const response = await fetch(`${API_BASE}/tests/${encodeURIComponent(source)}/preprocess`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') message = body.detail;
    } catch { /* Keep the HTTP error when the server did not return JSON. */ }
    throw new Error(message);
  }
  return response.json();
}

/** Only the fields used by this filter are persisted; plot UI state is never sent. */
export function serializePreprocessingFilter(spec: FilterSpec): PreprocessingFilter {
  switch (spec.kind) {
    case 'detrend': return { kind: spec.kind };
    case 'moving_avg': return { kind: spec.kind, window_s: spec.windowS };
    case 'despike': return { kind: spec.kind, window_s: spec.windowS, max_spike_s: spec.maxSpikeS,
      threshold: spec.threshold, abs_floor: spec.absFloor, replacement: spec.replacement };
    case 'bandpass': case 'bandstop': return { kind: spec.kind, order: spec.order, f1: spec.f1, f2: spec.f2 };
    default: return { kind: spec.kind, order: spec.order, f1: spec.f1 };
  }
}

export function preprocessingFilterUi(filter: PreprocessingFilter): FilterUi {
  const text = (value: number | null | undefined, fallback: string) => value == null ? fallback : String(value);
  return { ...DEFAULT_FILTER_UI, kind: filter.kind,
    order: text(filter.order, DEFAULT_FILTER_UI.order), f1: text(filter.f1, ''), f2: text(filter.f2, ''),
    winS: text(filter.window_s, DEFAULT_FILTER_UI.winS),
    despikeWindowMs: text(filter.window_s == null ? undefined : filter.window_s * 1000, DEFAULT_FILTER_UI.despikeWindowMs),
    maxSpikeMs: text(filter.max_spike_s === undefined ? undefined : filter.max_spike_s * 1000, DEFAULT_FILTER_UI.maxSpikeMs),
    threshold: text(filter.threshold, DEFAULT_FILTER_UI.threshold), absFloor: text(filter.abs_floor, DEFAULT_FILTER_UI.absFloor),
    replacement: filter.replacement ?? DEFAULT_FILTER_UI.replacement,
  };
}

/** Validate against native sample metadata, never a display-decimated plot. */
export function preprocessingFilterError(ui: FilterUi, fs: number, nRows: number): string {
  if (!ui.kind) return '';
  if (!Number.isFinite(fs) || fs <= 0) return 'A valid native sample rate is required.';
  const spec = buildFilterSpec(ui);
  if (!spec) return 'Complete the filter settings with valid values.';
  if (['lowpass', 'highpass', 'bandpass', 'bandstop'].includes(spec.kind)) {
    if ([spec.f1, spec.f2].some(value => value !== undefined && value >= fs / 2))
      return `Cutoff frequencies must be below ${Number((fs / 2).toPrecision(7))} Hz (Nyquist).`;
    const sections = spec.kind === 'bandpass' || spec.kind === 'bandstop' ? spec.order! : Math.ceil(spec.order! / 2);
    if (nRows <= 3 * (2 * sections + 1)) return 'The recording is too short for this filter order.';
  }
  if (spec.kind === 'moving_avg' && Math.max(1, roundNativeSamples(spec.windowS! * fs)) >= nRows)
    return 'The averaging window must be shorter than the recording.';
  if (spec.kind === 'despike') {
    let window = Math.max(3, roundNativeSamples(spec.windowS! * fs));
    if (window % 2 === 0) window++;
    const spike = Math.max(1, Math.floor(spec.maxSpikeS! * fs + 1e-12));
    if (window <= 2 * spike) return 'At this sample rate, the window must cover more than twice the maximum spike samples.';
    if (window > 100001) return 'The despike window cannot exceed 100,001 native samples.';
    if (window >= nRows) return 'The despike window must be shorter than the recording.';
  }
  return '';
}

/** Match Python's tie-to-even rounding used by the DSP sample-window resolver. */
function roundNativeSamples(value: number): number {
  const lower = Math.floor(value);
  return value - lower === .5 ? lower + (lower % 2) : Math.round(value);
}

/** Compare meaningful native settings, ignoring server defaults and list order. */
export function preprocessingFiltersEqual(left: readonly PreprocessingParameter[], right: readonly PreprocessingParameter[]): boolean {
  if (left.length !== right.length) return false;
  return left.every(entry => {
    const saved = right.find(item => item.column === entry.column);
    if (!saved || saved.filter.kind !== entry.filter.kind) return false;
    const requestedSpec = buildFilterSpec(preprocessingFilterUi(entry.filter));
    const savedSpec = buildFilterSpec(preprocessingFilterUi(saved.filter));
    return !!savedSpec && !!requestedSpec && JSON.stringify(serializePreprocessingFilter(savedSpec)) === JSON.stringify(serializePreprocessingFilter(requestedSpec));
  });
}

/** Only adopt this exact accepted operation, including restore requests. */
export function matchesPreprocessingRequest(operation: PreprocessingOperation | null | undefined, request: PreprocessingRequest): boolean {
  return !!operation && operation.request_id === request.request_id;
}

export function describePreprocessingFilter(filter: PreprocessingFilter): string {
  const number = (value: number | null | undefined) => value == null ? '—' : Number(value.toPrecision(7)).toString();
  const label = FILTER_LABELS[filter.kind] ?? filter.kind;
  if (filter.kind === 'detrend') return `${label} · linear`;
  if (filter.kind === 'moving_avg') return `${label} · ${number(filter.window_s)} s`;
  if (filter.kind === 'despike') return `${label} · ${number((filter.window_s ?? .025) * 1000)} ms window · ${number((filter.max_spike_s ?? .01) * 1000)} ms max spike · ${number(filter.threshold ?? 3.5)} MAD · min jump ${number(filter.abs_floor ?? 0)} · ${filter.replacement === 'median' ? 'local median' : 'linear'}`;
  return `${label} · ${number(filter.f1)}${filter.kind === 'bandpass' || filter.kind === 'bandstop' ? `–${number(filter.f2)}` : ''} Hz · order ${filter.order ?? 4}`;
}
