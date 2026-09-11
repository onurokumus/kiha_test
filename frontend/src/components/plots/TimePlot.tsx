import { loadedAnalysis } from '../../utils/analysisMetadata';
import React, { useEffect, useRef, useState, useMemo } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchFiltered, isAbortError } from '../../services/api';
import { FilterSpec, SelectedTestPoint, StatsCache, TimePlotConfig } from '../../types';
import { noSelect } from '../../constants/styles';
import {
  AXIS_STYLE,
  TIME_AXIS_STYLE,
  TP_SYNC_KEY,
} from '../../constants/uplotTheme';
import { PanZoomControl, xPanZoomPlugin } from '../../utils/uplotPanZoom';
import { AxisRange, validAxisRange } from '../../utils/timePlotRanges';
import { visibleYAutoFitPlugin, visibleYRange } from '../../utils/visibleYRange';
import {
  clearPlot,
  facetedSeriesValue,
  sortedFacetedDataIdx,
  syncPlot,
} from '../../utils/uplotSync';
import { FILTER_LABELS, FilterUi } from '../../constants/filters';
import { FilterRow } from '../controls/FilterRow';
import { PlotFilterDialog } from './PlotFilterDialog';
import { PlotExportControls, type PlotExportActions } from '../controls/PlotExportControls';
import { downloadPlotCsv, plotCsvRange, plotFilterDetails } from '../../utils/plotExport';
import { capturePlotPng, downloadPlotPng } from '../../utils/plotPngExport';
import { usePlotExportRegistration, type RegisterPlotExport } from '../../utils/plotExportRegistry';
import type { PlotExportData, PlotExportRequest } from '../../types';
import { SearchableSelect } from '../controls/SearchableSelect';
import { PlotStateOverlay, PlotEmptyState } from './PlotState';
import { TimeYAxisControls, type TimeYAxisActions } from './TimeYAxisControls';
import { TimePlotStatistics } from './TimePlotStatistics';
import { PlotAnnotations, type PlotAnnotationActions } from './PlotAnnotations';
import type { AnnotationManager } from '../../hooks/useAnnotations';
import { usePlotAnnotations, annotationAvailability } from '../../hooks/usePlotAnnotations';
import { annotationsPlugin, annotationImageDetails, traceTimeBounds, type AnnotationSource } from '../../utils/plotAnnotations';
import { PlotActionMenu } from './PlotActionMenu';
import { PlotHeader } from './PlotHeader';
import styles from './TimePlot.module.css';

function setRange(u: uPlot, axis: string, range: AxisRange | null) {
  // uPlot 1.6.32 uses null bounds for auto-range internally and at runtime;
  // its public setScale declaration only lists numeric bounds.
  const bounds = { min: range?.[0] ?? null, max: range?.[1] ?? null };
  u.setScale(axis, bounds as { min: number; max: number });
}

interface TimePlotProps {
  annotations: AnnotationManager;
  annotationsVisible: boolean;
  onAnnotationsVisibleChange: (visible: boolean) => void;
  cfg: TimePlotConfig;
  selectedTPs: SelectedTestPoint[];
  hiddenTPs: Set<string>;
  /** Trace-fetch failures keyed by `${selection.id}|${column}`. */
  traceErrors?: Record<string, string>;
  onRetryTraces?: () => void;
  statsCache: StatsCache;
  statsErrors: Record<string, string>;
  onRetryStatistics: (keys: string[]) => void;
  /** Per-test plottable columns — filtered traces are only fetched for TPs
   *  whose OWN test has this plot's column (cross-test selections would
   *  otherwise 400 with 'unknown columns'; same rule as SpectrumPlot). */
  columnsByTest: Record<string, string[]>;
  isExpanded: boolean;
  onToggleExpand: () => void;
  zoomDomain: [number, number] | null;
  onZoomChange: (domain: [number, number]) => void;
  onZoomReset?: () => void;
  yRange: AxisRange | null;
  zoomResetVersion: number;
  onYRangeChange: (range: AxisRange | null) => void;
  /** THIS plot's DSP filter, computed over each TP's own range in its own
   *  test. Per-plot only (opened from the plot actions menu). */
  filterSpec?: FilterSpec | null;
  filterUi?: FilterUi;
  onFilterUiChange?: (patch: Partial<FilterUi>) => void;
  showOriginal?: boolean;
  onShowOriginalChange?: (show: boolean) => void;
  fs?: number | null;
  isEditMode?: boolean;
  allConfigs?: TimePlotConfig[];
  onConfigChange?: (newKey: string) => void;
  registerExport?: RegisterPlotExport;
}

