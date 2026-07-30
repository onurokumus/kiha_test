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
  SpectrumData,
  TestInfo,
  TestMeta,
  TestPoint,
  TestPointsFile,
  TpStat,
  TpTraceResponse,
  WindowDisplayMode,
  XYData,
} from '../types';

export const API_BASE = (
  import.meta.env.VITE_API_BASE || `${import.meta.env.BASE_URL}api`
).replace(/\/+$/, '');

export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError';
}

// A GET is just a sendJson with no method/body — delegate so the error-detail
// extraction lives in one place (sendJson is hoisted, defined below).
function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return sendJson<T>(path, { signal });
}

export async function fetchTests(): Promise<TestInfo[]> {
  return getJson<TestInfo[]>('/tests');
}

export async function fetchMeta(name: string): Promise<TestMeta> {
  return getJson<TestMeta>(`/tests/${encodeURIComponent(name)}`);
}

export async function fetchTestPoints(name: string): Promise<TestPointsFile> {
  return getJson<TestPointsFile>(
    `/tests/${encodeURIComponent(name)}/testpoints`
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

export async function fetchSplitCandidates(name: string): Promise<IdCandidate[]> {
  return getJson<IdCandidate[]>(
    `/tests/${encodeURIComponent(name)}/split/candidates`
  );
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
  signal?: AbortSignal
): Promise<SpectrumData> {
  const params = new URLSearchParams({ col, mode });
  if (t0 !== null) params.set('t0', String(t0));
  if (t1 !== null) params.set('t1', String(t1));
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
  display: WindowDisplayMode = 'auto'
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
  if (t0 !== null) params.set('t0', String(t0));
  if (t1 !== null) params.set('t1', String(t1));
  return getJson<FilteredWindow>(
    `/tests/${encodeURIComponent(name)}/filter?${params}`,
    signal
  );
}

/** Replace the free-form user_meta descriptors; returns the full meta. */
export async function patchUserMeta(
  name: string,
  userMeta: Record<string, string>
): Promise<TestMeta> {
  return sendJson(`/tests/${encodeURIComponent(name)}/meta`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_meta: userMeta }),
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
    body: JSON.stringify({ formulas, sample_size: sampleSize }),
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

/** Undo a soft delete. 404s once the trash copy has been purged (~1 h). */
export async function restoreTest(
  name: string
): Promise<{ ok: boolean; restored: string }> {
  return sendJson(`/tests/${encodeURIComponent(name)}/restore`, {
    method: 'POST',
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
  signal?: AbortSignal
): Promise<XYData> {
  const params = new URLSearchParams({ x, y, max_pts: String(maxPts) });
  if (t0 !== null) params.set('t0', String(t0));
  if (t1 !== null) params.set('t1', String(t1));
  return getJson<XYData>(
    `/tests/${encodeURIComponent(name)}/xy?${params}`,
    signal
  );
}

// -- CSV downloads (streamed by the backend; use as plain <a href> targets) --

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

/** CSV of one SAVED test point (exact index boundaries, server-side). */
export function testPointCsvUrl(name: string, tpId: number, cols?: string[]): string {
  const q = cols?.length ? `?cols=${encodeURIComponent(cols.join(','))}` : '';
  return `${API_BASE}/tests/${encodeURIComponent(name)}/testpoints/${tpId}/export${q}`;
}
