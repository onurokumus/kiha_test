import { useState, useCallback, useMemo } from 'react';
import { PLOT_INSET, PLOT_INSET_X, PLOT_INSET_Y } from '../constants/scatterGeometry';
import { ScatterRangePoint, ScatterRangeVisibility, scatterExtents } from '../utils/scatterRanges';

type ZoomDomain = [number, number, number, number] | null;

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
  const [mainZoom, setMainZoom] = useState<ZoomDomain>(initialZoom);
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

  const handleMainWheel = useCallback(
    (e: React.WheelEvent<HTMLDivElement>) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const xRatio = (e.clientX - rect.left - PLOT_INSET.left) / (rect.width - PLOT_INSET_X);
      const yRatio = 1 - (e.clientY - rect.top - PLOT_INSET.top) / (rect.height - PLOT_INSET_Y);
      const zoomFactor = e.deltaY > 0 ? 1.2 : 0.8;

      setMainZoom((prev) => {
        const curXMin = prev ? prev[0] : defaultBounds.xMin;
        const curXMax = prev ? prev[1] : defaultBounds.xMax;
        const curYMin = prev ? prev[2] : defaultBounds.yMin;
        const curYMax = prev ? prev[3] : defaultBounds.yMax;

        const xRange = curXMax - curXMin;
        const yRange = curYMax - curYMin;
        const xCenter = curXMin + xRange * Math.max(0, Math.min(1, xRatio));
        const yCenter = curYMin + yRange * Math.max(0, Math.min(1, yRatio));

        const newXRange = xRange * zoomFactor;
        const newYRange = yRange * zoomFactor;

        return [
          xCenter - newXRange * xRatio,
          xCenter + newXRange * (1 - xRatio),
          yCenter - newYRange * yRatio,
          yCenter + newYRange * (1 - yRatio),
        ];
      });
    },
    [defaultBounds]
  );

  const handlePan = useCallback(
    (deltaX: number, deltaY: number, currentBounds?: { xMin: number; xMax: number; yMin: number; yMax: number }) => {
      setMainZoom((prev) => {
        if (!prev) {
          // First pan - use the provided current bounds from the component
          if (!currentBounds) return null;
          return [
            currentBounds.xMin - deltaX,
            currentBounds.xMax - deltaX,
            currentBounds.yMin - deltaY,
            currentBounds.yMax - deltaY,
          ];
        }

        return [
          prev[0] - deltaX,
          prev[1] - deltaX,
          prev[2] - deltaY,
          prev[3] - deltaY,
        ];
      });
    },
    []
  );

  const resetZoom = useCallback(() => {
    setMainZoom(null);
  }, []);

  return {
    mainZoom,
    setMainZoom,
    handleMainWheel,
    handlePan,
    resetZoom,
  };
};
