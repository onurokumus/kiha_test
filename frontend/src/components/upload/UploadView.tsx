import { useEffect, useMemo, useRef, useState } from 'react';
import { deleteTest, rawCsvUrl, rebuildTpStats, restoreTest } from '../../services/api';
import {
  cancelUploadSession,
  UploadDataOptions,
  UploadTimeMode,
} from '../../services/resumableUpload';
import { removeUploadRecord } from '../../services/uploadPersistence';
import {
  loadRememberedUploaderName,
  normalizeUploaderName,
  rememberUploaderName,
  uploaderNameError,
} from '../../services/uploaderAttribution';
import { TestInfo, UploadItem } from '../../types';
import { isBusyStatus } from '../../constants/status';
import { useConfirm } from '../feedback/confirm';
import styles from './UploadView.module.css';

interface Props {
  tests: TestInfo[];
  uploads: UploadItem[];
  pendingFiles: File[];
  defaultFsHz?: number;
  onStageUploadFiles: (files: File[]) => void;
  onStartUpload: (files: File[], options: UploadDataOptions) => void;
  onClearPendingFiles: () => void;
  onDismissUpload: (id: number) => void;
  onPauseUpload: (id: number) => void;
  onResumeUpload: (id: number, file?: File) => void;
  onAdoptServerUpload: (test: TestInfo, file: File) => Promise<void>;
  onCancelUpload: (id: number) => void;
  /** Open a ready test in the Analyze tab. */
  onOpenTest: (name: string) => void;
  /** A test was deleted server-side — parent drops caches + refreshes. */
  onTestDeleted: (name: string) => void;
  /** Server-side list changed (restore) — parent refreshes the list. */
  onTestsChanged: () => void;
  /** A test's TP averages were recomputed — parent drops its stats cache. */
  onStatsRebuilt: (name: string) => void;
}

const fmtBytes = (n?: number | null): string => {
  if (n === null || n === undefined) return '—';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
};

const fmtDuration = (s?: number | null): string => {
  if (s === null || s === undefined) return '—';
  if (s < 60) return `${s % 1 ? s.toFixed(1) : s} s`;
  const rounded = Math.round(s);
  const h = Math.floor(rounded / 3600);
  const m = Math.floor((rounded % 3600) / 60);
  const sec = rounded % 60;
  const mm = String(m).padStart(2, '0');
  const ss = String(sec).padStart(2, '0');
  return h ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
};

