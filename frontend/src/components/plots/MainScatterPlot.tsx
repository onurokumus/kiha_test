import { useTheme } from '../../hooks/useTheme';
import { createPortal } from 'react-dom';
import { themeSeriesColor } from '../../constants/uplotTheme';
import React, { useState, useRef, useCallback, useMemo, useEffect, useId } from 'react';
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ZAxis,
  Customized,
} from 'recharts';
import { DatasheetDataPoint, ScatterDataPoint, TestPoint } from '../../types';
import { AnimatedDot } from './AnimatedDot';
import { ClusterDot } from './ClusterDot';
import { SCATTER_MARGIN, PLOT_INSET, PLOT_INSET_X, PLOT_INSET_Y, X_AXIS_HEIGHT, Y_AXIS_WIDTH } from '../../constants/scatterGeometry';
import { useScatterNavigation } from '../../hooks/useScatterNavigation';
import { createScatterTicks } from '../../utils/scatterTicks';
import { ScatterGrid } from './ScatterGrid';
import styles from './MainScatterPlot.module.css';
import { PointSelectionMenu } from './PointSelectionMenu';
import { CustomScatterTooltip } from './CustomScatterTooltip';
import { ScatterRangeBars } from './ScatterRangeBars';
import { clusterPoints, shouldEnableClustering } from '../../utils/pointClustering';
import { scatterExtents } from '../../utils/scatterRanges';
import { PlotActionMenu } from './PlotActionMenu';
import { PlotExportControls, type PlotExportActions } from '../controls/PlotExportControls';
import { downloadScatterCsv, downloadScatterPng } from '../../utils/scatterExport';

interface MainScatterPlotProps {
  exportActions: React.RefObject<PlotExportActions>;
  navigationTarget: HTMLDivElement | null;
  scatterData: ScatterDataPoint[];
  datasheetData: DatasheetDataPoint[];
  rawDataCount: number;
  exportDisabledReason: string | null;
  xLabel: string;
  yLabel: string;
  xVariable: string;
  yVariable: string;
  mainZoom: [number, number, number, number] | null;
  onResetZoom: () => void;
  onZoomBy: (factor: number) => void;
  onSetView: (view: [number, number, number, number]) => void;
  onToggleTestPoint: (point: ScatterDataPoint) => void;
  onWheel: (e: React.WheelEvent<HTMLDivElement>) => void;
  onPan: (deltaX: number, deltaY: number, currentBounds?: { xMin: number; xMax: number; yMin: number; yMax: number }) => void;
  clusteringEnabled: boolean;
  showHorizontalErrorBars: boolean;
  showVerticalErrorBars: boolean;
}

interface MenuState {
  points: ScatterDataPoint[];
  position: { x: number; y: number };
}

