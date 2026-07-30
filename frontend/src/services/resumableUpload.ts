import { sha256 } from '@noble/hashes/sha256';
import { bytesToHex } from '@noble/hashes/utils';
import { API_BASE } from './api';

export const UPLOAD_CONCURRENCY = 3;
// Three chunks can be hashed/in-flight together; cap a bad server setting
// before it can force the tab to allocate an unbounded amount of memory.
const MAX_CLIENT_CHUNK_SIZE = 64 * 1024 * 1024;
// Also reject pathologically tiny server chunks before Array.from/Map state
// can scale to millions or billions of entries in a browser tab.
const MAX_CLIENT_CHUNKS = 100_000;
const MAX_ATTEMPTS = 5;

export interface UploadSessionChunk {
  index: number;
  size: number;
  sha256: string;
}

export interface UploadSession {
  upload_id: string;
  name: string;
  source_file: string;
  size_bytes: number;
  last_modified_ms: number;
  fs_hz?: number | null;
  time_mode?: UploadTimeMode;
  time_column?: string | null;
  chunk_size: number;
  total_chunks: number;
  state: string;
  received_bytes: number;
  received_chunks: number;
  chunks: UploadSessionChunk[];
}

export type UploadTimeMode = 'auto' | 'column' | 'generated';

/** Immutable data interpretation selected before any bytes are uploaded. */
export interface UploadDataOptions {
  /** Auto/column: fallback rate. Generated: authoritative sample rate. */
  fsHz?: number;
  timeMode?: UploadTimeMode;
  /** Existing source column in column mode; output column in generated mode. */
  timeColumn?: string;
}

export interface UploadInit {
  name: string;
  source_file: string;
  size_bytes: number;
  last_modified_ms: number;
  fs_hz?: number;
  time_mode?: UploadTimeMode;
  time_column?: string;
}

export interface UploadProgress {
  committedBytes: number;
  inFlightBytes: number;
  totalBytes: number;
  completedChunks: number;
  totalChunks: number;
}

export type UploadTransferPhase =
  | 'preparing'
  | 'verifying'
  | 'uploading'
  | 'retrying'
  | 'finalizing';

export interface UploadCallbacks {
  onSession?: (session: UploadSession) => void;
  onProgress?: (progress: UploadProgress) => void;
  onPhase?: (phase: UploadTransferPhase, retryAttempt?: number) => void;
}

export class UploadHttpError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message);
    this.name = 'UploadHttpError';
  }
}

class UploadNetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'UploadNetworkError';
  }
}

function abortError(): DOMException {
  return new DOMException('upload paused', 'AbortError');
}

function throwIfAborted(signal: AbortSignal): void {
  if (signal.aborted) throw abortError();
}

async function responseDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
  } catch {
    // Non-JSON error: fall back to HTTP status below.
  }
  return `${response.status} ${response.statusText}`;
}

async function requestJson<T>(
  path: string,
  init: RequestInit
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
  } catch (error) {
    if (init.signal?.aborted) throw abortError();
    throw new UploadNetworkError(
      error instanceof Error ? error.message : 'network request failed'
    );
  }
  if (!response.ok) {
    throw new UploadHttpError(response.status, await responseDetail(response));
  }
  return (await response.json()) as T;
}

export function createUploadSession(
  init: UploadInit,
  signal: AbortSignal
): Promise<UploadSession> {
  return requestJson<UploadSession>('/uploads', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(init),
    signal,
  });
}

export function getUploadSession(
  uploadId: string,
  testName: string,
  signal?: AbortSignal
): Promise<UploadSession> {
  return requestJson<UploadSession>(
    `/uploads/${encodeURIComponent(uploadId)}?name=${encodeURIComponent(testName)}`,
    { signal }
  );
}

async function completeUploadSession(
  uploadId: string,
  testName: string,
  signal: AbortSignal
): Promise<{ name: string; status: 'ingesting' | 'ready' }> {
  return requestJson(
    `/uploads/${encodeURIComponent(uploadId)}/complete?name=${encodeURIComponent(testName)}`,
    { method: 'POST', signal }
  );
}

export function cancelUploadSession(
  uploadId: string,
  testName: string
): Promise<{ ok: true; canceled: string }> {
  return requestJson(
    `/uploads/${encodeURIComponent(uploadId)}?name=${encodeURIComponent(testName)}`,
    { method: 'DELETE' }
  );
}

