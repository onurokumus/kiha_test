import uPlot from 'uplot';
import { AxisRange, validAxisRange } from './timePlotRanges';

/** Per-notch wheel zoom factor (span multiplier when zooming in). */
const WHEEL_STEP = 0.85;
/** Trailing idle delay before a wheel gesture commits its range upstream. */
const WHEEL_COMMIT_MS = 120;
/** Degenerate-span guard: stop zooming in below this x span. */
const MIN_SPAN = 1e-9;

const isPanGesture = (e: MouseEvent) => e.button === 1 || (e.button === 0 && e.shiftKey);

export interface PanZoomControl { cancel: () => void }

/**
 * uPlot plugin: mouse-wheel x-zoom around the cursor + x-pan via shift-drag or
 * middle-button drag. With commitY, left-drag selects X, Y, or both using a
 * ten-pixel threshold per axis; Alt+drag keeps the existing Y-only shortcut.
 * Otherwise plain left-drag stays uPlot's select-zoom rectangle
 * (suppressed for pan gestures through cursor.bind, uPlot's supported hook —
 * do NOT try capture-phase listeners instead: at the event target, capture and
 * bubble listeners fire in registration order, so uPlot's own mousedown wins).
 *
 * The new range is applied locally with setScale for instant feedback, then
 * `commit` fires (trailing-debounced for wheel, on mouseup for pan — NOT per
 * mousemove: a commit re-renders all 9 linked grid plots). Omit `commit` for plots
 * whose zoom is purely client-side (SpectrumPlot).
 */
