import { usePlotViewport, ViewportProps } from '../../utils/plotViewport';
import { loadedAnalysis } from '../../utils/analysisMetadata';
import React, { useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchXY, isAbortError } from '../../services/api';
import { SelectedTestPoint, TimePlotConfig, XYData, XYExportRequest, XYExportSource } from '../../types';
import { noSelect } from '../../constants/styles';
import { ACCENT, AXIS_STYLE, safeRange } from '../../constants/uplotTheme';
import { xyPanZoomPlugin } from '../../utils/uplotPanZoom';
import { syncPlot, clearPlot } from '../../utils/uplotSync';
import { PlotStateOverlay, PlotEmptyState } from './PlotState';
import { SearchableSelect } from '../controls/SearchableSelect';
import { PlotExportControls, type PlotExportActions } from '../controls/PlotExportControls';
import { downloadPlotCsv } from '../../utils/plotExport';
import { capturePlotPng, downloadPlotPng } from '../../utils/plotPngExport';
import { usePlotExportRegistration, type RegisterPlotExport } from '../../utils/plotExportRegistry';
import { PlotActionMenu } from './PlotActionMenu';
import { PlotHeader } from './PlotHeader';
import styles from './TimePlot.module.css';
import type { PanelSource } from './SpectrumPlot';

interface XYPlotProps extends ViewportProps {
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
  registerExport?: RegisterPlotExport;
}

const editSelectStyle: React.CSSProperties = {
  flex: '1 1 0',
  minWidth: 0,
  maxWidth: 180,
};

interface XYTrace {
  source: Pick<XYExportSource, 'test' | 'tp_id' | 't0' | 't1'>;
  data: XYData;
  label: string;
  color: string;
  x: number[];
  y: number[];
  stride: number;
}

