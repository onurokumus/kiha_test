import React, { CSSProperties, useId, useState } from 'react';
import { testPointCsvUrl } from '../../services/api';
import { SelectedTestPoint, TestInfo, WindowDisplayMode } from '../../types';
import { PlotDensity } from '../../services/analysisSession';
import { TestOptions } from './TestOptions';
import styles from './SelectedPointsPanel.module.css';

type PanelViewMode = 'tp' | 'full' | 'spectrum' | 'xy';
type PanelSource = 'tp' | 'full';

interface SelectedPointsPanelProps {
  selectedTPs: SelectedTestPoint[];
  hiddenTPs: Set<string>;
  onToggleVisibility: (id: string) => void;
  onRemoveTP: (id: string) => void;
  onClearAll: () => void;
  timeZoom: [number, number] | null;
  onResetTimeZoom: () => void;
  maxPoints?: number;
  loadingTestPointIds?: Set<string>;
  isEditMode?: boolean;
  onToggleEditMode?: () => void;
  viewMode: PanelViewMode;
  onViewModeChange: (mode: PanelViewMode) => void;
  specMode: 'fft' | 'welch';
  onSpecModeChange: (mode: 'fft' | 'welch') => void;
  specLogY: boolean;
  onSpecLogYChange: (logY: boolean) => void;
  /** Active test for full-test-sourced views (moved here from the header). */
  tests: TestInfo[];
  currentTest: string;
  onTestChange: (test: string) => void;
  fullPlotMode: WindowDisplayMode;
  onFullPlotModeChange: (mode: WindowDisplayMode) => void;
  specSource: PanelSource;
  onSpecSourceChange: (s: PanelSource) => void;
  xySource: PanelSource;
  onXYSourceChange: (s: PanelSource) => void;
  plotDensity: PlotDensity;
  onPlotDensityChange: (density: PlotDensity) => void;
}

const VIEW_MODES: Array<{ value: PanelViewMode; label: string }> = [
  { value: 'tp', label: 'Test points' },
  { value: 'full', label: 'Full test' },
  { value: 'spectrum', label: 'Spectrum' },
  { value: 'xy', label: 'XY' },
];

const DENSITIES: Array<{ value: PlotDensity; label: string; description: string }> = [
  { value: 'single', label: '1', description: 'Show one plot' },
  { value: 'quad', label: '4', description: 'Show four plots' },
  { value: 'nine', label: '9', description: 'Show nine plots' },
];

const FULL_PLOT_MODES: Array<{
  value: WindowDisplayMode;
  label: string;
  description: string;
}> = [
  {
    value: 'auto',
    label: 'Auto',
    description: 'Choose line or min/max from the visible sample count',
  },
  {
    value: 'line',
    label: 'Line',
    description: 'Show one trace; very wide ranges are safely thinned',
  },
  {
    value: 'envelope',
    label: 'Min/max',
    description: 'Show the minimum and maximum envelope',
  },
];

const RACK_SLOT_COUNT = 6;

