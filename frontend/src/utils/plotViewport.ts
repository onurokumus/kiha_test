import { useRef } from 'react';
import type uPlot from 'uplot';
import { AxisRange, validAxisRange } from './timePlotRanges';

export interface PlotViewport {
  context: string;
  x: AxisRange;
  y: AxisRange | null;
}
export interface ViewportProps {
  viewport?: PlotViewport | null;
  viewportContext?: string;
  onViewportChange?: (value: PlotViewport | null) => void;
}
export interface PlotViewports {
  spectrum: (PlotViewport | null)[];
  xy: (PlotViewport | null)[];
}
export const emptyPlotViewports = (): PlotViewports => ({
  spectrum: Array(9).fill(null),
  xy: Array(9).fill(null),
});
export function normalizePlotViewports(value: unknown): PlotViewports {
  const raw = value as Partial<PlotViewports> | null;
  const normalize = (items: unknown) =>
    Array.from({ length: 9 }, (_, i) => {
      const item = Array.isArray(items) ? items[i] : null;
      return item &&
        typeof item.context === 'string' &&
        validAxisRange(item.x) &&
        (item.y === null || validAxisRange(item.y))
        ? { context: item.context, x: item.x, y: item.y }
        : null;
    });
  return { spectrum: normalize(raw?.spectrum), xy: normalize(raw?.xy) };
}

/** uPlot batches scale work in a microtask. Flush our own sync/apply work via
 * batch() while muted, so an automatic range never overwrites the saved view.
 * User wheel/box/pan hooks then capture final scales through the same path. */
export function usePlotViewport(
  plotRef: { current: uPlot | null },
  props: ViewportProps,
  twoAxes: boolean
) {
  const latest = useRef(props);
  latest.current = props;
  const controller = useRef<{
    plugin: uPlot.Plugin;
    sync: (action: () => void) => void;
    reset: () => void;
  }>();
  if (!controller.current) {
    let muted = false;
    let resetTimer: ReturnType<typeof setTimeout> | undefined;
    let resetting = false;
    const publish = (value: PlotViewport | null) => {
      if (JSON.stringify(latest.current.viewport) !== JSON.stringify(value))
        latest.current.onViewportChange?.(value);
    };
    const read = (u: uPlot) => {
      if (muted || resetting || !latest.current.viewportContext) return;
      const x = [u.scales.x.min, u.scales.x.max];
      const y = twoAxes ? [u.scales.y.min, u.scales.y.max] : null;
      if (validAxisRange(x) && (y === null || validAxisRange(y)))
        publish({ context: latest.current.viewportContext, x, y });
    };
    const auto = () => {
      publish(null);
      const u = plotRef.current;
      if (!u) return;
      muted = true;
      try {
        u.batch(() => {
          u.setScale('x', { min: null as unknown as number, max: null as unknown as number });
          if (twoAxes)
            u.setScale('y', { min: null as unknown as number, max: null as unknown as number });
        });
      } finally {
        muted = false;
      }
    };
    const doubleClick = () => {
      resetting = true;
      publish(null);
      clearTimeout(resetTimer);
      resetTimer = setTimeout(() => {
        resetting = false;
      }, 0);
    };
    controller.current = {
      plugin: {
        hooks: {
          // Spectrum's Y follows visible X and may refit in a later microtask.
          // Only an X transaction can change its saved viewport; recording a
          // Y-only auto-fit would turn a default/reset view into a manual crop.
          setScale: (u, axis) => {
            if (twoAxes || axis === 'x') read(u);
          },
          ready: (u) => u.over.addEventListener('dblclick', doubleClick, true),
          destroy: (u) => {
            u.over.removeEventListener('dblclick', doubleClick, true);
            clearTimeout(resetTimer);
            resetting = false;
          },
        },
      },
      sync: (action) => {
        muted = true;
        try {
          action();
          const u = plotRef.current;
          if (!u) return;
          const saved = latest.current.viewport;
          u.batch(() => {
            if (saved && saved.context === latest.current.viewportContext) {
              u.setScale('x', { min: saved.x[0], max: saved.x[1] });
              if (twoAxes && saved.y) u.setScale('y', { min: saved.y[0], max: saved.y[1] });
            }
          });
        } finally {
          muted = false;
        }
      },
      reset: auto,
    };
  }
  return controller.current;
}