const EMPTY_TRACES: XYTrace[] = [];

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
  registerExport,
  viewport, viewportContext, onViewportChange,
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const exportActions = useRef<PlotExportActions>(null);
  const plotRef = useRef<uPlot | null>(null);
  const viewportControl = usePlotViewport(plotRef, {viewport, viewportContext, onViewportChange}, true);
  const structKeyRef = useRef('');
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [result, setResult] = useState<{ key: string; traces: XYTrace[]; error: string; partial: string }>({
    key: '', traces: [], error: '', partial: '',
  });
  const [loading, setLoading] = useState(false);
  const [retryVersion, setRetryVersion] = useState(0);
  const visibleTPs = selectedTPs.filter((point) => !hiddenTPs.has(point.id));
  const eligibleTPs = visibleTPs.filter((point) => {
    const columns = columnsByTest[point.test] ?? [];
    return columns.includes(xCol) && columns.includes(cfg.key);
  });
  // Include exact saved rows, schema eligibility and the loaded Full interval.
  const contextKey = JSON.stringify([source, xCol, cfg.key, source === 'full' ? [test, range]
    : eligibleTPs.map((point) => [point.id, point.tpId, point.tp.start_idx, point.tp.end_idx,
        point.tp.start_s, point.endS, point.name, point.color])]);
  const current = result.key === contextKey;
  const traces = current ? result.traces : EMPTY_TRACES;
  const error = current ? result.error : '';
  const partialMessage = current ? result.partial : '';
  const pending = loading || !current;

  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const empty = { key: contextKey, traces: [], error: '', partial: '' };
    if (!xCol || !cfg.key || (source === 'full' ? !test : !eligibleTPs.length)) {
      setResult(empty); setLoading(false); return;
    }
    let dead = false;
    const controller = new AbortController();
    setLoading(true);
    setResult((previous) => ({ ...previous, error: '', partial: '' }));
    const load = async () => {
      try {
        const requests = source === 'full' ? [{ test, tpId: undefined, label: test, color: ACCENT }]
          : eligibleTPs.map((point) => ({ test: point.test, tpId: point.tpId,
              label: `${point.name} · ${point.test} · TP ${point.tpId}`, color: point.color }));
        const results = await Promise.all(requests.map(async (item) => {
          try {
            const data = await fetchXY(item.test, xCol, cfg.key,
              source === 'full' ? range?.[0] ?? null : null,
              source === 'full' ? range?.[1] ?? null : null,
              source === 'full' ? 3000 : 1500, controller.signal, item.tpId);
            if (item.tpId !== undefined && data.tp_id !== item.tpId) {
              throw new Error('The backend did not confirm the requested test-point interval. Update the backend and retry.');
            }
            const pairs = data.series[cfg.key];
            if (!pairs || pairs.x.length !== pairs.y.length) throw new Error('The backend returned incomplete XY pairs.');
            return { trace: { label: item.label, color: item.color, x: pairs.x, y: pairs.y,
              stride: data.stride, data, source: { test: item.test, tp_id: item.tpId,
                ...(source === 'full' ? { t0: range?.[0] ?? null, t1: range?.[1] ?? null } : {}) },
            } as XYTrace };
          } catch (cause) {
            if (isAbortError(cause)) throw cause;
            return { failure: `${item.label}: ${cause instanceof Error ? cause.message : String(cause)}` };
          }
        }));
        if (dead) return;
        const loaded = results.flatMap((item) => item.trace ? [item.trace] : []);
        const failed = results.flatMap((item) => item.failure ? [item.failure] : []);
        setResult({ key: contextKey, traces: loaded,
          error: !loaded.length && failed.length ? failed.join('; ') : '',
          partial: loaded.length && failed.length
            ? `${failed.length} of ${requests.length} selected XY sources could not be loaded. ${failed[0]}` : '',
        });
      } catch (cause) {
        if (!dead && !isAbortError(cause)) setResult({ ...empty, error: cause instanceof Error ? cause.message : String(cause) });
      } finally {
        if (!dead) setLoading(false);
      }
    };
    const timer = window.setTimeout(load, 100);
    return () => { dead = true; window.clearTimeout(timer); controller.abort(); };
    // All request inputs and source identities are encoded by the key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextKey, retryVersion]);

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
      axes: [{ ...AXIS_STYLE, label: xCol }, { ...AXIS_STYLE, label: cfg.label }],
      legend: { show: isExpanded, live: true },
      cursor: { drag: { x: true, y: true } },
      plugins: [xyPanZoomPlugin(), viewportControl.plugin],
      series,
    });

    const data = [
      null,
      ...traces.map((tr) => [tr.x, tr.y]),
    ] as unknown as uPlot.AlignedData;

    const structKey = [
      JSON.stringify(series.map((s) => [s.label, s.stroke, s.fill])), box.w, box.h, isExpanded, xCol, cfg.label,
    ].join('|');
    viewportControl.sync(() => syncPlot({
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
    }));
  }, [viewportControl, viewportContext, traces, box, isExpanded, xCol, cfg.label]);

  const containerClass = `${styles.plotContainer} ${
    isExpanded ? styles.plotContainerExpanded : styles.plotContainerCollapsed
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
  if (!xCol) {
    emptyState = { title: 'Choose an X variable', detail: 'Use Edit plots to choose both variables for this XY plot.' };
  } else if (source === 'tp' && visibleTPs.length === 0) {
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

  const label = `${cfg.label} versus ${xCol}`;
  const exportScope = `Original stored ${cfg.label} (Y) and ${xCol} (X), including saved edits; no temporary filters. ${source === 'tp' ? 'Uses complete saved TP intervals.' : 'Uses the loaded Full-test time interval.'} Only visible legend sources are exported.`;
  const exportReason = pending ? 'Wait for XY data to finish loading.' : error || partialMessage ||
    (!traces.length ? 'Load XY data before exporting.' : null);
  const csvReason = exportReason || (traces.some(({ data }) => data.method_version !== 'kiha-xy-v2' ||
    data.i0 == null || data.i1 == null || !data.time_column) ? 'Reload with a current backend to export exact XY context.' :
    !hasData ? 'No finite XY pairs to export.' : null);
  const pngReason = exportReason || (!hasData ? 'No visible paired samples to capture.' : null);
  const visibleExportTraces = () => {
    if (exportReason) throw new Error(exportReason);
    const plot = plotRef.current;
    if (!plot) throw new Error('Wait for the XY canvas.');
    const visible = traces.filter((_trace, index) => plot.series[index + 1]?.show !== false);
    if (!visible.some((trace) => trace.x.length)) throw new Error('Show at least one nonempty XY source in the legend.');
    return { plot, visible };
  };
  const buildCsvRequest = (): XYExportRequest => {
    if (csvReason) throw new Error(csvReason);
    const { plot, visible } = visibleExportTraces();
    const crop = (axis: 'x' | 'y'): [number, number] | null => {
      const { min, max } = plot.scales[axis];
      if (min == null || max == null || !Number.isFinite(min) || !Number.isFinite(max) || min >= max) {
        throw new Error('Wait for valid XY axes before exporting.');
      }
      let low = Infinity, high = -Infinity;
      for (const trace of visible) for (const value of trace[axis]) {
        if (Number.isFinite(value)) { low = Math.min(low, value); high = Math.max(high, value); }
      }
      const automatic = safeRange(plot, low, high);
      // Stride can omit extrema: automatic viewport still means all native pairs.
      return min === automatic[0] && max === automatic[1] ? null : [min, max];
    };
    return { kind: 'xy', column: cfg.key, x_column: xCol, method_version: 'kiha-xy-v2',
      x_range: crop('x'), y_range: crop('y'),
      sources: visible.map(({ source: identity, data }) => ({ ...identity,
        expected_i0: data.i0!, expected_i1: data.i1!, expected_time_column: data.time_column!,
      })),
    };
  };
  const getPngSource = () => {
    if (pngReason) throw new Error(pngReason);
    const { plot, visible } = visibleExportTraces();
    return { plot, options: {
      provenance: { kind: 'xy', column: cfg.key, x_column: xCol, source_mode: source,
        sources: visible.map((trace) => ({ ...trace.source, loaded: loadedAnalysis(trace.data),
          pairs: { finite_count: trace.data.series[cfg.key].finite_count ?? null,
            missing_pair_count: trace.data.series[cfg.key].missing_pair_count ?? null,
            fallback_first_finite: trace.data.series[cfg.key].fallback_first_finite ?? null,
            displayed_count: trace.x.length, stride: trace.stride },
        })),
      },
      filename: `${cfg.key}_vs_${xCol}_${source}_xy.png`, title: `${cfg.label} (Y) vs ${xCol} (X)`,
      scope: [exportScope, ...visible.map(({ label: name, data }) => `${name}: rows [${data.i0 ?? '?'}, ${data.i1 ?? '?'}); sample times ${data.time_start_s ?? '?'} to ${data.time_end_s ?? '?'} s (${data.time_column ?? 'unknown time column'}); ${data.fs_hz ?? '?'} Hz; ${data.n_raw} source rows.`)],
      details: ['Only pairs with finite X and Y are drawn. No interpolation, sorting or resampling; both axes retain their stored variable units. CSV contains full-resolution pairs.',
        ...visible.map(({ label: name, data, stride, x }) => {
          const pairs = data.series[cfg.key];
          return `${name}: ${pairs.finite_count ?? '?'} finite pairs; ${pairs.missing_pair_count ?? '?'} omitted nonfinite pairs; ${x.length} displayed, stride 1:${stride}${pairs.fallback_first_finite ? '; first finite pair retained because stride missed all valid pairs' : ''}. Method: ${data.method_version ?? 'unavailable (legacy response)'}; source=stored, prefilter=none.`;
        }),
      ],
    } };
  };
  usePlotExportRegistration(registerExport, { label, scope: exportScope, defaultData: 'original',
    originalReason: csvReason, filteredReason: null, pngReason, buildCsvRequest,
    capturePng: () => { const { plot, options } = getPngSource(); return capturePlotPng(plot, options); },
  });
  const missingCount = traces.reduce((total, trace) => total + (trace.data.series[cfg.key].missing_pair_count ?? 0), 0);

  return (
    <div className={containerClass} style={{ ...noSelect }} role="group" aria-label={`${label} XY plot`}>
      <PlotHeader label={label} isExpanded={isExpanded} onToggleExpand={onToggleExpand}
        expandLabel={`${cfg.label} versus ${xCol}`}
        summary={traces.length > 0 && <span title={`${missingCount} source rows omitted because X or Y is nonfinite. Display stride may omit additional valid pairs; CSV uses full resolution.`} style={{ color: missingCount ? '#dcdcaa' : undefined }}>
          {source === 'tp' ? `${traces.length} TP${traces.length === 1 ? '' : 's'} · ` : ''}
          1:{maxStride}{missingCount ? ` · ${missingCount} missing` : ''}
        </span>}

        actions={<>
          <PlotActionMenu label={label} targetRef={chartRef} contextKey={contextKey}
            getPlot={() => plotRef.current} exportActions={exportActions} onReset={viewportControl.reset} />


          <PlotExportControls hideTrigger actionsRef={exportActions} label={label} contextKey={contextKey} scope={exportScope}
            defaultData="original" originalReason={csvReason} filteredReason={null} pngReason={pngReason}
            csvLabel="Original finite pairs"
            csvDescription="Full-resolution pairs in original sample order, with source, sample and time identifiers. Rows with nonfinite X or Y are omitted. Default views export every pair; zoom and pan crop both axes. No interpolation or temporary filters."
            onCsv={(_data, signal, includeMetadata) => downloadPlotCsv({ ...buildCsvRequest(), include_metadata: includeMetadata }, signal)}
            onPng={(signal, includeMetadata) => { const { plot, options } = getPngSource(); return downloadPlotPng(plot, { ...options, signal, includeMetadata }); }} />

        </>}>
        {isEditMode && allConfigs.length > 0 && (
          <div
            style={{
              flex: '1 1 100%',
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
      </PlotHeader>
      <div className={styles.plotViewport}>
        <div
          ref={chartRef} tabIndex={0} aria-label={`Plot canvas for ${label}`}
          className={styles.plotCanvas}
          title="Drag to zoom · Shift-drag or middle-drag to pan · Wheel to zoom · Double-click to reset"
        />
        <PlotStateOverlay
          loading={pending}
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
