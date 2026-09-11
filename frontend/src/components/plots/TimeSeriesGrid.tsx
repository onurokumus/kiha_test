import { PlotViewports, PlotViewport } from '../../utils/plotViewport';
import { AnalysisSource } from '../../services/sessionSources';
import React, { useMemo } from 'react';
import {
  FilterSpec,
  SelectedTestPoint,
  StatsCache,
  SpectrumXAxis,
  TimePlotConfig,
  WindowDisplayMode,
} from '../../types';
import { DEFAULT_FILTER_UI, FilterUi } from '../../constants/filters';
import { TimePlot } from './TimePlot';
import { FullTestPlot } from './FullTestPlot';
import { WaterfallPlot } from './WaterfallPlot';
import { SpectrumPlot } from './SpectrumPlot';
import { XYPlot } from './XYPlot';
import { AxisRange } from '../../utils/timePlotRanges';
import { createPlotExportRegistry } from '../../utils/plotExportRegistry';
import { MultiPlotExportControls } from '../controls/MultiPlotExportControls';
import { useAnnotations } from '../../hooks/useAnnotations';
import styles from './TimeSeriesGrid.module.css';

export type TimeViewMode = 'tp' | 'full' | 'spectrum' | 'xy';
export type TimeSeriesGridDensity = 'single' | 'quad' | 'nine';

interface TimeSeriesGridProps {
  viewports?: PlotViewports;
  sourceCatalog?: AnalysisSource[];
  onViewportChange?: (kind: keyof PlotViewports, index: number, value: PlotViewport | null) => void;
  viewMode: TimeViewMode;
  /** Number of plots shown in the normal grid. Expanded mode always shows one. */
  density?: TimeSeriesGridDensity;
  test: string;
  columns: string[];
  selectedTPs: SelectedTestPoint[];
  hiddenTPs: Set<string>;
  /** Selected-point trace failures keyed by `${selection.id}|${column}`. */
  traceErrors?: Record<string, string>;
  onRetryTraces?: () => void;
  statsCache: StatsCache;
  statsErrors: Record<string, string>;
  onRetryStatistics: (keys: string[]) => void;
  onBrowseFullTest?: () => void;
  expandedPlot: number | null;
  onToggleExpand: (index: number) => void;
  timeZoom: [number, number] | null;
  onTimeZoomChange: (domain: [number, number]) => void;
  onTimeZoomReset: () => void;
  timeYRanges: (AxisRange | null)[];
  timeZoomResetVersion: number;
  onTimeYRangeChange: (index: number, range: AxisRange | null) => void;
  fullPlotMode: WindowDisplayMode;
  waterfallWindow: number;
  waterfallOverlap: number;
  specMode: 'fft' | 'welch' | 'waterfall';
  specXAxis: SpectrumXAxis;
  specRpmCol: string;
  specLogY: boolean;
  specSource: 'tp' | 'full';
  /** Active test's sample rate (Nyquist hint in per-plot filter rows). */
  fs: number | null;
  /** Per-plot DSP filters, index-aligned with plotConfigs. */
  plotFilters: FilterUi[];
  plotFilterSpecs: (FilterSpec | null)[];
  plotShowOriginal: boolean[];
  annotationsVisible: boolean;
  onAnnotationsVisibleChange: (visible: boolean) => void;
  onPlotShowOriginalChange: (index: number, show: boolean) => void;
  onPlotFilterChange?: (index: number, patch: Partial<FilterUi>) => void;
  xySource: 'tp' | 'full';
  /** XY mode per-plot columns, index-aligned with plotConfigs. Y '' = follow
   *  the shared grid slot; editing an XY Y never touches plotConfigs. */
  xyYCols: string[];
  onXYYColChange?: (index: number, col: string) => void;
  xyXCols: string[];
  onXYXColChange?: (index: number, col: string) => void;
  columnsByTest: Record<string, string[]>;
  isEditMode?: boolean;
  plotConfigs: string[];
  onPlotConfigChange?: (configs: string[]) => void;
}

