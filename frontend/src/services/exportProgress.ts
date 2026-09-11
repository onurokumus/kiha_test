/** Progress callbacks follow one AbortSignal, shared by CSV and canvas paths. */
export interface ExportProgress {
  stage: string;
  completed?: number;
  total?: number | null;
  unit?: string | null;
  plot?: number | null;
  source?: string | null;
  cancellationUnconfirmed?: boolean;
}
interface ServerProgress extends ExportProgress { id: string; state: string; error?: string | null }
const listeners = new WeakMap<AbortSignal, (value: ExportProgress) => void>();
export function observeExport(signal: AbortSignal, listener: (value: ExportProgress) => void) {
  listeners.set(signal, listener);
  return () => listeners.delete(signal);
}
export function reportExport(signal: AbortSignal | undefined, value: ExportProgress) {
  if (signal) listeners.get(signal)?.(value);
}
export function checkExport(signal?: AbortSignal) {
  if (signal?.aborted) throw new DOMException('Export canceled', 'AbortError');
}
export async function yieldExport(signal?: AbortSignal) {
  await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
  checkExport(signal);
}

async function errorFrom(response: Response) {
  const body = await response.json().catch(() => null);
  const detail = body?.detail;
  const validation = Array.isArray(detail) ? Array.from(new Set(detail
    .map((entry: { msg?: unknown }) => typeof entry?.msg === 'string' ? entry.msg.replace(/^Value error, /, '') : '')
    .filter(Boolean))).join(' ') : '';
  return new Error(typeof detail === 'string' ? detail : validation || `Export failed (${response.status}).`);
}

/** Keep the original all-or-error binary request; poll a bounded, ephemeral
 * tracker alongside it. Cancellation has its own request and survives aborting
 * the download. The tracker contains no retained export files. */
export async function fetchExportFile(base: string, path: string, body: BodyInit,
  requestType: string, expectedType: string, signal: AbortSignal) {
  checkExport(signal);
  reportExport(signal, { stage: 'Starting export' });
  // Finish registering even if Cancel races this small request, then cancel
  // its token. Unclaimed tokens also expire automatically on the server.
  const created = await fetch(`${base}/export-progress`, { method: 'POST', signal: AbortSignal.timeout(5000) });
  if (!created.ok) throw await errorFrom(created);
  const job = await created.json() as ServerProgress;
  const url = `${base}/export-progress/${encodeURIComponent(job.id)}`;
  const pollController = new AbortController();
  let cancelRequest: Promise<ServerProgress | null> | null = null;
  let receiving = false;
  const cancel = () => {
    cancelRequest ??= fetch(url, { method: 'DELETE', keepalive: true, signal: AbortSignal.timeout(3000) })
      .then(async (response) => response.ok ? await response.json() as ServerProgress : null).catch(() => null);
  };
  signal.addEventListener('abort', cancel, { once: true });
  if (signal.aborted) cancel();
  const polling = (async () => {
    while (!pollController.signal.aborted && !signal.aborted) {
      try {
        const response = await fetch(url, { signal: pollController.signal });
        if (response.ok) {
          const value = await response.json() as ServerProgress;
          if (!receiving && !signal.aborted) reportExport(signal, value);
        } else if (!receiving) reportExport(signal, { stage: 'Progress unavailable; waiting for export' });
      } catch { /* The binary response reports failures; retry transient polling errors. */ }
      if (!pollController.signal.aborted) await new Promise((resolve) => window.setTimeout(resolve, 350));
    }
  })();
  try {
    checkExport(signal);
    const response = await fetch(`${base}${path}`, {
      method: 'POST', body, signal, headers: { 'Content-Type': requestType, 'X-Export-ID': job.id },
    });
    if (!response.ok) throw await errorFrom(response);
    if (!response.headers.get('content-type')?.includes(expectedType)) {
      throw new Error('The server did not return the requested export file. Update the backend and retry.');
    }
    receiving = true;
    const length = response.headers.get('content-length');
    // Fetch decompresses responses; compressed Content-Length is not a byte total.
    const total = !response.headers.get('content-encoding') && length ? Number(length) : null;
    const parts: BlobPart[] = [];
    let completed = 0;
    const reader = response.body?.getReader();
    if (!reader) throw new Error('The server returned no export file.');
    try {
      while (!signal.aborted) {
        checkExport(signal);
        const { done, value } = await reader.read();
        if (done) break;
        parts.push(value);
        completed += value.byteLength;
        reportExport(signal, { stage: 'Receiving file', completed, total, unit: 'bytes' });
      }
    } finally { reader.releaseLock(); }
    checkExport(signal);
    if (!completed) throw new Error('The export file is empty.');
    return { response, blob: new Blob(parts, { type: expectedType }) };
  } catch (error) {
    cancel(); // failed/abandoned transports must not leave server work running
    throw error;
  } finally {
    pollController.abort();
    await polling;
    signal.removeEventListener('abort', cancel);
    if (signal.aborted) {
      cancel();
      reportExport(signal, { stage: 'Canceling; waiting for the current block to finish' });
      let status: ServerProgress | null = await (cancelRequest as Promise<ServerProgress | null> | null);
      const deadline = Date.now() + 12000;
      while (status && !['completed', 'canceled', 'failed'].includes(status.state) && Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 200));
        status = await fetch(url, { signal: AbortSignal.timeout(2000) })
          .then(async (response) => response.ok ? await response.json() as ServerProgress : null).catch(() => null);
      }
      reportExport(signal, { stage: 'Canceled', cancellationUnconfirmed: !status || !['completed', 'canceled', 'failed'].includes(status.state) });
    }
  }
}
