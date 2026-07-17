import React, { useState, useRef, useCallback, useMemo, useEffect } from 'react';
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ZAxis,
} from 'recharts';
import { ScatterDataPoint, TestPoint } from '../../types';
import { AnimatedDot } from './AnimatedDot';
import { ClusterDot } from './ClusterDot';
import { formatValue } from '../../utils/formatters';
import { PointSelectionMenu } from './PointSelectionMenu';
import { CustomScatterTooltip } from './CustomScatterTooltip';
import { clusterPoints, shouldEnableClustering } from '../../utils/pointClustering';

interface MainScatterPlotProps {
  scatterData: ScatterDataPoint[];
  rawDataCount: number;
  xLabel: string;
  yLabel: string;
  mainZoom: [number, number, number, number] | null;
  onToggleTestPoint: (point: ScatterDataPoint) => void;
  onWheel: (e: React.WheelEvent<HTMLDivElement>) => void;
  onPan: (deltaX: number, deltaY: number, currentBounds?: { xMin: number; xMax: number; yMin: number; yMax: number }) => void;
  clusteringEnabled: boolean;
}

interface MenuState {
  points: ScatterDataPoint[];
  position: { x: number; y: number };
}

export const MainScatterPlot: React.FC<MainScatterPlotProps> = ({
  scatterData,
  rawDataCount,
  xLabel,
  yLabel,
  mainZoom,
  onToggleTestPoint,
  onWheel,
  onPan,
  clusteringEnabled,
}) => {
  const [menuState, setMenuState] = useState<MenuState | null>(null);
  const [highlightedPointId, setHighlightedPointId] = useState<string | null>(null);
  const [chartDimensions, setChartDimensions] = useState({ width: 0, height: 0 });
  const chartRef = useRef<HTMLDivElement>(null);
  const pointPositions = useRef<Map<string, { cx: number; cy: number }>>(new Map());

  // Track chart dimensions for clustering recalculation
  useEffect(() => {
    if (!chartRef.current) return;

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        setChartDimensions({ width, height });
      }
    });

    resizeObserver.observe(chartRef.current);

    return () => {
      resizeObserver.disconnect();
    };
  }, []);

  // Update menu points when scatterData changes (to reflect selection state changes)
  useEffect(() => {
    if (menuState) {
      const updatedPoints = menuState.points.map((menuPoint) => {
        const currentPoint = scatterData.find((p) => p.id === menuPoint.id);
        return currentPoint || menuPoint;
      });
      setMenuState((prev) => {
        if (!prev) return null;
        return {
          ...prev,
          points: updatedPoints,
        };
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scatterData]);

  // Pan state
  const [isPanning, setIsPanning] = useState(false);
  const [suppressTooltip, setSuppressTooltip] = useState(false);
  const panStart = useRef<{ x: number; y: number; clientX: number; clientY: number } | null>(null);
  const hasDragged = useRef(false);
  const rafIdRef = useRef<number | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const latestMouseEvent = useRef<{ clientX: number; clientY: number } | null>(null);
  const boundsRef = useRef<{
    initialXMin: number;
    initialXMax: number;
    initialYMin: number;
    initialYMax: number;
    currentXMin: number;
    currentXMax: number;
    currentYMin: number;
    currentYMax: number;
  }>({
    initialXMin: 0,
    initialXMax: 1,
    initialYMin: 0,
    initialYMax: 1,
    currentXMin: 0,
    currentXMax: 1,
    currentYMin: 0,
    currentYMax: 1,
  });
  const mainZoomRef = useRef(mainZoom);

  // Calculate bounds for clustering - optimized to avoid spread operators
  const bounds = useMemo(() => {
    if (!scatterData.length) {
      return {
        initialXMin: 0,
        initialXMax: 1,
        initialYMin: 0,
        initialYMax: 1,
        currentXMin: 0,
        currentXMax: 1,
        currentYMin: 0,
        currentYMax: 1,
      };
    }

    // Use reduce instead of spread operator for better performance with large arrays
    let xMin = scatterData[0].x;
    let xMax = scatterData[0].x;
    let yMin = scatterData[0].y;
    let yMax = scatterData[0].y;

    for (let i = 1; i < scatterData.length; i++) {
      const point = scatterData[i];
      if (point.x < xMin) xMin = point.x;
      if (point.x > xMax) xMax = point.x;
      if (point.y < yMin) yMin = point.y;
      if (point.y > yMax) yMax = point.y;
    }

    const pad = 0.05;
    const xPadding = (xMax - xMin) * pad;
    const yPadding = (yMax - yMin) * pad;

    return {
      initialXMin: xMin - xPadding,
      initialXMax: xMax + xPadding,
      initialYMin: yMin - yPadding,
      initialYMax: yMax + yPadding,
      currentXMin: mainZoom ? mainZoom[0] : xMin - xPadding,
      currentXMax: mainZoom ? mainZoom[1] : xMax + xPadding,
      currentYMin: mainZoom ? mainZoom[2] : yMin - yPadding,
      currentYMax: mainZoom ? mainZoom[3] : yMax + yPadding,
    };
  }, [scatterData, mainZoom]);

  // Update refs when values change
  useEffect(() => {
    boundsRef.current = bounds;
  }, [bounds]);

  useEffect(() => {
    mainZoomRef.current = mainZoom;
  }, [mainZoom]);

  const enableClustering = clusteringEnabled && shouldEnableClustering(rawDataCount);

  // Compute clusters
  const clusteredData = useMemo(() => {
    if (!enableClustering || !chartRef.current) {
      return { clusters: [], individualPoints: scatterData };
    }

    // Separate selected and non-selected points
    const selectedPoints = scatterData.filter((p) => p.isSelected);
    const nonSelectedPoints = scatterData.filter((p) => !p.isSelected);

    const rect = chartRef.current.getBoundingClientRect();
    const chartWidth = rect.width - 70; // Account for margins
    const chartHeight = rect.height - 50;

    // Only cluster non-selected points
    const result = clusterPoints(
      nonSelectedPoints,
      bounds.currentXMin,
      bounds.currentXMax,
      bounds.currentYMin,
      bounds.currentYMax,
      chartWidth,
      chartHeight,
      // Overlap scale, not FMS's 30px decluttering scale: dots are r=6, so
      // centers <14px apart render as visually touching/stacked circles.
      14,
      2 // even a pair of stacked points must show a count badge
    );

    // Add selected points back as individual points
    return {
      clusters: result.clusters,
      individualPoints: [...result.individualPoints, ...selectedPoints],
    };
  }, [scatterData, bounds, enableClustering, chartDimensions]);

  // Prepare data for rendering
  const renderData = useMemo(() => {
    if (!enableClustering) {
      return scatterData;
    }

    // Start with individual points from clustering
    const individualData = [...clusteredData.individualPoints];
    const clusterData: Array<{
      x: number;
      y: number;
      id: string;
      isCluster: boolean;
      clusterCount: number;
      clusterPoints: ScatterDataPoint[];
      color: string;
      isSelected: boolean;
      test: string;
      name: string;
      label: string;
      tp: TestPoint;
    }> = [];

    // Process each cluster
    clusteredData.clusters.forEach((cluster, idx) => {
      let clusterPoints = cluster.points;
      let highlightedPoint: ScatterDataPoint | null = null;

      // If a point is highlighted, check if it's in this cluster
      if (highlightedPointId) {
        const foundIndex = clusterPoints.findIndex((p) => p.id === highlightedPointId);
        if (foundIndex !== -1) {
          // Remove highlighted point from cluster
          highlightedPoint = clusterPoints[foundIndex];
          clusterPoints = clusterPoints.filter((_, i) => i !== foundIndex);
        }
      }

      // Only create cluster if we still have enough points
      if (clusterPoints.length >= 2) {
        clusterData.push({
          x: cluster.centerX,
          y: cluster.centerY,
          id: `cluster-${idx}`,
          isCluster: true,
          clusterCount: clusterPoints.length,
          clusterPoints: clusterPoints,
          color: '#4a90d9',
          isSelected: false,
          test: '',
          name: '',
          label: '',
          tp: {} as TestPoint,
        });
      } else {
        // If cluster too small, add remaining points as individuals
        individualData.push(...clusterPoints);
      }

      // Add highlighted point as individual (will be highlighted by AnimatedDot)
      if (highlightedPoint) {
        individualData.push(highlightedPoint);
      }
    });

    return [...clusterData, ...individualData] as (ScatterDataPoint & {
      isCluster?: boolean;
      clusterCount?: number;
      clusterPoints?: ScatterDataPoint[];
    })[];
  }, [scatterData, clusteredData, enableClustering, highlightedPointId]);

  const handlePointClick = useCallback(
    (
      clickedPoint: ScatterDataPoint & {
        isCluster?: boolean;
        clusterPoints?: ScatterDataPoint[];
      },
      cx: number,
      cy: number,
      event: React.MouseEvent
    ) => {
      // Handle cluster click
      if (clickedPoint.isCluster && clickedPoint.clusterPoints) {
        setMenuState({
          points: clickedPoint.clusterPoints,
          position: {
            x: event.clientX,
            y: event.clientY,
          },
        });
        return;
      }

      // Store the position for this point
      pointPositions.current.set(clickedPoint.id, { cx, cy });

      // Find all points within a 15px radius
      const CLICK_RADIUS = 15;
      const nearbyPoints: ScatterDataPoint[] = [];

      pointPositions.current.forEach((pos, id) => {
        const distance = Math.sqrt(Math.pow(pos.cx - cx, 2) + Math.pow(pos.cy - cy, 2));
        if (distance <= CLICK_RADIUS) {
          const point = scatterData.find((p) => p.id === id);
          if (point) {
            nearbyPoints.push(point);
          }
        }
      });

      // If multiple points are nearby, show menu
      if (nearbyPoints.length > 1) {
        const rect = chartRef.current?.getBoundingClientRect();
        if (rect) {
          setMenuState({
            points: nearbyPoints,
            position: {
              x: event.clientX,
              y: event.clientY,
            },
          });
        }
      } else {
        // Single point, toggle directly
        onToggleTestPoint(clickedPoint);
      }
    },
    [scatterData, onToggleTestPoint]
  );

  const handleMenuSelect = useCallback(
    (point: ScatterDataPoint) => {
      onToggleTestPoint(point);
      // Don't close menu - let user select multiple points
      // setMenuState(null);
      // setHighlightedPointId(null);
    },
    [onToggleTestPoint]
  );

  const handleMenuClose = useCallback(() => {
    setMenuState(null);
    setHighlightedPointId(null);
  }, []);

  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    // Only start panning on left click
    // AnimatedDot calls stopPropagation, so clicks on points won't reach here
    if (e.button === 0) {
      const rect = chartRef.current?.getBoundingClientRect();
      if (!rect) return;

      // If menu is open, check if the click is on the menu
      if (menuState) {
        const target = e.target as HTMLElement;
        const isMenuClick = target.closest('[data-menu-container]');

        if (isMenuClick) {
          // Click is on menu, don't interfere
          return;
        }

        // Check if mouse is inside the plot area
        const isInsidePlot =
          e.clientX >= rect.left &&
          e.clientX <= rect.right &&
          e.clientY >= rect.top &&
          e.clientY <= rect.bottom;

        if (!isInsidePlot) {
          // Mouse is outside plot, don't start panning
          return;
        }

        // Mouse is inside plot, close menu and allow panning
        setMenuState(null);
        setHighlightedPointId(null);
      }

      setIsPanning(true);
      hasDragged.current = false;
      panStart.current = {
        x: e.clientX,
        y: e.clientY,
        clientX: e.clientX,
        clientY: e.clientY,
      };

      // Prevent text selection while dragging
      e.preventDefault();
    }
  }, [menuState]);

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!isPanning || !panStart.current || !chartRef.current) return;

      // Store latest mouse coordinates
      latestMouseEvent.current = { clientX: e.clientX, clientY: e.clientY };

      // If moved more than 3 pixels, consider it a drag
      const deltaPixelsX = e.clientX - panStart.current.clientX;
      const deltaPixelsY = e.clientY - panStart.current.clientY;

      if (Math.abs(deltaPixelsX) > 3 || Math.abs(deltaPixelsY) > 3) {
        hasDragged.current = true;
      }

      if (!hasDragged.current) return;

      // Suppress tooltip when dragging starts
      if (!suppressTooltip) {
        setSuppressTooltip(true);
      }

      // Throttle pan updates using requestAnimationFrame
      if (rafIdRef.current !== null) {
        return; // Skip this update, previous one is still pending
      }

      rafIdRef.current = requestAnimationFrame(() => {
        rafIdRef.current = null;

        if (!chartRef.current || !panStart.current || !latestMouseEvent.current) return;

        const rect = chartRef.current.getBoundingClientRect();
        const chartWidth = rect.width - 70; // Account for margins
        const chartHeight = rect.height - 50;

        // Use latest mouse coordinates for smooth panning
        const deltaPixelsX = latestMouseEvent.current.clientX - panStart.current.clientX;
        const deltaPixelsY = latestMouseEvent.current.clientY - panStart.current.clientY;

        // Use refs to avoid stale closure issues
        const currentBounds = boundsRef.current;
        const currentZoom = mainZoomRef.current;

        // Calculate data space delta based on current zoom
        const curXMin = currentZoom ? currentZoom[0] : currentBounds.currentXMin;
        const curXMax = currentZoom ? currentZoom[1] : currentBounds.currentXMax;
        const curYMin = currentZoom ? currentZoom[2] : currentBounds.currentYMin;
        const curYMax = currentZoom ? currentZoom[3] : currentBounds.currentYMax;

        const xRange = curXMax - curXMin;
        const yRange = curYMax - curYMin;

        const deltaX = (deltaPixelsX / chartWidth) * xRange;
        const deltaY = (-deltaPixelsY / chartHeight) * yRange;

        // Pass current bounds to handlePan for consistent first-pan behavior
        onPan(deltaX, deltaY, {
          xMin: curXMin,
          xMax: curXMax,
          yMin: curYMin,
          yMax: curYMax,
        });

        // Update pan start for continuous panning
        panStart.current.clientX = latestMouseEvent.current.clientX;
        panStart.current.clientY = latestMouseEvent.current.clientY;
      });
    },
    [isPanning, onPan, suppressTooltip]
  );

  const handleMouseUp = useCallback(() => {
    setIsPanning(false);
    panStart.current = null;
    hasDragged.current = false;
    // Cancel any pending animation frame
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
  }, []);

  const handleMouseLeave = useCallback(() => {
    setIsPanning(false);
    panStart.current = null;
    hasDragged.current = false;
    // Clear tooltip suppression when leaving the chart
    setSuppressTooltip(false);
    // Cancel any pending animation frame
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
  }, []);

  const handlePointHover = useCallback(() => {
    // Clear tooltip suppression when hovering over a point
    if (suppressTooltip) {
      setSuppressTooltip(false);
    }
  }, [suppressTooltip]);

  // Memoize the shape renderer to prevent recreating on every render
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const shapeRenderer = useCallback((props: unknown) => {
    const shapeProps = props as {
      cx: number;
      cy: number;
      payload: ScatterDataPoint & {
        isCluster?: boolean;
        clusterCount?: number;
        clusterPoints?: ScatterDataPoint[];
      };
    };

    // Store position for click detection
    pointPositions.current.set(shapeProps.payload.id, {
      cx: shapeProps.cx,
      cy: shapeProps.cy,
    });

    // Render cluster dot
    if (shapeProps.payload.isCluster && shapeProps.payload.clusterCount) {
      return (
        <ClusterDot
          cx={shapeProps.cx}
          cy={shapeProps.cy}
          count={shapeProps.payload.clusterCount}
          onClick={(event) =>
            handlePointClick(
              shapeProps.payload,
              shapeProps.cx,
              shapeProps.cy,
              event
            )
          }
        />
      );
    }

    // During panning, use simple circles for better performance
    if (isPanning) {
      const r = shapeProps.payload.isSelected ? 8 : 6;
      return (
        <circle
          cx={shapeProps.cx}
          cy={shapeProps.cy}
          r={r}
          fill={shapeProps.payload.color}
          stroke={shapeProps.payload.isSelected ? '#fff' : 'transparent'}
          strokeWidth={2}
          style={{ cursor: 'pointer' }}
          onMouseDown={(e) => {
            e.stopPropagation();
          }}
          onClick={(e) => {
            e.stopPropagation();
            handlePointClick(shapeProps.payload, shapeProps.cx, shapeProps.cy, e as any);
          }}
        />
      );
    }

    // Render individual point with full effects when not panning
    return (
      <AnimatedDot
        cx={shapeProps.cx}
        cy={shapeProps.cy}
        payload={shapeProps.payload}
        onToggle={(event) =>
          handlePointClick(
            shapeProps.payload,
            shapeProps.cx,
            shapeProps.cy,
            event
          )
        }
        isHighlighted={highlightedPointId === shapeProps.payload.id}
        onPointHover={handlePointHover}
      />
    );
  }, [handlePointClick, highlightedPointId, handlePointHover, isPanning]);

  const handleWheel = useCallback((e: React.WheelEvent<HTMLDivElement>) => {
    if (menuState && chartRef.current) {
      // Check if mouse position is within the plot div bounds
      const rect = chartRef.current.getBoundingClientRect();
      const isInsidePlot =
        e.clientX >= rect.left &&
        e.clientX <= rect.right &&
        e.clientY >= rect.top &&
        e.clientY <= rect.bottom;

      if (isInsidePlot) {
        // Mouse is over the plot area, close menu and zoom
        setMenuState(null);
        setHighlightedPointId(null);
        onWheel(e);
      } else {
        // Mouse is outside the plot area, just close menu without zooming
        setMenuState(null);
        setHighlightedPointId(null);
      }
    } else {
      // No menu open, zoom normally
      onWheel(e);
    }
  }, [menuState, onWheel]);

  return (
    <div
      ref={chartRef}
      style={{ flex: 1, minHeight: 0, position: 'relative', cursor: isPanning ? 'grabbing' : 'default' }}
      onWheel={handleWheel}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseLeave}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 10, right: 20, bottom: 40, left: 35 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#3c3c3c" />
          <XAxis
            dataKey="x"
            type="number"
            name={xLabel}
            tick={{ fill: '#a0a0a0', fontSize: 11 }}
            label={{ value: xLabel, position: 'bottom', fill: '#a0a0a0', fontSize: 12 }}
            stroke="#3c3c3c"
            domain={mainZoom ? [mainZoom[0], mainZoom[1]] : [bounds.initialXMin, bounds.initialXMax]}
            allowDataOverflow
            tickFormatter={(v) => formatValue(v).toString()}
          />
          <YAxis
            dataKey="y"
            type="number"
            name={yLabel}
            tick={{ fill: '#a0a0a0', fontSize: 11 }}
            label={{
              value: yLabel,
              angle: -90,
              position: 'insideLeft',
              fill: '#a0a0a0',
              fontSize: 12,
            }}
            stroke="#3c3c3c"
            domain={mainZoom ? [mainZoom[2], mainZoom[3]] : [bounds.initialYMin, bounds.initialYMax]}
            allowDataOverflow
            tickFormatter={(v) => formatValue(v).toString()}
          />
          <ZAxis range={[100, 100]} />
          <Tooltip
            cursor={isPanning || suppressTooltip ? false : { strokeDasharray: '3 3' }}
            content={<CustomScatterTooltip />}
            isAnimationActive={false}
            active={isPanning || suppressTooltip ? false : undefined}
          />
          <Scatter
            data={renderData}
            shape={shapeRenderer}
            isAnimationActive={false}
          />
        </ScatterChart>
      </ResponsiveContainer>

      {/* Point Selection Menu */}
      {menuState && (
        <PointSelectionMenu
          points={menuState.points}
          position={menuState.position}
          onSelect={handleMenuSelect}
          onClose={handleMenuClose}
          onHover={setHighlightedPointId}
        />
      )}
    </div>
  );
};