export const TimeSeriesGrid: React.FC<TimeSeriesGridProps> = ({
  viewMode,
  viewports, sourceCatalog = [], onViewportChange,
  density = 'nine',
  test,
  columns,
  selectedTPs,
  hiddenTPs,
  traceErrors = {},
  onRetryTraces,
  statsCache,
  statsErrors,
  onRetryStatistics,
  onBrowseFullTest,
  expandedPlot,
  onToggleExpand,
  timeZoom,
  onTimeZoomChange,
  onTimeZoomReset,
  timeYRanges,
  timeZoomResetVersion,
  onTimeYRangeChange,
  fullPlotMode,
  waterfallWindow, waterfallOverlap,
  specMode,
  specXAxis,
  specRpmCol,
  specLogY,
  specSource,
  fs,
  plotFilters,
  plotFilterSpecs,
  plotShowOriginal,
  annotationsVisible,
  onAnnotationsVisibleChange,
  onPlotShowOriginalChange,
  onPlotFilterChange,
  xySource,
  xyYCols,
  onXYYColChange,
  xyXCols,
  onXYXColChange,
  columnsByTest,
  isEditMode = false,
  plotConfigs,
  onPlotConfigChange,
}) => {
  const exportRegistry = useMemo(createPlotExportRegistry, []);
  const annotations = useAnnotations(viewMode === 'full' ? [test]
    : viewMode === 'tp' ? selectedTPs.map((point) => point.test) : []);
  const annotationProps = { annotations, annotationsVisible, onAnnotationsVisibleChange };
  const densityClass: Record<TimeSeriesGridDensity, string> = {
    single: styles.gridSingle,
    quad: styles.gridQuad,
    nine: styles.gridNine,
  };
  const gridClass = `${styles.gridContainer} ${
    expandedPlot === null ? densityClass[density] : styles.gridExpanded
  }`;

  const allConfigs: TimePlotConfig[] = columns.map((c) => ({ key: c, label: c }));

  const handleConfigChange = (index: number, newKey: string) => {
    if (onPlotConfigChange) {
      const newConfigs = [...plotConfigs];
      newConfigs[index] = newKey;
      onPlotConfigChange(newConfigs);
    }
  };

  const plotLimit: Record<TimeSeriesGridDensity, number> = {
    single: 1,
    quad: 4,
    nine: 9,
  };
  const plotsToShow: Array<{ cfg: TimePlotConfig; index: number }> =
    expandedPlot === null
      ? plotConfigs
          .slice(0, plotLimit[density])
          .map((key, index) => ({ cfg: { key, label: key }, index }))
      : plotConfigs[expandedPlot] !== undefined
        ? [
            {
              cfg: {
                key: plotConfigs[expandedPlot],
                label: plotConfigs[expandedPlot],
              },
              index: expandedPlot,
            },
          ]
        : [];
  const visibleSelectionFingerprint = selectedTPs
    .filter((selection) => !hiddenTPs.has(selection.id))
    .map((selection) => `${selection.id}:${selection.tp.start_s}:${selection.endS}`)
    .join('|');

  const needsSelection =
    viewMode === 'tp' ||
    (viewMode === 'spectrum' && specSource === 'tp') ||
    (viewMode === 'xy' && xySource === 'tp');

  if (needsSelection && selectedTPs.length === 0 && !isEditMode) {
    return (
      <div className={styles.emptyWorkspace}>
        <svg className={styles.emptyDiagram} viewBox="0 0 280 100" fill="none" aria-hidden="true">
          <path
            d="M0 25H280M0 50H280M0 75H280M35 0V100M105 0V100M175 0V100M245 0V100"
            stroke="currentColor"
            strokeOpacity=".13"
          />
          <path
            d="M0 68H35L48 53L60 67H82L95 31L108 74L120 54H148L163 42L177 62H203L217 25L230 65H280"
            stroke="#85bddd"
            strokeWidth="2"
          />
          <path
            d="M0 77H38L50 65L62 78H86L100 49L112 85L125 68H151L166 58L180 73H207L222 48L235 78H280"
            stroke="#d7ba7d"
            strokeWidth="1.5"
            strokeOpacity=".8"
          />
        </svg>
        <h2>Compare your test points</h2>
        <p>
          Select points in the scatter plot to overlay their signals here. Choose points from
          different tests to compare operating conditions.
        </p>
        {onBrowseFullTest && (
          <button className="btn btn-primary" onClick={onBrowseFullTest}>
            Browse full test
          </button>
        )}
        <span className={styles.emptyHint}>
          Full test view lets you explore a complete run before selecting points.
        </span>
      </div>
    );
  }

  return (
    <div className={styles.gridShell}>
      <div className={styles.exportToolbar}>
        {(viewMode === 'tp' || viewMode === 'full') && <label>
          <input type="checkbox" checked={annotationsVisible}
            onChange={(event) => onAnnotationsVisibleChange(event.target.checked)} /> Show time notes
        </label>}
        <MultiPlotExportControls registry={exportRegistry}
          contextKey={JSON.stringify([viewMode, test, density, expandedPlot, visibleSelectionFingerprint, plotConfigs, plotFilterSpecs, plotShowOriginal, fullPlotMode, timeZoom, waterfallWindow, waterfallOverlap, specSource, specMode, specXAxis, specRpmCol, specLogY, xySource, xyXCols, xyYCols])}
          kind={viewMode === 'spectrum' && specMode === 'waterfall' ? 'waterfall' : viewMode === 'spectrum' || viewMode === 'xy' ? viewMode : 'time'}
          defaultColumns={density === 'nine' ? 3 : 2}
          disabledReason={expandedPlot !== null ? 'Restore the grid to export multiple plots.' : null}
          title={viewMode === 'xy' ? 'XY plots' : viewMode === 'spectrum' ? `Spectrum · ${specMode.toUpperCase()}` : viewMode === 'tp' ? 'Test points' : `Full test · ${test}`} />
      </div>
      <div className={gridClass}>
        {plotsToShow.map(({ cfg, index: idx }) => {
          const wrapperClass = `${styles.plotWrapper} ${styles.plotWrapperVisible}`;
          const displayedY = viewMode === 'xy' ? xyYCols[idx] || cfg.key : cfg.key;
          const missingY = !columns.includes(displayedY);
          const missingX = viewMode === 'xy' && !columns.includes(xyXCols[idx] ?? '');
          if (missingY || missingX) return (
            <section key={`plot-${idx}`} className={`${wrapperClass} ${styles.unavailable}`} aria-label={`Unavailable plot ${idx + 1}`}>
              <strong>{displayedY} · variable unavailable</strong>
              <p>Saved plot {idx + 1} stays in this slot. Choose an available variable to resume analysis.</p>
              {missingY && <label>Y variable <select className="input" aria-label={`Variable for plot ${idx + 1}`} value={displayedY}
                onChange={event => viewMode === 'xy' ? onXYYColChange?.(idx, event.target.value) : handleConfigChange(idx, event.target.value)}>
                <option value={displayedY}>{displayedY} (unavailable)</option>
                {columns.map(column => <option key={column} value={column}>{column}</option>)}
              </select></label>}
              {missingX && <label>X variable <select className="input" aria-label={`X variable for plot ${idx + 1}`} value={xyXCols[idx] ?? ''}
                onChange={event => onXYXColChange?.(idx, event.target.value)}>
                <option value={xyXCols[idx] ?? ''}>{xyXCols[idx] || 'Choose X'} (unavailable)</option>
                {columns.map(column => <option key={column} value={column}>{column}</option>)}
              </select></label>}
              <button className="btn" onClick={() => onToggleExpand(idx)}>{expandedPlot === idx ? 'Restore grid' : 'Maximize slot'}</button>
            </section>
          );
          const sourceToken = (name: string) => {
            const source = sourceCatalog.find(item => item.name === name);
            return [source?.id ?? name, source?.revision ?? null];
          };
          const viewportProps = (kind: 'spectrum' | 'xy') => {
            const source = kind === 'spectrum' ? specSource : xySource;
            const context = JSON.stringify([kind, displayedY,
              kind === 'spectrum' ? specMode === 'waterfall' ? [specMode, waterfallWindow, waterfallOverlap] : [specMode, specXAxis, specRpmCol] : xyXCols[idx], source,
              source === 'full' ? [sourceToken(test), timeZoom] : selectedTPs
                .filter(point => !hiddenTPs.has(point.id))
                .map(point => [sourceToken(point.test), point.tpId, point.tp.start_s, point.endS,
                  ...(kind === 'spectrum' && specMode === 'waterfall' ? [point.tp.start_idx, point.tp.end_idx] : [])])
                .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)))]);
            return { viewport: viewports?.[kind][idx], viewportContext: context,
              onViewportChange: (value: PlotViewport | null) => onViewportChange?.(kind, idx, value) };
          };
          const shared = {
            cfg,
            isExpanded: expandedPlot === idx,
            onToggleExpand: () => onToggleExpand(idx),
            isEditMode,
            allConfigs,
            onConfigChange: (newKey: string) => handleConfigChange(idx, newKey),
          };
          const filterProps = {
            fs,
            filterSpec: plotFilterSpecs[idx] ?? null,
            filterUi: plotFilters[idx] ?? DEFAULT_FILTER_UI,
            onFilterUiChange: (patch: Partial<FilterUi>) => onPlotFilterChange?.(idx, patch),
            showOriginal: plotShowOriginal[idx] ?? false,
            onShowOriginalChange: (show: boolean) => onPlotShowOriginalChange(idx, show),
          };

          return (
            <div key={`plot-${idx}`} className={wrapperClass}>
              {viewMode === 'tp' && (
                <TimePlot
                  key={`tp:${cfg.key}:${visibleSelectionFingerprint}`}
                  {...shared}
                  {...filterProps}
                  {...annotationProps}
                  registerExport={exportRegistry.registrations[idx]}
                  selectedTPs={selectedTPs}
                  hiddenTPs={hiddenTPs}
                  traceErrors={traceErrors}
                  onRetryTraces={onRetryTraces}
                  statsCache={statsCache}
                  statsErrors={statsErrors}
                  onRetryStatistics={onRetryStatistics}
                  columnsByTest={columnsByTest}
                  zoomDomain={timeZoom}
                  onZoomChange={onTimeZoomChange}
                  onZoomReset={onTimeZoomReset}
                  yRange={timeYRanges[idx] ?? null}
                  zoomResetVersion={timeZoomResetVersion}
                  onYRangeChange={(range) => onTimeYRangeChange(idx, range)}
                />
              )}
              {viewMode === 'full' && (
                <FullTestPlot
                  key={`full:${test}:${cfg.key}`}
                  {...shared}
                  {...filterProps}
                  {...annotationProps}
                  registerExport={exportRegistry.registrations[idx]}
                  test={test}
                  selectedTPs={selectedTPs}
                  hiddenTPs={hiddenTPs}
                  range={timeZoom}
                  displayMode={fullPlotMode}
                  onRangeChange={onTimeZoomChange}
                  onZoomReset={onTimeZoomReset}
                />
              )}
              {viewMode === 'spectrum' && specMode === 'waterfall' && (
                <WaterfallPlot {...shared} {...viewportProps('spectrum')} test={test} source={specSource}
                  selectedTPs={selectedTPs} hiddenTPs={hiddenTPs} columnsByTest={columnsByTest}
                  range={timeZoom} nperseg={waterfallWindow} overlap={waterfallOverlap} logColor={specLogY}
                  registerExport={exportRegistry.registrations[idx]} />
              )}
              {viewMode === 'spectrum' && specMode !== 'waterfall' && (
                <SpectrumPlot
                  {...viewportProps('spectrum')}
                  key={`spectrum:${cfg.key}:${specMode}:${specXAxis}:${specRpmCol}:${specSource}:${
                    specSource === 'full' ? test : visibleSelectionFingerprint
                  }`}
                  {...shared}
                  test={test}
                  source={specSource}
                  registerExport={exportRegistry.registrations[idx]}
                  selectedTPs={selectedTPs}
                  hiddenTPs={hiddenTPs}
                  columnsByTest={columnsByTest}
                  range={timeZoom}
                  specMode={specMode}
                  axisMode={specXAxis}
                  rpmColumn={specRpmCol}
                  logY={specLogY}
                />
              )}
              {viewMode === 'xy' && (
                <XYPlot
                  {...viewportProps('xy')}
                  key={`xy:${xyYCols[idx] || cfg.key}:${xyXCols[idx] ?? ''}:${xySource}:${
                    xySource === 'full' ? test : visibleSelectionFingerprint
                  }`}
                  {...shared}
                  cfg={xyYCols[idx] ? { key: xyYCols[idx], label: xyYCols[idx] } : cfg}
                  onConfigChange={(newKey: string) => onXYYColChange?.(idx, newKey)}
                  test={test}
                  xCol={xyXCols[idx] ?? ''}
                  onXColChange={(c) => onXYXColChange?.(idx, c)}
                  source={xySource}
                  registerExport={exportRegistry.registrations[idx]}
                  selectedTPs={selectedTPs}
                  hiddenTPs={hiddenTPs}
                  columnsByTest={columnsByTest}
                  range={timeZoom}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
