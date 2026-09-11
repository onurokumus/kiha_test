/**
 * API layer for the PTT FastAPI backend.
 *
 * In dev, '/api' is proxied to http://127.0.0.1:8000 by vite. The production
 * build uses Vite's /ptt/ base, so this becomes same-origin '/ptt/api' for
 * nginx. VITE_API_BASE can override it for a deliberately separate API host.
 * Gotcha: /filter takes `cols`+`type`, /spectrum takes `col`+`mode`; POST
 * /split/auto returns a BARE LIST proposal that must be wrapped in the
 * TestPointsFile shape before PUT /testpoints persists it.
 */
import {
  DataWindow,
  EditOps,
  FilteredWindow,
  FilterSpec,
  FormulaPreview,
  FormulaRecipe,
  FormulaRecipeList,
  FormulaSpec,
  IdCandidate,
  AutoSplitOptions,
  AutoSplitProposal,
  AnyPlotExportRequest,
  PlotExportBundleRequest,
  SpectrumData,
  TestInfo,
  TrashEntry,
  TestMeta,
  TestPoint,
  TestPointsFile,
  TpStat,
  TpTraceResponse,
  WindowDisplayMode,
  XYData,
} from '../types';
import { fetchExportFile } from './exportProgress';
import type { AppSettings } from '../constants/settings';
import type { AnnotationDocument, TimeAnnotation } from '../utils/plotAnnotations';
import type { ComponentCatalog, ComponentIds, ComponentKind, HardwareComponent } from '../utils/components';
import type { AnalysisSourceCatalog } from './sessionSources';

export const API_BASE = (
  import.meta.env.VITE_API_BASE || `${import.meta.env.BASE_URL}api`
).replace(/\/+$/, '');

export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError';
}

// A GET is just a sendJson with no method/body — delegate so the error-detail
// extraction lives in one place (sendJson is hoisted, defined below).
export function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return sendJson<T>(path, { signal });
}

export async function fetchTests(): Promise<TestInfo[]> {
  return getJson<TestInfo[]>('/tests');
}

export async function fetchAnalysisSources(): Promise<AnalysisSourceCatalog> {
  const catalog = await getJson<AnalysisSourceCatalog>('/analysis-sources');
  if (catalog.version !== 1 || !Array.isArray(catalog.sources)) throw new Error('Unsupported source catalog. Update the application and retry.');
  return catalog;
}

/** Shared page defaults. Null means the server has never been configured. */
export async function fetchDefaultSettings(
  signal?: AbortSignal
): Promise<AppSettings | null> {
  const response = await getJson<{ settings: AppSettings | null }>(
    '/settings/defaults',
    signal
  );
  return response.settings;
}

/** Atomically replace the server-wide baseline used by browsers without a
 * personal settings override. */
export async function putDefaultSettings(settings: AppSettings): Promise<AppSettings> {
  const response = await sendJson<{ settings: AppSettings }>('/settings/defaults', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  return response.settings;
}

export async function fetchMeta(name: string, signal?: AbortSignal, expectedSourceId?: string | null): Promise<TestMeta> {
  return getJson<TestMeta>(`/tests/${encodeURIComponent(name)}${expectedSourceId ? `?expected_source_id=${encodeURIComponent(expectedSourceId)}` : ''}`, signal);
}

export function fetchAnnotations(name: string, signal?: AbortSignal): Promise<AnnotationDocument> {
  return getJson(`/tests/${encodeURIComponent(name)}/annotations`, signal);
}

export function saveAnnotations(document: AnnotationDocument, annotations: TimeAnnotation[]): Promise<AnnotationDocument> {
  return sendJson(`/tests/${encodeURIComponent(document.test)}/annotations`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_revision: document.revision,
      expected_data_bounds: document.data_bounds, annotations }),
  });
}

export async function fetchTestPoints(name: string, expectedSourceId?: string | null): Promise<TestPointsFile> {
  return getJson<TestPointsFile>(
    `/tests/${encodeURIComponent(name)}/testpoints${expectedSourceId ? `?expected_source_id=${encodeURIComponent(expectedSourceId)}` : ''}`
  );
}

export async function fetchTpStats(
  name: string,
  col: string
): Promise<TpStat[]> {
  return getJson<TpStat[]>(
    `/tests/${encodeURIComponent(name)}/tp_stats?col=${encodeURIComponent(col)}`
  );
}

/** Force a fresh recompute of a test's cached test-point averages. The
 *  backend swaps the result in atomically, so this never leaves the stats
 *  unavailable; returns how many cached columns were recomputed. */
export async function rebuildTpStats(
  name: string
): Promise<{ name: string; columns_recomputed: number }> {
  return sendJson(`/tests/${encodeURIComponent(name)}/tp_stats/rebuild`, {
    method: 'POST',
  });
}