export const SelectedPointsPanel: React.FC<SelectedPointsPanelProps> = ({
  selectedTPs,
  hiddenTPs,
  onToggleVisibility,
  onRemoveTP,
  onClearAll,
  timeZoom,
  onResetTimeZoom,
  maxPoints = 6,
  loadingTestPointIds = new Set(),
  isEditMode = false,
  onToggleEditMode,
  viewMode,
  onViewModeChange,
  specMode,
  onSpecModeChange,
  specLogY,
  onSpecLogYChange,
  tests,
  currentTest,
  onTestChange,
  fullPlotMode,
  onFullPlotModeChange,
  specSource,
  onSpecSourceChange,
  xySource,
  onXYSourceChange,
  plotDensity,
  onPlotDensityChange,
}) => {
  const [selectionOpen, setSelectionOpen] = useState(false);
  const selectionTrayId = useId();
  const activeSource = viewMode === 'spectrum' ? specSource : xySource;
  const hasSourceControl = viewMode === 'spectrum' || viewMode === 'xy';
  const hasTestControl =
    viewMode === 'full' ||
    (viewMode === 'spectrum' && specSource === 'full') ||
    (viewMode === 'xy' && xySource === 'full');
  const hasContextControls = hasSourceControl || hasTestControl;

  const selectionSummary =
    selectedTPs.length === 0
      ? `No test points selected. ${maxPoints} may be selected.`
      : `${selectedTPs.length} of ${maxPoints} test points selected.`;

  return (
    <section className={styles.deck} aria-label="Analysis controls">
      <div className={styles.commandRow}>
        <div className={styles.modeGroup} role="group" aria-label="Plot mode">
          {VIEW_MODES.map((mode) => (
            <button
              key={mode.value}
              type="button"
              className={styles.segmentButton}
              aria-pressed={viewMode === mode.value}
              onClick={() => onViewModeChange(mode.value)}
            >
              {mode.label}
            </button>
          ))}
        </div>

        <div className={styles.toolGroup} aria-label="Plot tools">
          <div className={styles.densityControl}>
            <span className={styles.utilityLabel}>Layout</span>
            <div className={styles.densityButtons} role="group" aria-label="Plot layout">
              {DENSITIES.map((density) => (
                <button
                  key={density.value}
                  type="button"
                  className={styles.compactButton}
                  aria-pressed={plotDensity === density.value}
                  title={density.description}
                  onClick={() => onPlotDensityChange(density.value)}
                >
                  {density.label}
                </button>
              ))}
            </div>
          </div>

          {timeZoom && (
            <button
              type="button"
              className={styles.toolButton}
              onClick={onResetTimeZoom}
              title="Reset the time range shown in all plots"
            >
              Reset zoom
            </button>
          )}

          {onToggleEditMode && (
            <button
              type="button"
              className={styles.toolButton}
              aria-pressed={isEditMode}
              onClick={onToggleEditMode}
            >
              {isEditMode ? 'Editing plots' : 'Edit plots'}
            </button>
          )}
        </div>
      </div>

      {hasContextControls && (
        <div className={styles.contextRow} aria-label="Current mode options">
          <span className={styles.contextLabel}>Mode setup</span>

          {hasSourceControl && (
            <div className={styles.contextControl}>
              <span className={styles.utilityLabel}>Source</span>
              <div className={styles.sourceButtons} role="group" aria-label="Data source">
                {(['tp', 'full'] as PanelSource[]).map((source) => (
                  <button
                    key={source}
                    type="button"
                    className={styles.contextButton}
                    aria-pressed={activeSource === source}
                    onClick={() =>
                      viewMode === 'spectrum'
                        ? onSpecSourceChange(source)
                        : onXYSourceChange(source)
                    }
                    title={
                      source === 'tp'
                        ? 'Compute over the selected test points'
                        : 'Compute over the active test or its zoom range'
                    }
                  >
                    {source === 'tp' ? 'Selected TPs' : 'Full test'}
                  </button>
                ))}
              </div>
            </div>
          )}

          {hasTestControl && (
            <label className={styles.contextControl}>
              <span className={styles.utilityLabel}>Test</span>
              <select
                className={styles.select}
                value={currentTest}
                onChange={(event) => onTestChange(event.target.value)}
              >
                <TestOptions tests={tests} />
              </select>
            </label>
          )}

          {viewMode === 'full' && (
            <div className={styles.contextControl}>
              <span className={styles.utilityLabel}>Trace</span>
              <div
                className={styles.traceButtons}
                role="group"
                aria-label="Full-test trace style"
              >
                {FULL_PLOT_MODES.map((mode) => (
                  <button
                    key={mode.value}
                    type="button"
                    className={styles.contextButton}
                    aria-pressed={fullPlotMode === mode.value}
                    title={mode.description}
                    onClick={() => onFullPlotModeChange(mode.value)}
                  >
                    {mode.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {viewMode === 'spectrum' && (
            <>
              <label className={styles.contextControl}>
                <span className={styles.utilityLabel}>Estimator</span>
                <select
                  className={styles.select}
                  value={specMode}
                  onChange={(event) => onSpecModeChange(event.target.value as 'fft' | 'welch')}
                >
                  <option value="fft">FFT magnitude</option>
                  <option value="welch">Welch PSD</option>
                </select>
              </label>
              <button
                type="button"
                className={styles.contextButton}
                aria-pressed={specLogY}
                onClick={() => onSpecLogYChange(!specLogY)}
                title="Use a log10 magnitude axis"
              >
                Log scale
              </button>
            </>
          )}
        </div>
      )}

      <div className={styles.selectionTray}>
        <button
          type="button"
          className={styles.trayToggle}
          aria-expanded={selectionOpen}
          aria-controls={selectionTrayId}
          aria-label={`${selectionSummary} ${
            selectionOpen ? 'Collapse selection tray.' : 'Expand selection tray.'
          }`}
          onClick={() => setSelectionOpen((open) => !open)}
        >
          <span className={styles.trayTitle}>Selected points</span>
          <span className={styles.colorRack} aria-hidden="true">
            {Array.from({ length: RACK_SLOT_COUNT }, (_, index) => {
              const point = selectedTPs[index];
              const pointStyle = point
                ? ({ '--rack-color': point.color } as CSSProperties)
                : undefined;
              return (
                <span
                  key={index}
                  className={`${styles.rackSlot} ${
                    point ? styles.rackSlotFilled : styles.rackSlotEmpty
                  } ${point && hiddenTPs.has(point.id) ? styles.rackSlotHidden : ''}`}
                  style={pointStyle}
                />
              );
            })}
          </span>
          <span className={styles.selectionCount} aria-live="polite" aria-atomic="true">
            <strong>{selectedTPs.length}</strong>
            <span aria-hidden="true"> / </span>
            <span className={styles.screenReaderOnly}> of </span>
            {maxPoints}
          </span>
          <span className={styles.chevron} aria-hidden="true" />
        </button>

        {selectionOpen && (
          <div id={selectionTrayId} className={styles.trayBody}>
            {selectedTPs.length === 0 ? (
              <p className={styles.emptyMessage}>
                Select points on the scatter plot to compare their traces.
              </p>
            ) : (
              <>
                <ul className={styles.selectionList} aria-label="Selected test points">
                  {selectedTPs.map((point) => {
                    const isVisible = !hiddenTPs.has(point.id);
                    const isLoading = loadingTestPointIds.has(point.id);
                    const selectionColor = {
                      '--selection-color': point.color,
                    } as CSSProperties;

                    return (
                      <li
                        key={point.id}
                        className={`${styles.selectionItem} ${
                          isVisible ? '' : styles.selectionItemHidden
                        }`}
                        style={selectionColor}
                      >
                        <button
                          type="button"
                          className={styles.visibilityButton}
                          aria-pressed={isVisible}
                          aria-label={`${isVisible ? 'Hide' : 'Show'} ${point.name} from ${
                            point.test
                          }`}
                          title={`${isVisible ? 'Hide' : 'Show'} this test point`}
                          onClick={() => onToggleVisibility(point.id)}
                        >
                          <span className={styles.pointSwatch} aria-hidden="true" />
                          <span className={styles.pointText}>
                            <span className={styles.pointName}>{point.name}</span>
                            <span
                              className={styles.pointTest}
                              title={`${point.test}${point.label ? ` — ${point.label}` : ''}`}
                            >
                              {point.test}
                            </span>
                          </span>
                          {isLoading && (
                            <span
                              className={styles.loadingIndicator}
                              role="status"
                              aria-label={`Loading ${point.name}`}
                            />
                          )}
                        </button>
                        <a
                          className={styles.itemAction}
                          href={testPointCsvUrl(point.test, point.tpId)}
                          download
                          aria-label={`Download ${point.name} from ${point.test} as CSV`}
                          title="Download all columns as CSV"
                        >
                          CSV
                        </a>
                        <button
                          type="button"
                          className={styles.itemAction}
                          aria-label={`Remove ${point.name} from the selection`}
                          title="Remove from selection"
                          onClick={() => onRemoveTP(point.id)}
                        >
                          ×
                        </button>
                      </li>
                    );
                  })}
                </ul>
                <button type="button" className={styles.clearButton} onClick={onClearAll}>
                  Clear selection
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </section>
  );
};