function parseXhrBody<T>(xhr: XMLHttpRequest): T | null {
  if (xhr.response && typeof xhr.response === 'object') {
    return xhr.response as T;
  }
  try {
    return JSON.parse(xhr.responseText) as T;
  } catch {
    return null;
  }
}

function uploadChunkOnce(
  session: UploadSession,
  index: number,
  blob: Blob,
  checksum: string,
  signal: AbortSignal,
  onProgress: (loadedFileBytes: number) => void
): Promise<UploadSession> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    const xhr = new XMLHttpRequest();
    const path =
      `${API_BASE}/uploads/${encodeURIComponent(session.upload_id)}` +
      `/chunks/${index}?name=${encodeURIComponent(session.name)}`;
    xhr.open('PUT', path);
    xhr.responseType = 'json';
    // A bounded chunk should not remain wedged forever. At the 16 MiB default,
    // five minutes still permits links slower than 60 KiB/s before retrying.
    xhr.timeout = 5 * 60 * 1000;
    xhr.setRequestHeader('X-Chunk-SHA256', checksum);

    const cleanup = () => signal.removeEventListener('abort', onAbort);
    const onAbort = () => xhr.abort();
    signal.addEventListener('abort', onAbort, { once: true });

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable || event.total <= 0) return;
      // Multipart boundaries make XHR's total slightly larger than the Blob.
      // Scaling keeps aggregate progress in actual source-file bytes.
      onProgress(Math.min(blob.size, (event.loaded / event.total) * blob.size));
    };
    xhr.onload = () => {
      cleanup();
      const body = parseXhrBody<UploadSession & { detail?: string }>(xhr);
      if (xhr.status >= 200 && xhr.status < 300 && body) {
        onProgress(blob.size);
        resolve(body);
        return;
      }
      reject(
        new UploadHttpError(
          xhr.status,
          typeof body?.detail === 'string'
            ? body.detail
            : `${xhr.status} ${xhr.statusText}`
        )
      );
    };
    xhr.onerror = () => {
      cleanup();
      reject(new UploadNetworkError('connection lost while uploading a chunk'));
    };
    xhr.onabort = () => {
      cleanup();
      reject(abortError());
    };
    xhr.ontimeout = () => {
      cleanup();
      reject(new UploadNetworkError('chunk upload timed out'));
    };

    const form = new FormData();
    form.append('file', blob, `${session.source_file}.part-${index}`);
    // Do not set Content-Type: the browser must add FormData's random boundary.
    xhr.send(form);
  });
}

function retryable(error: unknown): boolean {
  if (error instanceof UploadNetworkError) return true;
  if (!(error instanceof UploadHttpError)) return false;
  return (
    error.status === 0 ||
    error.status === 408 ||
    error.status === 425 ||
    error.status === 429 ||
    error.status >= 500
  );
}

function waitWithAbort(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(abortError());
    };
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

async function uploadChunkWithRetry(
  session: UploadSession,
  index: number,
  blob: Blob,
  checksum: string,
  signal: AbortSignal,
  onProgress: (loadedFileBytes: number) => void,
  onPhase: UploadCallbacks['onPhase']
): Promise<UploadSession> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    throwIfAborted(signal);
    try {
      if (attempt > 1) onPhase?.('uploading');
      return await uploadChunkOnce(
        session,
        index,
        blob,
        checksum,
        signal,
        onProgress
      );
    } catch (error) {
      if (signal.aborted) throw abortError();
      lastError = error;
      if (!retryable(error) || attempt === MAX_ATTEMPTS) throw error;
      onProgress(0);
      onPhase?.('retrying', attempt);
      const baseMs = Math.min(8000, 500 * 2 ** (attempt - 1));
      const jitteredMs = baseMs * (0.75 + Math.random() * 0.5);
      await waitWithAbort(jitteredMs, signal);
    }
  }
  throw lastError;
}

async function hashBlob(blob: Blob, signal: AbortSignal): Promise<string> {
  throwIfAborted(signal);
  const bytes = new Uint8Array(await blob.arrayBuffer());
  throwIfAborted(signal);
  // @noble/hashes works on the production plain-HTTP origin where
  // crypto.subtle is unavailable because the page is not a secure context.
  return bytesToHex(sha256(bytes));
}