/** One test point's traces for several columns, relative time from TP start.
 *  Long points come back as an ordered min/max trace (spikes survive). */
export async function fetchTestPointTrace(
  name: string,
  tpId: number,
  cols: string[],
  maxPoints = 1500
): Promise<TpTraceResponse> {
  const colsParam = encodeURIComponent(cols.join(','));
  return getJson<TpTraceResponse>(
    `/tests/${encodeURIComponent(name)}/testpoints/${tpId}/data` +
      `?cols=${colsParam}&max_points=${maxPoints}`
  );
}

/** Windowed full-test read. Auto chooses line/envelope from the visible sample
 *  count; callers can force either representation. Re-fetched on zoom/pan. */
export async function fetchWindow(
  name: string,
  cols: string[],
  t0: number | null,
  t1: number | null,
  px: number,
  signal?: AbortSignal,
  display: WindowDisplayMode = 'auto'
): Promise<DataWindow> {
  const params = new URLSearchParams({
    cols: cols.join(','),
    px: String(Math.max(200, Math.round(px))),
    display,
  });
  if (t0 !== null) params.set('t0', String(t0));
  if (t1 !== null) params.set('t1', String(t1));
  return getJson<DataWindow>(
    `/tests/${encodeURIComponent(name)}/data?${params.toString()}`,
    signal
  );
}

async function sendJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      // non-JSON error body; keep the status text
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function fetchSplitCandidates(name: string, signal?: AbortSignal): Promise<IdCandidate[]> {
  return getJson<IdCandidate[]>(
    `/tests/${encodeURIComponent(name)}/split/candidates`, signal
  );
}

export async function previewAutoSplit(
  name: string,
  options: AutoSplitOptions,
  signal?: AbortSignal
): Promise<AutoSplitProposal> {
  return sendJson(`/tests/${encodeURIComponent(name)}/split/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(options),
    signal,
  });
}

/** Proposal only — the backend does NOT persist; PUT the result to save. */
export async function autoSplit(
  name: string,
  col: string,
  ignoreZero: boolean,
  minLenS: number
): Promise<TestPoint[]> {
  const params = new URLSearchParams({
    col,
    ignore_zero: String(ignoreZero),
    min_len_s: String(minLenS),
  });
  return sendJson(`/tests/${encodeURIComponent(name)}/split/auto?${params}`, {
    method: 'POST',
  });
}

export async function putTestPoints(
  name: string,
  payload: TestPointsFile
): Promise<{ ok: boolean; n: number }> {
  return sendJson(`/tests/${encodeURIComponent(name)}/testpoints`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function uploadTestPoints(
  name: string,
  file: File
): Promise<unknown> {
  const fd = new FormData();
  fd.append('file', file);
  return sendJson(`/tests/${encodeURIComponent(name)}/testpoints/upload`, {
    method: 'POST',
    body: fd,
  });
}

export async function fetchSpectrum(
  name: string,
  col: string,
  mode: 'fft' | 'welch',
  t0: number | null,
  t1: number | null,
  rpmCol: string | null = null,
  signal?: AbortSignal,
  tpId?: number
): Promise<SpectrumData> {
  const params = new URLSearchParams({ col, mode });
  if (tpId !== undefined) params.set('tp_id', String(tpId));
  else {
    if (t0 !== null) params.set('t0', String(t0));
    if (t1 !== null) params.set('t1', String(t1));
  }
  if (rpmCol) params.set('rpm_col', rpmCol);
  return getJson<SpectrumData>(
    `/tests/${encodeURIComponent(name)}/spectrum?${params}`,
    signal
  );
}

/** Server-side filtered window, using the same requested representation as
 *  fetchWindow so both arrays align. Query gotcha: filter kind is `type`. */
export async function fetchFiltered(
  name: string,
  cols: string[],
  spec: FilterSpec,
  t0: number | null,
  t1: number | null,
  px: number,
  signal?: AbortSignal,
  display: WindowDisplayMode = 'auto',
  tpId?: number
): Promise<FilteredWindow> {
  const params = new URLSearchParams({
    cols: cols.join(','),
    type: spec.kind,
    px: String(Math.max(200, Math.round(px))),
    display,
  });
  if (spec.order !== undefined) params.set('order', String(spec.order));
  if (spec.f1 !== undefined) params.set('f1', String(spec.f1));
  if (spec.f2 !== undefined) params.set('f2', String(spec.f2));
  if (spec.windowS !== undefined) params.set('window_s', String(spec.windowS));
  if (spec.maxSpikeS !== undefined) params.set('max_spike_s', String(spec.maxSpikeS));
  if (spec.threshold !== undefined) params.set('threshold', String(spec.threshold));
  if (spec.absFloor !== undefined) params.set('abs_floor', String(spec.absFloor));
  if (spec.replacement !== undefined) params.set('replacement', spec.replacement);
  if (tpId !== undefined) {
    params.set('tp_id', String(tpId));
  } else {
    if (t0 !== null) params.set('t0', String(t0));
    if (t1 !== null) params.set('t1', String(t1));
  }
  return getJson<FilteredWindow>(
    `/tests/${encodeURIComponent(name)}/filter?${params}`,
    signal
  );
}

/** Global component identities, independent of test folder names. */
export const fetchComponents = (signal?: AbortSignal): Promise<ComponentCatalog> => getJson('/components', signal);
export const createComponent = (kind: ComponentKind, name: string): Promise<HardwareComponent> =>
  sendJson('/components', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind, name }) });

/** Patch supplied fields; component changes require their loaded revision. */
export async function patchUserMeta(
  name: string,
  userMeta?: Record<string, string>,
  fields: { description?: string; notes?: string; components?: ComponentIds; expected_components_revision?: number;
    component_rpm_column?: string | null; expected_component_rpm_revision?: number } = {}
): Promise<TestMeta> {
  return sendJson(`/tests/${encodeURIComponent(name)}/meta`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_meta: userMeta, ...fields }),
  });
}

/** Destructive rebuild (rename/drop columns, trim, NaN policy).
 *  Returns immediately with status 'rebuilding'; poll the test list. */
export async function editTest(
  name: string,
  ops: EditOps
): Promise<{ name: string; status: string }> {
  return sendJson(`/tests/${encodeURIComponent(name)}/edit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(ops),
  });
}

