import React, { useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchXY, isAbortError } from '../../services/api';
import { SelectedTestPoint, TimePlotConfig } from '../../types';
import { noSelect } from '../../constants/styles';
import { ACCENT, AXIS_STYLE, safeRange } from '../../constants/uplotTheme';
import { xyPanZoomPlugin } from '../../utils/uplotPanZoom';
import { syncPlot, clearPlot } from '../../utils/uplotSync';
import { PlotStateOverlay, PlotEmptyState } from './PlotState';
import { SearchableSelect } from '../controls/SearchableSelect';
import styles from './TimePlot.module.css';
import type { PanelSource } from './SpectrumPlot';

interface XYPlotProps {
  test: string;
  xCol: string;
  cfg: TimePlotConfig; // y column
  /** Data source: point clouds of the selected TPs, or of the active test. */
  source: PanelSource;
  selectedTPs: SelectedTestPoint[];
  hiddenTPs: Set<string>;
  columnsByTest: Record<string, string[]>;
  /** Time range for source='full' (null = whole test). */
  range: [number, number] | null;
  isExpanded: boolean;
  onToggleExpand: () => void;
  isEditMode?: boolean;
  allConfigs?: TimePlotConfig[];
  onConfigChange?: (newKey: string) => void;
  /** Change this plot's own X column (per-plot, not shared). */
  onXColChange?: (newX: string) => void;
}

const editSelectStyle: React.CSSProperties = {
  flex: '1 1 0',
  minWidth: 0,
  maxWidth: 180,
};

interface XYTrace {
  label: string;
  color: string;
  x: number[];
  y: number[];
  stride: number;
}

/** Variable-vs-variable scatter (this cell's own x and y columns, both
 *  pickable in Edit Plots mode): either the active test over its zoom range,
 *  or one point cloud per selected test point (each over its own time range,
 *  in TP colors). Rendered as points via uPlot mode 2 — trajectories are not
 *  x-sorted. */