function expectedChunkSize(fileSize: number, chunkSize: number, index: number): number {
  return Math.min(chunkSize, Math.max(0, fileSize - index * chunkSize));
}

function validatedChunks(
  session: UploadSession,
  file: File
): Map<number, UploadSessionChunk> {
  if (session.name === '' || session.size_bytes !== file.size) {
    throw new Error('the selected file does not match this upload session');
  }
  if (
    !Number.isInteger(session.chunk_size) ||
    session.chunk_size <= 0 ||
    session.chunk_size > MAX_CLIENT_CHUNK_SIZE
  ) {
    throw new Error(
      `server selected an unsafe chunk size (${session.chunk_size} bytes)`
    );
  }
  const expectedTotal = Math.ceil(file.size / session.chunk_size);
  if (
    !Number.isSafeInteger(expectedTotal) ||
    expectedTotal > MAX_CLIENT_CHUNKS
  ) {
    throw new Error(
      `server selected too many upload chunks (${expectedTotal.toLocaleString()})`
    );
  }
  if (
    !Number.isInteger(session.total_chunks) ||
    session.total_chunks !== expectedTotal
  ) {
    throw new Error('upload session has inconsistent file/chunk metadata');
  }
  const result = new Map<number, UploadSessionChunk>();
  for (const chunk of session.chunks) {
    const size = expectedChunkSize(file.size, session.chunk_size, chunk.index);
    if (
      !Number.isInteger(chunk.index) ||
      chunk.index < 0 ||
      chunk.index >= expectedTotal ||
      chunk.size !== size ||
      !/^[0-9a-f]{64}$/i.test(chunk.sha256) ||
      result.has(chunk.index)
    ) {
      throw new Error('upload session contains invalid committed chunk metadata');
    }
    result.set(chunk.index, { ...chunk, sha256: chunk.sha256.toLowerCase() });
  }
  const committedBytes = [...result.values()].reduce(
    (sum, chunk) => sum + chunk.size,
    0
  );
  if (
    session.received_chunks !== result.size ||
    session.received_bytes !== committedBytes
  ) {
    throw new Error('upload session progress disagrees with its chunk manifest');
  }
  return result;
}

async function verifyCommittedChunks(
  file: File,
  session: UploadSession,
  committed: Map<number, UploadSessionChunk>,
  signal: AbortSignal
): Promise<Map<number, string>> {
  const chunks = [...committed.values()].sort((a, b) => a.index - b.index);
  const hashes = new Map<number, string>();
  let cursor = 0;
  const workers = Array.from(
    { length: Math.min(UPLOAD_CONCURRENCY, chunks.length) },
    async () => {
      while (cursor < chunks.length) {
        const position = cursor;
        cursor += 1;
        const chunk = chunks[position];
        const start = chunk.index * session.chunk_size;
        const hash = await hashBlob(
          file.slice(start, start + chunk.size),
          signal
        );
        if (hash !== chunk.sha256) {
          throw new Error(
            `selected file differs from the original at chunk ${chunk.index + 1}; ` +
              'choose the original file or cancel this upload'
          );
        }
        hashes.set(chunk.index, hash);
      }
    }
  );
  await Promise.all(workers);
  return hashes;
}

/**
 * Create or resume a server upload session and transfer missing chunks.
 *
 * The backend is authoritative: every previously committed SHA-256 is checked
 * against a reselected File, PUT is idempotent, and completion only follows a
 * fresh manifest containing every expected chunk.
 */
