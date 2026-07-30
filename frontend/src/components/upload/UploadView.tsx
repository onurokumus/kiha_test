import { useMemo, useRef, useState } from 'react';
import {
  deleteTest,
  rawCsvUrl,
  rebuildTpStats,
  restoreTest,
} from '../../services/api';
import { cancelUploadSession } from '../../services/resumableUpload';
import { removeUploadRecord } from '../../services/uploadPersistence';
import { TestInfo, UploadItem } from '../../types';
import { isBusyStatus } from '../../constants/status';

interface Props {
  tests: TestInfo[];
  uploads: UploadItem[];
  onUploadFiles: (files: File[]) => void;
  onDismissUpload: (id: number) => void;
  onPauseUpload: (id: number) => void;
  onResumeUpload: (id: number, file?: File) => void;
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
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.round(s % 60);
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
  fontSize: 10,
  fontWeight: 600,
  color: '#909090',
  textTransform: 'uppercase',
  letterSpacing: 0.5,
  padding: '6px 10px',
  borderBottom: '1px solid #3c3c3c',
  whiteSpace: 'nowrap',
};
const thRight: React.CSSProperties = { ...thStyle, textAlign: 'right' };
const tdStyle: React.CSSProperties = {
  padding: '6px 10px',
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

/** Uploads tab: drop zone + full upload history with live status.
 *  Server rows come from the 2 s test-list poll (App polls while this tab
 *  is open). In-flight browser uploads and orphaned server sessions live in
 *  a separate transfer tray, keeping the archival data grid stable. */
export default function UploadView({
  tests,
  uploads,
  onUploadFiles,
  onDismissUpload,
  onPauseUpload,
  onResumeUpload,
  onCancelUpload,
  onOpenTest,
  onTestDeleted,
  onTestsChanged,
  onStatsRebuilt,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  // Names deleted from this page and still restorable (session-local undo).
  const [restorable, setRestorable] = useState<string[]>([]);
  const [busyRow, setBusyRow] = useState<string | null>(null);
  const [actionError, setActionError] = useState('');
  const [actionNote, setActionNote] = useState('');

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
  const rows = sortedTests.filter(
    (test) => test.status !== 'receiving' && !localOwnsTest(test)
  );
  const transferCount = uploads.length + serverTransfers.length;
  const totalBytes = rows.reduce((acc, t) => acc + (t.size_bytes ?? 0), 0);

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete test '${name}'?\n\nIt can be restored from this page for about an hour.`)) return;
    setBusyRow(name);
    setActionError('');
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
      !confirm(
        `Cancel incomplete upload '${test.name}'?\n\n` +
          'Its verified partial data will be permanently removed.'
      )
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
        `cancel '${test.name}' failed: ${
          error instanceof Error ? error.message : String(error)
        }`
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
    const pct = u.progress === null ? null : Math.round(u.progress * 100);
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
                  u.phase === 'paused'
                    ? '#909090'
                    : u.phase === 'error'
                      ? '#f48771'
                      : '#569cd6',
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
          {test.total_chunks
            ? ` • ${test.received_chunks ?? 0}/${test.total_chunks} chunks`
            : ''}
        </span>
      </div>
    );
  };

  const uploadActions = (u: UploadItem) => {
    if (u.phase === 'paused' || u.phase === 'error') {
      return (
        <span style={{ display: 'inline-flex', gap: 6 }}>
          {u.requiresFile ? (
            <label className="btn" style={{ cursor: 'pointer' }}>
              Select original CSV
              <input
                type="file"
                accept=".csv"
                style={{ display: 'none' }}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) onResumeUpload(u.id, file);
                  event.target.value = '';
                }}
              />
            </label>
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
        <button
          className="btn"
          style={{ color: '#f48771' }}
          onClick={() => onCancelUpload(u.id)}
        >
          Cancel
        </button>
      </span>
    );
  };

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
      <div style={{ maxWidth: 1200, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* Drop zone / picker */}
        <div
          onClick={() => fileRef.current?.click()}
          style={{
            border: '2px dashed #555',
            borderRadius: 6,
            padding: '28px 16px',
            textAlign: 'center',
            cursor: 'pointer',
            background: '#232324',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.borderColor = '#569cd6')}
          onMouseLeave={(e) => (e.currentTarget.style.borderColor = '#555')}
        >
          <div style={{ fontSize: 15, color: '#569cd6', marginBottom: 6 }}>⬆ Upload test CSV</div>
          <div style={{ fontSize: 11, color: '#909090' }}>
            Click to choose files, or drop .csv files anywhere in this window.
            The test name is taken from the file name.
          </div>
          <input
            ref={fileRef}
            type="file"
            accept=".csv"
            multiple
            style={{ display: 'none' }}
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              if (files.length) onUploadFiles(files);
              e.target.value = '';
            }}
          />
        </div>

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
                  </div>
                  <div className="upload-transfer-progress">{serverProgressCell(test)}</div>
                  <div className="upload-transfer-actions">
                    <button
                      className="btn"
                      disabled={busyRow === test.name}
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
              <span key={name} className="badge" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                {name}
                <button
                  onClick={() => handleRestore(name)}
                  disabled={busyRow === name}
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
          <div style={{ color: '#f48771', fontSize: 12 }}>{actionError}</div>
        )}
        {actionNote && (
          <div style={{ color: '#4ec9b0', fontSize: 12 }}>{actionNote}</div>
        )}

        {/* History table */}
        <div className="panel upload-history-panel" style={{ padding: 0 }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'baseline',
              gap: 8,
              padding: '8px 10px',
              borderBottom: '1px solid #3c3c3c',
            }}
          >
            <span className="section-title" style={{ marginBottom: 0 }}>Upload history</span>
            <span style={{ fontSize: 11, color: '#909090' }}>
              {rows.length} test{rows.length === 1 ? '' : 's'} • {fmtBytes(totalBytes)} on disk
            </span>
          </div>
          {rows.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', color: '#909090', fontSize: 12 }}>
              No completed uploads yet.
            </div>
          ) : (
            <table className="upload-history-table">
              <thead>
                <tr>
                  <th style={{ ...thStyle, width: '15%' }}>Test</th>
                  <th style={{ ...thStyle, width: '10%' }}>Status</th>
                  <th className="upload-history-uploaded" style={{ ...thStyle, width: '14%' }}>
                    Uploaded
                  </th>
                  <th className="upload-history-source" style={{ ...thStyle, width: '15%' }}>
                    Source file
                  </th>
                  <th style={{ ...thRight, width: '8%' }}>Size</th>
                  <th className="upload-history-metrics" style={{ ...thRight, width: '17%' }}>
                    Data
                  </th>
                  <th style={{ ...thStyle, width: '21%' }} />
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => {
                  const busy = isBusyStatus(t.status);
                  return (
                    <tr key={t.name}>
                      <td style={{ ...tdStyle, fontWeight: 600, overflow: 'hidden' }}>
                        <span className="upload-history-ellipsis" title={t.name}>
                          {t.name}
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
                        {fmtDate(t.created_at)}
                        {t.edited_at ? ' *' : ''}
                      </td>
                      <td
                        className="upload-history-source"
                        style={{
                          ...tdStyle,
                          color: '#909090',
                          overflow: 'hidden',
                        }}
                        title={t.source_file ?? undefined}
                      >
                        <span className="upload-history-ellipsis">
                          {t.source_file ?? '—'}
                        </span>
                      </td>
                      <td style={tdRight}>{fmtBytes(t.size_bytes)}</td>
                      <td
                        className="upload-history-metrics"
                        style={{ ...tdRight, overflow: 'hidden' }}
                      >
                        <div title={`${fmtCount(t.n_rows)} rows × ${fmtCount(t.n_columns)} columns`}>
                          {fmtCount(t.n_rows)} × {fmtCount(t.n_columns)}
                        </div>
                        <div className="upload-history-metrics-secondary">
                          {fmtDuration(t.duration_s)} · {t.fs_hz ?? '—'} Hz ·{' '}
                          {t.ingest_seconds != null ? `${t.ingest_seconds} s ingest` : '— ingest'}
                        </div>
                      </td>
                      <td
                        className="upload-history-actions-cell"
                        style={{ ...tdStyle, textAlign: 'right', whiteSpace: 'normal' }}
                      >
                        <span className="upload-history-actions">
                          {t.status === 'ready' && (
                            <button className="btn" onClick={() => onOpenTest(t.name)}>
                              Analyze →
                            </button>
                          )}
                          {(t.status === 'ready' || t.status === 'error') && (
                            <a
                              className="btn"
                              href={rawCsvUrl(t.name)}
                              download
                              title="download the original uploaded CSV"
                              style={{ textDecoration: 'none' }}
                            >
                              ⬇ CSV
                            </a>
                          )}
                          {t.status === 'ready' && (
                            <button
                              className="btn"
                              disabled={busyRow === t.name}
                              onClick={() => handleRebuildStats(t.name)}
                              title="recompute this test's test-point averages (rarely changes anything; old values keep serving until it finishes)"
                            >
                              {busyRow === t.name ? '⟳ stats…' : '↻ stats'}
                            </button>
                          )}
                          {!busy && (
                            <button
                              className="btn"
                              disabled={busyRow === t.name}
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
          )}
        </div>
        <div style={{ fontSize: 10, color: '#666' }}>
          * edited since upload (hover the date for when). Status updates live every 2 s while this page is open.
        </div>
      </div>
    </div>
  );
}
