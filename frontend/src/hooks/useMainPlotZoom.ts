import { useState, useCallback, useMemo } from 'react';
import { ScatterDataPoint } from '../types';

type ZoomDomain = [number, number, number, number] | null;

export const useMainPlotZoom = (scatterData: ScatterDataPoint[]) => {
  const [mainZoom, setMainZoom] = useState<ZoomDomain>(null);

  // Calculate default bounds - reused for consistency
  const defaultBounds = useMemo(() => {
    if (!scatterData.length) {
      return { xMin: 0, xMax: 1, yMin: 0, yMax: 1 };
    }
    const xVals = scatterData.map((d) => d.x);
    const yVals = scatterData.map((d) => d.y);
    const xMin = Math.min(...xVals);
    const xMax = Math.max(...xVals);
    const yMin = Math.min(...yVals);
    const yMax = Math.max(...yVals);
    const pad = 0.05;

    return {
      xMin: xMin - (xMax - xMin) * pad,
      xMax: xMax + (xMax - xMin) * pad,
      yMin: yMin - (yMax - yMin) * pad,
      yMax: yMax + (yMax - yMin) * pad,
    };
  }, [scatterData]);

  const handleMainWheel = useCallback(
    (e: React.WheelEvent<HTMLDivElement>) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const xRatio = (e.clientX - rect.left - 50) / (rect.width - 70);
      const yRatio = 1 - (e.clientY - rect.top - 10) / (rect.height - 50);
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
    handleMainWheel,
    handlePan,
    resetZoom,
  };
};
