import React from 'react';
import styles from './PlotState.module.css';

export interface PlotEmptyState {
  title: string;
  detail?: string;
}

export interface PlotStateOverlayProps {
  /** True while the current plot request is in flight. */
  loading: boolean;
  /** True when a usable chart is already mounted beneath this layer. */
  hasData: boolean;
  /** Request error text. When data exists, the layer explains that it is stale. */
  error?: string | null;
  /** Rendered after a completed, successful request that returned no data. */
  emptyState?: PlotEmptyState | null;
  /** Re-run the request that produced `error`. */
  onRetry?: () => void;
  loadingLabel?: string;
  updatingLabel?: string;
  errorTitle?: string;
  /** Non-blocking partial-result notice; does not dim the chart. */
  partialMessage?: string | null;
}

const ActivityMark: React.FC = () => (
  <span className={styles.activityMark} aria-hidden="true">
    <span />
    <span />
    <span />
    <span />
    <span />
  </span>
);

const EmptyMark: React.FC = () => (
  <svg className={styles.emptyMark} viewBox="0 0 48 28" role="presentation" aria-hidden="true">
    <path d="M2 14h8l4-9 7 18 6-13 5 8h14" />
  </svg>
);

/**
 * Shared plot-state layer. Place it as the final child of a
 * `position: relative; overflow: hidden` chart viewport.
 */
export const PlotStateOverlay: React.FC<PlotStateOverlayProps> = ({
  loading,
  hasData,
  error,
  emptyState,
  onRetry,
  loadingLabel = 'Loading telemetry',
  updatingLabel = 'Updating view',
  errorTitle = hasData ? 'Could not update this view' : 'Could not load this view',
  partialMessage,
}) => {
  const hasError = Boolean(error);
  const initialLoad = loading && !hasData && !hasError;
  const updating = loading && hasData && !hasError;
  const empty = !loading && !hasError && !hasData && Boolean(emptyState);
  const dimData = hasData && (updating || hasError);

  return (
    <>
      {dimData && <div className={styles.dataShade} aria-hidden="true" />}

      {initialLoad && (
        <div className={styles.fullState} role="status" aria-live="polite">
          <div className={styles.stateCard}>
            <ActivityMark />
            <div>
              <div className={styles.stateTitle}>{loadingLabel}</div>
              <div className={styles.stateDetail}>Preparing the latest samples...</div>
            </div>
          </div>
        </div>
      )}

      {updating && (
        <div className={styles.statusPill} role="status" aria-live="polite">
          <ActivityMark />
          <span>{updatingLabel}</span>
          <span className={styles.staleNote}>showing previous data</span>
        </div>
      )}

      {hasError && (
        <div
          className={hasData ? styles.errorStrip : styles.fullState}
          role="alert"
          aria-live="assertive"
        >
          <div className={hasData ? styles.errorStripInner : styles.errorCard}>
            <span className={styles.errorMark} aria-hidden="true">
              !
            </span>
            <div className={styles.messageCopy}>
              <div className={styles.stateTitle}>{errorTitle}</div>
              <div className={styles.stateDetail}>
                {hasData && <span>Showing the last available data. </span>}
                {error}
              </div>
            </div>
            {onRetry && (
              <button type="button" className={styles.retryButton} onClick={onRetry}>
                Retry
              </button>
            )}
          </div>
        </div>
      )}

      {empty && emptyState && (
        <div className={styles.fullState} role="status" aria-live="polite">
          <div className={styles.emptyCard}>
            <EmptyMark />
            <div className={styles.stateTitle}>{emptyState.title}</div>
            {emptyState.detail && <div className={styles.stateDetail}>{emptyState.detail}</div>}
          </div>
        </div>
      )}

      {!loading && !hasError && hasData && partialMessage && (
        <div className={styles.partialPill} role="status">
          <span className={styles.partialMark} aria-hidden="true" />
          <span>{partialMessage}</span>
          {onRetry && (
            <button type="button" className={styles.inlineRetry} onClick={onRetry}>
              Retry
            </button>
          )}
        </div>
      )}
    </>
  );
};
