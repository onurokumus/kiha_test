import { PlotViewports, PlotViewport } from '../../utils/plotViewport';
import { AnalysisSource } from '../../services/sessionSources';
import React, { useId, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  FilterSpec,
  SelectedTestPoint,
  StatsCache,
  SpectrumXAxis,
  TimePlotConfig,
  WindowDisplayMode,
} from '../../types';
import { DEFAULT_FILTER_UI, FilterUi, filterForSampleRates } from '../../constants/filters';
import { WaterfallBand } from '../../constants/waterfall';
import { TimePlot } from './TimePlot';
import { FullTestPlot, type FullTestDisplayDetail } from './FullTestPlot';
import { WaterfallPlot } from './WaterfallPlot';
import { SpectrumPlot } from './SpectrumPlot';
import { XYPlot } from './XYPlot';
import { AxisRange } from '../../utils/timePlotRanges';
import { SavedWaterfallColorRange } from '../../utils/waterfallColorRange';
import { createPlotExportRegistry } from '../../utils/plotExportRegistry';
import { MultiPlotExportControls } from '../controls/MultiPlotExportControls';
import { SearchableSelect } from '../controls/SearchableSelect';
import styles from './TimeSeriesGrid.module.css';

export type TimeViewMode = 'tp' | 'full' | 'spectrum' | 'xy';
export type TimeSeriesGridDensity = 'single' | 'quad' | 'nine';

interface TimeSeriesGridProps {
  detailTarget: HTMLSpanElement | null;
  exportTarget: HTMLDivElement | null;
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
  waterfallResolution: number | null;
  waterfallBand: WaterfallBand;
  waterfallColorRanges: (SavedWaterfallColorRange | null)[];
  onWaterfallColorRangeChange: (index: number, column: string, logColor: boolean, range: AxisRange | null) => void;
  specMode: 'fft' | 'welch' | 'waterfall';
  specXAxis: SpectrumXAxis;
  specRpmCol: string;
  specLogY: boolean;
  specSource: 'tp' | 'full';
  /** Active test's sample rate (Nyquist hint in per-plot filter rows). */
  fs: number | null;
  sampleRatesByTest: Record<string, number | null | undefined>;
  /** Per-plot DSP filters, index-aligned with plotConfigs. */
  plotFilters: FilterUi[];
  plotFilterSpecs: (FilterSpec | null)[];
  plotShowOriginal: boolean[];
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
  plotConfigs: string[];
  fullPlotExtraColumns: string[][];
  onFullPlotExtraColumnsChange: (index: number, columns: string[]) => void;
  onPlotConfigChange?: (configs: string[]) => void;
}

