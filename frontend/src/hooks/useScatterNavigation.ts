import { useCallback, useEffect, useRef, useState, type RefObject, type PointerEvent, type KeyboardEvent, type MouseEvent } from 'react';
import { PLOT_INSET, PLOT_INSET_X, PLOT_INSET_Y } from '../constants/scatterGeometry';

type View = [number, number, number, number];
type Mode = 'pan' | 'zoom';
interface Gesture {
  pointerId: number; mode: Mode; startX: number; startY: number; x: number; y: number;
  rect: DOMRect; view: View; previous: View | null; moved: boolean;
}
interface Options {
  chartRef: RefObject<HTMLDivElement>;
  view: View;
  mainZoom: View | null;
  contextKey: string;
  onSetView: (view: View) => void;
  onResetZoom: () => void;
  onZoomBy: (factor: number) => void;
  onPan: (x: number, y: number, bounds: { xMin: number; xMax: number; yMin: number; yMax: number }) => void;
  onStart: () => void;
}

function isNavigationTarget(chart: HTMLDivElement, target: EventTarget | null): boolean {
  // React capture also visits body portals. Their controls must keep their
  // own clicks/focus, even when the popup happens to cover the plot rectangle.
  return target instanceof Element && chart.contains(target) &&
    !target.closest('button, a, input, select, textarea, dialog, [role="button"], [role="toolbar"], [role="menu"], [role="dialog"], [contenteditable]:not([contenteditable="false"])');
}

