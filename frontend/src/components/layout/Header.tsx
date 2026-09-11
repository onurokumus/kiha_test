import React, { useRef } from 'react';
import { TestInfo, UploadItem } from '../../types';

export type AppTab = 'analyze' | 'split' | 'edit' | 'uploads' | 'components' | 'settings';

interface HeaderProps {
  tests: TestInfo[];
  testListStatus: 'loading' | 'ready' | 'error';
  tab: AppTab;
  onTabChange: (tab: AppTab) => void;
  onImportFiles: (files: File[]) => void;
  uploads: UploadItem[];
  onDismissUpload: (id: number) => void;
  onPauseUpload: (id: number) => void;
  onResumeUpload: (id: number) => void;
  onCancelUpload: (id: number) => void;
  notice?: string;
  sessionControls?: React.ReactNode;
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
            <button onClick={item.requiresFile ? onOpenUploads : onResume} style={chipButton}>
              {item.requiresFile ? 'select file' : 'retry'}
            </button>
            <button onClick={onCancel} style={chipButton}>
              cancel
            </button>
          </>
        ) : (
          <button onClick={onDismiss} title="dismiss" style={chipButton}>
            ×
          </button>
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
      <span className="upload-chip-label">
        ⬆ {item.fileName} {label}
      </span>
      {item.phase === 'paused' ? (
        <button onClick={item.requiresFile ? onOpenUploads : onResume} style={chipButton}>
          {item.requiresFile ? 'select file' : 'resume'}
        </button>
      ) : (
        <button onClick={onPause} style={chipButton}>
          pause
        </button>
      )}
      <button onClick={onCancel} style={chipButton}>
        cancel
      </button>
    </span>
  );
};

/** Top bar: branding, tabs, upload, and compact transfer controls. */
export const Header: React.FC<HeaderProps> = ({
  tests,
  testListStatus,
  tab,
  onTabChange,
  onImportFiles,
  uploads,
  onDismissUpload,
  onPauseUpload,
  onResumeUpload,
  onCancelUpload,
  notice, sessionControls,
}) => {
  const importRef = useRef<HTMLInputElement>(null);
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
      type="button"
      className={'app-nav-button' + (tab === value ? ' active' : '')}
      aria-current={tab === value ? 'page' : undefined}
      onClick={() => onTabChange(value)}
    >
      {label}
    </button>
  );

  return (
    <header className="app-header">
      <div className="app-header-primary">
        <div className="app-brand">
          <img
            className="app-brand-mark"
            src={`${import.meta.env.BASE_URL}ptt-logo.webp`}
            width={32}
            height={32}
            alt=""
            decoding="async"
            draggable={false}
          />
          <span>
            Propeller Test Tool<small>Test data workbench</small>
          </span>
        </div>
        <nav className="app-nav" aria-label="Main navigation">
          {tabButton('analyze', 'Analyze')}
          {tabButton('split', 'Split')}
          {tabButton('edit', 'Edit')}
          {tabButton('uploads', 'Uploads')}
          {tabButton('components', 'Components')}
          {tabButton('settings', 'Settings')}
        </nav>
        <button className="btn app-import-button" onClick={() => importRef.current?.click()}>
          <span aria-hidden="true">＋</span> Import CSV
        </button>
        {sessionControls}
        <input
          ref={importRef}
          type="file"
          accept=".csv"
          multiple
          style={{ display: 'none' }}
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            if (files.length) onImportFiles(files);
            event.target.value = '';
          }}
        />
      </div>
      <div className="app-header-activity" aria-label="Upload activity">
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
          <span className="app-notice" role="status">
            {notice}
          </span>
        )}
      </div>
      <span className="app-header-summary" role="status">
        {testListStatus === 'ready' ? <>
          <span className="app-ready-dot" aria-hidden="true" />
          {tests.filter((test) => test.status === 'ready').length} ready tests
        </> : testListStatus === 'loading' ? 'Loading tests…' : 'Test count unavailable'}
      </span>
    </header>
  );
};
