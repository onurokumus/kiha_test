import { AnalysisSession, normalizeAnalysisSession } from './analysisSession';
import { validAxisRange } from '../utils/timePlotRanges';

export const MAX_SESSION_FILE_BYTES = 2 * 1024 * 1024;
export interface SessionFile {
  name: string;
  savedAt: string | null;
  session: AnalysisSession;
}
const object = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === 'object' && !Array.isArray(v);
const fail = (field: string): never => {
  throw new Error(
    `Invalid session ${field}. Choose a session JSON file saved by this application.`
  );
};

/** File import is stricter than best-effort recovery of old browser storage.
 * Optional v1 additions may be absent; supplied fields must have valid shapes. */
export function parseSessionFile(text: string): SessionFile {
  if (new Blob([text]).size > MAX_SESSION_FILE_BYTES)
    throw new Error('Session files must be 2 MB or smaller.');
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new Error('This file is not valid JSON.');
  }
  if (!object(raw)) return fail('document');
  let name = 'Imported session',
    savedAt: string | null = null;
  if ('format' in raw) {
    if (raw.format !== 'ptt-analysis-session') return fail('format');
    if (raw.version !== 1) throw new Error('This session file version is not supported.');
    if (typeof raw.name !== 'string' || !raw.name.trim() || raw.name.length > 120)
      return fail('name');
    if (typeof raw.savedAt !== 'string' || !Number.isFinite(Date.parse(raw.savedAt)))
      return fail('save date');
    name = raw.name;
    savedAt = raw.savedAt;
    raw = raw.session;
  }
  if (!object(raw)) return fail('settings');
  if (raw.version !== 1) throw new Error('This session settings version is not supported.');
  for (const key of ['currentTest', 'viewMode', 'plotConfigs', 'selections', 'filterState'])
    if (!(key in raw)) return fail(key);
  const normalized = normalizeAnalysisSession(raw);
  // For known scalar/structured fields, normalization must not silently repair
  // malformed values. Missing additive fields and legacy filter units are valid.
  const strings = ['currentTest', 'xAxis', 'yAxis', 'specRpmCol'];
  for (const key of strings) if (key in raw && typeof raw[key] !== 'string') return fail(key);
  for (const key of [
    'axesUserSet',
    'plotsUserEdited',
    'specLogY',
    'annotationsVisible',
    'scatterCollapsed',
    'clusteringEnabled',
    'datasheetVisible',
    'showHorizontalErrorBars',
    'showVerticalErrorBars',
  ]) {
    if (key in raw && typeof raw[key] !== 'boolean') return fail(key);
  }
  const enums: Record<string, unknown[]> = {
    viewMode: ['tp', 'full', 'spectrum', 'xy'],
    fullPlotMode: ['auto', 'line', 'envelope'],
    specMode: ['fft', 'welch', 'waterfall'],
    waterfallWindow: [64,128,256,512,1024,2048,4096,8192,16384],
    waterfallOverlap: [0,25,50,75],
    specXAxis: ['hz', 'per_rev'],
    specSource: ['tp', 'full'],
    xySource: ['tp', 'full'],
    plotDensity: ['single', 'quad', 'nine'],
  };
  for (const [key, values] of Object.entries(enums))
    if (key in raw && !values.includes(raw[key])) return fail(key);
  for (const key of [
    'plotConfigs',
    'xyXCols',
    'xyYCols',
    'plotShowOriginal',
    'plotFilters',
    'timeYRanges',
  ]) {
    const items = raw[key];
    if (items === undefined) continue;
    if (!Array.isArray(items) || items.length > 9) return fail(key);
    if (
      ['plotConfigs', 'xyXCols', 'xyYCols'].includes(key) &&
      items.some((item) => typeof item !== 'string')
    )
      return fail(key);
    if (key === 'plotShowOriginal' && items.some((item) => typeof item !== 'boolean'))
      return fail(key);
    if (
      key === 'plotFilters' &&
      items.some(
        (item, i) =>
          !object(item) ||
          (item.kind !== undefined && item.kind !== normalized.plotFilters[i].kind) ||
          Object.entries(item).some(
            ([field, value]) =>
              !['window_s', 'max_spike_s'].includes(field) &&
              typeof value !== 'string' &&
              typeof value !== 'number'
          )
      )
    )
      return fail(key);
  }
  for (const key of ['mainZoom', 'timeZoom', 'fullRange']) {
    if (
      raw[key] != null &&
      (!Array.isArray(raw[key]) ||
        raw[key].some((n) => typeof n !== 'number' || !Number.isFinite(n)) ||
        normalized[key as 'mainZoom' | 'timeZoom' | 'fullRange'] === null)
    )
      return fail(key);
    if (
      Array.isArray(raw[key]) &&
      (!validAxisRange(raw[key].slice(0, 2)) ||
        (key === 'mainZoom' && !validAxisRange(raw[key].slice(2))))
    )
      return fail(key);
  }
  if ('expandedPlot' in raw && raw.expandedPlot !== null && normalized.expandedPlot === null)
    return fail('expandedPlot');
  if (
    'scatterRatio' in raw &&
    (typeof raw.scatterRatio !== 'number' || raw.scatterRatio !== normalized.scatterRatio)
  )
    return fail('scatterRatio');
  if (
    !Array.isArray(raw.selections) ||
    raw.selections.length !== normalized.selections.length ||
    raw.selections.some(
      (item) =>
        !object(item) ||
        typeof item.test !== 'string' ||
        typeof item.tpId !== 'number' ||
        !Number.isInteger(item.tpId) ||
        typeof item.hidden !== 'boolean' ||
        (item.color !== undefined &&
          (typeof item.color !== 'string' || !/^#[0-9a-f]{6}$/i.test(item.color)))
    )
  )
    return fail('selections');
  if (
    !object(raw.filterState) ||
    !Array.isArray(raw.filterState.tpKeys) ||
    !Array.isArray(raw.filterState.labels) ||
    !Array.isArray(raw.filterState.parameterFilters) ||
    [...raw.filterState.tpKeys, ...raw.filterState.labels].some((v) => typeof v !== 'string') ||
    raw.filterState.tpKeys.length > 1000 ||
    raw.filterState.labels.length > 1000 ||
    raw.filterState.parameterFilters.some(
      (v) =>
        !object(v) ||
        typeof v.column !== 'string' ||
        !['min', 'max', 'mean', 'any'].includes(String(v.mode)) ||
        [v.min, v.max].some((n) => n !== null && (typeof n !== 'number' || !Number.isFinite(n)))
    )
  )
    return fail('filters');
  if (
    raw.sources !== undefined &&
    (!Array.isArray(raw.sources) ||
      raw.sources.length > 1000 ||
      raw.sources.some(
        (v) =>
          !object(v) ||
          typeof v.name !== 'string' ||
          (v.id !== null &&
            (typeof v.id !== 'string' ||
              !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v.id))) ||
          (v.revision !== undefined && typeof v.revision !== 'string') ||
          !Array.isArray(v.test_points) ||
          v.test_points.some(
            (p) =>
              !object(p) ||
              typeof p.id !== 'number' ||
              !Number.isInteger(p.id) ||
              typeof p.revision !== 'string'
          )
      ))
  )
    return fail('source references');
  if (raw.plotViewports !== undefined) {
    if (!object(raw.plotViewports)) return fail('plot ranges');
    for (const kind of ['spectrum', 'xy'] as const) {
      const items = raw.plotViewports[kind];
      if (
        !Array.isArray(items) ||
        items.length > 9 ||
        items.some((item, i) => item !== null && !normalized.plotViewports[kind][i])
      )
        return fail(`${kind} ranges`);
    }
  }
  if (
    Array.isArray(raw.timeYRanges) &&
    raw.timeYRanges.some((v, i) => v !== null && !normalized.timeYRanges[i])
  )
    return fail('time Y ranges');
  return { name, savedAt, session: normalized };
}

export function downloadSessionFile(name: string, session: AnalysisSession): void {
  const label = name.trim();
  if (!label || label.length > 120) throw new Error('Enter a session name of 1–120 characters.');
  const text = JSON.stringify(
    {
      format: 'ptt-analysis-session',
      version: 1,
      name: label,
      savedAt: new Date().toISOString(),
      session,
    },
    null,
    2
  );
  if (new Blob([text]).size > MAX_SESSION_FILE_BYTES)
    throw new Error('This session exceeds the 2 MB file limit.');
  const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  const anchor = document.createElement('a');
  anchor.href = url;
  const filename = [...label]
    .map((char) => (char.charCodeAt(0) < 32 || /[<>:"/\\|?*]/.test(char) ? '_' : char))
    .join('');
  anchor.download = `${filename.replace(/[. ]+$/, '') || 'analysis'}.ptt-session.json`;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