/** Pointer capture starts only after a real drag, leaving point clicks intact. */
export function useScatterNavigation(options: Options) {
  const latest = useRef(options);
  latest.current = options;
  const [mode, setMode] = useState<Mode>('pan');
  const [isDragging, setIsDragging] = useState(false);
  const [box, setBox] = useState<{ left: number; top: number; width: number; height: number } | null>(null);
  const gesture = useRef<Gesture | null>(null);
  const frame = useRef<number | null>(null);
  const suppressClick = useRef(false);

  const draw = useCallback(() => {
    frame.current = null;
    const drag = gesture.current;
    if (!drag?.moved) return;
    const { view, rect } = drag;
    if (drag.mode === 'pan') {
      const dx = (drag.x - drag.startX) / (rect.width - PLOT_INSET_X) * (view[1] - view[0]);
      const dy = (drag.y - drag.startY) / (rect.height - PLOT_INSET_Y) * (view[3] - view[2]);
      latest.current.onSetView([view[0] - dx, view[1] - dx, view[2] + dy, view[3] + dy]);
    } else {
      setBox({ left: Math.min(drag.startX, drag.x), top: Math.min(drag.startY, drag.y),
        width: Math.abs(drag.x - drag.startX), height: Math.abs(drag.y - drag.startY) });
    }
  }, []);

  const finish = useCallback((cancel: boolean, restore = true) => {
    const drag = gesture.current;
    if (!drag) return;
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
    if (!cancel && drag.moved) {
      // Mouseup can precede the queued frame. Always commit its final position.
      if (drag.mode === 'pan') draw();
      else if (Math.abs(drag.x - drag.startX) >= 8 && Math.abs(drag.y - drag.startY) >= 8) {
        const { view, rect } = drag;
        const x = (pixel: number) => view[0] + (pixel - PLOT_INSET.left) / (rect.width - PLOT_INSET_X) * (view[1] - view[0]);
        const y = (pixel: number) => view[3] - (pixel - PLOT_INSET.top) / (rect.height - PLOT_INSET_Y) * (view[3] - view[2]);
        latest.current.onSetView([x(Math.min(drag.startX, drag.x)), x(Math.max(drag.startX, drag.x)),
          y(Math.max(drag.startY, drag.y)), y(Math.min(drag.startY, drag.y))]);
      }
    } else if (cancel && restore && drag.moved && drag.mode === 'pan') {
      if (drag.previous) latest.current.onSetView(drag.previous);
      else latest.current.onResetZoom();
    }
    gesture.current = null;
    const chart = latest.current.chartRef.current;
    if (chart?.hasPointerCapture(drag.pointerId)) chart.releasePointerCapture(drag.pointerId);
    setIsDragging(false);
    setBox(null);
  }, [draw]);

  useEffect(() => {
    const update = (event: globalThis.PointerEvent) => {
      const drag = gesture.current;
      if (!drag || event.pointerId !== drag.pointerId) return;
      const { rect } = drag;
      drag.x = event.clientX - rect.left;
      drag.y = event.clientY - rect.top;
      if (drag.mode === 'zoom') {
        drag.x = Math.max(PLOT_INSET.left, Math.min(rect.width - PLOT_INSET.right, drag.x));
        drag.y = Math.max(PLOT_INSET.top, Math.min(rect.height - PLOT_INSET.bottom, drag.y));
      }
    };
    const move = (event: globalThis.PointerEvent) => {
      const drag = gesture.current;
      if (!drag || event.pointerId !== drag.pointerId) return;
      if (!event.buttons) { finish(true); return; }
      update(event);
      if (!drag.moved && Math.hypot(drag.x - drag.startX, drag.y - drag.startY) >= 4) {
        drag.moved = true;
        suppressClick.current = true;
        setIsDragging(true);
        latest.current.chartRef.current?.setPointerCapture(drag.pointerId);
      }
      if (drag.moved && frame.current === null) frame.current = requestAnimationFrame(draw);
    };
    const up = (event: globalThis.PointerEvent) => {
      if (gesture.current?.pointerId !== event.pointerId) return;
      update(event);
      finish(false);
    };
    const cancel = () => finish(true);
    const key = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape' && gesture.current) { event.preventDefault(); cancel(); }
    };
    const chart = latest.current.chartRef.current;
    const keyboardFocus = (event: globalThis.KeyboardEvent) => {
      if (!['Shift', 'Control', 'Alt', 'Meta'].includes(event.key)) {
        chart?.removeAttribute('data-pointer-focus');
      }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', cancel);
    window.addEventListener('blur', cancel);
    window.addEventListener('keydown', key);
    // Capture also sees Tab and keyboard focus restored from portaled menus
    // whose own key handlers stop propagation.
    window.addEventListener('keydown', keyboardFocus, true);
    // React's delegated wheel listener is passive. Block page scrolling only
    // inside the plot; leave Ctrl/Meta+wheel available for browser zoom.
    const wheel = (event: WheelEvent) => {
      if (!chart || event.ctrlKey || event.metaKey) return;
      const rect = chart.getBoundingClientRect();
      if (event.clientX >= rect.left + PLOT_INSET.left && event.clientX <= rect.right - PLOT_INSET.right &&
          event.clientY >= rect.top + PLOT_INSET.top && event.clientY <= rect.bottom - PLOT_INSET.bottom && event.deltaY) event.preventDefault();
    };
    chart?.addEventListener('wheel', wheel, { passive: false });
    const observer = new ResizeObserver(cancel);
    if (chart) observer.observe(chart);
    return () => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', cancel); window.removeEventListener('blur', cancel);
      window.removeEventListener('keydown', key); chart?.removeEventListener('wheel', wheel); observer.disconnect();
      window.removeEventListener('keydown', keyboardFocus, true);
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [draw, finish]);

  useEffect(() => { finish(true, false); }, [options.contextKey, finish]);

  const onPointerDownCapture = (event: PointerEvent<HTMLDivElement>) => {
    if (!isNavigationTarget(event.currentTarget, event.target)) return;
    // Programmatic focus after preventDefault can inherit :focus-visible
    // from a keyboard-focused control. Pointer gestures must not show it.
    event.currentTarget.setAttribute('data-pointer-focus', 'true');
    if (event.button !== 0 && event.button !== 1) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    if (x < PLOT_INSET.left || x > rect.width - PLOT_INSET.right || y < PLOT_INSET.top || y > rect.height - PLOT_INSET.bottom) return;
    if (rect.width <= PLOT_INSET_X || rect.height <= PLOT_INSET_Y) return;
    suppressClick.current = false;
    gesture.current = { pointerId: event.pointerId, mode: event.button === 1 ? 'pan' : event.shiftKey ? 'zoom' : mode,
      startX: x, startY: y, x, y, rect, view: [...latest.current.view], previous: latest.current.mainZoom, moved: false };
    latest.current.onStart();
    event.currentTarget.focus({ preventScroll: true });
    event.preventDefault();
  };
  const onClickCapture = (event: MouseEvent<HTMLDivElement>) => {
    if (!isNavigationTarget(event.currentTarget, event.target)) return;
    if (suppressClick.current) { event.preventDefault(); event.stopPropagation(); suppressClick.current = false; }
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget || event.ctrlKey || event.metaKey || event.altKey || gesture.current) return;
    const { view, onZoomBy, onPan, onResetZoom } = latest.current;
    const key = event.key.toLowerCase();
    if (!['+', '=', '-', 'home', 'arrowleft', 'arrowright', 'arrowup', 'arrowdown', 'p', 'z'].includes(key)) return;
    event.preventDefault(); event.stopPropagation(); latest.current.onStart();
    if (key === '+' || key === '=') onZoomBy(0.8);
    else if (key === '-') onZoomBy(1.25);
    else if (key === 'home') onResetZoom();
    else if (key === 'p' || key === 'z') setMode(key === 'p' ? 'pan' : 'zoom');
    else onPan((key === 'arrowleft' ? 1 : key === 'arrowright' ? -1 : 0) * (view[1] - view[0]) * 0.1,
      (key === 'arrowdown' ? 1 : key === 'arrowup' ? -1 : 0) * (view[3] - view[2]) * 0.1,
      { xMin: view[0], xMax: view[1], yMin: view[2], yMax: view[3] });
  };
  return { mode, activeMode: gesture.current?.mode ?? mode, setMode, isDragging, box, onPointerDownCapture, onClickCapture, onKeyDown,
    cancelDrag: () => finish(true) };
}
