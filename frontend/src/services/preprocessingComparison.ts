import { getJson } from './api';
import type { PreprocessingSource } from './preprocessing';
import { validAxisRange } from '../utils/timePlotRanges';

export type ComparisonSamples = (number | null)[];
export interface ComparisonEnvelope { min: ComparisonSamples; max: ComparisonSamples }
export interface PreprocessingComparisonData {
  source: PreprocessingSource;
  column: string;
  mode: 'raw' | 'envelope';
  level: number;
  n_raw: number;
  i0: number;
  i1: number;
  t: number[];
  original: ComparisonSamples | ComparisonEnvelope;
  filtered: ComparisonSamples | ComparisonEnvelope;
  range: { start: number; end: number };
  gaps: { start: number; end: number }[];
  summary: null | {
    finite_pairs: number;
    missing_pairs: number;
    rms_difference: number | null;
    max_abs_difference: number | null;
  };
  warnings: string[];
}

/** Both traces must come from the same guarded read and the same sample grid. */
export function validatePreprocessingComparison(
  data: PreprocessingComparisonData, source: PreprocessingSource, column: string,
): PreprocessingComparisonData {
  const fail = () => { throw new Error('The comparison does not match the saved data. Reload saved data and try again.'); };
  if (!data || data.source?.id !== source.id || data.source?.revision !== source.revision ||
      data.source?.name !== source.name || data.column !== column ||
      !['raw', 'envelope'].includes(data.mode) || !Array.isArray(data.t) ||
      !data.t.every((t, i, ts) => Number.isFinite(t) && (i === 0 || t > ts[i - 1])) ||
      !data.range || !validAxisRange([data.range.start, data.range.end])) fail();
  if (![data.n_raw, data.i0, data.i1, data.level].every(Number.isSafeInteger) || data.n_raw < 0 ||
      data.i0 < 0 || data.i1 - data.i0 !== data.n_raw || data.level < 1 ||
      data.mode === 'raw' && (data.level !== 1 || data.t.length !== data.n_raw) ||
      data.mode === 'envelope' && (data.level <= 1 || data.t.length > data.n_raw) ||
      !Array.isArray(data.warnings) || !data.warnings.every(warning => typeof warning === 'string')) fail();
  const validSamples = (values: unknown): values is ComparisonSamples => Array.isArray(values) &&
    values.length === data.t.length && values.every(value => value === null || typeof value === 'number' && Number.isFinite(value));
  for (const values of [data.original, data.filtered]) {
    if (data.mode === 'raw') { if (!validSamples(values)) fail(); }
    else {
      const envelope = values as ComparisonEnvelope;
      if (!envelope || !validSamples(envelope.min) || !validSamples(envelope.max)) fail();
      if (!envelope.min.every((min, index) => {
        const max = envelope.max[index];
        return min === null ? max === null : max !== null && min <= max;
      })) fail();
    }
  }
  if (data.mode === 'envelope') { if (data.summary !== null) fail(); }
  else {
    const summary = data.summary;
    if (!summary || ![summary.finite_pairs, summary.missing_pairs].every(Number.isSafeInteger) ||
        summary.finite_pairs < 0 || summary.missing_pairs < 0 ||
        summary.finite_pairs + summary.missing_pairs !== data.n_raw) fail();
    if ((summary!.rms_difference === null) !== (summary!.max_abs_difference === null)) fail();
    for (const value of [summary!.rms_difference, summary!.max_abs_difference]) {
      if (summary!.finite_pairs === 0 ? value !== null : value !== null && (typeof value !== 'number' || !Number.isFinite(value) || value < 0)) fail();
    }
  }
  return data;
}

export async function fetchPreprocessingComparison(
  name: string, source: PreprocessingSource, column: string,
  range: [number, number] | null, px: number, signal?: AbortSignal,
): Promise<PreprocessingComparisonData> {
  const query = new URLSearchParams({ column, source_id: source.id, source_revision: source.revision,
    px: String(Math.max(100, Math.min(4000, Math.round(px)))) });
  if (range) { query.set('t0', String(range[0])); query.set('t1', String(range[1])); }
  const data = await getJson<PreprocessingComparisonData>(`/tests/${encodeURIComponent(name)}/preprocess/compare?${query}`, signal);
  return validatePreprocessingComparison(data, source, column);
}

/** Pan stays inside the recording; oversize ranges become the complete record. */
export function constrainComparisonRange(
  requested: readonly [number, number], full: readonly [number, number],
): [number, number] | null {
  const span = requested[1] - requested[0], fullSpan = full[1] - full[0];
  if (!validAxisRange(requested) || !validAxisRange(full)) return null;
  if (span >= fullSpan * (1 - 1e-10)) return [full[0], full[1]];
  const start = Math.max(full[0], Math.min(requested[0], full[1] - span));
  return [start, start + span];
}

/** A bucket's independent extrema never describe an exact paired difference. */
export function comparisonCursorValues(data: PreprocessingComparisonData, index: number | null) {
  if (index === null || index < 0 || index >= data.t.length) return null;
  if (data.mode === 'envelope') {
    const original = data.original as ComparisonEnvelope, filtered = data.filtered as ComparisonEnvelope;
    return { time: data.t[index], original: [original.min[index], original.max[index]],
      filtered: [filtered.min[index], filtered.max[index]], difference: null };
  }
  const original = (data.original as ComparisonSamples)[index], filtered = (data.filtered as ComparisonSamples)[index];
  const difference = original === null || filtered === null ? null : filtered - original;
  return { time: data.t[index], original: [original], filtered: [filtered],
    difference: difference !== null && Number.isFinite(difference) ? difference : null };
}
