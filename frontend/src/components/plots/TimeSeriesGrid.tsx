import React from 'react';
import { SelectedTestPoint, TimePlotConfig } from '../../types';
import { TimePlot } from './TimePlot';
import { FullTestPlot } from './FullTestPlot';
import { SpectrumPlot } from './SpectrumPlot';
import { XYPlot } from './XYPlot';
import styles from './TimeSeriesGrid.module.css';

export type TimeViewMode = 'tp' | 'full' | 'spectrum' | 'xy';

interface TimeSeriesGridProps {
  viewMode: TimeViewMode;
  test: string;
  fs?: number | null;
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
  xySource: 'tp' | 'full';
  xCol: string;
  columnsByTest: Record<string, string[]>;
  isEditMode?: boolean;
  plotConfigs: string[];
  onPlotConfigChange?: (configs: string[]) => void;
}

export const TimeSeriesGrid: React.FC<TimeSeriesGridProps> = ({
  viewMode,
  test,
  fs,
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
  xySource,
  xCol,
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

        return (
          <div key={`plot-${idx}`} className={wrapperClass}>
            {viewMode === 'tp' && (
              <TimePlot
                {...shared}
                selectedTPs={selectedTPs}
                hiddenTPs={hiddenTPs}
                zoomDomain={timeZoom}
                onZoomChange={onTimeZoomChange}
                onZoomReset={onTimeZoomReset}
              />
            )}
            {viewMode === 'full' && (
              <FullTestPlot
                {...shared}
                test={test}
                fs={fs}
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
                test={test}
                xCol={xCol}
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
