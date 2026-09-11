import type { ReactNode } from 'react';
import styles from './PlotHeader.module.css';

interface Props {
  label: string;
  summary?: ReactNode;
  status?: ReactNode;
  isExpanded: boolean;
  onToggleExpand: () => void;
  expandLabel?: string;
  actions: ReactNode;
  children?: ReactNode;
}

/** Keep every grid mode's controls in a fixed-width pair, leaving the rest of
 * the header for source labels (including any units) and scientific context. */
export function PlotHeader({ label, summary, status, isExpanded, onToggleExpand,
  expandLabel = label, actions, children }: Props) {
  return <div className={styles.header} data-plot-header>
    <div className={styles.identity}>
      <div className={styles.title} title={label}>{label}</div>
      {summary && <div className={styles.summary}>{summary}</div>}
      {status && <div className={styles.status}>{status}</div>}
    </div>
    <div className={styles.actions} data-plot-toolbar>
      <button type="button" onClick={onToggleExpand} className={styles.expand}
        aria-label={`${isExpanded ? 'Minimize' : 'Expand'} ${expandLabel}`}
        title={`${isExpanded ? 'Minimize' : 'Expand'} this plot`}>
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
          <path d={isExpanded ? 'M2 6h4V2m8 8h-4v4M2 14l4-4m8-8-4 4' : 'M6 2H2v4m8 8h4v-4M2 2l4 4m8 8-4-4'} />
        </svg>
      </button>
      {actions}
    </div>
    {children}
  </div>;
}