export const MainScatterPlot: React.FC<MainScatterPlotProps> = ({
  exportActions,
  navigationTarget,
  scatterData,
  datasheetData,
  rawDataCount,
  exportDisabledReason,
  xLabel,
  yLabel,
  xVariable,
  yVariable,
  mainZoom,
  onResetZoom,
  onZoomBy,
  onSetView,
  onToggleTestPoint,
  onWheel,
  onPan,
  clusteringEnabled,
  showHorizontalErrorBars,
  showVerticalErrorBars,
}) => {
  const [menuState, setMenuState] = useState<MenuState | null>(null);
  const [highlightedPointId, setHighlightedPointId] = useState<string | null>(null);
  const [chartDimensions, setChartDimensions] = useState({ width: 0, height: 0 });
  const chartRef = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  const helpId = useId();
  const pointPositions = useRef<Map<string, { cx: number; cy: number }>>(new Map());
  const hoverTargetRef = useRef<Element | null>(null);
  const [hasPointHover, setHasPointHover] = useState(false);
  const dismissHover = useCallback(() => setHasPointHover(false), []);

  // The tooltip is portaled to document.body, outside Recharts' visibility
  // wrapper. Only an actual point hit may keep it open, never a stale payload.
  const trackPointHover = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const target = event.target instanceof Element
      ? event.target.closest('[data-scatter-hover-target]') : null;
    const owner = target && chartRef.current?.contains(target) ? target : null;
    if (owner === hoverTargetRef.current) return;
    hoverTargetRef.current = owner;
    setHasPointHover(Boolean(owner));
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') dismissHover(); };
    window.addEventListener('blur', dismissHover);
    window.addEventListener('resize', dismissHover);
    document.addEventListener('scroll', dismissHover, true);
    document.addEventListener('visibilitychange', dismissHover);
    document.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('blur', dismissHover);
      window.removeEventListener('resize', dismissHover);
      document.removeEventListener('scroll', dismissHover, true);
      document.removeEventListener('visibilitychange', dismissHover);
      document.removeEventListener('keydown', onKey);
    };
  }, [dismissHover]);

  useEffect(dismissHover, [dismissHover, xLabel, yLabel, mainZoom, scatterData]);

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

  /** The reference line participates in the automatic chart domain but never
   * in clustering, filtering, selection, or point-count calculations. */
  const domainData = useMemo(
    () => [...scatterData, ...datasheetData],
    [datasheetData, scatterData]
  );

  // Calculate bounds for clustering - optimized to avoid spread operators
  const bounds = useMemo(() => {
    const extents = scatterExtents(domainData, {
      horizontal: showHorizontalErrorBars,
      vertical: showVerticalErrorBars,
    });
    if (!extents) {
      return {
        initialXMin: 0,
        initialXMax: 1,
        initialYMin: 0,
        initialYMax: 1,
        currentXMin: mainZoom?.[0] ?? 0,
        currentXMax: mainZoom?.[1] ?? 1,
        currentYMin: mainZoom?.[2] ?? 0,
        currentYMax: mainZoom?.[3] ?? 1,
      };
    }

    const { xMin, xMax, yMin, yMax } = extents;

    const pad = 0.05;
    const xPadding =
      xMax > xMin ? (xMax - xMin) * pad : Math.max(Math.abs(xMin) * pad, 0.5);
    const yPadding =
      yMax > yMin ? (yMax - yMin) * pad : Math.max(Math.abs(yMin) * pad, 0.5);

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
  }, [domainData, mainZoom, showHorizontalErrorBars, showVerticalErrorBars]);

  const navigation = useScatterNavigation({ chartRef,
    view: [bounds.currentXMin, bounds.currentXMax, bounds.currentYMin, bounds.currentYMax],
    mainZoom, contextKey: JSON.stringify([xVariable, yVariable]), onSetView, onZoomBy, onPan, onResetZoom,
    onStart: () => { dismissHover(); setMenuState(null); setHighlightedPointId(null); },
  });
  const isPanning = navigation.isDragging;
  const xTicks = useMemo(() => createScatterTicks(bounds.currentXMin, bounds.currentXMax,
    chartDimensions.width - PLOT_INSET_X, 'x'), [bounds.currentXMin, bounds.currentXMax, chartDimensions.width]);
  const yTicks = useMemo(() => createScatterTicks(bounds.currentYMin, bounds.currentYMax,
    chartDimensions.height - PLOT_INSET_Y, 'y'), [bounds.currentYMin, bounds.currentYMax, chartDimensions.height]);

  const enableClustering = clusteringEnabled && shouldEnableClustering(rawDataCount);

  // Compute clusters
  const clusteredData = useMemo(() => {
    if (
      !enableClustering ||
      chartDimensions.width <= PLOT_INSET_X ||
      chartDimensions.height <= PLOT_INSET_Y
    ) {
      return { clusters: [], individualPoints: scatterData };
    }

    // Separate selected and non-selected points
    const selectedPoints = scatterData.filter((p) => p.isSelected);
    const nonSelectedPoints = scatterData.filter((p) => !p.isSelected);

    const chartWidth = chartDimensions.width - PLOT_INSET_X;
    const chartHeight = chartDimensions.height - PLOT_INSET_Y;

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

  // The 15px "nearby points" click test reads dot pixel positions accumulated
  // by the shape renderer. Recharts overwrites the entry for each dot it still
  // draws, but points that dropped out of renderData (filtered away, clustered,
  // or off-screen after zoom/pan/axis change) would otherwise linger with stale
  // coordinates and get falsely counted as "nearby". Drop the whole map the
  // moment renderData changes reference — the shape renderer repopulates it with
  // exactly the currently-drawn dots on this same render (bug 1.16).
  const renderedDataRef = useRef(renderData);
  if (renderedDataRef.current !== renderData) {
    renderedDataRef.current = renderData;
    pointPositions.current.clear();
  }

  const handlePointClick = useCallback(
    (
      clickedPoint: ScatterDataPoint & {
        isCluster?: boolean;
        clusterPoints?: ScatterDataPoint[];
      },
      cx: number,
      cy: number,
      event: { clientX: number; clientY: number }
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

  const handleMenuClose = useCallback((restoreFocus = false) => {
    setMenuState(null);
    setHighlightedPointId(null);
    if (restoreFocus) chartRef.current?.focus({ preventScroll: true });
  }, []);

  const handleMouseLeave = useCallback(() => {
    hoverTargetRef.current = null;
    setHasPointHover(false);
  }, []);

  // Memoize the shape renderer to prevent recreating on every render
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
          data-scatter-hover-target="point"
          cx={shapeProps.cx}
          cy={shapeProps.cy}
          r={r}
          fill={themeSeriesColor(shapeProps.payload.color, theme === 'dark')}
          data-export-fill={shapeProps.payload.color}
          data-export-stroke={shapeProps.payload.isSelected ? shapeProps.payload.color : 'transparent'}
          stroke={shapeProps.payload.isSelected ? themeSeriesColor(shapeProps.payload.color, theme === 'dark') : 'transparent'}
          strokeWidth={2}
          style={{ cursor: 'pointer' }}
          onMouseDown={(e) => {
            e.stopPropagation();
          }}
          onClick={(e) => {
            e.stopPropagation();
            handlePointClick(shapeProps.payload, shapeProps.cx, shapeProps.cy, e);
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
      />
    );
  }, [handlePointClick, highlightedPointId, isPanning, theme]);

  const datasheetShapeRenderer = useCallback((props: unknown) => {
    const shapeProps = props as { cx: number; cy: number };
    return (
      <circle
        data-scatter-hover-target="datasheet"
        cx={shapeProps.cx}
        cy={shapeProps.cy}
        r={3.5}
        fill="var(--surface, #f7f8fa)"
        stroke="var(--warning, #806b20)"
        strokeWidth={1.75}
      />
    );
  }, []);

  const handleWheel = useCallback((event: React.WheelEvent<HTMLDivElement>) => {
    if (navigation.isDragging) return;
    dismissHover(); setMenuState(null); setHighlightedPointId(null);
    onWheel(event);
  }, [navigation.isDragging, onWheel, dismissHover]);

  const exportReason = exportDisabledReason ||
    (scatterData.length + datasheetData.length === 0 ? 'No points to export. Adjust the axes or filters.' : null);
  const exportContext = JSON.stringify([xLabel, yLabel, mainZoom]);
  const exportFilename = `scatter_${yVariable}_vs_${xVariable}`;
  const exportCsv = async (_data: unknown, signal: AbortSignal) => {
    if (exportReason) throw new Error(exportReason);
    await downloadScatterCsv(scatterData, datasheetData, xVariable, yVariable, exportFilename, signal);
  };
  const exportPng = async (signal: AbortSignal) => {
    if (exportReason) throw new Error(exportReason);
    const svg = chartRef.current?.querySelector<SVGSVGElement>('svg.recharts-surface');
    if (!svg) throw new Error('Wait for the scatter plot to finish rendering.');
    await downloadScatterPng(svg, exportFilename, signal);
  };

  return (
    <div className={styles.root}>
      {navigationTarget && createPortal(<div className={styles.toolbar} role="group" aria-label="Scatter navigation">
        <div className={styles.modeGroup} role="group" aria-label="Drag behavior">
          <button type="button" className={styles.tool} aria-label="Pan scatter plot" aria-pressed={navigation.mode === 'pan'}
            title="Pan (P). Drag to move the view; click a point to select it." onClick={() => navigation.setMode('pan')}>
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 10V4a1.3 1.3 0 0 1 2.6 0v5-6a1.3 1.3 0 0 1 2.6 0v6-4a1.3 1.3 0 0 1 2.6 0v5-2a1.3 1.3 0 0 1 2.6 0v4c0 4-2.3 6-5.5 6-2.2 0-3.4-1-4.6-2.6L4 11.5c-1-1.3.5-2.8 1.6-1.8L7 11" /></svg>
          </button>
          <button type="button" className={styles.tool} aria-label="Box zoom scatter plot" aria-pressed={navigation.mode === 'zoom'}
            title="Box zoom (Z). Drag a rectangle; Shift+drag also zooms in Pan mode. Escape cancels." onClick={() => navigation.setMode('zoom')}>
            <svg viewBox="0 0 20 20" aria-hidden="true"><path strokeDasharray="2 2" d="M3 9V3h11v4M3 12v3h4"/><circle cx="11.5" cy="11.5" r="4"/><path d="m14.5 14.5 3 3"/></svg>
          </button>
        </div>
        <span className={styles.divider} />
        <button type="button" className={styles.tool} aria-label="Zoom in scatter plot" title="Zoom in (+)" onClick={() => onZoomBy(0.8)}>
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10h12M10 4v12"/></svg>
        </button>
        <button type="button" className={styles.tool} aria-label="Zoom out scatter plot" title="Zoom out (−)" onClick={() => onZoomBy(1.25)}>
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10h12"/></svg>
        </button>
        <button type="button" className={styles.tool} aria-label="Reset zoom" title="Fit all points (Home). Double-click the background also resets the view."
          onClick={onResetZoom} data-zoomed={!!mainZoom}>
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 3H3v4m10-4h4v4M3 13v4h4m10-4v4h-4"/><path d="M7 7h6v6H7z"/></svg>
        </button>
      </div>, navigationTarget)}
        <PlotExportControls label="scatter" contextKey={exportContext}
          actionsRef={exportActions} supportsMetadata={false} hideTrigger
          scope={`${xLabel} / ${yLabel}`}
          defaultData="original" originalReason={exportReason} filteredReason={null} pngReason={exportReason}
          csvLabel="Test-point means"
          csvDescription="One row per filtered test point, with X/Y means, min/max values and source identifiers. Includes the enabled datasheet line as separate reference rows. Zoom and overlap grouping do not remove CSV rows."
          pngDescription="Current scatter view with axes, point colors, overlap groups, enabled range bars and the datasheet line. Saves a clean PNG at twice the displayed size."
          onCsv={exportCsv} onPng={exportPng} />
        <PlotActionMenu label="test-point overview" targetRef={chartRef} exportActions={exportActions}
          contextKey={exportContext} onReset={onResetZoom} hideTrigger />
      <span id={helpId} className={styles.srOnly}>Click a point to select it. Scroll to zoom at the pointer. Drag to pan,
        or Shift-drag to box zoom. With the plot focused, use arrow keys to pan, plus and minus to zoom,
        Home to fit all points, P for pan, Z for box zoom, and Escape to cancel a drag.</span>
      <div ref={chartRef} tabIndex={0} aria-label="Plot canvas for test-point overview" aria-describedby={helpId}
        className={styles.canvas} data-drag-mode={navigation.activeMode} data-dragging={isPanning}
        onWheel={handleWheel} onPointerDownCapture={navigation.onPointerDownCapture}
        onClickCapture={navigation.onClickCapture} onKeyDown={navigation.onKeyDown}
        onMouseLeave={handleMouseLeave} onPointerMoveCapture={trackPointHover}
        onLostPointerCapture={navigation.cancelDrag}
        onDoubleClick={(event) => {
          if (!(event.target instanceof Element && event.target.closest('[data-scatter-hover-target]'))) onResetZoom();
        }}>
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={SCATTER_MARGIN}>
          <Customized component={<ScatterGrid xTicks={xTicks} yTicks={yTicks} />} />
          <XAxis
            dataKey="x"
            type="number"
            name={xLabel}
            tickLine={false}
            tickMargin={8}
            tick={({ x, y, payload }) => {
              const label = xTicks.format(payload.value);
              const halfWidth = label.length * 3.2;
              const anchor = x - halfWidth < PLOT_INSET.left ? 'start'
                : x + halfWidth > chartDimensions.width - SCATTER_MARGIN.right ? 'end' : 'middle';
              return <text className="recharts-cartesian-axis-tick-value" x={x} y={y} dy={11}
                fill="var(--muted, #626f83)" fontSize={11} textAnchor={anchor}>{label}</text>;
            }}
            label={{ value: xLabel, position: 'insideBottom', offset: 0, fill: 'var(--muted, #626f83)', fontSize: 12 }}
            stroke="var(--plot-axis, #b2bed0)"
            domain={mainZoom ? [mainZoom[0], mainZoom[1]] : [bounds.initialXMin, bounds.initialXMax]}
            allowDataOverflow
            height={X_AXIS_HEIGHT}
            ticks={xTicks.ticks}
            interval={0}
            tickFormatter={xTicks.format}
          />
          <YAxis
            dataKey="y"
            type="number"
            name={yLabel}
            tickLine={false}
            tickMargin={8}
            tick={{ fill: 'var(--muted, #626f83)', fontSize: 11 }}
            label={{
              value: yLabel,
              angle: -90,
              position: 'insideLeft',
              offset: 0,
              style: { textAnchor: 'middle' },
              fill: 'var(--muted, #626f83)',
              fontSize: 12,
            }}
            stroke="var(--plot-axis, #b2bed0)"
            domain={mainZoom ? [mainZoom[2], mainZoom[3]] : [bounds.initialYMin, bounds.initialYMax]}
            allowDataOverflow
            width={Y_AXIS_WIDTH}
            ticks={yTicks.ticks}
            interval={0}
            tickFormatter={yTicks.format}
          />
          <ZAxis range={[100, 100]} />
          <Tooltip
            cursor={isPanning || !hasPointHover || menuState ? false : { stroke: 'var(--plot-axis, #b2bed0)', strokeDasharray: '3 4' }}
            content={<CustomScatterTooltip chartRef={chartRef} />}
            isAnimationActive={false}
            active={isPanning || !hasPointHover || menuState ? false : undefined}
          />
          {(showHorizontalErrorBars || showVerticalErrorBars) && (
            <Customized
              component={
                <ScatterRangeBars
                  points={scatterData}
                  horizontal={showHorizontalErrorBars}
                  vertical={showVerticalErrorBars}
                />
              }
            />
          )}
          {datasheetData.length > 0 && (
            <Scatter
              className="datasheet-scatter-series"
              name="Datasheet"
              data={datasheetData}
              line={{
                stroke: 'var(--warning, #806b20)',
                strokeWidth: 2,
                strokeDasharray: '7 4',
                fill: 'none',
              }}
              lineType="joint"
              lineJointType="linear"
              shape={datasheetShapeRenderer}
              activeShape={datasheetShapeRenderer}
              isAnimationActive={false}
            />
          )}
          <Scatter
            data={renderData}
            shape={shapeRenderer}
            isAnimationActive={false}
          />
        </ScatterChart>
      </ResponsiveContainer>
      {navigation.box && <div className={styles.zoomBox} data-scatter-zoom-box="true" style={navigation.box} />}
      </div>

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
