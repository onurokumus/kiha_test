import React, { useEffect, useId, useRef, useState } from 'react';
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
          <label className={styles.axisField}>
            <span className={styles.axisLabel}>X</span>
            <select
              value={xAxis}
              onChange={(event) => onXAxisChange(event.target.value)}
              className={styles.axisSelect}
              aria-label="Scatter plot X axis"
            >
              {columns.map((column) => (
                <option key={column} value={column}>
                  {column}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.axisField}>
            <span className={styles.axisLabel}>Y</span>
            <select
              value={yAxis}
              onChange={(event) => onYAxisChange(event.target.value)}
              className={styles.axisSelect}
              aria-label="Scatter plot Y axis"
            >
              {columns.map((column) => (
                <option key={column} value={column}>
                  {column}
                </option>
              ))}
            </select>
          </label>
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
