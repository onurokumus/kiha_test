import React from 'react';
import {
  FilterSpec,
  SelectedTestPoint,
  TimePlotConfig,
  WindowDisplayMode,
} from '../../types';
import { DEFAULT_FILTER_UI, FilterUi } from '../../constants/filters';
import { TimePlot } from './TimePlot';
import { FullTestPlot } from './FullTestPlot';
import { SpectrumPlot } from './SpectrumPlot';
import { XYPlot } from './XYPlot';
import styles from './TimeSeriesGrid.module.css';

export type TimeViewMode = 'tp' | 'full' | 'spectrum' | 'xy';
export type TimeSeriesGridDensity = 'single' | 'quad' | 'nine';

interface TimeSeriesGridProps {
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
  expandedPlot: number | null;
  onToggleExpand: (index: number) => void;
  timeZoom: [number, number] | null;
  onTimeZoomChange: (domain: [number, number]) => void;
  onTimeZoomReset: () => void;
  fullPlotMode: WindowDisplayMode;
  specMode: 'fft' | 'welch';
  specLogY: boolean;
  specSource: 'tp' | 'full';
  /** Active test's sample rate (Nyquist hint in per-plot filter rows). */
  fs: number | null;
  /** Per-plot DSP filters (replace raw traces), index-aligned with plotConfigs. */
  plotFilters: FilterUi[];
  plotFilterSpecs: (FilterSpec | null)[];
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
  density = 'nine',
  test,
  columns,
  selectedTPs,
  hiddenTPs,
  traceErrors = {},
  onRetryTraces,
  expandedPlot,
  onToggleExpand,
  timeZoom,
  onTimeZoomChange,
  onTimeZoomReset,
  fullPlotMode,
  specMode,
  specLogY,
  specSource,
  fs,
  plotFilters,
  plotFilterSpecs,
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
    .map(
      (selection) =>
        `${selection.id}:${selection.tp.start_s}:${selection.endS}`
    )
    .join('|');

  return (
    <div className={styles.gridShell}>
      <div className={gridClass}>
        {plotsToShow.map(({ cfg, index: idx }) => {
          const wrapperClass = `${styles.plotWrapper} ${styles.plotWrapperVisible}`;
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
          };

          return (
            <div key={`plot-${idx}`} className={wrapperClass}>
              {viewMode === 'tp' && (
                <TimePlot
                  key={`tp:${cfg.key}:${visibleSelectionFingerprint}`}
                  {...shared}
                  {...filterProps}
                  selectedTPs={selectedTPs}
                  hiddenTPs={hiddenTPs}
                  traceErrors={traceErrors}
                  onRetryTraces={onRetryTraces}
                  columnsByTest={columnsByTest}
                  zoomDomain={timeZoom}
                  onZoomChange={onTimeZoomChange}
                  onZoomReset={onTimeZoomReset}
                />
              )}
              {viewMode === 'full' && (
                <FullTestPlot
                  key={`full:${test}:${cfg.key}`}
                  {...shared}
                  {...filterProps}
                  test={test}
                  range={timeZoom}
                  displayMode={fullPlotMode}
                  onRangeChange={onTimeZoomChange}
                  onZoomReset={onTimeZoomReset}
                />
              )}
              {viewMode === 'spectrum' && (
                <SpectrumPlot
                  key={`spectrum:${cfg.key}:${specMode}:${specSource}:${
                    specSource === 'full' ? test : visibleSelectionFingerprint
                  }`}
                  {...shared}
                  test={test}
                  source={specSource}
                  selectedTPs={selectedTPs}
                  hiddenTPs={hiddenTPs}
                  columnsByTest={columnsByTest}
                  range={timeZoom}
                  specMode={specMode}
                  logY={specLogY}
                />
              )}
              {viewMode === 'xy' && (
                <XYPlot
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
