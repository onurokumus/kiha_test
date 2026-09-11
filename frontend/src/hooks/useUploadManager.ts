import { useCallback, useEffect, useRef, useState } from 'react';
import { componentIds, sameComponents } from '../utils/components';
import { fetchTests, isAbortError } from '../services/api';
import {
  cancelUploadSession,
  createUploadSession,
  getUploadSession,
  runResumableUpload,
  UploadDataOptions,
  UploadHttpError,
  UploadInit,
  UploadProgress,
  UploadSession,
  UploadTransferPhase,
} from '../services/resumableUpload';
import {
  loadUploadRecords,
  removeUploadRecord,
  saveUploadRecord,
  UploadResumeRecord,
} from '../services/uploadPersistence';
import { normalizeUploaderName } from '../services/uploaderAttribution';
import { normalizeTestText } from '../utils/testNotes';
import { TestInfo, UploadItem, UploadPhase } from '../types';

interface Options {
  tests: TestInfo[];
  fsHz?: number;
  onTestsChanged: (tests: TestInfo[]) => void;
  onNotice: (message: string) => void;
}

const SESSION_SETTLE_TIMEOUT_MS = 10_000;
const SESSION_DISCOVERY_TIMEOUT_MS = 10_000;
const SESSION_ABORT_GRACE_MS = 250;

interface SessionWaitResult {
  timedOut: boolean;
  session?: UploadSession;
}

function waitForSession(
  pending: Promise<UploadSession | undefined>,
  timeoutMs: number
): Promise<SessionWaitResult> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result: SessionWaitResult) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      resolve(result);
    };
    const timer = window.setTimeout(
      () => finish({ timedOut: true }),
      timeoutMs
    );
    void pending.then(
      (session) => finish({ timedOut: false, session }),
      () => finish({ timedOut: false })
    );
  });
}

