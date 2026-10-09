import type uPlot from 'uplot';
import { getPlotAppearance, subscribePlotAppearance } from './plotAppearance';

/** Resize XY markers without replacing the plot, touching data, or fitting axes. */
export function pointAppearancePlugin(): uPlot.Plugin {
  const base = new Map<uPlot.Series, { size: number; width: number; strokeWidth: number }>();
  let pointScale = getPlotAppearance().pointScale;
  let unsubscribe: (() => void) | undefined;

  const apply = (plot: uPlot) => {
    for (const series of plot.series.slice(1)) {
      if (!series.points) continue;
      let original = base.get(series);
      if (!original) {
        original = { size: series.points.size ?? 5, width: series.points.width ?? 1,
          strokeWidth: series.width ?? 1 };
        base.set(series, original);
      }
      series.points.size = original.size * pointScale;
      series.points.width = original.width * pointScale;
      // XY draws its point paths as the primary series, using series.width.
      series.width = original.strokeWidth * pointScale;
      (series as uPlot.Series & { _paths: uPlot.Series.Paths | null })._paths = null;
    }
  };

  return {
    hooks: {
      init: apply,
      ready: plot => {
        unsubscribe = subscribePlotAppearance(() => {
          const next = getPlotAppearance().pointScale;
          if (next === pointScale) return;
          pointScale = next;
          apply(plot);
          plot.batch(() => {
            plot.redraw(false, false);
            // Cursor dots use the current series point diameter.
            plot.setCursor({ left: plot.cursor.left ?? -10, top: plot.cursor.top ?? -10 }, false);
          });
        });
      },
      destroy: () => { unsubscribe?.(); base.clear(); },
    },
  };
}