export async function runResumableUpload(
  file: File,
  init: UploadInit,
  existingUploadId: string | undefined,
  externalSignal: AbortSignal,
  callbacks: UploadCallbacks = {}
): Promise<{ name: string; status: 'ingesting' | 'ready' }> {
  const controller = new AbortController();
  const relayAbort = () => controller.abort();
  externalSignal.addEventListener('abort', relayAbort, { once: true });
  if (externalSignal.aborted) controller.abort();
  const signal = controller.signal;

  try {
    callbacks.onPhase?.('preparing');
    const session = existingUploadId
      ? await getUploadSession(existingUploadId, init.name, signal)
      : await createUploadSession(init, signal);
    if (
      !session.upload_id ||
      (existingUploadId !== undefined &&
        session.upload_id !== existingUploadId) ||
      session.name !== init.name ||
      session.source_file !== init.source_file ||
      session.size_bytes !== init.size_bytes ||
      session.last_modified_ms !== init.last_modified_ms ||
      (session.fs_hz ?? undefined) !== init.fs_hz ||
      (session.time_mode ?? 'auto') !== (init.time_mode ?? 'auto') ||
      (session.time_column ?? undefined) !== init.time_column
    ) {
      throw new Error('server resumed a session for a different local file');
    }
    // Only expose/persist a response after its immutable identity agrees with
    // the File and request that produced it.
    callbacks.onSession?.(session);
    if (
      session.state === 'finalizing' ||
      session.state === 'ingesting' ||
      session.state === 'ready'
    ) {
      callbacks.onPhase?.('finalizing');
      return await completeUploadSession(
        session.upload_id,
        session.name,
        signal
      );
    }
    if (session.state !== 'receiving') {
      throw new Error(
        `upload session cannot resume while it is ${session.state}`
      );
    }

    const committed = validatedChunks(session, file);
    const inFlight = new Map<number, number>();
    const emitProgress = () => {
      callbacks.onProgress?.({
        committedBytes: [...committed.values()].reduce(
          (sum, chunk) => sum + chunk.size,
          0
        ),
        inFlightBytes: [...inFlight.values()].reduce(
          (sum, bytes) => sum + bytes,
          0
        ),
        totalBytes: file.size,
        completedChunks: committed.size,
        totalChunks: session.total_chunks,
      });
    };
    emitProgress();

    callbacks.onPhase?.('verifying');
    const localHashes = await verifyCommittedChunks(
      file,
      session,
      committed,
      signal
    );

    const missing = Array.from(
      { length: session.total_chunks },
      (_, index) => index
    ).filter((index) => !committed.has(index));
    let cursor = 0;
    callbacks.onPhase?.('uploading');

    const workers = Array.from(
      { length: Math.min(UPLOAD_CONCURRENCY, missing.length) },
      async () => {
        while (cursor < missing.length) {
          throwIfAborted(signal);
          const position = cursor;
          cursor += 1;
          const index = missing[position];
          const start = index * session.chunk_size;
          const blob = file.slice(
            start,
            start + expectedChunkSize(file.size, session.chunk_size, index)
          );
          const checksum = await hashBlob(blob, signal);
          localHashes.set(index, checksum);
          inFlight.set(index, 0);
          emitProgress();
          const updated = await uploadChunkWithRetry(
            session,
            index,
            blob,
            checksum,
            signal,
            (loaded) => {
              inFlight.set(index, loaded);
              emitProgress();
            },
            callbacks.onPhase
          );
          const serverChunk = updated.chunks.find((chunk) => chunk.index === index);
          if (
            !serverChunk ||
            serverChunk.size !== blob.size ||
            serverChunk.sha256.toLowerCase() !== checksum
          ) {
            throw new Error(
              `server did not confirm chunk ${index + 1} with the expected checksum`
            );
          }
          inFlight.delete(index);
          committed.set(index, {
            index,
            size: blob.size,
            sha256: checksum,
          });
          emitProgress();
        }
      }
    );

    try {
      await Promise.all(workers);
    } catch (error) {
      controller.abort();
      await Promise.allSettled(workers);
      throw error;
    }

    const finalSession = await getUploadSession(
      session.upload_id,
      session.name,
      signal
    );
    const finalChunks = validatedChunks(finalSession, file);
    if (finalChunks.size !== session.total_chunks) {
      throw new Error(
        `server has ${finalChunks.size} of ${session.total_chunks} chunks; upload can be resumed`
      );
    }
    for (const [index, chunk] of finalChunks) {
      if (localHashes.get(index) !== chunk.sha256) {
        throw new Error(
          `server checksum disagrees for chunk ${index + 1}; upload was not finalized`
        );
      }
    }

    callbacks.onPhase?.('finalizing');
    return await completeUploadSession(
      session.upload_id,
      session.name,
      signal
    );
  } finally {
    controller.abort();
    externalSignal.removeEventListener('abort', relayAbort);
  }
}
