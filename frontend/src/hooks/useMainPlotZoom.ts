import { useState, useCallback, useMemo, type Dispatch, type SetStateAction } from 'react';
import { PLOT_INSET_Y } from '../constants/scatterGeometry';
import { ScatterRangePoint, ScatterRangeVisibility, scatterExtents } from '../utils/scatterRanges';
import {
  boundsViewport, panScatterViewport, scatterPlotAnchor, scatterWheelFactor,
  validScatterViewport, withinScatterLimits, zoomScatterViewport,
  type ScatterBounds, type ScatterViewport,
} from '../utils/scatterViewport';

type ZoomDomain = ScatterViewport | null;

const paddedAxis = (min: number, max: number): [number, number] => {
  const span = max - min;
  const padding = span > 0 ? span * 0.05 : Math.max(Math.abs(min) * 0.05, 0.5);
  return [min - padding, max + padding];
};

export const useMainPlotZoom = (
  scatterData: ScatterRangePoint[],
  initialZoom: ZoomDomain = null,
  rangeVisibility: ScatterRangeVisibility = { horizontal: false, vertical: false }
) => {
  const [mainZoom, updateZoom] = useState<ZoomDomain>(() =>
    initialZoom && validScatterViewport(initialZoom) ? initialZoom : null);
  // Keep the public React setter contract for session restore and axis changes,
  // while preventing invalid persisted or caller-supplied numbers reaching SVG.
  const setMainZoom: Dispatch<SetStateAction<ZoomDomain>> = useCallback(next => {
    updateZoom(previous => {
      const value = typeof next === 'function' ? next(previous) : next;
      return value === null || validScatterViewport(value) ? value : previous;
    });
  }, []);
  const horizontalRangesVisible = rangeVisibility.horizontal;
  const verticalRangesVisible = rangeVisibility.vertical;

  // Calculate default bounds - reused for consistency
  const defaultBounds = useMemo(() => {
    const extents = scatterExtents(scatterData, {
      horizontal: horizontalRangesVisible,
      vertical: verticalRangesVisible,
    });
    if (!extents) {
      return { xMin: 0, xMax: 1, yMin: 0, yMax: 1 };
    }
    const { xMin, xMax, yMin, yMax } = extents;
    const [paddedXMin, paddedXMax] = paddedAxis(xMin, xMax);
    const [paddedYMin, paddedYMax] = paddedAxis(yMin, yMax);

    return {
      xMin: paddedXMin,
      xMax: paddedXMax,
      yMin: paddedYMin,
      yMax: paddedYMax,
    };
  }, [horizontalRangesVisible, scatterData, verticalRangesVisible]);

  const defaultView = useMemo(() => {
    const view = boundsViewport(defaultBounds);
    return validScatterViewport(view) ? view : [0, 1, 0, 1] as ScatterViewport;
  }, [defaultBounds]);

  const setView = useCallback((view: ScatterViewport) => {
    if (withinScatterLimits(view, defaultView)) setMainZoom([...view]);
  }, [defaultView, setMainZoom]);

  const zoomBy = useCallback((factor: number) => {
    if (factor === 1) return;
    setMainZoom(previous => zoomScatterViewport(previous ?? defaultView, factor, defaultView) ?? previous);
  }, [defaultView, setMainZoom]);

  const handleMainWheel = useCallback(
    (e: React.WheelEvent<HTMLDivElement>) => {
      // Leave browser zoom shortcuts (and trackpad pinch delivered as Ctrl+wheel) alone.
      if (e.ctrlKey || e.metaKey) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const anchor = scatterPlotAnchor(e.clientX, e.clientY, rect);
      const factor = scatterWheelFactor(e.deltaY, e.deltaMode, rect.height - PLOT_INSET_Y);
      if (!anchor || factor === null || factor === 1) return;
      setMainZoom(previous => zoomScatterViewport(previous ?? defaultView, factor, defaultView, anchor) ?? previous);
    },
    [defaultView, setMainZoom]
  );

  const handlePan = useCallback(
    (deltaX: number, deltaY: number, currentBounds?: ScatterBounds) => {
      if (deltaX === 0 && deltaY === 0) return;
      setMainZoom(previous => {
        const view = previous ?? (currentBounds ? boundsViewport(currentBounds) : defaultView);
        return panScatterViewport(view, deltaX, deltaY, defaultView) ?? previous;
      });
    },
    [defaultView, setMainZoom]
  );

  const resetZoom = useCallback(() => {
    setMainZoom(null);
  }, [setMainZoom]);

  return {
    mainZoom,
    setMainZoom,
    handleMainWheel,
    handlePan,
    resetZoom,
    zoomBy,
    setView,
  };
};