interface OverlayTrace {
  id: string;
  label: string;
  color: string;
  t: number[];
  y: (number | null)[];
  kind: 'original' | 'filtered';
}

/** Filtered result for one TP: 1 segment (line) or 2 (envelope min/max). */
interface FilteredTpTrace {
  analysis?: Record<string, unknown>;
  id: string;
  i0: number;
  i1: number;
  color: string;
  name: string;
  segs: { t: number[]; y: (number | null)[] }[];
  warning: boolean;
  gapWarning: boolean;
  segmentWarning: boolean;
  replacementCount?: number;
  spikeEventCount?: number;
}

interface FilterResultState {
  requestKey: string;
  traces: FilteredTpTrace[];
  error: string;
  partialMessage: string;
}

const EMPTY_FILTER_RESULT: FilterResultState = {
  requestKey: '',
  traces: [],
  error: '',
  partialMessage: '',
};

/** TP-overlay time plot: one line per selected test point, relative time
 *  from TP start. uPlot mode 2 (facets) — each series keeps its own time
 *  array, so TPs of different lengths overlay without resampling. */
export const TimePlot: React.FC<TimePlotProps> = ({
  annotations, annotationsVisible, onAnnotationsVisibleChange,
  cfg,
  selectedTPs,
  hiddenTPs,
  traceErrors = {},
  onRetryTraces,
  statsCache,
  statsErrors,
  onRetryStatistics,
  columnsByTest,
  isExpanded,
  onToggleExpand,
  zoomDomain,
  onZoomChange,
  onZoomReset,
  yRange,
  zoomResetVersion,
  onYRangeChange,
  filterSpec = null,
  filterUi,
  onFilterUiChange,
  showOriginal = false,
  onShowOriginalChange,
  fs = null,
  isEditMode = false,
  allConfigs = [],
  onConfigChange,
  registerExport,
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const yAxisActions = useRef<TimeYAxisActions>(null);
  const exportActions = useRef<PlotExportActions>(null);
  const annotationActions = useRef<PlotAnnotationActions>(null);
  const plotRef = useRef<uPlot | null>(null);
  const structKeyRef = useRef('');
  // Latest zoom-commit callback — a reused uPlot keeps its build-time closures.
  const onZoomChangeRef = useRef(onZoomChange);
  onZoomChangeRef.current = onZoomChange;
  const onYRangeChangeRef = useRef(onYRangeChange);
  onYRangeChangeRef.current = onYRangeChange;
  const zoomDomainRef = useRef(zoomDomain);
  zoomDomainRef.current = zoomDomain;
  const yRangeRef = useRef(yRange);
  yRangeRef.current = yRange;
  const appliedXRef = useRef(zoomDomain);
  const gestureControlRef = useRef<PanZoomControl | null>(null);
  const appliedResetRef = useRef(zoomResetVersion);
  const appliedTracesRef = useRef<OverlayTrace[]>([]);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [filterResult, setFilterResult] =
    useState<FilterResultState>(EMPTY_FILTER_RESULT);
  const [fbusy, setFbusy] = useState(false);
  const [filterRetryVersion, setFilterRetryVersion] = useState(0);
  // per-cell filter row visibility, toggled by the ≈ header button
  const [showFilter, setShowFilter] = useState(false);

  const visibleTPs = selectedTPs.filter((s) => !hiddenTPs.has(s.id));
  const tpFingerprint = visibleTPs
    .map((s) => `${s.id}:${s.tp.start_s}:${s.endS}:${s.tp.start_idx}:${s.tp.end_idx}`)
    .join('|');
  const eligibleTPs = visibleTPs.filter((selected) =>
    (columnsByTest[selected.test] ?? []).includes(cfg.key)
  );
  const eligibleFingerprint = eligibleTPs.map((selected) => selected.id).join('|');
  const filterRequestKey =
    filterSpec && eligibleTPs.length > 0
      ? JSON.stringify([
          cfg.key,
          tpFingerprint,
          eligibleFingerprint,
          filterSpec,
          filterRetryVersion,
        ])
      : '';
  const currentFilterResult =
    filterResult.requestKey === filterRequestKey
      ? filterResult
      : EMPTY_FILTER_RESULT;
  const fovers = currentFilterResult.traces;
  const ferror = currentFilterResult.error;
  const filterPartialMessage = currentFilterResult.partialMessage;
  const filterPending =
    Boolean(filterSpec && eligibleTPs.length > 0) &&
    (fbusy || filterResult.requestKey !== filterRequestKey);

  const traces: OverlayTrace[] = useMemo(() => {
    const out: OverlayTrace[] = [];
    selectedTPs.forEach((s) => {
      if (hiddenTPs.has(s.id)) return;
      const trace = s.traces[cfg.key];
      if (!trace) return;
      const t: number[] = [];
      const y: (number | null)[] = [];
      trace.t.forEach((tv, i) => {
        if (tv === null) return; // time itself should never be NaN; skip if so
        t.push(tv);
        y.push(trace.y[i]);
      });
      if (t.length > 0)
        out.push({ id: s.id, label: `${s.name} · ${s.test} · original`, color: s.color, t, y, kind: 'original' });
    });
    return out;
  }, [selectedTPs, hiddenTPs, cfg.key]);

  // Track the chart area's box; uPlot needs explicit pixel dimensions.
  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setBox({ w: el.clientWidth, h: el.clientHeight });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Filtered trace per visible TP: exact saved rows in its own test,
  // returned relative to the actual first sample. Fetched once per
  // (spec, TP set, column) — zoom stays client-side like the raw traces.
  useEffect(() => {
    // Only TPs whose own test has this column can be filtered — the raw
    // traces are already restricted the same way by App's trace fetcher.
    const eligible = visibleTPs.filter((s) =>
      (columnsByTest[s.test] ?? []).includes(cfg.key)
    );
    if (!filterSpec || eligible.length === 0) {
      setFilterResult(EMPTY_FILTER_RESULT);
      setFbusy(false);
      return;
    }
    let dead = false;
    const controller = new AbortController();
    const px = chartRef.current?.clientWidth || 800;
    const failureMessages = new Set<string>();
    setFbusy(true);
    const timer = window.setTimeout(() => {
      Promise.all(
        eligible.map(async (s): Promise<FilteredTpTrace | null> => {
          try {
            const w = await fetchFiltered(
              s.test, [cfg.key], filterSpec, null, null, px,
              controller.signal, 'auto', s.tpId
            );
            // Older backends ignore unknown query parameters. Never present a
            // full/inclusive window as a correctly aligned filtered TP.
            if (w.tp_id !== s.tpId || !Number.isFinite(w.time_origin_s) ||
                !w.relative_t || w.relative_t.length !== w.t.length) {
              throw new Error('Exact test-point filter alignment is unavailable. Update the backend and retry.');
            }
            const sr = w.series[cfg.key];
            if (!sr) throw new Error('The filter response is missing this signal.');
            const ys: (number | null)[][] =
              w.mode === 'envelope'
                ? [(sr as { max: (number | null)[] }).max, (sr as { min: (number | null)[] }).min]
                : [sr as (number | null)[]];
            if (ys.some((values) => !Array.isArray(values) || values.length !== w.t.length)) {
              throw new Error('The filtered time axis does not match its samples.');
            }
            const segs = ys.map((yArr) => {
              const t: number[] = [];
              const y: (number | null)[] = [];
              w.relative_t!.forEach((tv, i) => {
                if (tv === null) return;
                t.push(tv);
                y.push(yArr[i]);
              });
              return { t, y };
            });
            return {
              id: s.id,
              analysis: loadedAnalysis(w),
              i0: w.i0,
              i1: w.i1,
              color: s.color,
              name: `${s.name} · ${s.test}`,
              segs,
              warning: !!w.boundary_warning,
              gapWarning: (w.time_gap_count ?? 0) > 0,
              segmentWarning: !!w.gap_segment_warning,
              replacementCount: w.replacement_counts?.[cfg.key],
              spikeEventCount: w.spike_event_counts?.[cfg.key],
            };
          } catch (e) {
            if (isAbortError(e)) throw e;
            failureMessages.add(e instanceof Error ? e.message : String(e));
            console.error(`filter failed for ${s.id}/${cfg.key}:`, e);
            return null;
          }
        })
      )
        .then((res) => {
          if (dead) return;
          const ok = res.filter((r): r is FilteredTpTrace => r !== null);
          const failed = eligible.length - ok.length;
          if (failed === eligible.length) {
            setFilterResult({
              requestKey: filterRequestKey,
              traces: [],
              error: `The filter could not be applied to ${failed} test point${
                failed === 1 ? '' : 's'
              }. ${[...failureMessages].join(' ')}`,
              partialMessage: '',
            });
          } else {
            setFilterResult({
              requestKey: filterRequestKey,
              traces: ok,
              error: '',
              partialMessage:
                failed > 0
                  ? `${failed} of ${eligible.length} filtered test-point trace${
                      eligible.length === 1 ? '' : 's'
                    } could not be updated. ${[...failureMessages].join(' ')}`
                  : '',
            });
          }
        })
        .catch((e) => {
          if (!dead && !isAbortError(e)) {
            setFilterResult({
              requestKey: filterRequestKey,
              traces: [],
              error: String(e instanceof Error ? e.message : e),
              partialMessage: '',
            });
          }
        })
        .finally(() => !dead && setFbusy(false));
    }, 300);
    return () => {
      dead = true;
      window.clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    filterSpec,
    tpFingerprint,
    cfg.key,
    columnsByTest,
    filterRetryVersion,
    filterRequestKey,
  ]);

  // Destroy only on unmount; syncPlot reuses/rebuilds in place (perf 2.4).
  useEffect(() => () => clearPlot(plotRef, structKeyRef), []);

  const filteredTraces: OverlayTrace[] = useMemo(
    () =>
      fovers.flatMap((filtered) =>
        filtered.segs.map((segment, index) => ({
          id: filtered.id,
          label: `${filtered.name} · filtered${
            filtered.segs.length > 1 ? (index === 0 ? ' max' : ' min') : ''
          }`,
          color: filtered.color,
          t: segment.t,
          y: segment.y,
          kind: 'filtered' as const,
        }))
      ),
    [fovers]
  );
  const showingFiltered =
    Boolean(filterSpec) && !filterPending && !ferror && fovers.length > 0;
  const showingRawFallback = Boolean(filterSpec && ferror);
  const showingOverlay = showingFiltered && showOriginal && traces.length > 0;
  const plottedTraces = useMemo(
    () =>
      showingFiltered
        ? showOriginal ? [...traces, ...filteredTraces] : filteredTraces
        : !filterSpec || showingRawFallback || showOriginal
          ? traces
          : [],
    [
      showingFiltered,
      filteredTraces,
      filterSpec,
      showingRawFallback,
      showOriginal,
      traces,
    ]
  );

  const annotationSources = useMemo<AnnotationSource[]>(() => eligibleTPs.flatMap((point) => {
    const bounds = traceTimeBounds(point.traces[cfg.key]);
    const seriesIndices = plottedTraces.flatMap((trace, index) => trace.id === point.id ? [index + 1] : []);
    return bounds && seriesIndices.length ? [{ key: point.id, test: point.test,
      label: `${point.test} · TP ${point.tpId} · ${point.name}`, origin: bounds[0], bounds,
      color: point.color, seriesIndices }] : [];
  }), [eligibleTPs, cfg.key, plottedTraces]);
  const annotationItems = usePlotAnnotations(annotationSources, annotations, annotationsVisible, plotRef);

  useEffect(() => {
    const el = chartRef.current;
    if (!el || plottedTraces.length === 0 || box.w < 40 || box.h < 40) {
      clearPlot(plotRef, structKeyRef);
      return;
    }
    const resetting = appliedResetRef.current !== zoomResetVersion;
    if (resetting) gestureControlRef.current?.cancel();
    appliedResetRef.current = zoomResetVersion;

    const series: uPlot.Series[] = [
      {},
      ...plottedTraces.map(
        (trace) =>
          ({
            label: trace.label,
            stroke: showingOverlay && trace.kind === 'original' ? `${trace.color}99` : trace.color,
            width: trace.kind === 'filtered' ? 2 : showingOverlay ? 1 : 1.5,
            dash: showingOverlay && trace.kind === 'original' ? [5, 4] : [],
            spanGaps: false,
            value: facetedSeriesValue,
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
        x: {
          time: false,
          // Read current state even after a rebuild while zoomed. A fixed
          // build-time range would prevent a later reset from fitting data.
          range: (_u, min, max) => zoomDomainRef.current ?? [min, max],
        },
        y: {
          range: (u, min, max) => yRangeRef.current ?? visibleYRange(u, min, max),
        },
      },
      axes: [{ ...TIME_AXIS_STYLE }, { ...AXIS_STYLE, scale: 'y' }],
      legend: { show: isExpanded, live: true },
      cursor: {
        dataIdx: sortedFacetedDataIdx,
        drag: { x: true, y: false },
        sync: { key: TP_SYNC_KEY, scales: ['x', null] },
      },
      plugins: [visibleYAutoFitPlugin(() => yRangeRef.current !== null), annotationsPlugin((plot) => annotationItems.current(plot)), xPanZoomPlugin(
        (r) => onZoomChangeRef.current(r),
        (r) => {
          yRangeRef.current = r;
          onYRangeChangeRef.current(r);
        },
        gestureControlRef
      )],
      series,
      hooks: {
        setSelect: [
          (u) => {
            if (u.select.width > 10) {
              const t0 = u.posToVal(u.select.left, 'x');
              const t1 = u.posToVal(u.select.left + u.select.width, 'x');
              u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
              onZoomChangeRef.current([t0, t1]);
            }
          },
        ],
      },
    });

    const data = [
      null,
      ...plottedTraces.map((trace) => [trace.t, trace.y]),
    ] as unknown as uPlot.AlignedData;

    // Axis ranges stay out of the struct key. Reuse the plot, swap data
    // without transient auto-ranging, and update the controlled scales.
    const structKey = [
      JSON.stringify(series.map((s) => [s.label, s.stroke, s.width, s.dash])), box.w, box.h, isExpanded,
    ].join('|');
    syncPlot({
      plotRef,
      structKeyRef,
      el,
      structKey,
      makeOpts,
      data,
      resetScales: false,
      onUpdate: (u) => {
        u.batch(() => {
          // A Y-only update must not rewind the local X wheel feedback while
          // its shared range is still waiting for the trailing commit.
          if (resetting || zoomDomain !== appliedXRef.current || (!zoomDomain && plottedTraces !== appliedTracesRef.current)) {
            setRange(u, 'x', zoomDomain);
          }
          setRange(u, 'y', yRange);
        });
      },
      onCreate: (u) => {
        // The legend renders inside the container and eats chart height.
        if (isExpanded) {
          const legend = u.root.querySelector('.u-legend') as HTMLElement | null;
          const legendH = legend?.offsetHeight ?? 0;
          if (legendH > 0) {
            u.setSize({ width: box.w, height: Math.max(60, box.h - legendH) });
          }
        }
      },
    });
    appliedXRef.current = zoomDomain;
    appliedTracesRef.current = plottedTraces;
  }, [plottedTraces, showingOverlay, zoomDomain, yRange, box, isExpanded, zoomResetVersion, annotationItems]);

  const containerClass = `${styles.plotContainer} ${
    isExpanded ? styles.plotContainerExpanded : styles.plotContainerCollapsed
  }`;

  const relevantTraceErrors = eligibleTPs
    .map((selected) => traceErrors[`${selected.id}|${cfg.key}`])
    .filter((message): message is string => Boolean(message));
  const failedTraceCount = relevantTraceErrors.length;
  const rawHasData = traces.some((trace) =>
    trace.t.some(
      (time, index) =>
        Number.isFinite(time) &&
        trace.y[index] !== null &&
        Number.isFinite(trace.y[index])
    )
  );
  const hasData = plottedTraces.some((trace) =>
    trace.t.some(
      (time, index) =>
        Number.isFinite(time) &&
        trace.y[index] !== null &&
        Number.isFinite(trace.y[index])
    )
  );
  const loadingTraces =
    eligibleTPs.filter(
      (selected) =>
        !(cfg.key in selected.traces) &&
        !traceErrors[`${selected.id}|${cfg.key}`]
    ).length > 0;
  const traceError =
    failedTraceCount > 0 && !rawHasData
      ? Array.from(new Set(relevantTraceErrors)).join(' ')
      : '';
  const visibleError = ferror || (!filterSpec ? traceError : '');
  const rawPartialMessage =
    failedTraceCount > 0 && rawHasData
      ? `${failedTraceCount} of ${eligibleTPs.length} test-point trace${
          eligibleTPs.length === 1 ? '' : 's'
        } could not be loaded.`
      : '';
  const partialMessage =
    [
      filterSpec ? filterPartialMessage : rawPartialMessage,
      filterSpec && (ferror || showOriginal) ? rawPartialMessage || traceError : '',
    ]
      .filter(Boolean)
      .join(' ');
  const retry = () => {
    if (filterSpec && (ferror || filterPartialMessage)) setFilterRetryVersion((version) => version + 1);
    if (!filterSpec || ((showOriginal || ferror) && failedTraceCount > 0)) onRetryTraces?.();
  };
  const replacementSampleCount = fovers.reduce(
    (total, filtered) => total + (filtered.replacementCount ?? 0),
    0
  );
  const spikeEventCount = fovers.reduce(
    (total, filtered) => total + (filtered.spikeEventCount ?? 0),
    0
  );
  const hasDespikeCounts =
    filterSpec?.kind === 'despike' &&
    fovers.length > 0 &&
    fovers.every(
      (filtered) =>
        filtered.replacementCount !== undefined &&
        filtered.spikeEventCount !== undefined
    );
  const filterSummary = hasDespikeCounts
    ? spikeEventCount === 0
      ? 'Despike · no events found'
      : `Despike · ${spikeEventCount} event${
          spikeEventCount === 1 ? '' : 's'
        }, ${replacementSampleCount} sample${
          replacementSampleCount === 1 ? '' : 's'
        } repaired`
    : filterSpec
      ? `${FILTER_LABELS[filterSpec.kind]} applied`
      : 'Filtered signal';
  const filterNeedsAttention = Boolean(filterUi?.kind && !filterSpec);
  const originalExportReason = !eligibleTPs.length ? 'Select test points containing this variable.'
    : loadingTraces ? 'Wait for all original traces to load.'
      : failedTraceCount ? 'Retry missing original traces before exporting original data.' : null;
  const filteredExportReason = !filterSpec ? 'Apply a valid filter to export filtered data.'
    : filterPending ? 'Wait for the filter to finish.'
      : ferror || filterPartialMessage ? 'Retry the filter for every visible test point before exporting filtered data.'
        : !showingFiltered ? 'No filtered result is available.' : null;
  const pngExportReason = annotationAvailability(annotationSources, annotations, annotationsVisible) || (!hasData ? 'Wait for a plot with samples.'
    : filterSpec && filteredExportReason ? filteredExportReason
      : (!filterSpec || showOriginal) ? originalExportReason : null);
  const exportTPs = () => {
    const shown = new Set(plottedTraces.filter((_trace, index) =>
      plotRef.current?.series[index + 1]?.show !== false).map((trace) => trace.id));
    const points = eligibleTPs.filter((point) => shown.has(point.id));
    if (!points.length) throw new Error('Show at least one test-point trace before exporting.');
    return points;
  };
  let emptyState: PlotEmptyState;
  if (visibleTPs.length === 0) {
    emptyState = {
      title: 'Select test points to compare',
      detail: 'Choose one or more points on the scatter plot to overlay their time traces.',
    };
  } else if (eligibleTPs.length === 0) {
    emptyState = {
      title: `${cfg.label} is not available`,
      detail: 'None of the visible test points contain this signal.',
    };
  } else if (filterSpec && !ferror) {
    emptyState = {
      title: 'No filtered samples',
      detail: `The ${cfg.label} filter result contains no plottable values.`,
    };
  } else {
    emptyState = {
      title: 'No time-series samples',
      detail: `The selected ${cfg.label} traces contain no plottable values.`,
    };
  }

  const buildCsvRequest = (data: PlotExportData): PlotExportRequest => {
    const reason = data === 'original' ? originalExportReason
      : data === 'filtered' ? filteredExportReason : originalExportReason || filteredExportReason;
    if (reason) throw new Error(reason);
    const sources = exportTPs().map((point) => {
      const result = fovers.find((item) => item.id === point.id);
      const summary = statsCache[point.test]?.[cfg.key]?.[point.tpId]?.summary;
      const i0 = data !== 'original' && result ? result.i0 : summary?.i0 ?? point.tp.start_idx;
      const i1 = data !== 'original' && result ? result.i1 : summary?.i1 ?? point.tp.end_idx;
      return { test: point.test, tp_id: point.tpId, px: 800, display: 'auto' as const,
        ...(i0 != null && i1 != null ? { expected_i0: i0, expected_i1: i1 } : {}) };
    });
    return { column: cfg.key, data, sources,
      filter: data === 'original' ? null : filterSpec, x_range: plotCsvRange(plotRef.current, zoomDomain !== null, true) };
  };
  const getPngSource = () => {
    if (pngExportReason) throw new Error(pngExportReason);
    const plot = plotRef.current;
    if (!plot) throw new Error('Wait for the plot to render.');
    const points = exportTPs();
    const visibleTraces = plottedTraces.filter((_trace, index) => plot.series[index + 1]?.show !== false);
    const hasOriginal = visibleTraces.some((trace) => trace.kind === 'original');
    const hasFiltered = visibleTraces.some((trace) => trace.kind === 'filtered');
    const filteredIds = new Set(visibleTraces.filter((trace) => trace.kind === 'filtered').map((trace) => trace.id));
    const visibleResults = fovers.filter((result) => filteredIds.has(result.id));
    const repairCounts = filterSpec?.kind === 'despike' && visibleResults.length
      ? [`Visible filtered TPs (complete TP counts): ${visibleResults.reduce((n, r) => n + (r.spikeEventCount ?? 0), 0)} events; ${visibleResults.reduce((n, r) => n + (r.replacementCount ?? 0), 0)} samples repaired.`] : [];
    return { plot, options: {
      provenance: { kind: 'time', column: cfg.key, source_mode: 'tp',
        annotations: { visible: annotationsVisible, items: annotationItems.current(plot) },
        sources: points.map((point) => ({ test: point.test, test_point_id: String(point.tpId),
          traces: visibleTraces.filter((trace) => trace.id === point.id).map((trace) => ({
            kind: trace.kind, label: trace.label,
            loaded: trace.kind === 'filtered' ? fovers.find((item) => item.id === point.id)?.analysis
              ?? { status: 'unavailable_legacy_response' }
              : loadedAnalysis(point.traces[cfg.key]),
          })),
        })),
      },
      filename: `${new Set(points.map((p) => p.test)).size === 1 ? points[0].test : 'multiple-tests'}_${cfg.key}_test-points_${hasOriginal && hasFiltered ? 'both' : hasFiltered ? 'filtered' : 'original'}.png`,
      title: `${cfg.label} · Test points`,
      scope: ['Time (s) relative to each TP first stored sample; current X/Y view.',
        ...points.map((p) => `${p.test} · TP ${p.tpId} · ${p.name}`)],
      details: [...plotFilterDetails(hasFiltered ? filterSpec : null),
        ...annotationImageDetails(annotationItems.current(plot), annotationsVisible),
        'Plot representation may be reduced. CSV exports full-resolution samples.',
        ...(hasFiltered ? ['Filters process complete saved TPs before the displayed zoom.', ...repairCounts] : []),
        ...(visibleResults.some((f) => f.warning) ? ['Dataset edge: filter transients possible.'] : []),
        ...(visibleResults.some((f) => f.gapWarning || f.segmentWarning) ? ['Known gaps are processing boundaries; short segments may have no filtered trace.'] : [])],
    } };
  };
  const exportScope = `Test points with a visible trace in this plot. ${zoomDomain ? 'CSV uses the zoomed X range.' : 'CSV includes complete saved TPs.'} Time is relative to each TP's first stored sample. Filtering always uses each complete TP before any clipping.`;
  const defaultExportData = showingFiltered ? showOriginal ? 'both' : 'filtered' : 'original';
  usePlotExportRegistration(registerExport, {
    label: cfg.label, scope: exportScope, defaultData: defaultExportData,
    originalReason: originalExportReason, filteredReason: filteredExportReason, pngReason: pngExportReason,
    buildCsvRequest, capturePng: () => {
      const { plot, options } = getPngSource();
      return capturePlotPng(plot, options);
    },
  });

  return (
    <div className={containerClass} style={{ ...noSelect }} role="group" aria-label={`${cfg.label} time plot`}
      data-filter-display={plottedTraces.length > 0 ? showingOverlay ? 'overlay' : showingFiltered ? 'filtered' : 'original' : undefined}>
      <PlotHeader label={cfg.label} isExpanded={isExpanded} onToggleExpand={onToggleExpand}

        summary={<TimePlotStatistics column={cfg.key} selectedTPs={selectedTPs} hiddenTPs={hiddenTPs}
          columnsByTest={columnsByTest} statsCache={statsCache} errors={statsErrors}
          onRetry={onRetryStatistics} filterActive={Boolean(filterSpec)} />}
        status={<>
          {yRange && <span title="Fixed Y bounds; choose Auto-fit Y in the plot menu">Y fixed</span>}
          {showingFiltered && <span className={styles.filteredStatus} title={showingOverlay ? 'Original: thin dashed · filtered: solid' : 'Filtered signal'}>
            {showingOverlay ? 'original + filtered' : 'filtered'}
          </span>}
          {(ferror || filterNeedsAttention) && <span title={ferror || 'Open Filter settings in the plot menu'} style={{ color: '#e7c16f' }}>Filter needs attention</span>}
        </>}
        actions={<>
          <PlotActionMenu label={cfg.label} targetRef={chartRef} contextKey={JSON.stringify([cfg.key, tpFingerprint, filterSpec, showOriginal, zoomDomain])}
            getPlot={() => plotRef.current} exportActions={exportActions} annotationActions={annotationActions}
            canAnnotate={!!annotationSources.length} resetLabel="Reset linked time / TP Y axes" onReset={() => onZoomReset?.()}
            yAxis={{ automatic: yRange === null, enabled: hasData, onEdit: () => yAxisActions.current?.open() }}
            onResetY={() => onYRangeChange(null)}
            onEditFilter={filterUi && onFilterUiChange ? () => setShowFilter(true) : undefined}
            filterStatus={ferror || (filterNeedsAttention ? "Filter settings need attention" : undefined)}
            overlay={onShowOriginalChange ? { checked: showOriginal, enabled: !!filterSpec, onChange: onShowOriginalChange } : undefined}
            notes={{ visible: annotationsVisible, onChange: onAnnotationsVisibleChange }} />
          <TimeYAxisControls hideTrigger actionsRef={yAxisActions} label={cfg.label} range={yRange} disabled={!hasData}
            getRange={() => {
              const scale = plotRef.current?.scales.y;
              const range = [scale?.min, scale?.max];
              return validAxisRange(range) ? range : null;
            }}
            onChange={onYRangeChange} />
          <PlotAnnotations hideTrigger actionsRef={annotationActions} label={cfg.label} sources={annotationSources} manager={annotations}
            visible={annotationsVisible} onVisibleChange={onAnnotationsVisibleChange} getPlot={() => plotRef.current} />
          <PlotExportControls hideTrigger actionsRef={exportActions} label={cfg.label}
            contextKey={JSON.stringify([cfg.key, tpFingerprint, filterSpec, showOriginal])}
            scope={exportScope}
            defaultData={defaultExportData}
            originalReason={originalExportReason} filteredReason={filteredExportReason} pngReason={pngExportReason}
            onCsv={(data, signal, includeMetadata) => downloadPlotCsv({ ...buildCsvRequest(data), include_metadata: includeMetadata }, signal)}
            onPng={(signal, includeMetadata) => {
              const { plot, options } = getPngSource();
              return downloadPlotPng(plot, { ...options, signal, includeMetadata });
            }} />

        </>}>
        {isEditMode && allConfigs.length > 0 && (
          <SearchableSelect
            value={cfg.key}
            onChange={(nextKey) => onConfigChange?.(nextKey)}
            options={allConfigs.map((config) => ({
              value: config.key,
              label: config.label,
              keywords: [config.key],
            }))}
            ariaLabel="Plot variable"
            title="Change the variable shown in this plot"
            searchPlaceholder="Search plot variables..."
            optionNoun="variable"
            appearance="plot"
            size="compact"
            style={{
              position: 'absolute',
              left: 0,
              top: -2,
              width: 180,
              maxWidth: 'calc(100% - 76px)',
              zIndex: 5,
            }}
          />
        )}
      </PlotHeader>
      {showFilter && filterUi && onFilterUiChange && (
        <PlotFilterDialog label={cfg.label} onClose={() => setShowFilter(false)}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 4 }}>
          {filterUi && onFilterUiChange && (
            <FilterRow
              ui={filterUi}
              onChange={onFilterUiChange}
              fs={fs}
              title="Filter this plot only"
            />
          )}
          {showingFiltered && <span className="badge">{filterSummary}</span>}
          {fovers.some((f) => f.warning) && (
            <span style={{ fontSize: 10, color: '#dcdcaa' }}>
              ⚠ range touches data edge — filter transients possible
            </span>
          )}
          {fovers.some((f) => f.gapWarning) && (
            <span style={{ fontSize: 10, color: '#dcdcaa' }}>
              ⚠ filters run separately on each side of missing-data gaps
            </span>
          )}
          {fovers.some((f) => f.segmentWarning) && (
            <span style={{ fontSize: 10, color: '#dcdcaa' }}>
              short continuous regions have no filtered trace
            </span>
          )}
          {ferror && <span style={{ color: '#f48771', fontSize: 10 }}>{ferror}</span>}
          {filterSpec && <span style={{ flexBasis: '100%', fontSize: 10, color: '#a5b0b8' }}>
            Filters each complete saved TP; zoom does not change the filter. Original is stored data before this filter, including prior edits.
          </span>}
        </div>
        </PlotFilterDialog>
      )}
      <div className={styles.plotViewport} onDoubleClick={onZoomReset}>
        <div ref={chartRef} tabIndex={0} aria-label={`Plot canvas for ${cfg.label}`} className={styles.plotCanvas} />
        <PlotStateOverlay
          loading={filterSpec ? filterPending || (showOriginal && loadingTraces) : loadingTraces}
          hasData={hasData}
          error={visibleError}
          dataStatus={filterSpec && hasData ? showingOverlay
            ? 'Showing original and filtered data.'
            : showingFiltered ? 'Showing filtered data.' : 'Showing original data.' : undefined}
          emptyState={emptyState}
          onRetry={retry}
          loadingLabel={filterSpec ? filterPending ? 'Applying filter' : 'Loading original traces' : 'Loading test-point traces'}
          updatingLabel={filterSpec ? filterPending ? 'Applying filter' : 'Loading original traces' : 'Loading remaining traces'}
          errorTitle={
            ferror ? 'Could not apply the filter' : 'Could not load test-point traces'
          }
          partialMessage={partialMessage}
        />
      </div>
    </div>
  );
};
