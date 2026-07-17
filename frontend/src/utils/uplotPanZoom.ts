import uPlot from 'uplot';

/** Per-notch wheel zoom factor (span multiplier when zooming in). */
const WHEEL_STEP = 0.85;
/** Trailing idle delay before a wheel gesture commits its range upstream. */
const WHEEL_COMMIT_MS = 120;
/** Degenerate-span guard: stop zooming in below this x span. */
const MIN_SPAN = 1e-9;

const isPanGesture = (e: MouseEvent) => e.button === 1 || (e.button === 0 && e.shiftKey);

/**
 * uPlot plugin: mouse-wheel x-zoom around the cursor + x-pan via shift-drag or
 * middle-button drag. Plain left-drag stays uPlot's select-zoom rectangle
 * (suppressed for pan gestures through cursor.bind, uPlot's supported hook —
 * do NOT try capture-phase listeners instead: at the event target, capture and
 * bubble listeners fire in registration order, so uPlot's own mousedown wins).
 *
 * The new range is applied locally with setScale for instant feedback, then
 * `commit` fires (trailing-debounced for wheel, on mouseup for pan — NOT per
 * mousemove: a commit re-renders all 9 linked grid plots, and TimePlot
 * rebuilds its uPlot instance per zoomDomain change). Omit `commit` for plots
 * whose zoom is purely client-side (SpectrumPlot).
 */
export function xPanZoomPlugin(commit?: (range: [number, number]) => void): uPlot.Plugin {
  let destroyed = false;
  let wheelTimer = 0;
  let pending: [number, number] | null = null;

  const flush = () => {
    window.clearTimeout(wheelTimer);
    if (pending && commit) commit(pending);
    pending = null;
  };

  return {
    opts: (_u, opts) => {
      const cursor = (opts.cursor = opts.cursor ?? {});
      const bind = (cursor.bind = cursor.bind ?? {});
      bind.mousedown = (_self, _targ, handler) => (e) => {
        if (!isPanGesture(e)) handler(e);
        return null;
      };
    },
    hooks: {
      ready: (u) => {
        const onWheel = (e: WheelEvent) => {
          const min = u.scales.x.min;
          const max = u.scales.x.max;
          if (destroyed || min == null || max == null) return;
          e.preventDefault();
          const rect = u.over.getBoundingClientRect();
          const xVal = u.posToVal(e.clientX - rect.left, 'x');
          const factor = e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP;
          const nMin = xVal - (xVal - min) * factor;
          const nMax = xVal + (max - xVal) * factor;
          if (nMax - nMin < MIN_SPAN) return;
          u.setScale('x', { min: nMin, max: nMax });
          pending = [nMin, nMax];
          window.clearTimeout(wheelTimer);
          wheelTimer = window.setTimeout(flush, WHEEL_COMMIT_MS);
        };

        const onDown = (e: MouseEvent) => {
          if (destroyed || !isPanGesture(e)) return;
          const min0 = u.scales.x.min;
          const max0 = u.scales.x.max;
          if (min0 == null || max0 == null) return;
          e.preventDefault(); // also suppresses middle-button autoscroll
          const width = u.over.getBoundingClientRect().width;
          if (width <= 0) return;
          const valPerPx = (max0 - min0) / width;
          const x0 = e.clientX;
          let moved: [number, number] | null = null;
          u.over.style.cursor = 'grabbing';
          const onMove = (ev: MouseEvent) => {
            if (destroyed) return;
            const dv = (ev.clientX - x0) * valPerPx;
            moved = [min0 - dv, max0 - dv];
            u.setScale('x', { min: moved[0], max: moved[1] });
          };
          const onUp = () => {
            window.removeEventListener('mousemove', onMove);
            window.removeEventListener('mouseup', onUp);
            if (!destroyed) u.over.style.cursor = '';
            if (moved && commit) commit(moved);
          };
          window.addEventListener('mousemove', onMove);
          window.addEventListener('mouseup', onUp);
        };

        u.over.addEventListener('wheel', onWheel, { passive: false });
        u.over.addEventListener('mousedown', onDown);
      },
      // Flush (not drop) a pending wheel commit: plots are rebuilt whenever new
      // data lands, and dropping here would silently lose the last wheel ticks.
      destroy: () => {
        flush();
        destroyed = true;
      },
    },
  };
}
