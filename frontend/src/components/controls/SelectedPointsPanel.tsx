import React, { CSSProperties, useMemo } from 'react';
import { testPointCsvUrl } from '../../services/api';
import {
  SelectedTestPoint,
  SpectrumXAxis,
  TestInfo,
  WindowDisplayMode,
} from '../../types';
import { PlotDensity } from '../../services/analysisSession';
import { MAX_SELECTED_TEST_POINTS } from '../../constants/selection';
import { WATERFALL_WINDOWS, WATERFALL_OVERLAPS, WATERFALL_RESOLUTIONS, WaterfallBand } from '../../constants/waterfall';
import { SearchableSelect } from './SearchableSelect';
import { TestSelect } from './TestSelect';
import { AnalysisOptionsPopover } from './AnalysisOptionsPopover';
import styles from './SelectedPointsPanel.module.css';

type PanelViewMode = 'tp' | 'full' | 'spectrum' | 'xy';
type PanelSource = 'tp' | 'full';
type AnalysisView = 'time' | 'spectrum' | 'xy';

interface SelectedPointsPanelProps {
  exportTargetRef: React.Ref<HTMLDivElement>;
  detailTargetRef: React.Ref<HTMLSpanElement>;
  selectedTPs: SelectedTestPoint[];
  hiddenTPs: Set<string>;
  onToggleVisibility: (id: string) => void;
  onRemoveTP: (id: string) => void;
  onClearAll: () => void;
  timeZoom: [number, number] | null;
  hasYZoom?: boolean;
  onResetTimeZoom: () => void;
  maxPoints?: number;
  loadingTestPointIds?: Set<string>;
  isEditMode?: boolean;
  onToggleEditMode?: () => void;
  viewMode: PanelViewMode;
  onViewModeChange: (mode: PanelViewMode) => void;
  onWaterfallWindowChange: (value: number) => void;
  onWaterfallOverlapChange: (value: number) => void;
  onWaterfallResolutionChange: (value: number | null) => void;
  onWaterfallBandChange: (value: WaterfallBand) => void;
  waterfallWindow: number;
  waterfallOverlap: number;
  waterfallResolution: number | null;
  waterfallBand: WaterfallBand;
  specMode: 'fft' | 'welch' | 'waterfall';
  onSpecModeChange: (mode: 'fft' | 'welch' | 'waterfall') => void;
  specXAxis: SpectrumXAxis;
  onSpecXAxisChange: (axis: SpectrumXAxis) => void;
  specRpmCol: string;
  onSpecRpmColChange: (column: string) => void;
  spectrumColumns: string[];
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

const VIEW_MODES: Array<{ value: AnalysisView; label: string }> = [
  { value: 'time', label: 'Time' },
  { value: 'spectrum', label: 'Spectrum' },
  { value: 'xy', label: 'XY' },
];

const SPECTRUM_MODES = [
  { value: 'fft', label: 'FFT', description: 'FFT magnitude' },
  { value: 'welch', label: 'PSD', description: 'Welch power spectral density' },
  { value: 'waterfall', label: 'Waterfall', description: 'FFT magnitude over time' },
] as const;

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

export const SelectedPointsPanel: React.FC<SelectedPointsPanelProps> = ({
  exportTargetRef,
  detailTargetRef,
  selectedTPs,
  hiddenTPs,
  onToggleVisibility,
  onRemoveTP,
  onClearAll,
  timeZoom,
  hasYZoom = false,
  onResetTimeZoom,
  maxPoints = MAX_SELECTED_TEST_POINTS,
  loadingTestPointIds = new Set(),
  viewMode,
  onViewModeChange,
  waterfallWindow, waterfallOverlap, onWaterfallWindowChange, onWaterfallOverlapChange,
  waterfallResolution, waterfallBand, onWaterfallResolutionChange, onWaterfallBandChange,
  specMode,
  onSpecModeChange,
  specXAxis,
  onSpecXAxisChange,
  specRpmCol,
  onSpecRpmColChange,
  spectrumColumns,
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
  const activeSource: PanelSource = viewMode === 'spectrum'
    ? specSource
    : viewMode === 'xy' ? xySource : viewMode;
  const activeView: AnalysisView = viewMode === 'tp' || viewMode === 'full' ? 'time' : viewMode;

  // Keep the existing persisted mode/source fields, but make each user action
  // carry its source across views instead of silently switching datasets.
  const changeSource = (source: PanelSource) => {
    onSpecSourceChange(source);
    onXYSourceChange(source);
    if (activeView === 'time') onViewModeChange(source);
  };
  const changeView = (view: AnalysisView) => {
    onSpecSourceChange(activeSource);
    onXYSourceChange(activeSource);
    onViewModeChange(view === 'time' ? activeSource : view);
  };
  const rpmColumnOptions = useMemo(() => {
    const score = (column: string) => {
      const normalized = column.toLocaleLowerCase();
      if (normalized === 'rpm') return 0;
      if (/(^|[^a-z0-9])rpm([^a-z0-9]|$)/i.test(column)) return 1;
      if (normalized.includes('rpm')) return 2;
      return 3;
    };
    return spectrumColumns
      .map((column, index) => ({ column, index }))
      .sort((a, b) => score(a.column) - score(b.column) || a.index - b.index)
      .map(({ column }) => ({
        value: column,
        label: column,
        keywords: score(column) < 3 ? ['rpm', 'speed', 'shaft'] : undefined,
      }));
  }, [spectrumColumns]);

  const sharedSelectionTest = selectedTPs.length > 0 && selectedTPs.every(point => point.test === selectedTPs[0].test)
    ? selectedTPs[0].test : null;
  const optionsSummary = activeView === 'spectrum'
    ? specMode === 'waterfall'
      ? `${waterfallResolution === null ? `${waterfallWindow} samples` : `${waterfallResolution} Hz`} · ${waterfallBand === 'low' ? '0–200 Hz' : 'Full band'}${specLogY ? ' · Log color' : ''}`
      : `${specXAxis === 'hz' ? 'Hz' : 'Per rev'} · ${specLogY ? 'Log scale' : 'Linear'}${specXAxis === 'per_rev' && !specRpmCol ? ' · Choose RPM' : ''}`
    : FULL_PLOT_MODES.find(mode => mode.value === fullPlotMode)?.label;

  return (
    <section className={styles.deck} aria-label="Analysis controls">
      <div className={styles.commandRow}>
        <div className={styles.navigationGroup}>
        <div className={styles.primaryControls}>
          <div className={styles.contextControl}>
            <div className={styles.sourceButtons} role="group" aria-label="Data source">
              {(['tp', 'full'] as PanelSource[]).map(source => (
                <button
                  key={source}
                  type="button"
                  className={styles.segmentButton}
                  aria-pressed={activeSource === source}
                  onClick={() => changeSource(source)}
                >
                  {source === 'tp' ? 'Selected points' : 'Full test'}
                </button>
              ))}
            </div>
          </div>
          <div className={`${styles.contextControl} ${styles.viewControl}`}>
            <div className={styles.modeGroup} role="group" aria-label="Plot view">
              {VIEW_MODES.map(mode => (
                <button
                  key={mode.value}
                  type="button"
                  className={styles.segmentButton}
                  aria-pressed={activeView === mode.value}
                  onClick={() => changeView(mode.value)}
                >
                  {mode.label}
                </button>
              ))}
            </div>
          </div>
        </div>

          {activeSource === 'full' ? (
            <div className={`${styles.contextControl} ${styles.testControl}`}>
              <TestSelect
                tests={tests}
                className={styles.testSelect}
                value={currentTest}
                onChange={onTestChange}
                ariaLabel="Active test"
              />
            </div>
          ) : (
            <div className={styles.testControl} data-empty aria-hidden="true" />
          )}
        </div>

        <div className={styles.analysisGroup} data-analysis-tools aria-label="Analysis and layout tools">
          <span className={styles.resetSlot} hidden={!timeZoom && !hasYZoom}>
            <button
              type="button"
              className={styles.toolButton}
              aria-label="Reset zoom"
              disabled={!timeZoom && !hasYZoom}
              tabIndex={timeZoom || hasYZoom ? undefined : -1}
              onClick={onResetTimeZoom}
              title={viewMode === 'tp' ? 'Reset time and all test-point Y ranges' : 'Reset the time range shown in all plots'}
            >
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 6a5 5 0 1 1-.2 4M3 2v4h4" /></svg>
            </button>
          </span>
            <div className={`${styles.sourceButtons} ${styles.spectrumSlot}`} data-visible={activeView === 'spectrum'}
              role="group" aria-label="Spectrum type" aria-hidden={activeView !== 'spectrum'}>
              {SPECTRUM_MODES.map(mode => (
                <button
                  key={mode.value}
                  type="button"
                  className={styles.segmentButton}
                  aria-pressed={specMode === mode.value}
                  title={mode.description}
                  tabIndex={activeView === 'spectrum' ? undefined : -1}
                  onClick={() => onSpecModeChange(mode.value)}
                >
                  {mode.label}
                </button>
              ))}
            </div>
            <AnalysisOptionsPopover contextKey={`${activeView}-${activeSource}-${specMode}`}
              available={activeView === 'spectrum' || viewMode === 'full'} summary={optionsSummary}>
                {viewMode === 'full' && (
                  <div className={styles.contextControl}>
                    <span className={styles.utilityLabel}>Trace</span>
                    <div className={styles.traceButtons} role="group" aria-label="Full-test trace style">
                      {FULL_PLOT_MODES.map(mode => (
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
                {activeView === 'spectrum' && (
                  <>
                    {specMode !== 'waterfall' && (
                      <div className={styles.contextControl}>
                        <span className={styles.utilityLabel}>X axis</span>
                        <div className={styles.sourceButtons} role="group" aria-label="Spectrum x-axis">
                          {([
                            ['hz', 'Hz'],
                            ['per_rev', 'Per rev'],
                          ] as Array<[SpectrumXAxis, string]>).map(([axis, label]) => (
                            <button
                              key={axis}
                              type="button"
                              className={styles.contextButton}
                              aria-pressed={specXAxis === axis}
                              onClick={() => onSpecXAxisChange(axis)}
                              title={axis === 'hz' ? 'Frequency in hertz' : 'Cycles per revolution using mean RPM over each spectrum interval'}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                    {specMode === 'waterfall' && (
                      <>
                        <label className={styles.contextControl}>
                          <span className={styles.utilityLabel}>Bin spacing</span>
                          <select
                            className={styles.select}
                            value={waterfallResolution ?? 'manual'}
                            title="Longer windows resolve closer frequencies; each map reports its actual spacing."
                            onChange={event => onWaterfallResolutionChange(event.target.value === 'manual' ? null : Number(event.target.value))}
                          >
                            {WATERFALL_RESOLUTIONS.map(({ hz, label }) => <option key={hz} value={hz}>{label}</option>)}
                            <option value="manual">Manual window</option>
                          </select>
                        </label>
                        <label className={styles.contextControl}>
                          <span className={styles.utilityLabel}>Band</span>
                          <select className={styles.select} value={waterfallBand}
                            onChange={event => onWaterfallBandChange(event.target.value as WaterfallBand)}>
                            <option value="low">0–200 Hz</option>
                            <option value="full">Full range</option>
                          </select>
                        </label>
                        {waterfallResolution === null && (
                          <label className={styles.contextControl}>
                            <span className={styles.utilityLabel}>Window (samples)</span>
                            <select className={styles.select} value={waterfallWindow}
                              onChange={event => onWaterfallWindowChange(Number(event.target.value))}>
                              {WATERFALL_WINDOWS.map(n => <option key={n} value={n}>{n}</option>)}
                            </select>
                          </label>
                        )}
                        <label className={styles.contextControl}>
                          <span className={styles.utilityLabel}>Overlap</span>
                          <select className={styles.select} value={waterfallOverlap}
                            onChange={event => onWaterfallOverlapChange(Number(event.target.value))}>
                            {WATERFALL_OVERLAPS.map(n => <option key={n} value={n}>{n}%</option>)}
                          </select>
                        </label>
                      </>
                    )}
                    {specMode !== 'waterfall' && specXAxis === 'per_rev' && (
                      <div className={styles.contextControl}>
                        <span className={styles.utilityLabel}>RPM</span>
                        <SearchableSelect
                          className={styles.rpmSelect}
                          value={specRpmCol}
                          options={rpmColumnOptions}
                          onChange={onSpecRpmColChange}
                          ariaLabel="RPM variable for per-revolution spectrum"
                          placeholder="Choose RPM variable"
                          searchPlaceholder="Search RPM variables..."
                          emptyMessage="No variables available"
                          optionNoun="variable"
                          title="Mean absolute RPM over each FFT/PSD interval"
                          menuMinWidth={300}
                        />
                      </div>
                    )}
                    <button
                      type="button"
                      className={styles.contextButton}
                      aria-pressed={specLogY}
                      onClick={() => onSpecLogYChange(!specLogY)}
                      title={specMode === 'waterfall' ? 'Color shows log10(magnitude in U); zero uses the color floor' : 'Use a log10 magnitude axis'}
                    >
                      {specMode === 'waterfall' ? 'Log color' : 'Log scale'}
                    </button>
                  </>
                )}
            </AnalysisOptionsPopover>
        <div className={styles.toolGroup} aria-label="Plot tools">
          <span ref={detailTargetRef} className={styles.displayDetailTarget} />
          <div className={styles.densityControl}>
            <div className={styles.densityButtons} role="group" aria-label="Plot layout">
              {DENSITIES.map(density => (
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
        </div>
        </div>
      </div>

      <div className={styles.selectionTray}>
        <div className={styles.selectionHeading}>
          <span className={styles.trayTitle}>Selected points</span>
          <span
            className={styles.selectionCount}
            data-at-limit={selectedTPs.length >= maxPoints || undefined}
            aria-live="polite"
            aria-atomic="true"
            aria-label={`${selectedTPs.length} of ${maxPoints} test points selected`}
          >
            {selectedTPs.length} / {maxPoints}
          </span>
          {sharedSelectionTest && <span className={styles.sharedTest} title={sharedSelectionTest}>{sharedSelectionTest}</span>}
          <div className={styles.selectionActions}>
            <button type="button" className={styles.clearButton} onClick={onClearAll} disabled={selectedTPs.length === 0} aria-label="Clear selection">
              Clear
            </button>
            <div ref={exportTargetRef} className={styles.selectionExport} />
          </div>
        </div>
        <div className={styles.selectionBody}>
          <ul className={styles.selectionList} aria-label="Selected test points" tabIndex={selectedTPs.length > 0 ? 0 : undefined}>
            {selectedTPs.map(point => {
              const isVisible = !hiddenTPs.has(point.id);
              const isLoading = loadingTestPointIds.has(point.id);
              const selectionColor = { '--selection-color': point.color } as CSSProperties;
              return (
                <li
                  key={point.id}
                  className={`${styles.selectionItem} ${isVisible ? '' : styles.selectionItemHidden}`}
                  style={selectionColor}
                >
                  <button
                    type="button"
                    className={styles.visibilityButton}
                    aria-pressed={isVisible}
                    aria-label={`${isVisible ? 'Hide' : 'Show'} ${point.name} from ${point.test}`}
                    title={`${point.name} · ${point.test}${point.label ? ` — ${point.label}` : ''}`}
                    onClick={() => onToggleVisibility(point.id)}
                  >
                    <span className={styles.pointSwatch} aria-hidden="true" />
                    <span className={styles.pointName}>{point.name}</span>
                    {!sharedSelectionTest && <span className={styles.pointTest}>{point.test}</span>}
                    {isLoading && <span className={styles.loadingIndicator} role="status" aria-label={`Loading ${point.name}`} />}
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
          {selectedTPs.length === 0 && <span className={styles.emptyMessage}>Choose points from the scatter plot</span>}
        </div>
      </div>
    </section>
  );
};
