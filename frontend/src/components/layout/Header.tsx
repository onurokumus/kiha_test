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
  notice?: string;
}

/** One upload's chip: live progress while sending, sticky red on failure. */
const UploadChip: React.FC<{ item: UploadItem; onDismiss: () => void }> = ({
  item,
  onDismiss,
}) => {
  if (item.error) {
    return (
      <span
        className="badge"
        style={{
          color: '#f48771',
          background: '#4b1d1d',
          maxWidth: 360,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
        }}
        title={`${item.fileName}: ${item.error}`}
      >
        {/* text truncates; the dismiss button must never be clipped away */}
        <span
          style={{
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          ✗ {item.fileName}: {item.error}
        </span>
        <button
          onClick={onDismiss}
          title="dismiss"
          style={{
            flex: 'none',
            background: 'none',
            border: 'none',
            color: '#f48771',
            cursor: 'pointer',
            fontSize: 11,
            padding: 0,
          }}
        >
          ✕
        </button>
      </span>
    );
  }
  const pct =
    item.progress === null
      ? '…'
      : item.progress >= 1
        ? 'finishing…' // body handed to the network; awaiting server response
        : `${Math.round(item.progress * 100)}%`;
  return (
    <span className="badge" title={`uploading ${item.fileName}`}>
      ⬆ {item.fileName} {pct}
    </span>
  );
};

/** Top bar: branding, tabs, upload. The active-test picker lives next to the
 *  views that actually need one (mode bar, split/edit toolbars). */
export const Header: React.FC<HeaderProps> = ({
  tests,
  tab,
  onTabChange,
  onUploadFiles,
  uploads,
  onDismissUpload,
  notice,
}) => {
  const fileRef = useRef<HTMLInputElement>(null);
  // Local uploads already render their own progress chip; the badge only
  // counts 'receiving' tests streaming in from elsewhere (another window).
  const localActive = uploads.filter((u) => !u.error).length;
  const receiving = Math.max(
    0,
    tests.filter((t) => t.status === 'receiving').length - localActive
  );
  const ingesting = tests.filter((t) => t.status === 'ingesting').length;
  const rebuilding = tests.filter((t) => t.status === 'rebuilding').length;

  const tabButton = (value: AppTab, label: string) => (
    <button
      className={'btn-toggle' + (tab === value ? ' active' : '')}
      onClick={() => onTabChange(value)}
    >
      {label}
    </button>
  );

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '8px 16px',
        background: '#252526',
        borderBottom: '1px solid #3c3c3c',
      }}
    >
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
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length) onUploadFiles(files);
          e.target.value = '';
        }}
      />
      {uploads.map((u) => (
        <UploadChip key={u.id} item={u} onDismiss={() => onDismissUpload(u.id)} />
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
      <span style={{ flex: 1 }} />
      <span style={{ fontSize: 10, color: '#666' }}>
        drop .csv anywhere to upload
      </span>
    </div>
  );
};
