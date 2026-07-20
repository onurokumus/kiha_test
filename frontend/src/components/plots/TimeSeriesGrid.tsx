import React from 'react';
import { FilterSpec, SelectedTestPoint, TimePlotConfig } from '../../types';
import { DEFAULT_FILTER_UI, FilterUi } from '../../constants/filters';
import { TimePlot } from './TimePlot';
import { FullTestPlot } from './FullTestPlot';
import { SpectrumPlot } from './SpectrumPlot';
import { XYPlot } from './XYPlot';
import styles from './TimeSeriesGrid.module.css';

export type TimeViewMode = 'tp' | 'full' | 'spectrum' | 'xy';

interface TimeSeriesGridProps {
  viewMode: TimeViewMode;
  test: string;
  columns: string[];
  selectedTPs: SelectedTestPoint[];
  hiddenTPs: Set<string>;
  expandedPlot: number | null;
  onToggleExpand: (index: number) => void;
  timeZoom: [number, number] | null;
  onTimeZoomChange: (domain: [number, number]) => void;
  onTimeZoomReset: () => void;
  specMode: 'fft' | 'welch';
  specLogY: boolean;
  specSource: 'tp' | 'full';
  /** Active test's sample rate (Nyquist hint in per-plot filter rows). */
  fs: number | null;
  /** Per-plot DSP filters (dashed overlays), index-aligned with plotConfigs. */
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
  test,
  columns,
  selectedTPs,
  hiddenTPs,
  expandedPlot,
  onToggleExpand,
  timeZoom,
  onTimeZoomChange,
  onTimeZoomReset,
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
  const gridClass = `${styles.gridContainer} ${expandedPlot === null ? styles.gridNormal : styles.gridExpanded
    }`;

  const allConfigs: TimePlotConfig[] = columns.map((c) => ({ key: c, label: c }));

  const handleConfigChange = (index: number, newKey: string) => {
    if (onPlotConfigChange) {
      const newConfigs = [...plotConfigs];
      newConfigs[index] = newKey;
      onPlotConfigChange(newConfigs);
    }
  };

  const plotsToShow: TimePlotConfig[] = plotConfigs
    .slice(0, 9)
    .map((key) => ({ key, label: key }));

  return (
    <div className={gridClass}>
      {plotsToShow.map((cfg, idx) => {
        const isVisible = expandedPlot === null || expandedPlot === idx;
        if (!isVisible) return null;

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
                {...shared}
                {...filterProps}
                selectedTPs={selectedTPs}
                hiddenTPs={hiddenTPs}
                columnsByTest={columnsByTest}
                zoomDomain={timeZoom}
                onZoomChange={onTimeZoomChange}
                onZoomReset={onTimeZoomReset}
              />
            )}
            {viewMode === 'full' && (
              <FullTestPlot
                {...shared}
                {...filterProps}
                test={test}
                range={timeZoom}
                onRangeChange={onTimeZoomChange}
                onZoomReset={onTimeZoomReset}
              />
            )}
            {viewMode === 'spectrum' && (
              <SpectrumPlot
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
                {...shared}
                cfg={
                  xyYCols[idx]
                    ? { key: xyYCols[idx], label: xyYCols[idx] }
                    : cfg
                }
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
  );
};
