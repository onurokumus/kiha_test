import type uPlot from 'uplot';
import { getPlotAppearance, subscribePlotAppearance } from './plotAppearance';

/** Change trace widths without rebuilding the plot or touching its viewport. */
export function lineAppearancePlugin(): uPlot.Plugin {
  let baseWidths: number[] = [];
  let unsubscribe: (() => void) | undefined;
  const apply = (plot: uPlot): boolean => {
    const { lineScale } = getPlotAppearance();
    let changed = false;
    plot.series.forEach((series, index) => {
      if (index === 0) return;
      const width = baseWidths[index] * lineScale;
      if (series.width === width) return;
      series.width = width;
      // uPlot caches gap clipping with the stroke width in its generated path.
      // Invalidate only that cache: redraw(true) would also recalculate scales.
      (series as uPlot.Series & { _paths: uPlot.Series.Paths | null })._paths = null;
      changed = true;
    });
    return changed;
  };
  return {
    hooks: {
      init: plot => {
        baseWidths = plot.series.map(series => series.width ?? 1);
        apply(plot);
      },
      ready: plot => {
        unsubscribe = subscribePlotAppearance(() => {
          if (apply(plot)) plot.batch(() => plot.redraw(false, false));
        });
      },
      destroy: () => { unsubscribe?.(); },
    },
  };
}