function waitBriefly(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function createSessionWithTimeout(
  init: UploadInit
): Promise<UploadSession> {
  const controller = new AbortController();
  const timer = window.setTimeout(
    () => controller.abort(),
    SESSION_DISCOVERY_TIMEOUT_MS
  );
  try {
    return await createUploadSession(init, controller.signal);
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error('timed out while rediscovering the upload session');
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

function itemFromRecord(record: UploadResumeRecord, id: number): UploadItem {
  return {
    id,
    fileName: record.fileName,
    uploaderName: record.uploaderName,
    testName: record.testName,
    progress: 0,
    phase: 'paused',
    sessionId: record.uploadId,
    committedBytes: 0,
    totalBytes: record.sizeBytes,
    completedChunks: 0,
    totalChunks: record.totalChunks,
    requiresFile: true,
  };
}

function isActivePhase(phase: UploadPhase): boolean {
  return (
    phase === 'queued' ||
    phase === 'preparing' ||
    phase === 'verifying' ||
    phase === 'uploading' ||
    phase === 'retrying' ||
    phase === 'finalizing'
  );
}

function phaseForTransfer(phase: UploadTransferPhase): UploadPhase {
  return phase;
}

function uploadInit(
  item: UploadItem,
  file: File,
  options: UploadDataOptions
): UploadInit {
  const timeMode = options.timeMode ?? 'auto';
  const rawTimeColumn = options.timeColumn ?? '';
  const timeColumn =
    timeMode === 'column'
      ? rawTimeColumn
      : rawTimeColumn.trim() || (timeMode === 'generated' ? 'time_s' : '');
  return {
    name: item.testName,
    source_file: file.name,
    description: options.description ?? '',
    components: componentIds(options.components),
    ...(options.uploaderName
      ? { uploader_name: options.uploaderName }
      : {}),
    size_bytes: file.size,
    last_modified_ms: file.lastModified,
    ...(options.fsHz && options.fsHz > 0 ? { fs_hz: options.fsHz } : {}),
    time_mode: timeMode,
    ...(timeColumn ? { time_column: timeColumn } : {}),
  };
}

function resolveUploadOptions(
  selected: UploadDataOptions | undefined,
  fallbackFsHz: number | undefined
): UploadDataOptions {
  const timeMode = selected?.timeMode ?? 'auto';
  const rawTimeColumn = selected?.timeColumn ?? '';
  const timeColumn =
    timeMode === 'column' ? rawTimeColumn : rawTimeColumn.trim();
  const uploaderName = selected?.uploaderName
    ? normalizeUploaderName(selected.uploaderName)
    : '';
  return {
    ...(uploaderName ? { uploaderName } : {}),
    description: normalizeTestText(selected?.description ?? ''),
    components: componentIds(selected?.components),
    fsHz: selected?.fsHz ?? fallbackFsHz,
    timeMode,
    ...(timeColumn.trim() ? { timeColumn } : {}),
  };
}

function sameUploadOptions(
  left: UploadDataOptions | undefined,
  right: UploadDataOptions
): boolean {
  const leftMode = left?.timeMode ?? 'auto';
  const rightMode = right.timeMode ?? 'auto';
  const leftRawColumn = left?.timeColumn ?? '';
  const rightRawColumn = right.timeColumn ?? '';
  const leftColumn =
    (leftMode === 'column' ? leftRawColumn : leftRawColumn.trim()) ||
    (leftMode === 'generated' ? 'time_s' : '');
  const rightColumn =
    (rightMode === 'column' ? rightRawColumn : rightRawColumn.trim()) ||
    (rightMode === 'generated' ? 'time_s' : '');
  const leftUploader = left?.uploaderName
    ? normalizeUploaderName(left.uploaderName)
    : '';
  const rightUploader = right.uploaderName
    ? normalizeUploaderName(right.uploaderName)
    : '';
  return (
    leftUploader === rightUploader &&
    (left?.description ?? '') === (right.description ?? '') &&
    sameComponents(left?.components, right.components) &&
    leftMode === rightMode &&
    leftColumn === rightColumn &&
    left?.fsHz === right.fsHz
  );
}

function uploadOptionsFromSession(session: UploadSession): UploadDataOptions {
  return {
    description: session.description ?? '',
    components: componentIds(session.components),
    ...(session.uploader_name ? { uploaderName: session.uploader_name } : {}),
    ...(session.fs_hz !== null && session.fs_hz !== undefined
      ? { fsHz: session.fs_hz }
      : {}),
    timeMode: session.time_mode ?? 'auto',
    ...(session.time_column ? { timeColumn: session.time_column } : {}),
  };
}

function validateDiscoveredSession(
  session: UploadSession,
  item: UploadItem,
  file: File
): void {
  if (
    !session.upload_id ||
    session.name !== item.testName ||
    session.source_file !== file.name ||
    session.size_bytes !== file.size ||
    session.last_modified_ms !== file.lastModified
  ) {
    throw new Error('server rediscovered a session for a different local file');
  }
}

export function useUploadManager({
  tests,
  fsHz,
  onTestsChanged,
  onNotice,
}: Options) {
  const initialRecords = useRef(loadUploadRecords());
  const initialItems = useRef(
    initialRecords.current.map((record, index) => itemFromRecord(record, index + 1))
  );
  const [uploads, setUploads] = useState<UploadItem[]>(initialItems.current);
  const itemsRef = useRef<UploadItem[]>(initialItems.current);
  const sequence = useRef(initialItems.current.length);
  const files = useRef(new Map<number, File>());
  // Upload setup is immutable session identity. Keep the exact values selected
  // for each item instead of silently switching attribution/time on retry.
  const uploadOptions = useRef<Map<number, UploadDataOptions>>(
    new Map(
      initialRecords.current.map((record, index) => [
        index + 1,
        {
          uploaderName: record.uploaderName,
          description: record.description ?? '',
          components: componentIds(record.components),
          fsHz: record.fsHz,
          timeMode: record.timeMode ?? 'auto',
          timeColumn: record.timeColumn,
        },
      ])
    )
  );
  // React state can lag an onSession callback by one render. Cancellation must
  // still know the server id during that small window.
  const sessionIds = useRef(
    new Map(
      initialItems.current
        .filter((item) => item.sessionId)
        .map((item) => [item.id, item.sessionId as string])
    )
  );
  const pendingSessions = useRef(
    new Map<number, Promise<UploadSession | undefined>>()
  );
  const sessionRecoveries = useRef(
    new Map<number, Promise<UploadSession>>()
  );
  const controllers = useRef(new Map<number, AbortController>());
  const paused = useRef(new Set<number>());
  const canceled = useRef(new Set<number>());
  const runVersions = useRef(new Map<number, number>());
  const queue = useRef<Promise<void>>(Promise.resolve());

  const updateUploads = useCallback(
    (update: (current: UploadItem[]) => UploadItem[]) => {
      // The Promise queue can run in a microtask before React evaluates a
      // functional state updater. Keep the imperative mirror authoritative
      // and update it synchronously so a just-enqueued item is immediately
      // visible to runOne instead of being left at "queued" forever.
      const next = update(itemsRef.current);
      itemsRef.current = next;
      setUploads(next);
    },
    []
  );

  const updateItem = useCallback(
    (id: number, patch: Partial<UploadItem>) => {
      updateUploads((current) =>
        current.map((item) => (item.id === id ? { ...item, ...patch } : item))
      );
    },
    [updateUploads]
  );

  const rememberSession = useCallback(
    (id: number, session: UploadSession) => {
      sessionIds.current.set(id, session.upload_id);
      const itemOptions = uploadOptions.current.get(id) ?? {};
      const uploaderName = session.uploader_name ?? itemOptions.uploaderName;
      const description = session.description ?? '';
      const components = componentIds(session.components);
      uploadOptions.current.set(id, { ...itemOptions, uploaderName, description, components });
      updateItem(id, {
        sessionId: session.upload_id,
        uploaderName,
        totalChunks: session.total_chunks,
        committedBytes: session.received_bytes,
        completedChunks: session.received_chunks,
      });
      const prior = loadUploadRecords().find(
        (record) => record.uploadId === session.upload_id
      );
      saveUploadRecord({
        version: 1,
        uploadId: session.upload_id,
        testName: session.name,
        fileName: session.source_file,
        description,
        components,
        ...(uploaderName ? { uploaderName } : {}),
        sizeBytes: session.size_bytes,
        lastModifiedMs: session.last_modified_ms,
        ...(itemOptions.fsHz && itemOptions.fsHz > 0
          ? { fsHz: itemOptions.fsHz }
          : {}),
        timeMode: itemOptions.timeMode ?? 'auto',
        ...(itemOptions.timeColumn
          ? { timeColumn: itemOptions.timeColumn }
          : {}),
        chunkSize: session.chunk_size,
        totalChunks: session.total_chunks,
        createdAt: prior?.createdAt ?? new Date().toISOString(),
      });
    },
    [updateItem]
  );

  const recoverUnknownSession = useCallback(
    (
      id: number,
      item: UploadItem,
      file: File,
      pending: Promise<UploadSession | undefined>,
      originalController: AbortController | undefined
    ): Promise<UploadSession> => {
      const existing = sessionRecoveries.current.get(id);
      if (existing) return existing;
      const recovery = (async () => {
        const first = await waitForSession(
          pending,
          SESSION_SETTLE_TIMEOUT_MS
        );
        if (first.session) {
          validateDiscoveredSession(first.session, item, file);
          return first.session;
        }

        // The init response did not arrive. Bound the old fetch, allow its
        // tiny server handler a short grace period, then repeat the backend's
        // identity-idempotent POST to recover the authoritative id.
        originalController?.abort();
        await waitBriefly(SESSION_ABORT_GRACE_MS);
        const late = await waitForSession(pending, 1000);
        if (late.session) {
          validateDiscoveredSession(late.session, item, file);
          return late.session;
        }
        const discovered = await createSessionWithTimeout(
          uploadInit(item, file, uploadOptions.current.get(id) ?? {})
        );
        validateDiscoveredSession(discovered, item, file);
        return discovered;
      })();
      sessionRecoveries.current.set(id, recovery);
      void recovery.then(
        () => {
          if (sessionRecoveries.current.get(id) === recovery) {
            sessionRecoveries.current.delete(id);
          }
        },
        () => {
          if (sessionRecoveries.current.get(id) === recovery) {
            sessionRecoveries.current.delete(id);
          }
        }
      );
      return recovery;
    },
    []
  );

  const refreshTests = useCallback(async () => {
    try {
      onTestsChanged(await fetchTests());
    } catch {
      // The existing App poller also refreshes while an upload/test is busy.
    }
  }, [onTestsChanged]);

  // Reconcile persisted browser records with the server. Normal file inputs
  // cannot retain access to a local File across refresh, so these stay paused
  // until the user explicitly reselects the original file.
  useEffect(() => {
    let alive = true;
    for (const record of initialRecords.current) {
      const item = itemsRef.current.find(
        (candidate) => candidate.sessionId === record.uploadId
      );
      if (!item) continue;
      void getUploadSession(record.uploadId, record.testName)
        .then((session) => {
          if (!alive) return;
          if (
            session.state === 'ingesting' ||
            session.state === 'ready' ||
            session.state === 'error'
          ) {
            removeUploadRecord(record.uploadId);
            updateUploads((current) =>
              current.filter((candidate) => candidate.id !== item.id)
            );
            void refreshTests();
            return;
          }
          const committedBytes = session.chunks.reduce(
            (sum, chunk) => sum + chunk.size,
            0
          );
          const recordOptions = uploadOptions.current.get(item.id) ?? {};
          const uploaderName =
            session.uploader_name ?? recordOptions.uploaderName;
          uploadOptions.current.set(item.id, {
            ...recordOptions,
            ...uploadOptionsFromSession(session),
            ...(uploaderName ? { uploaderName } : {}),
          });
          updateItem(item.id, {
            uploaderName,
            progress:
              session.size_bytes > 0
                ? Math.min(1, committedBytes / session.size_bytes)
                : 0,
            committedBytes,
            completedChunks: session.chunks.length,
            totalChunks: session.total_chunks,
          });
        })
        .catch((error) => {
          if (!alive) return;
          if (error instanceof UploadHttpError && error.status === 404) {
            removeUploadRecord(record.uploadId);
            updateUploads((current) =>
              current.filter((candidate) => candidate.id !== item.id)
            );
            return;
          }
          updateItem(item.id, {
            error: `could not check resumable upload: ${
              error instanceof Error ? error.message : String(error)
            }`,
          });
        });
    }
    return () => {
      alive = false;
    };
  }, [refreshTests, updateItem, updateUploads]);

  const applyProgress = useCallback(
    (id: number, progress: UploadProgress) => {
      const transferred = Math.min(
        progress.totalBytes,
        progress.committedBytes + progress.inFlightBytes
      );
      updateItem(id, {
        progress:
          progress.totalBytes > 0 ? transferred / progress.totalBytes : 0,
        committedBytes: progress.committedBytes,
        totalBytes: progress.totalBytes,
        completedChunks: progress.completedChunks,
        totalChunks: progress.totalChunks,
      });
    },
    [updateItem]
  );

  const runOne = useCallback(
    async (id: number, file: File, version: number) => {
      if (canceled.current.has(id) || paused.current.has(id)) return;
      const item = itemsRef.current.find((candidate) => candidate.id === id);
      if (!item) return;
      const isCurrentRun = () => runVersions.current.get(id) === version;
      const controller = new AbortController();
      controllers.current.set(id, controller);
      updateItem(id, {
        phase: 'preparing',
        error: undefined,
        requiresFile: false,
        retryAttempt: undefined,
      });

      let uploadId = item.sessionId;
      const itemOptions = uploadOptions.current.get(id) ?? {};
      let sessionSettled = false;
      let settleSession: (session: UploadSession | undefined) => void =
        () => undefined;
      const pendingSession = new Promise<UploadSession | undefined>(
        (resolve) => {
          settleSession = (session) => {
            if (sessionSettled) return;
            sessionSettled = true;
            resolve(session);
          };
        }
      );
      pendingSessions.current.set(id, pendingSession);
      try {
        const result = await runResumableUpload(
          file,
          uploadInit(item, file, itemOptions),
          uploadId,
          controller.signal,
          {
            onSession: (session: UploadSession) => {
              uploadId = session.upload_id;
              sessionIds.current.set(id, session.upload_id);
              settleSession(session);
              // Let the tiny init/GET finish so Cancel receives the durable
              // server id, then stop before hashing or sending any chunk.
              if (canceled.current.has(id)) {
                controller.abort();
                return;
              }
              rememberSession(id, session);
              if (paused.current.has(id) || !isCurrentRun()) {
                controller.abort();
              }
            },
            onProgress: (progress) => {
              if (isCurrentRun()) applyProgress(id, progress);
            },
            onPhase: (phase, retryAttempt) => {
              if (!isCurrentRun()) return;
              updateItem(id, {
                phase: phaseForTransfer(phase),
                retryAttempt:
                  phase === 'retrying' ? (retryAttempt ?? 1) + 1 : undefined,
              });
            },
          }
        );
        if (uploadId) removeUploadRecord(uploadId);
        files.current.delete(id);
        uploadOptions.current.delete(id);
        sessionIds.current.delete(id);
        updateUploads((current) =>
          current.filter((candidate) => candidate.id !== id)
        );
        onNotice(
          result.status === 'ready'
            ? `${result.name}: upload and ingestion complete`
            : `${result.name}: upload complete, ingesting…`
        );
      } catch (error) {
        if (canceled.current.has(id)) return;
        if (!isCurrentRun()) return;
        if (paused.current.has(id) || isAbortError(error)) {
          const current = itemsRef.current.find(
            (candidate) => candidate.id === id
          );
          updateItem(id, {
            phase: 'paused',
            progress: current?.totalBytes
              ? current.committedBytes / current.totalBytes
              : 0,
            retryAttempt: undefined,
          });
          return;
        }
        const message = error instanceof Error ? error.message : String(error);
        const needsReselection =
          /selected file|different local file|does not match/i.test(message);
        if (needsReselection) files.current.delete(id);
        updateItem(id, {
          phase: 'error',
          error: message,
          retryAttempt: undefined,
          requiresFile: needsReselection || !files.current.has(id),
        });
      } finally {
        settleSession(undefined);
        if (pendingSessions.current.get(id) === pendingSession) {
          pendingSessions.current.delete(id);
        }
        controllers.current.delete(id);
        // Queue progression must not wait on this cosmetic refresh. The App
        // poller also refreshes active tests, and a stalled GET /tests should
        // never hold every later file in the local queue.
        void refreshTests();
      }
    },
    [
      applyProgress,
      onNotice,
      refreshTests,
      rememberSession,
      updateItem,
      updateUploads,
    ]
  );

  const enqueue = useCallback(
    (id: number, file: File) => {
      const version = (runVersions.current.get(id) ?? 0) + 1;
      runVersions.current.set(id, version);
      queue.current = queue.current
        .catch(() => undefined)
        .then(() => {
          if (
            runVersions.current.get(id) !== version ||
            paused.current.has(id) ||
            canceled.current.has(id)
          ) {
            return;
          }
          return runOne(id, file, version);
        });
    },
    [runOne]
  );

  const resumeUpload = useCallback(
    (id: number, selectedFile?: File) => {
      const item = itemsRef.current.find((candidate) => candidate.id === id);
      if (!item) return;
      const file = selectedFile ?? files.current.get(id);
      if (!file) {
        updateItem(id, {
          phase: 'paused',
          requiresFile: true,
          error: 'select the original CSV to resume',
        });
        return;
      }
      if (file.name !== item.fileName || file.size !== item.totalBytes) {
        updateItem(id, {
          phase: 'error',
          requiresFile: true,
          error:
            `selected file does not match: expected ${item.fileName} ` +
            `(${item.totalBytes.toLocaleString()} bytes)`,
        });
        return;
      }
      files.current.set(id, file);
      if (!uploadOptions.current.has(id)) {
        uploadOptions.current.set(
          id,
          resolveUploadOptions(
            { uploaderName: item.uploaderName },
            fsHz
          )
        );
      }
      paused.current.delete(id);
      canceled.current.delete(id);
      updateItem(id, {
        phase: 'queued',
        error: undefined,
        requiresFile: false,
      });
      enqueue(id, file);
    },
    [enqueue, fsHz, updateItem]
  );

  const adoptServerUpload = useCallback(
    async (test: TestInfo, file: File) => {
      if (!test.upload_id || test.status !== 'receiving') {
        throw new Error('this upload is no longer available to resume');
      }

      const session = await getUploadSession(test.upload_id, test.name);
      if (
        session.upload_id !== test.upload_id ||
        session.name !== test.name ||
        session.state !== 'receiving'
      ) {
        throw new Error('the server upload changed before it could be resumed');
      }
      if (
        session.source_file !== file.name ||
        session.size_bytes !== file.size ||
        session.last_modified_ms !== file.lastModified
      ) {
        throw new Error(
          `selected file does not match the original upload: expected ` +
            `${session.source_file} (${session.size_bytes.toLocaleString()} bytes)`
        );
      }

      const authoritativeOptions = uploadOptionsFromSession(session);
      const existing = itemsRef.current.find(
        (item) => item.sessionId === session.upload_id
      );
      if (existing) {
        uploadOptions.current.set(existing.id, authoritativeOptions);
        updateItem(existing.id, {
          uploaderName: authoritativeOptions.uploaderName,
        });
        if (existing.phase === 'paused' || existing.phase === 'error') {
          resumeUpload(existing.id, file);
        }
        return;
      }
      if (itemsRef.current.some((item) => item.testName === session.name)) {
        throw new Error('this upload is already managed in this browser');
      }

      const id = ++sequence.current;
      const item: UploadItem = {
        id,
        fileName: session.source_file,
        uploaderName: authoritativeOptions.uploaderName,
        testName: session.name,
        progress:
          session.size_bytes > 0
            ? Math.min(1, session.received_bytes / session.size_bytes)
            : 0,
        phase: 'queued',
        sessionId: session.upload_id,
        committedBytes: session.received_bytes,
        totalBytes: session.size_bytes,
        completedChunks: session.received_chunks,
        totalChunks: session.total_chunks,
        requiresFile: false,
      };
      files.current.set(id, file);
      uploadOptions.current.set(id, authoritativeOptions);
      sessionIds.current.set(id, session.upload_id);
      updateUploads((current) => [...current, item]);
      rememberSession(id, session);
      paused.current.delete(id);
      canceled.current.delete(id);
      enqueue(id, file);
    },
    [enqueue, rememberSession, resumeUpload, updateItem, updateUploads]
  );

  const startUploads = useCallback(
    async (
      selectedFiles: File[],
      selectedOptions?: UploadDataOptions
    ) => {
      const resolvedOptions = resolveUploadOptions(selectedOptions, fsHz);
      let serverTests: TestInfo[];
      try {
        serverTests = await fetchTests();
      } catch {
        serverTests = tests;
      }
      const statusByName = new Map(
        serverTests.map((test) => [test.name, test.status])
      );
      const localNames = new Set(itemsRef.current.map((item) => item.testName));

      const queued: Array<{ item: UploadItem; file: File }> = [];
      for (const file of selectedFiles) {
        if (!file.name.toLowerCase().endsWith('.csv')) {
          onNotice(`${file.name}: only .csv files can be uploaded`);
          continue;
        }
        const testName = file.name
          .replace(/\.[^.]+$/, '')
          .replace(/[^A-Za-z0-9._-]+/g, '_');
        if (!/[A-Za-z0-9]/.test(testName) || file.size === 0) {
          const id = ++sequence.current;
          updateUploads((current) => [
            ...current,
            {
              id,
              fileName: file.name,
              uploaderName: resolvedOptions.uploaderName,
              testName: testName || '(invalid name)',
              progress: 0,
              phase: 'error',
              committedBytes: 0,
              totalBytes: file.size,
              completedChunks: 0,
              totalChunks: 0,
              error:
                file.size === 0
                  ? 'the selected CSV is empty'
                  : 'file name must contain at least one letter or digit',
            },
          ]);
          continue;
        }

        const resumable = itemsRef.current.find(
          (item) =>
            item.testName === testName &&
            (!!item.sessionId || files.current.has(item.id)) &&
            (item.phase === 'paused' || item.phase === 'error')
        );
        if (resumable) {
          if (!sameUploadOptions(
            uploadOptions.current.get(resumable.id),
            resolvedOptions
          )) {
            updateItem(resumable.id, {
              phase: 'error',
              error:
                'this paused upload uses different import settings; cancel it ' +
                'before importing the file with the new setup',
            });
            onNotice(
              `${file.name}: cancel the paused upload before changing its import setup`
            );
            continue;
          }
          resumeUpload(resumable.id, file);
          continue;
        }

        const id = ++sequence.current;
        // A receiving name is intentionally allowed through: POST /uploads is
        // a tiny idempotent init/resume request, and the backend validates the
        // full file identity. This recovers even if localStorage was cleared.
        const serverStatus = statusByName.get(testName);
        const duplicate =
          localNames.has(testName) ||
          (serverStatus !== undefined && serverStatus !== 'receiving');
        const item: UploadItem = {
          id,
          fileName: file.name,
          uploaderName: resolvedOptions.uploaderName,
          testName,
          progress: 0,
          phase: duplicate ? 'error' : 'queued',
          committedBytes: 0,
          totalBytes: file.size,
          completedChunks: 0,
          totalChunks: 0,
          error: duplicate
            ? `test '${testName}' already exists — delete or rename it first`
            : undefined,
        };
        localNames.add(testName);
        if (!item.error) {
          files.current.set(id, file);
          uploadOptions.current.set(id, resolvedOptions);
          queued.push({ item, file });
        }
        updateUploads((current) => [...current, item]);
      }
      // Files are queued serially so three parallel chunks is also the global
      // request limit; selecting many files cannot multiply server load.
      for (const entry of queued) enqueue(entry.item.id, entry.file);
    },
    [enqueue, fsHz, onNotice, resumeUpload, tests, updateItem, updateUploads]
  );

  const pauseUpload = useCallback(
    (id: number) => {
      const item = itemsRef.current.find((candidate) => candidate.id === id);
      if (!item) return;
      const pendingSession = pendingSessions.current.get(id);
      const controller = controllers.current.get(id);
      const sessionId = item.sessionId ?? sessionIds.current.get(id);
      const file = files.current.get(id);
      paused.current.add(id);
      runVersions.current.set(id, (runVersions.current.get(id) ?? 0) + 1);
      // Preserve the tiny init/GET long enough to learn the durable id.
      // onSession persists it and aborts before any chunk starts.
      if (sessionId || !pendingSession) {
        controller?.abort();
      }
      updateItem(id, {
        phase: 'paused',
        progress:
          item.totalBytes > 0
            ? item.committedBytes / item.totalBytes
            : 0,
        retryAttempt: undefined,
      });

      if (!sessionId && pendingSession && file) {
        void recoverUnknownSession(
          id,
          item,
          file,
          pendingSession,
          controller
        ).then(
          (session) => {
            if (canceled.current.has(id) || !paused.current.has(id)) return;
            rememberSession(id, session);
          },
          (error) => {
            if (canceled.current.has(id) || !paused.current.has(id)) return;
            updateItem(id, {
              phase: 'paused',
              error: `paused, but could not preserve resume metadata: ${
                error instanceof Error ? error.message : String(error)
              }`,
            });
          }
        );
      }
    },
    [recoverUnknownSession, rememberSession, updateItem]
  );

  const cancelUpload = useCallback(
    async (id: number) => {
      const item = itemsRef.current.find((candidate) => candidate.id === id);
      if (!item) return;
      const pendingSession = pendingSessions.current.get(id);
      const controller = controllers.current.get(id);
      const file = files.current.get(id);
      canceled.current.add(id);
      paused.current.delete(id);
      runVersions.current.set(id, (runVersions.current.get(id) ?? 0) + 1);
      let sessionId = item.sessionId ?? sessionIds.current.get(id);
      let recovery = sessionRecoveries.current.get(id);
      if (!sessionId && !recovery && pendingSession && file) {
        recovery = recoverUnknownSession(
          id,
          item,
          file,
          pendingSession,
          controller
        );
      }
      // If initialization is in flight, do not abort its tiny POST: the
      // backend may have committed the reservation already. onSession aborts
      // the transfer immediately after returning its authoritative id.
      if (sessionId || !recovery) {
        controller?.abort();
      }
      if (!sessionId && !recovery) {
        files.current.delete(id);
        uploadOptions.current.delete(id);
        updateUploads((current) =>
          current.filter((candidate) => candidate.id !== id)
        );
        return;
      }
      updateItem(id, { phase: 'preparing', error: undefined });
      try {
        if (!sessionId && recovery) {
          const discovered = await recovery;
          sessionId = discovered.upload_id;
          rememberSession(id, discovered);
        }
        if (!sessionId) {
          throw new Error('upload session id was not returned');
        }
        await cancelUploadSession(sessionId, item.testName);
        removeUploadRecord(sessionId);
        files.current.delete(id);
        uploadOptions.current.delete(id);
        sessionIds.current.delete(id);
        updateUploads((current) =>
          current.filter((candidate) => candidate.id !== id)
        );
      } catch (error) {
        canceled.current.delete(id);
        updateItem(id, {
          phase: 'error',
          error: `cancel failed: ${
            error instanceof Error ? error.message : String(error)
          }`,
        });
      } finally {
        await refreshTests();
      }
    },
    [
      recoverUnknownSession,
      refreshTests,
      rememberSession,
      updateItem,
      updateUploads,
    ]
  );

  const dismissUpload = useCallback(
    (id: number) => {
      const item = itemsRef.current.find((candidate) => candidate.id === id);
      // A server session must be canceled, not merely hidden/orphaned.
      if (!item || item.sessionId || sessionIds.current.has(id)) return;
      files.current.delete(id);
      uploadOptions.current.delete(id);
      updateUploads((current) =>
        current.filter((candidate) => candidate.id !== id)
      );
    },
    [updateUploads]
  );

  const hasActiveUpload =
    uploads.some((item) => isActivePhase(item.phase)) ||
    uploads.some(
      (item) =>
        item.phase === 'paused' &&
        !item.sessionId &&
        pendingSessions.current.has(item.id)
    );
  useEffect(() => {
    if (!hasActiveUpload) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [hasActiveUpload]);

  return {
    uploads,
    startUploads,
    pauseUpload,
    resumeUpload,
    adoptServerUpload,
    cancelUpload,
    dismissUpload,
  };
}