const fmtDate = (iso?: string | null): string => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })}`;
};

const fmtCount = (n?: number | null): string =>
  n === null || n === undefined ? '—' : n.toLocaleString();

/** Colored status chip; active states pulse via inline animation class. */
const StatusChip: React.FC<{ status: string }> = ({ status }) => {
  const palette: Record<string, { color: string; bg: string }> = {
    ready: { color: '#4ec9b0', bg: '#173d35' },
    error: { color: '#f48771', bg: '#4b1d1d' },
    receiving: { color: '#569cd6', bg: '#1e3a52' },
    ingesting: { color: '#569cd6', bg: '#1e3a52' },
    rebuilding: { color: '#569cd6', bg: '#1e3a52' },
  };
  const { color, bg } = palette[status] ?? { color: '#909090', bg: '#3c3c3c' };
  const active = isBusyStatus(status);
  return (
    <span
      className={active ? 'upload-pulse' : undefined}
      style={{
        fontSize: 10,
        color,
        background: bg,
        padding: '2px 8px',
        borderRadius: 3,
        whiteSpace: 'nowrap',
      }}
    >
      {active ? `⟳ ${status}…` : status}
    </span>
  );
};

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  fontSize: 11,
  fontWeight: 600,
  color: '#909090',
  padding: '11px 12px',
  borderBottom: '1px solid #3c3c3c',
  whiteSpace: 'nowrap',
};
const thRight: React.CSSProperties = { ...thStyle, textAlign: 'right' };
const tdStyle: React.CSSProperties = {
  padding: '13px 12px',
  borderBottom: '1px solid #2d2d2d',
  fontSize: 12,
  whiteSpace: 'nowrap',
  verticalAlign: 'middle',
};
const tdRight: React.CSSProperties = {
  ...tdStyle,
  textAlign: 'right',
  fontVariantNumeric: 'tabular-nums',
};

const HEADER_SNIFF_BYTES = 64 * 1024;

function parseHeaderLine(line: string, separator: string): string[] {
  const fields: string[] = [];
  let value = '';
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"') {
      if (quoted && line[index + 1] === '"') {
        value += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (char === separator && !quoted) {
      fields.push(value);
      value = '';
    } else {
      value += char;
    }
  }
  fields.push(value);
  return fields;
}

async function readCsvHeader(file: File): Promise<string[]> {
  const sample = await file.slice(0, HEADER_SNIFF_BYTES).text();
  const firstLine = sample.split(/\r?\n/).find((line) => line.trim());
  if (!firstLine) return [];
  const candidates = [';', '\t', '|', ','];
  let best = [firstLine];
  for (const separator of candidates) {
    const parsed = parseHeaderLine(firstLine, separator);
    if (parsed.length > best.length) best = parsed;
  }
  return best
    .map((field, index) => (index === 0 ? field.replace(/^\uFEFF/, '') : field))
    .filter((field) => field.length > 0);
}

const likelyTimeColumn = (columns: string[]): string =>
  columns.find((column) => {
    const name = column.trim().toLowerCase();
    return name.startsWith('time') || ['t', 't_s', 'zaman'].includes(name);
  }) ?? '';

/** Uploads tab: drop zone + full upload history with live status.
 *  Server rows come from the 2 s test-list poll (App polls while this tab
 *  is open). In-flight browser uploads and orphaned server sessions live in
 *  a separate transfer tray, keeping the archival data grid stable. */
export default function UploadView({
  tests,
  uploads,
  pendingFiles,
  defaultFsHz,
  onStageUploadFiles,
  onStartUpload,
  onClearPendingFiles,
  onDismissUpload,
  onPauseUpload,
  onResumeUpload,
  onAdoptServerUpload,
  onCancelUpload,
  onOpenTest,
  onTestDeleted,
  onTestsChanged,
  onStatsRebuilt,
}: Props) {
  const confirmAction = useConfirm();
  const fileRef = useRef<HTMLInputElement>(null);
  const resumeFileRef = useRef<HTMLInputElement>(null);
  const resumeTargetRef = useRef<UploadItem | TestInfo | null>(null);
  const uploaderRef = useRef<HTMLInputElement>(null);
  const setupRef = useRef<HTMLElement>(null);
  // Names deleted from this page and still restorable (session-local undo).
  const [restorable, setRestorable] = useState<string[]>([]);
  const [busyRow, setBusyRow] = useState<string | null>(null);
  const [actionError, setActionError] = useState('');
  const [actionNote, setActionNote] = useState('');
  const [historyQuery, setHistoryQuery] = useState('');
  const [historyStatus, setHistoryStatus] = useState('all');
  const [uploaderName, setUploaderName] = useState(loadRememberedUploaderName);
  const [timeMode, setTimeMode] = useState<UploadTimeMode>('auto');
  const [timeColumn, setTimeColumn] = useState('');
  const [generatedColumn, setGeneratedColumn] = useState('time_s');
  const [fsInput, setFsInput] = useState(defaultFsHz ? String(defaultFsHz) : '');
  const [commonColumns, setCommonColumns] = useState<string[]>([]);
  const [headerNote, setHeaderNote] = useState('');
  const [headersLoading, setHeadersLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    if (pendingFiles.length === 0) {
      setTimeMode('auto');
      setTimeColumn('');
      setGeneratedColumn('time_s');
      setFsInput(defaultFsHz ? String(defaultFsHz) : '');
      setCommonColumns([]);
      setHeaderNote('');
      setHeadersLoading(false);
      return () => {
        alive = false;
      };
    }
    setHeadersLoading(true);
    void Promise.all(pendingFiles.map(readCsvHeader))
      .then((headers) => {
        if (!alive) return;
        const first = headers[0] ?? [];
        const shared = first.filter((column) =>
          headers.every((columns) => columns.includes(column))
        );
        setCommonColumns(shared);
        setHeaderNote(
          shared.length
            ? pendingFiles.length > 1
              ? `${shared.length} columns are shared by all selected files.`
              : `${shared.length} columns found in the CSV header.`
            : 'No shared header columns could be detected; use Auto or Generated time.'
        );
        setTimeColumn((current) => (shared.includes(current) ? current : likelyTimeColumn(shared)));
        setHeadersLoading(false);
      })
      .catch((error) => {
        if (!alive) return;
        setCommonColumns([]);
        setHeaderNote(
          `Could not inspect the CSV header: ${
            error instanceof Error ? error.message : String(error)
          }`
        );
        setHeadersLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [defaultFsHz, pendingFiles]);

  useEffect(() => {
    if (pendingFiles.length === 0) return;
    const uploaderInput = uploaderRef.current;
    const frame = window.requestAnimationFrame(() => {
      if (uploaderInput && !uploaderInput.value.trim()) {
        uploaderInput.focus();
      } else {
        setupRef.current?.focus();
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [pendingFiles]);

  const parsedFs = Number(fsInput);
  const validFs = Number.isFinite(parsedFs) && parsedFs > 0;
  const selectedTimeColumn = timeColumn;
  const workingTimeColumn = timeMode === 'generated' ? generatedColumn.trim() : selectedTimeColumn;
  const pyramidFieldSource = commonColumns.find(
    (column) =>
      column !== workingTimeColumn &&
      (workingTimeColumn === `${column}__min` || workingTimeColumn === `${column}__max`)
  );
  const uploaderError = uploaderNameError(uploaderName);
  const setupError =
    pendingFiles.length === 0
      ? ''
      : uploaderError
        ? uploaderError
        : headersLoading
          ? 'Inspecting the selected CSV headers…'
          : timeMode === 'column' && !selectedTimeColumn.trim()
            ? 'Choose the CSV column that contains time.'
            : timeMode === 'column' && !commonColumns.includes(selectedTimeColumn)
              ? `'${selectedTimeColumn}' is not present in every selected CSV header.`
              : timeMode === 'generated' && !generatedColumn.trim()
                ? 'Enter a name for the generated time column.'
                : pyramidFieldSource
                  ? `'${workingTimeColumn}' conflicts with analysis fields for ` +
                    `'${pyramidFieldSource}'; choose another time-column name.`
                  : timeMode === 'generated' && !validFs
                    ? 'Enter a sample rate greater than 0 Hz.'
                    : fsInput && !validFs
                      ? 'Sample rate must be a number greater than 0.'
                      : '';

  const beginConfiguredUpload = () => {
    if (setupError || pendingFiles.length === 0) return;
    const normalizedUploaderName = normalizeUploaderName(uploaderName);
    setUploaderName(normalizedUploaderName);
    rememberUploaderName(normalizedUploaderName);
    onStartUpload(pendingFiles, {
      uploaderName: normalizedUploaderName,
      timeMode,
      ...(validFs ? { fsHz: parsedFs } : {}),
      ...(timeMode === 'column'
        ? { timeColumn: selectedTimeColumn }
        : timeMode === 'generated'
          ? { timeColumn: generatedColumn.trim() }
          : {}),
    });
  };

  const sortedTests = useMemo(
    () =>
      [...tests].sort((a, b) =>
        // ISO UTC strings — lexicographic desc == newest first
        (b.created_at ?? '').localeCompare(a.created_at ?? '')
      ),
    [tests]
  );

  const localOwnsTest = (test: TestInfo) =>
    uploads.some((upload) =>
      upload.sessionId
        ? upload.sessionId === test.upload_id
        : test.status === 'receiving' && upload.testName === test.name
    );
  const serverTransfers = sortedTests.filter(
    (test) => test.status === 'receiving' && !localOwnsTest(test)
  );
  // Transfers have their own stable tray. Keeping them out of the data grid
  // means transient progress controls cannot resize history columns.
  const rows = sortedTests.filter((test) => test.status !== 'receiving' && !localOwnsTest(test));
  const transferCount = uploads.length + serverTransfers.length;
  const totalBytes = rows.reduce((acc, t) => acc + (t.size_bytes ?? 0), 0);
  const visibleRows = rows.filter((test) => {
    const matchesStatus =
      historyStatus === 'all' ||
      (historyStatus === 'processing' ? isBusyStatus(test.status) : test.status === historyStatus);
    const terms = historyQuery.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    const searchableText = [test.name, test.source_file, test.uploader_name]
      .join(' ')
      .toLocaleLowerCase();
    return matchesStatus && terms.every((term) => searchableText.includes(term));
  });

  const selectOriginalCsv = (target: UploadItem | TestInfo) => {
    resumeTargetRef.current = target;
    resumeFileRef.current?.click();
  };

  const handleDelete = async (name: string) => {
    if (
      !(await confirmAction({
        title: `Delete test '${name}'?`,
        description: 'The test will be removed from the active data library.',
        detail: 'You can restore it from this page for about an hour.',
        confirmLabel: 'Move to trash',
        tone: 'danger',
      }))
    ) {
      return;
    }
    setBusyRow(name);
    setActionError('');
    setActionNote('');
    try {
      await deleteTest(name);
      setRestorable((prev) => [name, ...prev.filter((n) => n !== name)]);
      onTestDeleted(name);
    } catch (e) {
      setActionError(`delete '${name}' failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusyRow(null);
    }
  };

  const handleCancelReceiving = async (test: TestInfo) => {
    if (!test.upload_id) return;
    if (
      !(await confirmAction({
        title: `Cancel upload '${test.name}'?`,
        description: 'The incomplete upload will stop and cannot be resumed.',
        detail: 'Its verified partial data will be permanently removed.',
        confirmLabel: 'Cancel upload',
        tone: 'danger',
      }))
    ) {
      return;
    }
    setBusyRow(test.name);
    setActionError('');
    setActionNote('');
    try {
      await cancelUploadSession(test.upload_id, test.name);
      removeUploadRecord(test.upload_id);
      setActionNote(`${test.name}: incomplete upload canceled`);
      onTestsChanged();
    } catch (error) {
      setActionError(
        `cancel '${test.name}' failed: ${error instanceof Error ? error.message : String(error)}`
      );
    } finally {
      setBusyRow(null);
    }
  };

  const handleResumeReceiving = async (test: TestInfo, file: File) => {
    setBusyRow(test.name);
    setActionError('');
    setActionNote('');
    try {
      await onAdoptServerUpload(test, file);
      setActionNote(`${test.name}: original CSV accepted; resuming upload`);
    } catch (error) {
      setActionError(
        `resume '${test.name}' failed: ${error instanceof Error ? error.message : String(error)}`
      );
    } finally {
      setBusyRow(null);
    }
  };

  const handleRebuildStats = async (name: string) => {
    setBusyRow(name);
    setActionError('');
    setActionNote('');
    try {
      const res = await rebuildTpStats(name);
      onStatsRebuilt(name);
      setActionNote(
        `${name}: test-point averages recomputed (${res.columns_recomputed} column${res.columns_recomputed === 1 ? '' : 's'})`
      );
    } catch (e) {
      setActionError(`rebuild stats for '${name}' failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusyRow(null);
    }
  };

  const handleRestore = async (name: string) => {
    setBusyRow(name);
    setActionError('');
    setActionNote('');
    try {
      await restoreTest(name);
      setRestorable((prev) => prev.filter((n) => n !== name));
      onTestsChanged();
    } catch (e) {
      setActionError(`restore '${name}' failed: ${e instanceof Error ? e.message : e}`);
      // A 404 means the trash copy is gone for good — drop the dead chip.
      if (e instanceof Error && /no restorable copy/.test(e.message)) {
        setRestorable((prev) => prev.filter((n) => n !== name));
      }
    } finally {
      setBusyRow(null);
    }
  };

  /** Progress cell for a server row that has a matching local transfer. */
  const progressCell = (u: UploadItem) => {
    const pct =
      u.progress === null ? null : Math.max(0, Math.min(100, Math.round(u.progress * 100)));
    const phase =
      u.phase === 'retrying'
        ? `retry ${u.retryAttempt ?? ''}`
        : u.phase === 'error'
          ? 'error'
          : u.phase === 'finalizing'
            ? 'finalizing…'
            : u.phase === 'verifying'
              ? 'verifying…'
              : u.phase === 'preparing'
                ? 'preparing…'
                : u.phase === 'queued'
                  ? 'queued'
                  : u.phase === 'paused'
                    ? 'paused'
                    : pct === null
                      ? 'uploading…'
                      : `${pct}%`;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0, width: '100%' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div
            role="progressbar"
            aria-label={`Upload progress for ${u.fileName}`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct ?? undefined}
            aria-valuetext={phase}
            style={{
              flex: 1,
              height: 6,
              background: '#3c3c3c',
              borderRadius: 3,
              overflow: 'hidden',
            }}
          >
            <div
              className={pct === null ? 'upload-pulse' : undefined}
              style={{
                width: pct === null ? '100%' : `${pct}%`,
                height: '100%',
                background:
                  u.phase === 'paused' ? '#909090' : u.phase === 'error' ? '#f48771' : '#569cd6',
                transition: 'width 0.2s ease',
              }}
            />
          </div>
          <span
            style={{
              fontSize: 10,
              color: u.phase === 'error' ? '#f48771' : '#569cd6',
              whiteSpace: 'nowrap',
            }}
          >
            {phase}
          </span>
        </div>
        <span style={{ fontSize: 9, color: '#777', whiteSpace: 'nowrap' }}>
          {fmtBytes(u.committedBytes)} verified
          {u.totalBytes ? ` / ${fmtBytes(u.totalBytes)}` : ''}
          {u.totalChunks ? ` • ${u.completedChunks}/${u.totalChunks} chunks` : ''}
        </span>
      </div>
    );
  };

  const serverProgressCell = (test: TestInfo) => {
    const committed = test.received_bytes ?? test.size_bytes ?? 0;
    const total = test.total_bytes ?? 0;
    const pct = total > 0 ? Math.min(100, Math.round((committed / total) * 100)) : null;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0, width: '100%' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div
            role="progressbar"
            aria-label={`Upload progress for ${test.name}`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct ?? undefined}
            style={{
              flex: 1,
              height: 6,
              background: '#3c3c3c',
              borderRadius: 3,
              overflow: 'hidden',
            }}
          >
            <div
              className={pct === null ? 'upload-pulse' : undefined}
              style={{
                width: pct === null ? '100%' : `${pct}%`,
                height: '100%',
                background: '#569cd6',
              }}
            />
          </div>
          <span style={{ fontSize: 10, color: '#569cd6', whiteSpace: 'nowrap' }}>
            {pct === null ? 'receiving…' : `${pct}%`}
          </span>
        </div>
        <span style={{ fontSize: 9, color: '#777', whiteSpace: 'nowrap' }}>
          {fmtBytes(committed)} verified
          {total ? ` / ${fmtBytes(total)}` : ''}
          {test.total_chunks ? ` • ${test.received_chunks ?? 0}/${test.total_chunks} chunks` : ''}
        </span>
      </div>
    );
  };

  const uploadActions = (u: UploadItem) => {
    if (u.phase === 'paused' || u.phase === 'error') {
      return (
        <span style={{ display: 'inline-flex', gap: 6 }}>
          {u.requiresFile ? (
            <button
              className="btn"
              onClick={() => selectOriginalCsv(u)}
              aria-label={`Select original CSV to resume ${u.testName}`}
            >
              Select original CSV
            </button>
          ) : (
            <button className="btn" onClick={() => onResumeUpload(u.id)}>
              {u.phase === 'error' ? 'Retry' : 'Resume'}
            </button>
          )}
          {u.sessionId ? (
            <button
              className="btn"
              style={{ color: '#f48771' }}
              onClick={() => onCancelUpload(u.id)}
            >
              Cancel
            </button>
          ) : (
            <button className="btn" onClick={() => onDismissUpload(u.id)}>
              Dismiss
            </button>
          )}
        </span>
      );
    }
    return (
      <span style={{ display: 'inline-flex', gap: 6 }}>
        <button className="btn" onClick={() => onPauseUpload(u.id)}>
          Pause
        </button>
        <button className="btn" style={{ color: '#f48771' }} onClick={() => onCancelUpload(u.id)}>
          Cancel
        </button>
      </span>
    );
  };

  return (
    <div className={styles.page}>
      <div className={styles.content}>
        <header className={styles.pageHeader}>
          <div>
            <h1>Test uploads</h1>
            <p>Import rig data, track transfers, and open a test for analysis.</p>
          </div>
          <span className={styles.storageSummary}>
            {rows.length} tests · {fmtBytes(totalBytes)} stored
          </span>
        </header>
        <input
          ref={fileRef}
          type="file"
          accept=".csv"
          multiple
          style={{ display: 'none' }}
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            if (files.length) onStageUploadFiles(files);
            event.target.value = '';
          }}
        />
        <input
          ref={resumeFileRef}
          type="file"
          accept=".csv"
          style={{ display: 'none' }}
          onChange={(event) => {
            const file = event.target.files?.[0];
            const target = resumeTargetRef.current;
            if (file && target) {
              if ('id' in target) onResumeUpload(target.id, file);
              else void handleResumeReceiving(target, file);
            }
            event.target.value = '';
            resumeTargetRef.current = null;
          }}
        />

        {pendingFiles.length === 0 ? (
          <button
            type="button"
            className={`upload-drop-zone ${styles.dropZone}`}
            onClick={() => fileRef.current?.click()}
          >
            <span className={styles.uploadIcon} aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5" />
              </svg>
            </span>
            <span className={styles.dropCopy}>
              <strong>Import test data</strong>
              <span>Drop CSV files anywhere, or browse to choose files.</span>
              <small>Review time and sample-rate settings before the upload starts.</small>
            </span>
            <span className={styles.browseButton}>Choose CSV files</span>
          </button>
        ) : (
          <section
            ref={setupRef}
            className="panel upload-setup-panel"
            aria-label="Import setup"
            tabIndex={-1}
          >
            <div className="upload-setup-heading">
              <div>
                <div className="section-title">Import setup</div>
                <div className="upload-setup-subtitle">
                  Every imported test uses elapsed seconds beginning at 0.
                </div>
              </div>
              <div className="upload-setup-files">
                {pendingFiles.map((file) => (
                  <span
                    key={`${file.name}-${file.lastModified}`}
                    className="badge"
                    title={file.name}
                  >
                    <span>{file.name}</span>
                    <small>{fmtBytes(file.size)}</small>
                  </span>
                ))}
              </div>
            </div>

            <div className="upload-uploader">
              <label className="upload-setup-field">
                <span>Uploaded by</span>
                <input
                  ref={uploaderRef}
                  className="input"
                  type="text"
                  name="uploaderName"
                  autoComplete="name"
                  required
                  value={uploaderName}
                  placeholder="e.g. Alex Kim or Test Lab"
                  aria-invalid={!!uploaderError}
                  aria-describedby="upload-uploader-help upload-setup-feedback"
                  onChange={(event) => setUploaderName(event.target.value)}
                />
              </label>
              <span id="upload-uploader-help" className="upload-uploader-help">
                Applies to all selected files and is remembered in this browser.
              </span>
            </div>

            <div className="upload-setup-grid">
              <label className="upload-setup-field">
                <span>Time basis</span>
                <select
                  className="input"
                  value={timeMode}
                  onChange={(event) => setTimeMode(event.target.value as UploadTimeMode)}
                >
                  <option value="auto">Auto-detect</option>
                  <option value="column">Use CSV column</option>
                  <option value="generated">Generate from sample rate</option>
                </select>
              </label>

              {timeMode === 'column' && (
                <label className="upload-setup-field">
                  <span>CSV time column</span>
                  <input
                    className="input"
                    list="upload-time-columns"
                    value={timeColumn}
                    placeholder="e.g. TIME"
                    aria-invalid={!!setupError && timeMode === 'column'}
                    aria-describedby="upload-setup-feedback"
                    onChange={(event) => setTimeColumn(event.target.value)}
                  />
                </label>
              )}

              {timeMode === 'generated' && (
                <label className="upload-setup-field">
                  <span>Working time column (creates or replaces)</span>
                  <input
                    className="input"
                    value={generatedColumn}
                    placeholder="time_s"
                    aria-invalid={
                      !!setupError &&
                      timeMode === 'generated' &&
                      (!generatedColumn.trim() || !!pyramidFieldSource)
                    }
                    aria-describedby="upload-setup-feedback"
                    onChange={(event) => setGeneratedColumn(event.target.value)}
                  />
                </label>
              )}

              <label className="upload-setup-field">
                <span>{timeMode === 'generated' ? 'Sample rate (Hz)' : 'Fallback rate (Hz)'}</span>
                <input
                  className="input"
                  type="number"
                  min="0"
                  step="any"
                  inputMode="decimal"
                  value={fsInput}
                  placeholder="2048"
                  aria-invalid={
                    !!setupError && (setupError.includes('rate') || setupError.includes('number'))
                  }
                  aria-describedby="upload-setup-feedback"
                  onChange={(event) => setFsInput(event.target.value)}
                />
              </label>
            </div>

            <datalist id="upload-time-columns">
              {commonColumns.map((column) => (
                <option key={column} value={column} />
              ))}
            </datalist>

            <div className="upload-time-preview">
              <span className="upload-time-preview-label">Result</span>
              <code>
                {timeMode === 'generated'
                  ? `${generatedColumn.trim() || 'time_s'}: 0.000000 s, ${(
                      1 / (validFs ? parsedFs : 2048)
                    ).toFixed(6)} s, …`
                  : `${
                      timeMode === 'column'
                        ? timeColumn || 'selected column'
                        : timeColumn
                          ? `${timeColumn} (auto-detected)`
                          : 'time_s (generated fallback)'
                    } → elapsed seconds from 0`}
              </code>
              <span>
                {timeMode === 'auto'
                  ? timeColumn
                    ? 'The measured spacing is preserved; an unusable clock falls back to the selected rate.'
                    : 'No time-like header was found, so time_s will be generated from the fallback rate.'
                  : timeMode === 'column'
                    ? 'Clock strings and numeric seconds keep their measured spacing.'
                    : commonColumns.includes(generatedColumn.trim())
                      ? `The existing '${generatedColumn.trim()}' source column will be replaced by sample index ÷ Hz.`
                      : 'The working axis is generated exactly as sample index ÷ Hz.'}
              </span>
            </div>

            <div className="upload-setup-footer">
              <span
                id="upload-setup-feedback"
                className={setupError ? 'upload-setup-error' : ''}
                role={setupError ? 'alert' : 'status'}
                aria-live="polite"
              >
                {setupError || headerNote}
              </span>
              <div className="upload-setup-actions">
                <button className="btn" onClick={() => fileRef.current?.click()}>
                  Change files
                </button>
                <button className="btn" onClick={onClearPendingFiles}>
                  Cancel
                </button>
                <button
                  className="btn btn-primary"
                  disabled={!!setupError}
                  onClick={beginConfiguredUpload}
                >
                  Upload {pendingFiles.length} {pendingFiles.length === 1 ? 'file' : 'files'}
                </button>
              </div>
            </div>
          </section>
        )}

        {transferCount > 0 && (
          <section className="panel upload-transfers-panel" aria-label="Active transfers">
            <div className="upload-transfers-header">
              <span className="section-title" style={{ marginBottom: 0 }}>
                Active transfers
              </span>
              <span>
                {transferCount} transfer{transferCount === 1 ? '' : 's'}
              </span>
            </div>
            <div>
              {uploads.map((u) => (
                <div
                  key={u.id}
                  className={`upload-transfer-row${u.phase === 'error' ? ' is-error' : ''}`}
                >
                  <div className="upload-transfer-meta">
                    <strong title={u.testName}>{u.testName}</strong>
                    <span title={u.fileName}>{u.fileName}</span>
                    <span
                      className="upload-transfer-uploader"
                      title={u.uploaderName ?? 'Uploader not recorded'}
                    >
                      {u.uploaderName ? `by ${u.uploaderName}` : 'Uploader not recorded'}
                    </span>
                    {u.error && (
                      <span className="upload-transfer-error" title={u.error}>
                        {u.error}
                      </span>
                    )}
                  </div>
                  <div className="upload-transfer-progress">{progressCell(u)}</div>
                  <div className="upload-transfer-actions">{uploadActions(u)}</div>
                </div>
              ))}
              {serverTransfers.map((test) => (
                <div key={`server-${test.upload_id ?? test.name}`} className="upload-transfer-row">
                  <div className="upload-transfer-meta">
                    <strong title={test.name}>{test.name}</strong>
                    <span title={test.source_file ?? undefined}>
                      {test.source_file ?? 'Upload from another browser'}
                    </span>
                    <span
                      className="upload-transfer-uploader"
                      title={test.uploader_name ?? 'Uploader not recorded'}
                    >
                      {test.uploader_name ? `by ${test.uploader_name}` : 'Uploader not recorded'}
                    </span>
                  </div>
                  <div className="upload-transfer-progress">{serverProgressCell(test)}</div>
                  <div className="upload-transfer-actions">
                    <button
                      className="btn"
                      disabled={busyRow !== null}
                      onClick={() => selectOriginalCsv(test)}
                      aria-label={`Select original CSV to resume ${test.name}`}
                    >
                      {busyRow === test.name ? 'checking...' : 'Select original CSV'}
                    </button>
                    <button
                      className="btn"
                      disabled={busyRow !== null}
                      onClick={() => handleCancelReceiving(test)}
                      style={{ color: '#f48771' }}
                    >
                      {busyRow === test.name ? 'canceling…' : 'Cancel'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Recently deleted (undo) */}
        {restorable.length > 0 && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: 8,
              background: '#252526',
              border: '1px solid #3c3c3c',
              borderRadius: 4,
              padding: '6px 10px',
              fontSize: 11,
              color: '#909090',
            }}
          >
            <span>Recently deleted (restorable for ~1 h):</span>
            {restorable.map((name) => (
              <span
                key={name}
                className="badge"
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
              >
                {name}
                <button
                  onClick={() => handleRestore(name)}
                  disabled={busyRow !== null}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: '#4ec9b0',
                    cursor: 'pointer',
                    fontSize: 10,
                    padding: 0,
                    textDecoration: 'underline',
                  }}
                >
                  undo
                </button>
              </span>
            ))}
          </div>
        )}

        {actionError && (
          <div className={styles.actionError} role="alert">
            {actionError}
          </div>
        )}
        {actionNote && (
          <div className={styles.actionNote} role="status">
            {actionNote}
          </div>
        )}

        {/* History table */}
        <div className="panel upload-history-panel" style={{ padding: 0 }}>
          <div className={styles.historyHeader}>
            <div className={styles.historyTitle}>
              <h2>Upload history</h2>
              <span role="status">
                {visibleRows.length === rows.length
                  ? rows.length
                  : `${visibleRows.length} of ${rows.length}`}{' '}
                {rows.length === 1 ? 'test' : 'tests'}
              </span>
            </div>
            <div className={styles.historyFilters}>
              <input
                className="input"
                type="search"
                aria-label="Search upload history"
                placeholder="Search test, file, or uploader…"
                value={historyQuery}
                onChange={(event) => setHistoryQuery(event.target.value)}
              />
              <select
                className="input"
                aria-label="Filter uploads by status"
                value={historyStatus}
                onChange={(event) => setHistoryStatus(event.target.value)}
              >
                <option value="all">All statuses</option>
                <option value="ready">Ready</option>
                <option value="processing">Processing</option>
                <option value="error">Error</option>
              </select>
            </div>
          </div>
          {visibleRows.length === 0 ? (
            <div className={styles.emptyState}>
              <strong>
                {rows.length === 0
                  ? 'Your test library starts here'
                  : 'No tests match these filters'}
              </strong>
              <span>
                {rows.length === 0
                  ? 'Choose a CSV above. Completed uploads will appear here, ready to analyze.'
                  : 'Try a different test name, source file, or uploader.'}
              </span>
              {rows.length > 0 && (
                <button
                  className="btn"
                  onClick={() => {
                    setHistoryQuery('');
                    setHistoryStatus('all');
                  }}
                >
                  Clear filters
                </button>
              )}
            </div>
          ) : (
            <div
              className={styles.tableScroll}
              role="region"
              aria-label="Upload history table"
              tabIndex={0}
            >
              <table className="upload-history-table" aria-label="Uploaded tests">
                <thead>
                  <tr>
                    <th scope="col" style={{ ...thStyle, width: '27%' }}>
                      Test / source file
                    </th>
                    <th style={{ ...thStyle, width: '10%' }}>Status</th>
                    <th className="upload-history-uploaded" style={{ ...thStyle, width: '14%' }}>
                      Uploaded
                    </th>
                    <th style={{ ...thRight, width: '8%' }}>Size</th>
                    <th className="upload-history-metrics" style={{ ...thRight, width: '17%' }}>
                      Data
                    </th>
                    <th style={{ ...thRight, width: '24%' }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((t) => {
                    const busy = isBusyStatus(t.status);
                    return (
                      <tr key={t.name}>
                        <td style={{ ...tdStyle, fontWeight: 600, overflow: 'hidden' }}>
                          <span className={styles.testName} title={t.name}>
                            {t.name}
                          </span>
                          <span className={styles.sourceFile} title={t.source_file ?? undefined}>
                            {t.source_file ?? 'Source file unavailable'}
                          </span>
                          <span
                            className="upload-history-test-uploader"
                            title={t.uploader_name ?? 'Uploader not recorded'}
                          >
                            {t.uploader_name ? `by ${t.uploader_name}` : 'Uploader not recorded'}
                          </span>
                        </td>
                        <td style={{ ...tdStyle, overflow: 'hidden' }}>
                          <div className="upload-history-status">
                            <StatusChip status={t.status} />
                            {t.status === 'error' && t.error && (
                              <span className="upload-history-error" title={t.error}>
                                {t.error}
                              </span>
                            )}
                          </div>
                        </td>
                        <td
                          className="upload-history-uploaded"
                          style={{ ...tdStyle, overflow: 'hidden' }}
                          title={t.edited_at ? `edited ${fmtDate(t.edited_at)}` : undefined}
                        >
                          <div>
                            {fmtDate(t.created_at)}
                            {t.edited_at ? ' *' : ''}
                          </div>
                          <div
                            className="upload-history-uploaded-secondary"
                            title={t.uploader_name ?? 'Uploader not recorded'}
                          >
                            {t.uploader_name ? `by ${t.uploader_name}` : 'Uploader not recorded'}
                          </div>
                        </td>
                        <td style={tdRight}>{fmtBytes(t.size_bytes)}</td>
                        <td
                          className="upload-history-metrics"
                          style={{ ...tdRight, overflow: 'hidden' }}
                        >
                          <div
                            title={`${fmtCount(t.n_rows)} rows × ${fmtCount(t.n_columns)} columns`}
                          >
                            {fmtCount(t.n_rows)} × {fmtCount(t.n_columns)}
                          </div>
                          <div className="upload-history-metrics-secondary">
                            {fmtDuration(t.duration_s)} · {t.fs_hz ?? '—'} Hz ·{' '}
                            {t.ingest_seconds != null ? `${t.ingest_seconds} s ingest` : '— ingest'}
                          </div>
                          {t.time_column && (
                            <div className="upload-history-metrics-secondary">
                              {t.time_column} · {t.time_source ?? 'time'} · seconds from 0
                            </div>
                          )}
                          {(t.missing_rows_inserted ?? 0) > 0 && (
                            <div
                              className="upload-history-metrics-secondary"
                              style={{ color: '#dcdcaa' }}
                              title="Missing timestamps were represented by NaN signal rows"
                            >
                              {fmtCount(t.missing_rows_inserted)} missing row
                              {t.missing_rows_inserted === 1 ? '' : 's'} ·{' '}
                              {fmtCount(t.time_gap_count)} time gap
                              {t.time_gap_count === 1 ? '' : 's'}
                            </div>
                          )}
                        </td>
                        <td
                          className="upload-history-actions-cell"
                          style={{ ...tdStyle, textAlign: 'right', whiteSpace: 'normal' }}
                        >
                          <span className="upload-history-actions">
                            {t.status === 'ready' && (
                              <button
                                className={`btn ${styles.analyzeButton}`}
                                aria-label={`Analyze ${t.name}`}
                                onClick={() => onOpenTest(t.name)}
                              >
                                Analyze
                              </button>
                            )}
                            {(t.status === 'ready' || t.status === 'error') && (
                              <a
                                className="btn"
                                href={rawCsvUrl(t.name)}
                                download
                                aria-label={`Download original CSV for ${t.name}`}
                                title="download the original uploaded CSV"
                                style={{ textDecoration: 'none' }}
                              >
                                CSV
                              </a>
                            )}
                            {t.status === 'ready' && (
                              <button
                                className="btn"
                                disabled={busyRow !== null}
                                aria-label={`Recompute test-point averages for ${t.name}`}
                                onClick={() => handleRebuildStats(t.name)}
                                title="recompute this test's test-point averages (rarely changes anything; old values keep serving until it finishes)"
                              >
                                {busyRow === t.name ? 'Working…' : 'Recompute'}
                              </button>
                            )}
                            {!busy && (
                              <button
                                className="btn"
                                disabled={busyRow !== null}
                                aria-label={`Delete ${t.name}`}
                                onClick={() => handleDelete(t.name)}
                                style={{ color: '#f48771' }}
                              >
                                Delete
                              </button>
                            )}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <div className={styles.historyNote}>
          Status refreshes automatically. An asterisk marks a test edited after upload; hover its
          date for details.
        </div>
      </div>
    </div>
  );
}
