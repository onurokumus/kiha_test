/**
 * API layer for the PTT FastAPI backend.
 *
 * In dev, '/api' is proxied to http://127.0.0.1:8000 by vite (vite.config.ts),
 * so no CORS is involved. Gotcha: /filter takes `cols`+`type`, /spectrum takes
 * `col`+`mode`; POST /split/auto returns a BARE LIST proposal that must be
 * wrapped in the TestPointsFile shape before PUT /testpoints persists it.
 */
import {
  DataWindow,
  EditOps,
  FilteredWindow,
  FilterSpec,
  IdCandidate,
  SpectrumData,
  TestInfo,
  TestMeta,
  TestPoint,
  TestPointsFile,
  TpStat,
  TpTraceResponse,
  XYData,
} from '../types';

const API_BASE = '/api';

export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError';
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { signal });
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

/** Windowed full-test read: raw samples when the viewport is small enough,
 *  min/max envelope from the pyramid otherwise. Re-fetched on zoom/pan. */
export async function fetchWindow(
  name: string,
  cols: string[],
  t0: number | null,
  t1: number | null,
  px: number,
  signal?: AbortSignal
): Promise<DataWindow> {
  const params = new URLSearchParams({
    cols: cols.join(','),
    px: String(Math.max(200, Math.round(px))),
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

/** Upload a test CSV; ingest continues server-side (poll test status). */
export async function uploadTest(
  file: File,
  name = ''
): Promise<{ name: string; status: string }> {
  const fd = new FormData();
  fd.append('file', file);
  const query = name ? `?name=${encodeURIComponent(name)}` : '';
  return sendJson(`/tests/upload${query}`, { method: 'POST', body: fd });
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

/** Server-side filtered window, same raw/envelope shape as fetchWindow so it
 *  overlays on the raw series. Query gotcha: the filter kind is `type`. */
export async function fetchFiltered(
  name: string,
  cols: string[],
  spec: FilterSpec,
  t0: number | null,
  t1: number | null,
  px: number,
  signal?: AbortSignal
): Promise<FilteredWindow> {
  const params = new URLSearchParams({
    cols: cols.join(','),
    type: spec.kind,
    px: String(Math.max(200, Math.round(px))),
  });
  if (spec.order !== undefined) params.set('order', String(spec.order));
  if (spec.f1 !== undefined) params.set('f1', String(spec.f1));
  if (spec.f2 !== undefined) params.set('f2', String(spec.f2));
  if (spec.windowS !== undefined) params.set('window_s', String(spec.windowS));
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

/** Soft delete: the test moves to data/trash (restorable server-side). */
export async function deleteTest(name: string): Promise<unknown> {
  return sendJson(`/tests/${encodeURIComponent(name)}`, { method: 'DELETE' });
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

export async function healthCheck(): Promise<{ ok: boolean }> {
  return getJson<{ ok: boolean }>('/health');
}
