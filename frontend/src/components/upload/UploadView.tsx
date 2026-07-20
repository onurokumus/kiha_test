import { useMemo, useRef, useState } from 'react';
import {
  deleteTest,
  rawCsvUrl,
  rebuildTpStats,
  restoreTest,
} from '../../services/api';
import { TestInfo, UploadItem } from '../../types';
import { isBusyStatus } from '../../constants/status';

interface Props {
  tests: TestInfo[];
  uploads: UploadItem[];
  onUploadFiles: (files: File[]) => void;
  onDismissUpload: (id: number) => void;
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
 *  is open); local in-flight uploads are merged onto their matching
 *  'receiving' row by sanitized test name so a transfer never shows twice. */
export default function UploadView({
  tests,
  uploads,
  onUploadFiles,
  onDismissUpload,
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

  const activeUploads = uploads.filter((u) => !u.error);
  const failedUploads = uploads.filter((u) => u.error);
  const uploadByTestName = new Map(activeUploads.map((u) => [u.testName, u]));

  const rows = useMemo(
    () =>
      [...tests].sort((a, b) =>
        // ISO UTC strings — lexicographic desc == newest first
        (b.created_at ?? '').localeCompare(a.created_at ?? '')
      ),
    [tests]
  );

  const totalBytes = tests.reduce((acc, t) => acc + (t.size_bytes ?? 0), 0);

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
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 160 }}>
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
              transition: 'width 0.3s ease',
            }}
          />
        </div>
        <span style={{ fontSize: 10, color: '#569cd6', whiteSpace: 'nowrap' }}>
          {pct === null ? 'uploading…' : pct >= 100 ? 'finishing…' : `${pct}%`}
        </span>
      </div>
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

        {/* Failed local uploads (never reached the server list) */}
        {failedUploads.map((u) => (
          <div
            key={u.id}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              background: '#4b1d1d',
              color: '#f48771',
              borderRadius: 4,
              padding: '6px 10px',
              fontSize: 12,
            }}
          >
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              ✗ {u.fileName}: {u.error}
            </span>
            <span style={{ flex: 1 }} />
            <button
              className="btn"
              style={{ flex: 'none', color: '#f48771' }}
              onClick={() => onDismissUpload(u.id)}
            >
              dismiss
            </button>
          </div>
        ))}

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
        <div className="panel" style={{ padding: 0, overflow: 'auto' }}>
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
              {tests.length} test{tests.length === 1 ? '' : 's'} • {fmtBytes(totalBytes)} on disk
            </span>
          </div>
          {rows.length === 0 && activeUploads.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', color: '#909090', fontSize: 12 }}>
              No uploads yet.
            </div>
          ) : (
            <table style={{ borderCollapse: 'collapse', width: '100%' }}>
              <thead>
                <tr>
                  <th style={thStyle}>Test</th>
                  <th style={thStyle}>Status</th>
                  <th style={thStyle}>Uploaded</th>
                  <th style={thStyle}>Source file</th>
                  <th style={thRight}>Size</th>
                  <th style={thRight}>Duration</th>
                  <th style={thRight}>Rows</th>
                  <th style={thRight}>Cols</th>
                  <th style={thRight}>fs (Hz)</th>
                  <th style={thRight}>Ingest</th>
                  <th style={thStyle} />
                </tr>
              </thead>
              <tbody>
                {/* Local transfers the server does not list yet (pre-flight) */}
                {activeUploads
                  .filter((u) => !tests.some((t) => t.name === u.testName))
                  .map((u) => (
                    <tr key={`up-${u.id}`}>
                      <td style={tdStyle}>{u.testName}</td>
                      <td style={tdStyle}>{progressCell(u)}</td>
                      <td style={tdStyle}>—</td>
                      <td style={tdStyle}>{u.fileName}</td>
                      <td style={tdRight} colSpan={6} />
                      <td style={tdStyle} />
                    </tr>
                  ))}
                {rows.map((t) => {
                  const upload = uploadByTestName.get(t.name);
                  const busy = isBusyStatus(t.status);
                  return (
                    <tr key={t.name}>
                      <td style={{ ...tdStyle, fontWeight: 600 }}>{t.name}</td>
                      <td style={tdStyle}>
                        {upload ? (
                          progressCell(upload)
                        ) : (
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                            <StatusChip status={t.status} />
                            {t.status === 'receiving' && (
                              <span style={{ fontSize: 10, color: '#909090' }}>
                                {fmtBytes(t.size_bytes)} received
                              </span>
                            )}
                            {t.status === 'error' && t.error && (
                              <span
                                title={t.error}
                                style={{
                                  fontSize: 11,
                                  color: '#f48771',
                                  maxWidth: 260,
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                  display: 'inline-block',
                                  verticalAlign: 'middle',
                                }}
                              >
                                {t.error}
                              </span>
                            )}
                          </span>
                        )}
                      </td>
                      <td style={tdStyle} title={t.edited_at ? `edited ${fmtDate(t.edited_at)}` : undefined}>
                        {fmtDate(t.created_at)}
                        {t.edited_at ? ' *' : ''}
                      </td>
                      <td
                        style={{
                          ...tdStyle,
                          color: '#909090',
                          maxWidth: 220,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}
                        title={t.source_file ?? undefined}
                      >
                        {t.source_file ?? '—'}
                      </td>
                      <td style={tdRight}>{fmtBytes(t.size_bytes)}</td>
                      <td style={tdRight}>{fmtDuration(t.duration_s)}</td>
                      <td style={tdRight}>{fmtCount(t.n_rows)}</td>
                      <td style={tdRight}>{fmtCount(t.n_columns)}</td>
                      <td style={tdRight}>{t.fs_hz ?? '—'}</td>
                      <td style={tdRight}>{t.ingest_seconds != null ? `${t.ingest_seconds} s` : '—'}</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>
                        <span style={{ display: 'inline-flex', gap: 6 }}>
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
                          {!busy && !upload && (
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
