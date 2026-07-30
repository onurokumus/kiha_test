import React, { useRef } from 'react';
import { TestInfo, UploadItem } from '../../types';

export type AppTab = 'analyze' | 'split' | 'edit' | 'uploads' | 'settings';

interface HeaderProps {
  tests: TestInfo[];
  tab: AppTab;
  onTabChange: (tab: AppTab) => void;
  onUploadFiles: (files: File[]) => void;
  uploads: UploadItem[];
  onDismissUpload: (id: number) => void;
  onPauseUpload: (id: number) => void;
  onResumeUpload: (id: number) => void;
  onCancelUpload: (id: number) => void;
  notice?: string;
}

const chipButton: React.CSSProperties = {
  flex: 'none',
  background: 'none',
  border: 'none',
  color: 'inherit',
  cursor: 'pointer',
  fontSize: 10,
  padding: 0,
  textDecoration: 'underline',
};

/** One upload's chip: live verified progress plus pause/resume/cancel. */
const UploadChip: React.FC<{
  item: UploadItem;
  onDismiss: () => void;
  onPause: () => void;
  onResume: () => void;
  onCancel: () => void;
  onOpenUploads: () => void;
}> = ({ item, onDismiss, onPause, onResume, onCancel, onOpenUploads }) => {
  if (item.error) {
    return (
      <span
        className="badge upload-chip"
        style={{
          color: '#f48771',
          background: '#4b1d1d',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
        }}
        title={`${item.fileName}: ${item.error}`}
      >
        <span
          className="upload-chip-label"
          style={{
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          ✗ {item.fileName}: {item.error}
        </span>
        {item.sessionId ? (
          <>
            <button
              onClick={item.requiresFile ? onOpenUploads : onResume}
              style={chipButton}
            >
              {item.requiresFile ? 'select file' : 'retry'}
            </button>
            <button onClick={onCancel} style={chipButton}>cancel</button>
          </>
        ) : (
          <button onClick={onDismiss} title="dismiss" style={chipButton}>×</button>
        )}
      </span>
    );
  }

  const pct = item.progress === null ? '…' : `${Math.round(item.progress * 100)}%`;
  const label =
    item.phase === 'uploading'
      ? pct
      : item.phase === 'retrying'
        ? `retry ${item.retryAttempt ?? ''}`
        : item.phase === 'finalizing'
          ? 'finalizing…'
          : item.phase === 'verifying'
            ? 'verifying…'
            : item.phase === 'preparing'
              ? 'preparing…'
              : item.phase === 'queued'
                ? 'queued'
                : 'paused';

  return (
    <span
      className="badge upload-chip"
      title={`${item.fileName}: ${item.completedChunks}/${item.totalChunks || '?'} chunks committed`}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}
    >
      <span className="upload-chip-label">⬆ {item.fileName} {label}</span>
      {item.phase === 'paused' ? (
        <button
          onClick={item.requiresFile ? onOpenUploads : onResume}
          style={chipButton}
        >
          {item.requiresFile ? 'select file' : 'resume'}
        </button>
      ) : (
        <button onClick={onPause} style={chipButton}>pause</button>
      )}
      <button onClick={onCancel} style={chipButton}>cancel</button>
    </span>
  );
};

/** Top bar: branding, tabs, upload, and compact transfer controls. */
export const Header: React.FC<HeaderProps> = ({
  tests,
  tab,
  onTabChange,
  onUploadFiles,
  uploads,
  onDismissUpload,
  onPauseUpload,
  onResumeUpload,
  onCancelUpload,
  notice,
}) => {
  const fileRef = useRef<HTMLInputElement>(null);
  // Local active uploads render their own chip; only count transfers received
  // from another browser/window in the generic status badge.
  const localActive = uploads.filter((item) => !!item.sessionId).length;
  const receiving = Math.max(
    0,
    tests.filter((test) => test.status === 'receiving').length - localActive
  );
  const ingesting = tests.filter((test) => test.status === 'ingesting').length;
  const rebuilding = tests.filter((test) => test.status === 'rebuilding').length;

  const tabButton = (value: AppTab, label: string) => (
    <button
      className={'btn-toggle' + (tab === value ? ' active' : '')}
      onClick={() => onTabChange(value)}
    >
      {label}
    </button>
  );

  return (
    <div className="app-header">
      <div className="app-header-primary">
        <span style={{ fontWeight: 600, color: '#569cd6', fontSize: 14 }}>
          Propeller Test Tool
        </span>
        {tabButton('analyze', 'Analyze')}
        {tabButton('split', 'Split')}
        {tabButton('edit', 'Edit')}
        {tabButton('uploads', 'Uploads')}
        {tabButton('settings', 'Settings')}
        <button className="btn" onClick={() => fileRef.current?.click()}>
          ⬆ Upload CSV
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".csv"
          multiple
          style={{ display: 'none' }}
          onChange={(event) => {
            const selected = Array.from(event.target.files ?? []);
            if (selected.length) onUploadFiles(selected);
            event.target.value = '';
          }}
        />
      </div>
      <div className="app-header-activity">
      {uploads.map((item) => (
        <UploadChip
          key={item.id}
          item={item}
          onDismiss={() => onDismissUpload(item.id)}
          onPause={() => onPauseUpload(item.id)}
          onResume={() => onResumeUpload(item.id)}
          onCancel={() => onCancelUpload(item.id)}
          onOpenUploads={() => onTabChange('uploads')}
        />
      ))}
      {receiving > 0 && (
        <span className="badge" title="uploads currently being received">
          ⟳ receiving {receiving}
        </span>
      )}
      {ingesting > 0 && (
        <span className="badge" title="tests currently ingesting">
          ⟳ ingesting {ingesting}
        </span>
      )}
      {rebuilding > 0 && (
        <span className="badge" title="tests currently rebuilding">
          ⟳ rebuilding {rebuilding}
        </span>
      )}
      {notice && (
        <span style={{ fontSize: 11, color: '#569cd6' }}>{notice}</span>
      )}
      </div>
      <span className="app-header-hint">
        drop .csv anywhere to upload
      </span>
    </div>
  );
};