export function xPanZoomPlugin(
  commit?: (range: AxisRange) => void,
  /** TP plots opt in to left-drag box zoom and independent Alt+wheel/drag Y. */
  commitY?: (range: AxisRange) => void,
  control?: { current: PanZoomControl | null },
  boxOnly = false
): uPlot.Plugin {
  let destroyed = false;
  let wheelTimer = 0;
  let pending: [number, number] | null = null;
  let detachReadyListeners: (() => void) | null = null;
  let finishPan: (() => void) | null = null;

  const flush = () => {
    window.clearTimeout(wheelTimer);
    if (pending && commit) commit(pending);
    pending = null;
  };
  const cancel = () => {
    window.clearTimeout(wheelTimer);
    pending = null;
    finishPan?.();
  };

  return {
    opts: (_u, opts) => {
      const cursor = (opts.cursor = opts.cursor ?? {});
      const bind = (cursor.bind = cursor.bind ?? {});
      bind.mousedown = (_self, _targ, handler) => (e) => {
        // Our custom binding replaces uPlot's default primary-button filter.
        // Retain that filter so right-click only opens the plot action menu.
        if (e.button === 0 && !isPanGesture(e) && !commitY) handler(e);
        return null;
      };
    },
    hooks: {
      ready: (u) => {
        if (control) control.current = { cancel };
        const onWheel = (e: WheelEvent) => {
          // Ctrl+wheel belongs to desktop browser zoom, including trackpad pinch.
          if (commitY && e.ctrlKey) return;
          const axis = commitY && e.altKey ? 'y' : 'x';
          const min = u.scales[axis].min;
          const max = u.scales[axis].max;
          if (destroyed || min == null || max == null || e.deltaY === 0) return;
          e.preventDefault();
          const rect = u.over.getBoundingClientRect();
          const xVal = u.posToVal(axis === 'x' ? e.clientX - rect.left : e.clientY - rect.top, axis);
          const factor = e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP;
          const nMin = xVal - (xVal - min) * factor;
          const nMax = xVal + (max - xVal) * factor;
          if (!Number.isFinite(nMin) || !Number.isFinite(nMax) || (axis === 'x' && nMax - nMin < MIN_SPAN)) return;
          if (axis === 'y') {
            if (!validAxisRange([nMin, nMax])) return;
            flush();
            u.setScale('y', { min: nMin, max: nMax });
            commitY!([nMin, nMax]);
            return;
          }
          u.setScale('x', { min: nMin, max: nMax });
          pending = [nMin, nMax];
          window.clearTimeout(wheelTimer);
          wheelTimer = window.setTimeout(flush, WHEEL_COMMIT_MS);
        };

        const onDown = (e: MouseEvent) => {
          if (!destroyed && commitY && e.button === 0 && (!e.shiftKey || e.altKey)) {
            e.preventDefault();
            flush();
            finishPan?.();
            const rect = u.over.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return;
            const local = (event: MouseEvent) => ({
              x: Math.max(0, Math.min(rect.width, event.clientX - rect.left)),
              y: Math.max(0, Math.min(rect.height, event.clientY - rect.top)),
            });
            const start = local(e);
            const yOnly = e.altKey;
            let xActive = false;
            let yActive = false;
            const selection = (event: MouseEvent) => {
              const end = local(event);
              // The threshold activates an axis once per gesture. Keep it active
              // through reversals so releasing near the anchor retains that axis.
              xActive ||= !yOnly && Math.abs(end.x - start.x) > 10;
              yActive ||= Math.abs(end.y - start.y) > 10;
              return { end, x: xActive, y: yActive };
            };
            const onMove = (event: MouseEvent) => {
              const { end } = selection(event);
              const dx = Math.abs(end.x - start.x);
              const dy = Math.abs(end.y - start.y);
              const moved = yOnly ? dy > 0 : dx > 0 || dy > 0;
              // Preview follows the pointer from the first pixel; thresholds only
              // decide which axes commit. Filling inactive axes causes a snap as
              // a diagonal drag crosses the two thresholds at different times.
              // A one-pixel minimum keeps purely horizontal/vertical drags visible.
              const width = moved ? (yOnly ? rect.width : Math.max(1, dx)) : 0;
              const height = moved ? Math.max(1, dy) : 0;
              u.setSelect({
                left: yOnly ? 0 : Math.min(start.x, end.x, rect.width - width),
                top: Math.min(start.y, end.y, rect.height - height),
                width,
                height,
              }, false);
            };
            const cleanup = () => {
              window.removeEventListener('mousemove', onMove);
              window.removeEventListener('mouseup', onUp);
              window.removeEventListener('keydown', onKey);
              window.removeEventListener('blur', cleanup);
              if (!destroyed) u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
              finishPan = null;
            };
            const onKey = (event: KeyboardEvent) => {
              if (event.key === 'Escape') cleanup();
            };
            const onUp = (event: MouseEvent) => {
              const { end, x, y } = selection(event);
              const xRange: AxisRange = [u.posToVal(Math.min(start.x, end.x), 'x'), u.posToVal(Math.max(start.x, end.x), 'x')];
              const yRange: AxisRange = [u.posToVal(Math.max(start.y, end.y), 'y'), u.posToVal(Math.min(start.y, end.y), 'y')];
              cleanup();
              // Lock Y before publishing X, so auto-fit cannot undo the box's
              // vertical bounds. Only X is shared with the other TP plots.
              if (y && validAxisRange(yRange)) {
                commitY(yRange);
                u.setScale('y', { min: yRange[0], max: yRange[1] });
              }
              if (x && validAxisRange(xRange)) {
                u.setScale('x', { min: xRange[0], max: xRange[1] });
                commit?.(xRange);
              }
            };
            finishPan = cleanup;
            window.addEventListener('mousemove', onMove);
            window.addEventListener('mouseup', onUp);
            window.addEventListener('keydown', onKey);
            window.addEventListener('blur', cleanup);
            return;
          }
          if (boxOnly || (commitY && e.altKey)) return;
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
            cleanup();
            if (!destroyed && moved && commit) commit(moved);
          };
          const cleanup = () => {
            window.removeEventListener('mousemove', onMove);
            window.removeEventListener('mouseup', onUp);
            if (!destroyed) u.over.style.cursor = '';
            finishPan = null;
          };
          finishPan?.();
          finishPan = cleanup;
          window.addEventListener('mousemove', onMove);
          window.addEventListener('mouseup', onUp);
        };

        u.over.addEventListener('dblclick', cancel);
        if (!boxOnly) u.over.addEventListener('wheel', onWheel, { passive: false });
        u.over.addEventListener('mousedown', onDown);
        detachReadyListeners = () => {
          u.over.removeEventListener('wheel', onWheel);
          u.over.removeEventListener('dblclick', cancel);
          u.over.removeEventListener('mousedown', onDown);
        };
      },
      // Flush (not drop) a pending wheel commit: plots are rebuilt whenever new
      // data lands, and dropping here would silently lose the last wheel ticks.
      destroy: () => {
        flush();
        destroyed = true;
        finishPan?.();
        detachReadyListeners?.();
        finishPan = null;
        detachReadyListeners = null;
        if (control) control.current = null;
      },
    },
  };
}

/** Reuse axis-selective box zoom without replacing a plot's wheel/pan behavior. */
export function boxZoomPlugin(commitY: (range: AxisRange) => void = () => {}): uPlot.Plugin {
  return xPanZoomPlugin(undefined, commitY, undefined, true);
}