export const TimeSeriesGrid: React.FC<TimeSeriesGridProps> = ({
  detailTarget,
  exportTarget,
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
  waterfallWindow, waterfallOverlap, waterfallResolution, waterfallBand,
  waterfallColorRanges, onWaterfallColorRangeChange,
  specMode,
  specXAxis,
  specRpmCol,
  specLogY,
  specSource,
  fs,
  sampleRatesByTest,
  plotFilters,
  plotFilterSpecs,
  plotShowOriginal,
  onPlotShowOriginalChange,
  onPlotFilterChange,
  xySource,
  xyYCols,
  onXYYColChange,
  xyXCols,
  onXYXColChange,
  columnsByTest,
  plotConfigs,
  fullPlotExtraColumns,
  onFullPlotExtraColumnsChange,
  onPlotConfigChange,
}) => {
  const selectFocusScope = useId();
  const exportRegistry = useMemo(createPlotExportRegistry, []);
  const [plotDetails, setPlotDetails] = useState<Record<number, FullTestDisplayDetail | null>>({});
  const detailRegistrations = useMemo(() => Array.from({ length: 9 }, (_, index) =>
    (detail: FullTestDisplayDetail | null) => setPlotDetails(current =>
      current[index]?.mode === detail?.mode && current[index]?.level === detail?.level
        ? current : { ...current, [index]: detail })), []);
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

  if (needsSelection && selectedTPs.length === 0) {
    const variableOptions = allConfigs.map(config => ({ value: config.key, label: config.label, keywords: [config.key] }));
    return (
      <div className={styles.emptyWorkspace}>
        <h2>Select test points</h2>
        <p>Click points in the scatter plot to compare their signals.</p>
        {onBrowseFullTest && (
          <button className="btn btn-primary" onClick={onBrowseFullTest}>
            Browse full test
          </button>
        )}
        {allConfigs.length > 0 && plotsToShow.length > 0 && <div className={styles.emptySignals} aria-label="Plot variables">
          {plotsToShow.map(({ cfg, index }) => <div key={index} className={styles.emptySignal}
            data-select-focus-scope={`${selectFocusScope}-${index}`}
            role="group" aria-label={`Plot ${index + 1} variables`}>
            <span className={styles.slotNumber}>{index + 1}</span>
            {viewMode === 'xy' && <><span className={styles.axisLabel}>X</span>
              <SearchableSelect value={xyXCols[index] ?? ''} options={variableOptions}
                onChange={column => onXYXColChange?.(index, column)}
                ariaLabel={`X variable for plot ${index + 1}`} searchPlaceholder="Search X variables..."
                optionNoun="variable" appearance="title" className={styles.emptyPicker} />
              <span className={styles.axisLabel}>Y</span></>}
            <SearchableSelect value={viewMode === 'xy' ? xyYCols[index] || cfg.key : cfg.key}
              options={variableOptions} onChange={column => viewMode === 'xy'
                ? onXYYColChange?.(index, column) : handleConfigChange(index, column)}
              ariaLabel={`${viewMode === 'xy' ? 'Y variable' : 'Plot variable'} for plot ${index + 1}`}
              searchPlaceholder="Search plot variables..." optionNoun="variable"
              appearance="title" className={styles.emptyPicker} />
          </div>)}
        </div>}
      </div>
    );
  }

  const detailPlots = viewMode === 'full' ? plotsToShow.filter(({ cfg }) => columns.includes(cfg.key)) : [];
  const detailsReady = detailPlots.length > 0 && detailPlots.every(({ index }) => plotDetails[index]);
  const details = detailsReady ? detailPlots.map(({ cfg, index }) => ({ label: cfg.label, ...plotDetails[index]! })) : [];
  const levels = new Set(details.map(detail => detail.level));
  const representations = new Set(details.map(detail => `${detail.mode}:${detail.level}`));
  const firstDetail = details[0];
  const detailDescription = firstDetail && (representations.size === 1
    ? firstDetail.level === 1 ? 'Every sample is shown.'
      : firstDetail.mode === 'envelope'
        ? `Min/max of each group of up to ${firstDetail.level} samples, preserving peaks.`
        : `One sample out of every ${firstDetail.level} is shown.`
    : details.map(detail => `${detail.label}: 1:${detail.level} (${detail.mode === 'envelope' ? 'min/max groups' : 'sampled line'})`).join('\n'));

  return (
    <div className={styles.gridShell}>
      {detailTarget && firstDetail && createPortal(
        <span className={styles.displayDetail} tabIndex={0} data-plot-resolution
          title={`${detailDescription}\nDisplay only; CSV exports full-resolution samples.`}>
          {levels.size === 1 ? `1:${firstDetail.level}` : 'Mixed'}
        </span>, detailTarget,
      )}
      {exportTarget && createPortal(
        <MultiPlotExportControls registry={exportRegistry}
          contextKey={JSON.stringify([viewMode, test, density, expandedPlot, visibleSelectionFingerprint, plotConfigs, fullPlotExtraColumns, plotFilterSpecs, plotShowOriginal, fullPlotMode, timeZoom, waterfallWindow, waterfallOverlap, waterfallResolution, waterfallBand, waterfallColorRanges, specSource, specMode, specXAxis, specRpmCol, specLogY, xySource, xyXCols, xyYCols])}
          kind={viewMode === 'spectrum' && specMode === 'waterfall' ? 'waterfall' : viewMode === 'spectrum' || viewMode === 'xy' ? viewMode : 'time'}
          defaultColumns={density === 'nine' ? 3 : 2}
          disabledReason={expandedPlot !== null ? 'Restore the grid to export multiple plots.' : null}
          title={viewMode === 'xy' ? 'XY plots' : viewMode === 'spectrum' ? `Spectrum · ${specMode.toUpperCase()}` : viewMode === 'tp' ? 'Test points' : `Full test · ${test}`} />,
        exportTarget,
      )}
      <div className={gridClass}>
        {plotsToShow.map(({ cfg, index: idx }) => {
          const wrapperClass = `${styles.plotWrapper} ${styles.plotWrapperVisible}`;
          const displayedY = viewMode === 'xy' ? xyYCols[idx] || cfg.key : cfg.key;
          const missingY = !columns.includes(displayedY);
          const missingX = viewMode === 'xy' && !columns.includes(xyXCols[idx] ?? '');
          if (missingY || missingX) return (
            <section key={`plot-${idx}`} className={`${wrapperClass} ${styles.unavailable}`} aria-label={`Unavailable plot ${idx + 1}`}
              data-select-focus-scope={`${selectFocusScope}-${idx}`}>
              <strong>{displayedY} · variable unavailable</strong>
              <p>Saved plot {idx + 1} stays in this slot. Choose an available variable to resume analysis.</p>
              {missingX && <label>X variable <select className="input" aria-label={`X variable for plot ${idx + 1}`} value={xyXCols[idx] ?? ''}
                onChange={event => onXYXColChange?.(idx, event.target.value)}>
                <option value={xyXCols[idx] ?? ''}>{xyXCols[idx] || 'Choose X'} (unavailable)</option>
                {columns.map(column => <option key={column} value={column}>{column}</option>)}
              </select></label>}
              {missingY && <label>Y variable <select className="input" aria-label={`Variable for plot ${idx + 1}`} value={displayedY}
                onChange={event => viewMode === 'xy' ? onXYYColChange?.(idx, event.target.value) : handleConfigChange(idx, event.target.value)}>
                <option value={displayedY}>{displayedY} (unavailable)</option>
                {columns.map(column => <option key={column} value={column}>{column}</option>)}
              </select></label>}
              <button className="btn" onClick={() => onToggleExpand(idx)}>{expandedPlot === idx ? 'Restore grid' : 'Maximize slot'}</button>
            </section>
          );
          const sourceToken = (name: string) => {
            const source = sourceCatalog.find(item => item.name === name);
            return [source?.id ?? name, source?.revision ?? null];
          };
          const fullViewportContext = JSON.stringify(['full', sourceToken(test), cfg.key, fullPlotExtraColumns[idx] ?? []]);
          const viewportProps = (kind: 'spectrum' | 'xy') => {
            const source = kind === 'spectrum' ? specSource : xySource;
            // Legacy manual/full sessions use the same Hz/elapsed axes in v2.
            // Retain their context identity so reopening preserves the crop.
            const waterfallContext = waterfallResolution === null && waterfallBand === 'full'
              ? [specMode, waterfallWindow, waterfallOverlap]
              : [specMode, waterfallWindow, waterfallOverlap, waterfallResolution, waterfallBand];
            const contextParts: unknown[] = [kind, displayedY,
              kind === 'spectrum' ? specMode === 'waterfall' ? waterfallContext : [specMode, specXAxis, specRpmCol] : xyXCols[idx], source,
              source === 'full' ? [sourceToken(test), timeZoom] : selectedTPs
                .filter(point => !hiddenTPs.has(point.id))
                .map(point => [sourceToken(point.test), point.tpId, point.tp.start_s, point.endS,
                  ...(kind === 'spectrum' && specMode === 'waterfall' ? [point.tp.start_idx, point.tp.end_idx] : [])])
                .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)))];
            const legacyContext = JSON.stringify(contextParts);
            if (kind === 'spectrum' && specMode !== 'waterfall')
              contextParts[2] = [specMode, specXAxis, specRpmCol, specLogY];
            const context = JSON.stringify(contextParts);
            let viewport = viewports?.[kind][idx];
            // Preserve the frequency crop across linear/log switches and old
            // X-only sessions, but never apply linear Y bounds to log values.
            if (viewport && viewport.context !== context && kind === 'spectrum' && specMode !== 'waterfall') {
              try {
                const previous = JSON.parse(viewport.context);
                if (Array.isArray(previous) && Array.isArray(previous[2])) {
                  previous[2] = previous[2].slice(0, 3);
                  if (JSON.stringify(previous) === legacyContext) viewport = { ...viewport, context, y: null };
                }
              } catch { /* An unrelated context simply starts at automatic bounds. */ }
            }
            return { viewport, viewportContext: context,
              onViewportChange: (value: PlotViewport | null) => onViewportChange?.(kind, idx, value) };
          };
          const shared = {
            cfg,
            isExpanded: expandedPlot === idx,
            onToggleExpand: () => onToggleExpand(idx),
            allConfigs,
            onConfigChange: (newKey: string) => handleConfigChange(idx, newKey),
          };
          const sourceRates = viewMode === 'tp' ? selectedTPs
            .filter(point => !hiddenTPs.has(point.id) && (columnsByTest[point.test] ?? []).includes(cfg.key))
            .map(point => sampleRatesByTest[point.test]) : [fs];
          const plotFilter = filterForSampleRates(plotFilterSpecs[idx] ?? null, sourceRates);
          const filterProps = {
            fs: plotFilter.fs,
            filterSpec: plotFilter.spec,
            filterUi: plotFilters[idx] ?? DEFAULT_FILTER_UI,
            onFilterUiChange: (patch: Partial<FilterUi>) => onPlotFilterChange?.(idx, patch),
            showOriginal: plotShowOriginal[idx] ?? false,
            onShowOriginalChange: (show: boolean) => onPlotShowOriginalChange(idx, show),
          };

          return (
            <div key={`plot-${idx}`} className={wrapperClass} data-select-focus-scope={`${selectFocusScope}-${idx}`}>
              {viewMode === 'tp' && (
                <TimePlot
                  key={`tp:${cfg.key}:${visibleSelectionFingerprint}`}
                  {...shared}
                  {...filterProps}
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
                  onDisplayDetailChange={detailRegistrations[idx]}
                  viewport={viewports?.full[idx]}
                  viewportContext={fullViewportContext}
                  onViewportChange={value => onViewportChange?.('full', idx, value)}
                  {...shared}
                  additionalConfigs={(fullPlotExtraColumns[idx] ?? []).filter(column => columns.includes(column) && column !== cfg.key)
                    .map(column => ({ key: column, label: column }))}
                  onAdditionalColumnsChange={columns => onFullPlotExtraColumnsChange(idx, columns)}
                  {...filterProps}
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
                  resolutionHz={waterfallResolution} frequencyBand={waterfallBand}
                  colorRange={waterfallColorRanges[idx]?.column === cfg.key
                    ? waterfallColorRanges[idx]?.[specLogY ? 'log' : 'linear'] ?? null : null}
                  onColorRangeChange={colorRange => onWaterfallColorRangeChange(idx, cfg.key, specLogY, colorRange)}
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