/** Validate equations against a test and return a small full-resolution sample
 * without modifying the stored data. */
export async function previewFormulas(
  name: string,
  formulas: FormulaSpec[],
  sampleSize = 8
): Promise<FormulaPreview> {
  return sendJson(`/tests/${encodeURIComponent(name)}/formulas/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      formulas,
      sample_size: sampleSize,
    }),
  });
}

export async function fetchFormulaRecipes(): Promise<FormulaRecipeList> {
  return getJson<FormulaRecipeList>('/formula-recipes');
}

export async function saveFormulaRecipe(
  name: string,
  formulas: FormulaSpec[],
  description = ''
): Promise<FormulaRecipe> {
  return sendJson(`/formula-recipes/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ formulas, description }),
  });
}

export async function deleteFormulaRecipe(
  name: string
): Promise<{ ok: true; name: string }> {
  return sendJson(`/formula-recipes/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
}

/** Soft delete: the test moves to data/trash (restorable server-side). */
export async function deleteTest(name: string): Promise<unknown> {
  return sendJson(`/tests/${encodeURIComponent(name)}`, { method: 'DELETE' });
}

export function fetchTrash(signal?: AbortSignal): Promise<{ entries: TrashEntry[]; retention_seconds: number | null }> {
  return getJson('/trash', signal);
}

export function restoreTrash(id: string, name: string): Promise<{ ok: boolean; restored: string }> {
  return sendJson(`/trash/${encodeURIComponent(id)}/restore`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
  });
}

export function deleteTrash(ids: string[]): Promise<{ deleted_ids: string[]; failures: { id: string; error: string }[] }> {
  return sendJson('/trash', {
    method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }),
  });
}

export async function renameTest(
  name: string,
  newName: string
): Promise<{ ok: boolean; name: string }> {
  return sendJson(
    `/tests/${encodeURIComponent(name)}/rename?new_name=${encodeURIComponent(newName)}`,
    { method: 'POST' }
  );
}

/** Variable-vs-variable pairs over a time range, stride-decimated. */
export async function fetchXY(
  name: string,
  x: string,
  y: string,
  t0: number | null,
  t1: number | null,
  maxPts = 3000,
  signal?: AbortSignal,
  tpId?: number,
): Promise<XYData> {
  const params = new URLSearchParams({ x, y_col: y, max_pts: String(maxPts) });
  if (tpId !== undefined) params.set('tp_id', String(tpId));
  else {
    if (t0 !== null) params.set('t0', String(t0));
    if (t1 !== null) params.set('t1', String(t1));
  }
  return getJson<XYData>(
    `/tests/${encodeURIComponent(name)}/xy?${params}`,
    signal
  );
}

// -- CSV downloads (streamed by the backend; use as plain <a href> targets) --

/** Full-resolution single-plot export. The backend stages all sources before
 * responding; buffer the complete response before offering a browser file. */
export async function fetchPlotCsv(request: AnyPlotExportRequest, signal: AbortSignal): Promise<{ blob: Blob; filename: string }> {
  const description = 'kind' in request ? request.kind === 'spectrum' ? `${request.mode}_${request.axis}` : `vs_${request.x_column}_xy` : request.data;
  return fetchPlotExportFile(exportPath(request), serializePlotRequest(request), request.include_metadata ? 'application/zip' : 'text/csv',
    `${request.sources.length === 1 ? request.sources[0].test : 'multiple-sources'}_${request.column}_${description}.${request.include_metadata ? 'zip' : 'csv'}`, signal);
}

export async function fetchPlotCsvBundle(request: PlotExportBundleRequest, signal: AbortSignal): Promise<{ blob: Blob; filename: string }> {
  const path = request.plots[0] && exportPath(request.plots[0].request);
  if (!path || request.plots.some((entry) => exportPath(entry.request) !== path)) throw new Error('Export one plot mode at a time.');
  return fetchPlotExportFile(`${path}/bundle`, {
    ...request, plots: request.plots.map((entry) => ({ ...entry, request: serializePlotRequest(entry.request) })),
  }, 'application/zip', `plots_${request.layout}.zip`, signal);
}

function exportPath(request: AnyPlotExportRequest) {
  return 'kind' in request ? request.kind === 'xy' ? '/xy-export' : '/spectrum-export' : '/plot-export';
}

function serializePlotRequest(request: AnyPlotExportRequest) {
  if ('kind' in request) return request;
  const spec = request.filter;
  return { ...request, filter: spec ? {
      kind: spec.kind, order: spec.order, f1: spec.f1, f2: spec.f2,
      window_s: spec.windowS, max_spike_s: spec.maxSpikeS,
      threshold: spec.threshold, abs_floor: spec.absFloor, replacement: spec.replacement,
  } : null };
}

async function fetchPlotExportFile(path: string, request: unknown, contentType: string, fallback: string, signal: AbortSignal) {
  const { response, blob } = await fetchExportFile(API_BASE, path, JSON.stringify(request),
    'application/json', contentType, signal);
  const disposition = response.headers.get('content-disposition') ?? '';
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
  const plain = /filename="([^"]+)"/i.exec(disposition)?.[1];
  let filename = plain ?? fallback;
  if (encoded) { try { filename = decodeURIComponent(encoded); } catch { /* Use plain fallback. */ } }
  return { blob, filename };
}

/** The original uploaded CSV (raw.csv), kept for provenance. */
export function rawCsvUrl(name: string): string {
  return `${API_BASE}/tests/${encodeURIComponent(name)}/raw`;
}

/** Full-resolution CSV export of a test, optionally a [t0, t1] window
 *  and/or a column subset (time column is always included first). */
export function exportCsvUrl(
  name: string,
  opts: { cols?: string[]; t0?: number | null; t1?: number | null } = {}
): string {
  const params = new URLSearchParams();
  if (opts.cols?.length) params.set('cols', opts.cols.join(','));
  if (opts.t0 !== undefined && opts.t0 !== null) params.set('t0', String(opts.t0));
  if (opts.t1 !== undefined && opts.t1 !== null) params.set('t1', String(opts.t1));
  const q = params.toString();
  return `${API_BASE}/tests/${encodeURIComponent(name)}/export${q ? `?${q}` : ''}`;
}

/** Full-resolution TP CSV with test_point_id. Omit draft for exact saved bounds;
 *  explicit draft rows are half-open and never save/modify TP definitions. */
export function testPointCsvUrl(
  name: string,
  tpId: number,
  cols?: string[],
  draft?: { start_idx: number; end_idx: number }
): string {
  const params = new URLSearchParams();
  if (cols?.length) params.set('cols', cols.join(','));
  if (draft) {
    params.set('start_idx', String(draft.start_idx));
    params.set('end_idx', String(draft.end_idx));
  }
  const q = params.toString();
  return `${API_BASE}/tests/${encodeURIComponent(name)}/testpoints/${tpId}/export${q ? `?${q}` : ''}`;
}


/** Package the exact captured PNG and synchronous loaded-context snapshot. */
export async function fetchPlotImagePackage(image: Blob, metadata: Record<string, unknown>, filename: string, signal?: AbortSignal) {
  const encoded = JSON.stringify({ ...metadata, filename }) + '\n';
  if (new TextEncoder().encode(encoded).length > 2 * 1024 * 1024) {
    throw new Error('Analysis metadata exceeds 2 MiB. Select fewer plots or sources.');
  }
  const { blob } = await fetchExportFile(API_BASE, '/plot-image-export', new Blob([encoded, image]),
    'application/octet-stream', 'application/zip', signal ?? new AbortController().signal);
  return blob;
}
