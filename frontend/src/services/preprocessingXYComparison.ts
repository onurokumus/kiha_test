import { getJson } from './api';
import type { PreprocessingSource } from './preprocessing';
import type { ComparisonSamples } from './preprocessingComparison';
import { validAxisRange } from '../utils/timePlotRanges';

export interface ComparisonXYPairs { x: ComparisonSamples; y: ComparisonSamples }
export interface ComparisonXYSummary {
  finite_pairs: number;
  missing_pairs: number;
  native_finite_pairs: number;
  native_missing_pairs: number;
}
export interface PreprocessingXYComparisonData {
  source: PreprocessingSource;
  x: string;
  y: string;
  mode: 'raw' | 'sampled';
  stride: number;
  n_raw: number;
  n_sampled: number;
  i0: number;
  i1: number;
  indices: number[];
  fallback_indices: number[];
  t: number[];
  original: ComparisonXYPairs;
  filtered: ComparisonXYPairs;
  range: { start: number; end: number };
  summary: { original: ComparisonXYSummary; filtered: ComparisonXYSummary };
  gaps: { start: number; end: number }[];
  warnings: string[];
}

/** XY must use actual row pairs. Time envelopes cannot be turned into a scatter. */
export function validatePreprocessingXYComparison(
  data: PreprocessingXYComparisonData, source: PreprocessingSource, x: string, y: string,
): PreprocessingXYComparisonData {
  const fail = () => { throw new Error('The XY comparison does not match the saved data. Reload saved data and try again.'); };
  if (!data || data.source?.id !== source.id || data.source?.revision !== source.revision ||
      data.source?.name !== source.name || data.x !== x || data.y !== y ||
      !['raw', 'sampled'].includes(data.mode) ||
      ![data.i0, data.i1, data.n_raw, data.n_sampled, data.stride].every(Number.isSafeInteger) ||
      data.i0 < 0 || data.n_raw < 0 || data.i1 - data.i0 !== data.n_raw ||
      data.stride < 1 || data.n_sampled < 0 || data.n_sampled > Math.min(12000, data.n_raw) ||
      data.mode === 'raw' && (data.stride !== 1 || data.n_sampled !== data.n_raw) ||
      data.mode === 'sampled' && data.stride <= 1 ||
      !Array.isArray(data.t) || data.t.length !== data.n_sampled ||
      !data.t.every((value, index, values) => Number.isFinite(value) && (!index || value > values[index - 1])) ||
      !Array.isArray(data.indices) || data.indices.length !== data.n_sampled ||
      !data.indices.every((value, index, values) => Number.isSafeInteger(value) && value >= data.i0 && value < data.i1 &&
        (!index || value > values[index - 1])) ||
      !Array.isArray(data.fallback_indices) || data.fallback_indices.length > 2 ||
      !data.fallback_indices.every(value => Number.isSafeInteger(value) && data.indices.includes(value)) ||
      !data.indices.every(value => (value - data.i0) % data.stride === 0 || data.fallback_indices.includes(value)) ||
      !data.range || !validAxisRange([data.range.start, data.range.end]) ||
      !Array.isArray(data.warnings) || !data.warnings.every(warning => typeof warning === 'string')) fail();
  for (const kind of ['original', 'filtered'] as const) {
    const pair = data[kind], summary = data.summary?.[kind];
    if (!pair || !summary) fail();
    for (const values of [pair.x, pair.y]) {
      if (!Array.isArray(values) || values.length !== data.n_sampled ||
          !values.every(value => value === null || typeof value === 'number' && Number.isFinite(value))) fail();
    }
    if (!pair.x.every((value, index) => (value === null) === (pair.y[index] === null))) fail();
    const finite = pair.x.filter(value => value !== null).length;
    if (![summary.finite_pairs, summary.missing_pairs, summary.native_finite_pairs, summary.native_missing_pairs]
      .every(value => Number.isSafeInteger(value) && value >= 0) ||
      summary.finite_pairs !== finite || summary.missing_pairs + finite !== data.n_sampled ||
      summary.native_finite_pairs + summary.native_missing_pairs !== data.n_raw ||
      summary.native_finite_pairs < finite || summary.native_missing_pairs < summary.missing_pairs) fail();
  }
  return data;
}

export async function fetchPreprocessingXYComparison(
  name: string, source: PreprocessingSource, x: string, y: string,
  range: [number, number] | null, signal?: AbortSignal,
): Promise<PreprocessingXYComparisonData> {
  const query = new URLSearchParams({ x, y, source_id: source.id, source_revision: source.revision, max_points: '6000' });
  if (range) { query.set('t0', String(range[0])); query.set('t1', String(range[1])); }
  const data = await getJson<PreprocessingXYComparisonData>(`/tests/${encodeURIComponent(name)}/preprocess/compare/xy?${query}`, signal);
  return validatePreprocessingXYComparison(data, source, x, y);
}

/** Inspect the other version at the same row, never its independently nearest point. */
export function comparisonXYCursorValues(data: PreprocessingXYComparisonData, index: number | null) {
  if (index === null || !Number.isSafeInteger(index) || index < 0 || index >= data.n_sampled) return null;
  const difference = (axis: 'x' | 'y') => {
    const original = data.original[axis][index], filtered = data.filtered[axis][index];
    const value = original === null || filtered === null ? null : filtered - original;
    return value !== null && Number.isFinite(value) ? value : null;
  };
  return { index: data.indices[index], time: data.t[index],
    original: { x: data.original.x[index], y: data.original.y[index] },
    filtered: { x: data.filtered.x[index], y: data.filtered.y[index] },
    difference: { x: difference('x'), y: difference('y') } };
}
