import React, { useRef } from 'react';
import { TestInfo } from '../../types';

export type AppTab = 'analyze' | 'split' | 'edit';

interface HeaderProps {
  tests: TestInfo[];
  tab: AppTab;
  onTabChange: (tab: AppTab) => void;
  onUploadFiles: (files: File[]) => void;
  notice?: string;
}

/** Top bar: branding, tabs, upload. The active-test picker lives next to the
 *  views that actually need one (mode bar, split/edit toolbars). */
export const Header: React.FC<HeaderProps> = ({
  tests,
  tab,
  onTabChange,
  onUploadFiles,
  notice,
}) => {
  const fileRef = useRef<HTMLInputElement>(null);
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