export const XYPlot: React.FC<XYPlotProps> = ({
  test,
  xCol,
  cfg,
  source,
  selectedTPs,
  hiddenTPs,
  columnsByTest,
  range,
  isExpanded,
  onToggleExpand,
  isEditMode = false,
  allConfigs = [],
  onConfigChange,
  onXColChange,
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const structKeyRef = useRef('');
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [traces, setTraces] = useState<XYTrace[]>([]);
  const [loading, setLoading] = useState(
    Boolean(xCol && cfg.key && (source === 'full' ? test : selectedTPs.length))
  );
  const [error, setError] = useState('');
  const [partialMessage, setPartialMessage] = useState('');
  const [retryVersion, setRetryVersion] = useState(0);

  const visibleTPs = selectedTPs.filter((s) => !hiddenTPs.has(s.id));
  const tpFingerprint = visibleTPs
    .map((s) => `${s.id}:${s.tp.start_s}:${s.endS}`)
    .join('|');

  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setBox({ w: el.clientWidth, h: el.clientHeight });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!xCol || !cfg.key) {
      setLoading(false);
      setError('');
      return;
    }
    let dead = false;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setPartialMessage('');

    const load = async () => {
      try {
        if (source === 'full') {
          if (!test) return;
          const testCols = columnsByTest[test] ?? [];
          if (testCols.length && (!testCols.includes(xCol) || !testCols.includes(cfg.key))) {
            if (!dead) {
              setError(`${test} has no '${!testCols.includes(xCol) ? xCol : cfg.key}'`);
              setLoading(false);
            }
            return;
          }
          const d = await fetchXY(
            test, xCol, cfg.key, range?.[0] ?? null, range?.[1] ?? null,
            3000, controller.signal
          );
          if (dead) return;
          const s = d.series[cfg.key];
          setTraces(
            s && s.x.length
              ? [{ label: `${cfg.key} vs ${xCol}`, color: ACCENT, x: s.x, y: s.y, stride: d.stride }]
              : []
          );
        } else {
          const eligible = visibleTPs.filter((s) => {
            const cols = columnsByTest[s.test] ?? [];
            return cols.includes(cfg.key) && cols.includes(xCol);
          });
          let failed = 0;
          const results = await Promise.all(
            eligible.map(async (s) => {
              try {
                const d = await fetchXY(
                  s.test, xCol, cfg.key, s.tp.start_s, s.endS,
                  1500, controller.signal
                );
                const sr = d.series[cfg.key];
                if (!sr || sr.x.length === 0) return null;
                return {
                  label: `${s.name} · ${s.test}`,
                  color: s.color,
                  x: sr.x,
                  y: sr.y,
                  stride: d.stride,
                };
              } catch (e) {
                if (isAbortError(e)) throw e;
                console.error(`xy failed for ${s.id}/${cfg.key}:`, e);
                failed += 1;
                return null;
              }
            })
          );
          if (dead) return;
          const ok = results.filter((r): r is XYTrace => r !== null);
          if (failed > 0 && ok.length === 0) {
            throw new Error(
              `XY data was unavailable for ${failed} selected test point${failed === 1 ? '' : 's'}.`
            );
          }
          setTraces(ok);
          setPartialMessage(
            failed > 0
              ? `${failed} of ${eligible.length} selected test point${eligible.length === 1 ? '' : 's'} could not be loaded.`
              : ''
          );
        }
        if (!dead) setError('');
      } catch (e) {
        if (!dead && !isAbortError(e)) {
          setError(String(e instanceof Error ? e.message : e));
        }
      } finally {
        if (!dead) setLoading(false);
      }
    };

    const timer = window.setTimeout(load, 100);
    return () => {
      dead = true;
      window.clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [test, xCol, cfg.key, range, source, tpFingerprint, columnsByTest, retryVersion]);

  // Destroy only on unmount; syncPlot reuses/rebuilds in place (perf 2.4).
  useEffect(() => () => clearPlot(plotRef, structKeyRef), []);

  useEffect(() => {
    const el = chartRef.current;
    if (!el || traces.length === 0 || box.w < 40 || box.h < 40) {
      clearPlot(plotRef, structKeyRef);
      return;
    }

    const pointsPaths = uPlot.paths.points!();
    const series: uPlot.Series[] = [
      {},
      ...traces.map(
        (tr) =>
          ({
            label: tr.label,
            stroke: tr.color,
            fill: tr.color + '80',
            width: 1,
            paths: pointsPaths,
            facets: [
              { scale: 'x', auto: true },
              { scale: 'y', auto: true },
            ],
          }) as uPlot.Series
      ),
    ];

    const makeOpts = (): uPlot.Options => ({
      mode: 2,
      width: box.w,
      height: box.h,
      scales: {
        x: { time: false, range: safeRange as uPlot.Scale.Range },
        y: { range: safeRange as uPlot.Scale.Range },
      },
      axes: [{ ...AXIS_STYLE }, { ...AXIS_STYLE }],
      legend: { show: isExpanded, live: true },
      cursor: { drag: { x: true, y: true } },
      plugins: [xyPanZoomPlugin()],
      series,
    });

    const data = [
      null,
      ...traces.map((tr) => [tr.x, tr.y]),
    ] as unknown as uPlot.AlignedData;

    const structKey = [
      series.map((s) => s.label ?? '').join('~'), box.w, box.h, isExpanded,
    ].join('|');
    syncPlot({
      plotRef,
      structKeyRef,
      el,
      structKey,
      makeOpts,
      data,
      onCreate: (u) => {
        if (isExpanded) {
          const legend = u.root.querySelector('.u-legend') as HTMLElement | null;
          const legendH = legend?.offsetHeight ?? 0;
          if (legendH > 0) {
            u.setSize({ width: box.w, height: Math.max(60, box.h - legendH) });
          }
        }
      },
    });
  }, [traces, box, isExpanded]);

  const containerClass = `${styles.plotContainer} ${
    isExpanded ? styles.plotContainerExpanded : styles.plotContainerCollapsed
  }`;

  const buttonClass = `${styles.expandButton} ${
    isExpanded ? styles.expandButtonExpanded : styles.expandButtonCollapsed
  }`;

  const maxStride = traces.reduce((m, tr) => Math.max(m, tr.stride), 0);
  const eligibleTpCount = visibleTPs.filter((selected) => {
    const columns = columnsByTest[selected.test] ?? [];
    return columns.includes(cfg.key) && columns.includes(xCol);
  }).length;
  const hasData = traces.some((trace) =>
    trace.x.some(
      (x, index) =>
        Number.isFinite(x) &&
        trace.y[index] !== undefined &&
        Number.isFinite(trace.y[index])
    )
  );
  let emptyState: PlotEmptyState;
  if (source === 'tp' && visibleTPs.length === 0) {
    emptyState = {
      title: 'Select test points to compare',
      detail: 'Choose one or more points on the scatter plot to build an XY view.',
    };
  } else if (source === 'tp' && eligibleTpCount === 0) {
    emptyState = {
      title: 'No compatible test points',
      detail: `The visible points need both ${cfg.label} and ${xCol}.`,
    };
  } else {
    emptyState = {
      title: 'No paired samples',
      detail:
        source === 'full' && range
          ? 'Reset the time zoom or choose a wider range.'
          : `No finite ${cfg.label} and ${xCol} pairs were found.`,
    };
  }

  return (
    <div className={containerClass} style={{ ...noSelect }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 4,
          position: 'relative',
          gap: 6,
        }}
      >
        <div style={{ fontSize: 12, color: '#c0c0c0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {cfg.label} <span style={{ color: '#909090' }}>vs {xCol}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          {traces.length > 0 && (
            <span style={{ fontSize: 10, color: '#909090' }}>
              {source === 'tp' ? `${traces.length} TP${traces.length === 1 ? '' : 's'} · ` : ''}
              1:{maxStride}
            </span>
          )}
          <button
            type="button"
            onClick={onToggleExpand}
            className={buttonClass}
            aria-label={`${isExpanded ? 'Minimize' : 'Expand'} ${cfg.label} versus ${xCol}`}
            title={`${isExpanded ? 'Minimize' : 'Expand'} this plot`}
          >
            <span className={styles.expandButtonIcon}>{isExpanded ? '▪' : '▣'}</span>
          </button>
        </div>
        {isEditMode && allConfigs.length > 0 && (
          <div
            style={{
              position: 'absolute',
              left: 0,
              top: -2,
              right: 60,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              zIndex: 5,
            }}
          >
            <SearchableSelect
              value={cfg.key}
              onChange={(nextKey) => onConfigChange?.(nextKey)}
              options={allConfigs.map((config) => ({
                value: config.key,
                label: config.label,
                keywords: [config.key],
              }))}
              style={editSelectStyle}
              ariaLabel="Y variable"
              title="Y column"
              searchPlaceholder="Search Y variables..."
              optionNoun="variable"
              appearance="plot"
              size="compact"
            />
            <span style={{ fontSize: 10, color: '#909090', flexShrink: 0 }}>vs</span>
            <SearchableSelect
              value={xCol}
              onChange={(nextKey) => onXColChange?.(nextKey)}
              options={allConfigs.map((config) => ({
                value: config.key,
                label: config.label,
                keywords: [config.key],
              }))}
              style={editSelectStyle}
              ariaLabel="X variable"
              title="X column (this plot only)"
              searchPlaceholder="Search X variables..."
              optionNoun="variable"
              appearance="plot"
              size="compact"
            />
          </div>
        )}
      </div>
      <div className={styles.plotViewport}>
        <div
          ref={chartRef}
          className={styles.plotCanvas}
          title="Drag to zoom · Shift-drag or middle-drag to pan · Wheel to zoom · Double-click to reset"
        />
        <PlotStateOverlay
          loading={loading}
          hasData={hasData}
          error={error}
          emptyState={emptyState}
          onRetry={() => setRetryVersion((version) => version + 1)}
          loadingLabel="Loading paired samples"
          updatingLabel="Updating XY view"
          errorTitle="Could not load the XY view"
          partialMessage={partialMessage}
        />
      </div>
    </div>
  );
};
