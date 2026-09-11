/** Keep loaded response metadata, never large display arrays or fresh UI state. */
export function loadedAnalysis(response: object | undefined | null): Record<string, unknown> {
  if (!response) return { status: 'unavailable_legacy_response' };
  const record = response as Record<string, unknown>;
  const metadata = Object.fromEntries(Object.entries(record).filter(([key]) =>
    !['t', 'relative_t', 'series', 'freqs', 'mag', 'x', 'y', 'sample_indices'].includes(key)));
  return { ...metadata, source_context_status: record.analysis ? 'loaded_with_values' : 'unavailable_legacy_response' };
}

export function imageMetadata(plots: Record<string, unknown>[], layout: '2x2' | '3x3' | null = null) {
  // Clone before encoding yields so later UI changes cannot alter this record.
  return structuredClone({ schema: 'kiha-analysis-v1', created_at_utc: new Date().toISOString(),
    format: 'png', layout, plots,
    limitations: ['Image captures the loaded reduced display; CSV is calculated from current stored data.',
      'Missing legacy context and unrecorded units/history are unavailable, not inferred.'] });
}
