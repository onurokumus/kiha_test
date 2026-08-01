import React, { useEffect, useId, useRef, useState } from 'react';
import { SearchableSelect } from './SearchableSelect';
import styles from './AxisControls.module.css';

export interface AxisControlsProps {
  columns: string[];
  xAxis: string;
  yAxis: string;
  onXAxisChange: (axis: string) => void;
  onYAxisChange: (axis: string) => void;
  mainZoom: [number, number, number, number] | null;
  onResetZoom: () => void;
  onReloadData: () => void;
  isLoading: boolean;
  clusteringAvailable: boolean;
  clusteringEnabled: boolean;
  onClusteringChange: (enabled: boolean) => void;
  datasheetZone: string;
  datasheetVisible: boolean;
  datasheetStatus: string;
  onDatasheetVisibilityChange: (visible: boolean) => void;
}

export const AxisControls: React.FC<AxisControlsProps> = ({
  columns,
  xAxis,
  yAxis,
  onXAxisChange,
  onYAxisChange,
  mainZoom,
  onResetZoom,
  onReloadData,
  isLoading,
  clusteringAvailable,
  clusteringEnabled,
  onClusteringChange,
  datasheetZone,
  datasheetVisible,
  datasheetStatus,
  onDatasheetVisibilityChange,
}) => {
  const [moreOpen, setMoreOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const moreButtonRef = useRef<HTMLButtonElement>(null);
  const popoverId = useId();

  useEffect(() => {
    if (!moreOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setMoreOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setMoreOpen(false);
      moreButtonRef.current?.focus();
    };

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [moreOpen]);

  const reloadData = () => {
    onReloadData();
    setMoreOpen(false);
  };

  return (
    <div ref={rootRef} className={styles.root}>
      <div className={styles.commandBar}>
        <span className={styles.contextLabel}>TP mean scatter</span>

        <div className={styles.axisFields}>
          <div className={styles.axisField}>
            <span className={styles.axisLabel}>X</span>
            <SearchableSelect
              value={xAxis}
              onChange={onXAxisChange}
              options={columns.map((column) => ({ value: column, label: column }))}
              className={styles.axisSelect}
              appearance="embedded"
              ariaLabel="Scatter plot X axis"
              searchPlaceholder="Search X-axis signals..."
              optionNoun="signal"
            />
          </div>

          <div className={styles.axisField}>
            <span className={styles.axisLabel}>Y</span>
            <SearchableSelect
              value={yAxis}
              onChange={onYAxisChange}
              options={columns.map((column) => ({ value: column, label: column }))}
              className={styles.axisSelect}
              appearance="embedded"
              ariaLabel="Scatter plot Y axis"
              searchPlaceholder="Search Y-axis signals..."
              optionNoun="signal"
            />
          </div>
        </div>

        {mainZoom && (
          <button type="button" onClick={onResetZoom} className={styles.resetButton}>
            Reset zoom
          </button>
        )}

        <button
          ref={moreButtonRef}
          type="button"
          className={`${styles.moreButton} ${moreOpen ? styles.moreButtonOpen : ''}`}
          aria-expanded={moreOpen}
          aria-controls={popoverId}
          onClick={() => setMoreOpen((open) => !open)}
        >
          <span>More</span>
          <span className={styles.moreGlyph} aria-hidden="true">
            •••
          </span>
        </button>
      </div>

      {moreOpen && (
        <div
          id={popoverId}
          className={styles.popover}
          role="group"
          aria-label="Scatter plot options"
        >
          <button
            type="button"
            className={styles.reloadButton}
            onClick={reloadData}
            disabled={isLoading}
            title={isLoading ? 'Loading data…' : 'Reload data'}
          >
            <span className={styles.reloadIcon} aria-hidden="true">
              ↻
            </span>
            <span className={styles.actionCopy}>
              <strong>{isLoading ? 'Reloading data…' : 'Reload data'}</strong>
              <small>Refresh test-point statistics and traces</small>
            </span>
          </button>

          {datasheetZone && (
            <div className={styles.optionRow}>
              <span className={styles.actionCopy}>
                <strong>Datasheet line</strong>
                <small title={datasheetStatus}>{datasheetStatus}</small>
              </span>
              <button
                type="button"
                className={styles.switch}
                role="switch"
                aria-label={`Show datasheet line from ${datasheetZone}`}
                aria-checked={datasheetVisible}
                onClick={() => onDatasheetVisibilityChange(!datasheetVisible)}
              >
                <span aria-hidden="true" />
              </button>
            </div>
          )}

          {clusteringAvailable && (
            <div className={styles.optionRow}>
              <span className={styles.actionCopy}>
                <strong>Cluster overlaps</strong>
                <small>Group nearby points to keep dense runs legible</small>
              </span>
              <button
                type="button"
                className={styles.switch}
                role="switch"
                aria-label="Cluster overlapping scatter points"
                aria-checked={clusteringEnabled}
                onClick={() => onClusteringChange(!clusteringEnabled)}
              >
                <span aria-hidden="true" />
              </button>
            </div>
          )}

          <div className={styles.gestureHelp}>
            <span className={styles.gestureTitle}>Scatter gestures</span>
            <ul>
              <li>
                <kbd>Click</kbd>
                <span>Select or deselect a point</span>
              </li>
              <li>
                <kbd>Scroll</kbd>
                <span>Zoom around the pointer</span>
              </li>
              <li>
                <kbd>Drag</kbd>
                <span>Pan the current view</span>
              </li>
            </ul>
          </div>
        </div>
      )}
    </div>
  );
};