/**
 * uPlot plugin for a fully client-side two-dimensional point cloud.
 *
 * - wheel: zoom both axes around the cursor
 * - shift-drag or middle-drag: pan both axes
 * - plain left-drag: supplied by the adjacent boxZoomPlugin
 * - double-click: left to uPlot's built-in auto-range reset
 */
export function xyPanZoomPlugin(): uPlot.Plugin {
  let destroyed = false;
  let detachReadyListeners: (() => void) | null = null;
  let finishPan: (() => void) | null = null;

  return {
    opts: (_u, opts) => {
      const cursor = (opts.cursor = opts.cursor ?? {});
      const bind = (cursor.bind = cursor.bind ?? {});
      bind.mousedown = (_self, _targ, handler) => (e) => {
        if (e.button === 0 && !isPanGesture(e)) handler(e);
        return null;
      };
    },
    hooks: {
      ready: (u) => {
        const onWheel = (e: WheelEvent) => {
          const xMin = u.scales.x.min;
          const xMax = u.scales.x.max;
          const yMin = u.scales.y.min;
          const yMax = u.scales.y.max;
          if (
            destroyed ||
            xMin == null ||
            xMax == null ||
            yMin == null ||
            yMax == null ||
            e.deltaY === 0
          ) {
            return;
          }

          const rect = u.over.getBoundingClientRect();
          if (rect.width <= 0 || rect.height <= 0) return;
          e.preventDefault();

          const xVal = u.posToVal(e.clientX - rect.left, 'x');
          const yVal = u.posToVal(e.clientY - rect.top, 'y');
          const factor = e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP;
          const nextX: [number, number] = [
            xVal - (xVal - xMin) * factor,
            xVal + (xMax - xVal) * factor,
          ];
          const nextY: [number, number] = [
            yVal - (yVal - yMin) * factor,
            yVal + (yMax - yVal) * factor,
          ];
          // XY axes can be micro/nano units; a fixed time-sized minimum span
          // prevents legitimate zooms. Use the shared relative tick guard.
          if (!validAxisRange(nextX) || !validAxisRange(nextY)) {
            return;
          }

          u.batch(() => {
            u.setScale('x', { min: nextX[0], max: nextX[1] });
            u.setScale('y', { min: nextY[0], max: nextY[1] });
          });
        };

        const onDown = (e: MouseEvent) => {
          if (destroyed || !isPanGesture(e)) return;
          const xMin = u.scales.x.min;
          const xMax = u.scales.x.max;
          const yMin = u.scales.y.min;
          const yMax = u.scales.y.max;
          if (
            xMin == null ||
            xMax == null ||
            yMin == null ||
            yMax == null
          ) {
            return;
          }

          const rect = u.over.getBoundingClientRect();
          if (rect.width <= 0 || rect.height <= 0) return;
          e.preventDefault(); // also suppresses middle-button autoscroll

          const x0 = e.clientX;
          const y0 = e.clientY;
          const xPerPx = (xMax - xMin) / rect.width;
          const yPerPx = (yMax - yMin) / rect.height;
          u.over.style.cursor = 'grabbing';

          const cleanup = () => {
            window.removeEventListener('mousemove', onMove);
            window.removeEventListener('mouseup', onUp);
            if (!destroyed) u.over.style.cursor = '';
            finishPan = null;
          };
          const onMove = (event: MouseEvent) => {
            if (destroyed) return;
            const dx = (event.clientX - x0) * xPerPx;
            const dy = (event.clientY - y0) * yPerPx;
            u.batch(() => {
              u.setScale('x', { min: xMin - dx, max: xMax - dx });
              // Screen y grows downward, while a normal numeric y scale grows upward.
              u.setScale('y', { min: yMin + dy, max: yMax + dy });
            });
          };
          const onUp = () => cleanup();

          finishPan?.();
          finishPan = cleanup;
          window.addEventListener('mousemove', onMove);
          window.addEventListener('mouseup', onUp);
        };

        u.over.addEventListener('wheel', onWheel, { passive: false });
        u.over.addEventListener('mousedown', onDown);
        detachReadyListeners = () => {
          u.over.removeEventListener('wheel', onWheel);
          u.over.removeEventListener('mousedown', onDown);
        };
      },
      destroy: () => {
        destroyed = true;
        finishPan?.();
        detachReadyListeners?.();
        finishPan = null;
        detachReadyListeners = null;
      },
    },
  };
}
