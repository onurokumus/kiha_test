import { useLayoutEffect } from 'react';
import type uPlot from 'uplot';
import { useTheme } from '../hooks/useTheme';
import { redrawPlotTheme } from '../constants/uplotTheme';

/** Theme changes repaint existing canvases without rebuilding or refetching their data. */
export function usePlotTheme(plotRef: { current: uPlot | null }) {
  const theme = useTheme();
  useLayoutEffect(() => {
    if (plotRef.current) redrawPlotTheme(plotRef.current);
  }, [theme, plotRef]);
  return theme;
}
