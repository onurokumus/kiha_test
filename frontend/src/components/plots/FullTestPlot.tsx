import { loadedAnalysis } from '../../utils/analysisMetadata';
import React, { useEffect, useRef, useState, useMemo } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchFiltered, fetchWindow, isAbortError } from '../../services/api';
import {
  DataWindow,
  FilteredWindow,
  FilterSpec,
  SelectedTestPoint,
  TimePlotConfig,
  WindowDisplayMode,
} from '../../types';
import { noSelect } from '../../constants/styles';
import { FILTER_LABELS, FilterUi } from '../../constants/filters';
import { FilterRow } from '../controls/FilterRow';
import { PlotFilterDialog } from './PlotFilterDialog';
import { PlotExportControls, type PlotExportActions } from '../controls/PlotExportControls';
import { downloadPlotCsv, plotCsvRange, plotFilterDetails } from '../../utils/plotExport';
import { capturePlotPng, downloadPlotPng } from '../../utils/plotPngExport';
import { usePlotExportRegistration, type RegisterPlotExport } from '../../utils/plotExportRegistry';
import type { PlotExportData, PlotExportRequest } from '../../types';
import { SearchableSelect } from '../controls/SearchableSelect';
import {
  ACCENT,
  AXIS_STYLE,
  FULL_SYNC_KEY,
  TIME_AXIS_STYLE,
} from '../../constants/uplotTheme';
import { xPanZoomPlugin } from '../../utils/uplotPanZoom';
import { visibleYAutoFitPlugin, visibleYRange } from '../../utils/visibleYRange';
import {
  XRangeHighlight,
  xRangeHighlightsPlugin,
} from '../../utils/uplotRangeHighlights';
import { syncPlot, clearPlot } from '../../utils/uplotSync';
import { PlotStateOverlay } from './PlotState';
import { PlotAnnotations, type PlotAnnotationActions } from './PlotAnnotations';
import type { AnnotationManager } from '../../hooks/useAnnotations';
import { usePlotAnnotations, annotationAvailability } from '../../hooks/usePlotAnnotations';
import { annotationsPlugin, annotationImageDetails, type AnnotationSource } from '../../utils/plotAnnotations';
import { PlotActionMenu } from './PlotActionMenu';
import { PlotHeader } from './PlotHeader';
import styles from './TimePlot.module.css';

const FILTER_COLOR = '#dcdcaa';

interface FullTestPlotProps {
  annotations: AnnotationManager;
  annotationsVisible: boolean;
  onAnnotationsVisibleChange: (visible: boolean) => void;
  test: string;
  selectedTPs: SelectedTestPoint[];
  hiddenTPs: Set<string>;
  cfg: TimePlotConfig;
  range: [number, number] | null;
  displayMode: WindowDisplayMode;
  onRangeChange: (range: [number, number]) => void;
  onZoomReset?: () => void;
  /** THIS plot's DSP filter (null = none/incomplete params). Per-plot only
   *  (toggled by the ≈ header button; also shown when the cell is expanded). */
  filterSpec?: FilterSpec | null;
  filterUi?: FilterUi;
  onFilterUiChange?: (patch: Partial<FilterUi>) => void;
  showOriginal?: boolean;
  onShowOriginalChange?: (show: boolean) => void;
  /** Sample rate, for the Nyquist hint in the expanded filter row. */
  fs?: number | null;
  isExpanded: boolean;
  onToggleExpand: () => void;
  isEditMode?: boolean;
  allConfigs?: TimePlotConfig[];
  onConfigChange?: (newKey: string) => void;
  registerExport?: RegisterPlotExport;
}

interface FilterResultState {
  requestKey: string;
  window: FilteredWindow | null;
  error: string;
}

const EMPTY_FILTER_RESULT: FilterResultState = {
  requestKey: '',
  window: null,
  error: '',
};

/** uPlot mode 1 shares one time array across every line and envelope edge.
 * Equal lengths alone cannot establish that independently fetched values
 * belong at those timestamps. Reject incompatible responses explicitly. */
const windowsAlign = (original: DataWindow, filtered: DataWindow, key: string) => {
  if (
    original.mode !== filtered.mode ||
    original.level !== filtered.level ||
    original.i0 !== filtered.i0 ||
    original.i1 !== filtered.i1 ||
    original.t.length !== filtered.t.length ||
    !original.t.every((time, index) => time === filtered.t[index])
  ) return false;

  return [original, filtered].every((window) => {
    const values = window.series[key];
    if (!values) return false;
    if (window.mode === 'raw') {
      return Array.isArray(values) && values.length === window.t.length;
    }
    const envelope = values as { min: (number | null)[]; max: (number | null)[] };
    return Array.isArray(envelope.min) && Array.isArray(envelope.max) &&
      envelope.min.length === window.t.length && envelope.max.length === window.t.length;
  });
};

const hasPlottableSamples = (window: DataWindow | null, key: string) => {
  const values = window?.series[key];
  if (!window || !values) return false;
  const finiteAtTime = (value: number | null, index: number) =>
    value !== null && Number.isFinite(value) &&
    window.t[index] !== null && Number.isFinite(window.t[index]);
  if (window.mode === 'raw') return (values as (number | null)[]).some(finiteAtTime);
  const envelope = values as { min: (number | null)[]; max: (number | null)[] };
  return envelope.min.some(finiteAtTime) || envelope.max.some(finiteAtTime);
};

/** Full-test time plot: one column served by windowed reads. Auto switches
 *  between a line and a min/max band from sample density; displayMode can
 *  force either representation. Every zoom re-fetches the chosen form. */
export const FullTestPlot: React.FC<FullTestPlotProps> = ({
  annotations, annotationsVisible, onAnnotationsVisibleChange,
  test,
  selectedTPs,
  hiddenTPs,
  cfg,
  range,
  displayMode,
  onRangeChange,
  onZoomReset,
  filterSpec = null,
  filterUi,
  onFilterUiChange,
  showOriginal = false,
  onShowOriginalChange,
  fs = null,
  isExpanded,
  onToggleExpand,
  isEditMode = false,
  allConfigs = [],
  onConfigChange,
  registerExport,
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const exportActions = useRef<PlotExportActions>(null);
  const annotationActions = useRef<PlotAnnotationActions>(null);
  const plotRef = useRef<uPlot | null>(null);
  const structKeyRef = useRef('');
  // Latest range-commit callback (the reused uPlot instance keeps the closures
  // from its build, so pan/zoom + setSelect must read through this ref).
  const onRangeChangeRef = useRef(onRangeChange);
  onRangeChangeRef.current = onRangeChange;
  const pxRef = useRef(1200);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [loadedWindow, setWin] = useState<DataWindow | null>(null);
  const [loadedContext, setLoadedContext] = useState('');
  const contextKey = JSON.stringify([test, cfg.key]);
  const windowRequestKey = JSON.stringify([contextKey, range, displayMode]);
  const [loadedQuery, setLoadedQuery] = useState<{
    key: string; t0: number | null; t1: number | null; px: number; display: WindowDisplayMode;
  } | null>(null);
  const win = loadedContext === contextKey ? loadedWindow : null;
  const annotationSources = useMemo<AnnotationSource[]>(() => win ? [{ key: test, test,
    label: test, origin: 0, bounds: null, color: '#d7ba7d', seriesIndices: [1, 2, 3, 4] }] : [], [test, win]);
  const annotationItems = usePlotAnnotations(annotationSources, annotations, annotationsVisible, plotRef);
  const [loading, setLoading] = useState(Boolean(test && cfg.key));
  const [error, setError] = useState('');
  const [retryVersion, setRetryVersion] = useState(0);
  const highlights: XRangeHighlight[] = selectedTPs
    .filter((selection) => selection.test === test && !hiddenTPs.has(selection.id))
    .map((selection) => ({
      start: selection.tp.start_s,
      end: selection.endS,
      color: selection.color,
    }));
  const highlightsRef = useRef<readonly XRangeHighlight[]>(highlights);
  highlightsRef.current = highlights;
  const highlightsKey = highlights
    .map(({ start, end, color }) => `${start}:${end}:${color}`)
    .join('|');

  // Filter result state (spec comes from this plot's own filter row).
  const [filterResult, setFilterResult] =
    useState<FilterResultState>(EMPTY_FILTER_RESULT);
  const [fbusy, setFbusy] = useState(false);
  const [filterRetryVersion, setFilterRetryVersion] = useState(0);
  // per-cell filter row visibility, toggled by the ≈ header button
  const [showFilter, setShowFilter] = useState(false);

  // Associate a response with the exact raw window + filter request that
  // produced it. Parameter, zoom, and display-mode changes therefore hide
  // previous filtered data synchronously, before the fetch effect runs.
  const filterRequestKey =
    filterSpec && win
      ? JSON.stringify([
          test,
          cfg.key,
          range,
          displayMode,
          win.i0,
          win.i1,
          win.mode,
          win.level,
          filterSpec,
          retryVersion,
          filterRetryVersion,
        ])
      : '';
  const currentFilterResult =
    filterResult.requestKey === filterRequestKey
      ? filterResult
      : EMPTY_FILTER_RESULT;
  const fwin = currentFilterResult.window;
  const requestFilterError = currentFilterResult.error;
  const filterPending =
    Boolean(filterSpec && win) &&
    (fbusy || filterResult.requestKey !== filterRequestKey);

  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setBox({ w: el.clientWidth, h: el.clientHeight });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Windowed fetch, debounced and abortable (zoom bursts cancel stale reads)
  useEffect(() => {
    if (!test || !cfg.key) {
      setWin(null);
      setLoading(false);
      setError('');
      return;
    }
    let dead = false;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    const px = chartRef.current?.clientWidth || 1200;
    pxRef.current = px;
    const timer = window.setTimeout(() => {
      fetchWindow(
        test,
        [cfg.key],
        range?.[0] ?? null,
        range?.[1] ?? null,
        px,
        controller.signal,
        displayMode
      )
        .then((w) => {
          if (!dead) {
            setLoadedContext(contextKey);
            setWin(w);
            setLoadedQuery({ key: windowRequestKey, t0: range?.[0] ?? null,
              t1: range?.[1] ?? null, px: Math.max(200, Math.round(px)), display: displayMode });
            setError('');
          }
        })
        .catch((e) => {
          if (!dead && !isAbortError(e)) setError(String(e instanceof Error ? e.message : e));
        })
        .finally(() => !dead && setLoading(false));
    }, 100);
    return () => {
      dead = true;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [test, cfg.key, range, displayMode, retryVersion, contextKey, windowRequestKey]);

  // Fetch the filtered result; keyed on win so it reuses the same px + range.
  useEffect(() => {
    if (!filterSpec || !win) {
      setFilterResult(EMPTY_FILTER_RESULT);
      setFbusy(false);
      return;
    }
    let dead = false;
    const controller = new AbortController();
    setFbusy(true);
    const timer = window.setTimeout(() => {
      fetchFiltered(test, [cfg.key], filterSpec, range?.[0] ?? null, range?.[1] ?? null,
        pxRef.current, controller.signal, displayMode)
        .then((w) => {
          if (!dead) {
            setFilterResult({
              requestKey: filterRequestKey,
              window: w,
              error: '',
            });
          }
        })
        .catch((e) => {
          if (!dead && !isAbortError(e)) {
            setFilterResult({
              requestKey: filterRequestKey,
              window: null,
              error: String(e instanceof Error ? e.message : e),
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
    // filterRequestKey also contains raw-request identity. Wait for `win` to
    // change before filtering a newly requested zoom/display window.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [win, filterSpec, filterRetryVersion]);

  // A mismatched completed response is a recoverable filter failure, not an
  // empty result. The raw fallback and Retry remain available, and timestamps
  // are never silently borrowed from an incompatible original window.
  const alignmentError = fwin && win && !filterPending &&
    !windowsAlign(win, fwin, cfg.key)
      ? 'The filtered result does not align with the original time samples. Showing original data; retry the filter.'
      : '';
  const ferror = requestFilterError || alignmentError;
  const filteredWindow =
    fwin &&
    win &&
    !filterPending &&
    !ferror
      ? fwin
      : null;
  const showingFiltered = Boolean(filterSpec && filteredWindow);
  const showingOverlay = showingFiltered && showOriginal;
  const showingRawFallback = Boolean(filterSpec && (error || ferror));
  const displayedWindow = showingFiltered
    ? filteredWindow
    : !filterSpec || showingRawFallback
      ? win
      : null;

  // Destroy the uPlot instance only on unmount; data/structure changes reuse or
  // rebuild it in place via syncPlot (perf 2.4), so there is no per-run cleanup.
  useEffect(() => () => clearPlot(plotRef, structKeyRef), []);

  useEffect(() => {
    const el = chartRef.current;
    const base = displayedWindow?.series[cfg.key];
    if (!el || !displayedWindow || !base || box.w < 40 || box.h < 40) {
      clearPlot(plotRef, structKeyRef);
      return;
    }

    const series: uPlot.Series[] = [{}];
    const bands: uPlot.Band[] = [];
    const data: (number | null)[][] = [displayedWindow.t];
    const addWindow = (window: DataWindow, filtered: boolean, subdued: boolean) => {
      const values = window.series[cfg.key];
      const color = filtered ? FILTER_COLOR : ACCENT;
      const label = `${cfg.key} ${filtered ? 'filtered' : 'original'}`;
      const style: uPlot.Series = {
        stroke: subdued ? `${color}80` : color,
        width: subdued ? 1 : filtered ? 2 : window.mode === 'envelope' ? 1 : 1.5,
        dash: subdued ? [5, 4] : [],
        spanGaps: false,
      };
      if (window.mode === 'envelope') {
        const envelope = values as { min: (number | null)[]; max: (number | null)[] };
        // Each window owns its own band; never connect an original edge to a
        // filtered edge. Add original first so filtered edges remain readable.
        const upperIndex = series.length;
        series.push(
          { ...style, label: `${label} max` },
          { ...style, label: `${label} min` }
        );
        bands.push({
          series: [upperIndex, upperIndex + 1],
          fill: color + (subdued ? '16' : filtered ? '32' : '40'),
        });
        data.push(envelope.max, envelope.min);
      } else {
        series.push({ ...style, label });
        data.push(values as (number | null)[]);
      }
    };
    if (showingOverlay && win) addWindow(win, false, true);
    addWindow(displayedWindow, showingFiltered, false);

    // A NaN in the time column serializes as null; uPlot's x array
    // must be ascending numbers, so a single null corrupts the whole window.
    // Drop those samples across every parallel array (all the same length).
    // TimePlot filters null t the same way (1.19).
    if (displayedWindow.t.some((v) => v == null)) {
      const keep: number[] = [];
      for (let i = 0; i < displayedWindow.t.length; i++) {
        if (displayedWindow.t[i] != null) keep.push(i);
      }
      for (let c = 0; c < data.length; c++) data[c] = keep.map((i) => data[c][i]);
    }

    const makeOpts = (): uPlot.Options => ({
      width: box.w,
      height: box.h,
      series,
      bands,
      scales: { x: { time: false }, y: { range: visibleYRange } },
      axes: [{ ...TIME_AXIS_STYLE }, { ...AXIS_STYLE }],
      legend: { show: isExpanded, live: true },
      cursor: {
        drag: { x: true, y: false },
        points: { size: 6 },
        sync: { key: FULL_SYNC_KEY, scales: ['x', null] },
      },
      plugins: [
        visibleYAutoFitPlugin(),
        annotationsPlugin((plot) => annotationItems.current(plot)),
        xRangeHighlightsPlugin(() => highlightsRef.current),
        xPanZoomPlugin((r) => onRangeChangeRef.current(r)),
      ],
      hooks: {
        setSelect: [
          (u) => {
            if (u.select.width > 10) {
              const t0 = u.posToVal(u.select.left, 'x');
              const t1 = u.posToVal(u.select.left + u.select.width, 'x');
              u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
              onRangeChangeRef.current([t0, t1]);
            }
          },
        ],
      },
    });

    // Rebuild only when the series structure or size changes; a same-structure
    // refetch (the common zoom/pan case) re-ranges x to the new window via
    // setData's default resetScales — exactly what a fresh build did.
    const structKey = [
      displayedWindow.mode,
      showingOverlay ? 'overlay' : showingFiltered ? 'filtered' : 'raw',
      cfg.key,
      box.w,
      box.h,
      isExpanded,
    ].join('|');
    syncPlot({
      plotRef,
      structKeyRef,
      el,
      structKey,
      makeOpts,
      data: data as uPlot.AlignedData,
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [displayedWindow, showingFiltered, showingOverlay, win, box, isExpanded]);

  // Selection changes do not alter the trace data or uPlot structure. Redraw
  // the reused canvas so newly selected (or revealed) test-point bars appear
  // immediately without triggering a full-test refetch.
  useEffect(() => {
    plotRef.current?.redraw();
  }, [highlightsKey]);

  const containerClass = `${styles.plotContainer} ${
    isExpanded ? styles.plotContainerExpanded : styles.plotContainerCollapsed
  }`;

  const hasData =
    hasPlottableSamples(displayedWindow, cfg.key) ||
    (showingOverlay && hasPlottableSamples(win, cfg.key));
  const visibleError = error || ferror;
  const retry = () => {
    if (error) setRetryVersion((version) => version + 1);
    else setFilterRetryVersion((version) => version + 1);
  };
  const replacementCount = filteredWindow?.replacement_counts?.[cfg.key];
  const spikeEventCount = filteredWindow?.spike_event_counts?.[cfg.key];
  const filterSummary =
    filterSpec?.kind === 'despike' &&
    replacementCount !== undefined &&
    spikeEventCount !== undefined
      ? spikeEventCount === 0
        ? 'Despike · no events found'
        : `Despike · ${spikeEventCount} event${
            spikeEventCount === 1 ? '' : 's'
          }, ${replacementCount} sample${
            replacementCount === 1 ? '' : 's'
          } repaired`
      : filterSpec
        ? `${FILTER_LABELS[filterSpec.kind]} applied`
        : 'Filtered signal';
  const filterNeedsAttention = Boolean(filterUi?.kind && !filterSpec);
  const originalExportReason = !win || loading || loadedQuery?.key !== windowRequestKey
    ? 'Wait for the current time range to load.' : error ? 'Retry the time-series data before exporting.' : null;
  const filteredExportReason = originalExportReason || (!filterSpec ? 'Apply a valid filter to export filtered data.'
    : filterPending ? 'Wait for the filter to finish.'
      : ferror || !showingFiltered ? 'Retry the filter before exporting filtered data.' : null);
  const pngExportReason = originalExportReason || (filterSpec ? filteredExportReason : null)
    || annotationAvailability(annotationSources, annotations, annotationsVisible)
    || (!hasData ? 'Wait for a plot with samples.' : null);

  const buildCsvRequest = (data: PlotExportData): PlotExportRequest => {
    const reason = data === 'original' ? originalExportReason : filteredExportReason;
    if (reason) throw new Error(reason);
    if (!win || !loadedQuery || !plotRef.current) throw new Error('Wait for the current plot.');
    if (!plotRef.current.series.slice(1).some((series) => series.show !== false)) {
      throw new Error('Show at least one trace before exporting.');
    }
    return { column: cfg.key, data,
      sources: [{ test, t0: loadedQuery.t0, t1: loadedQuery.t1,
        px: loadedQuery.px, display: loadedQuery.display, expected_i0: win.i0, expected_i1: win.i1 }],
      filter: data === 'original' ? null : filterSpec, x_range: plotCsvRange(plotRef.current, range !== null) };
  };
  const getPngSource = () => {
    if (pngExportReason) throw new Error(pngExportReason);
    const plot = plotRef.current;
    if (!plot || !win || !loadedQuery) throw new Error('Wait for the current plot.');
    const edges = win.mode === 'envelope' ? 2 : 1;
    const visibleIndices = plot.series.slice(1).flatMap((series, index) => series.show === false ? [] : [index]);
    const hasFiltered = showingFiltered && visibleIndices.some((index) => !showingOverlay || index >= edges);
    const hasOriginal = visibleIndices.some((index) => !showingFiltered || (showingOverlay && index < edges));
    return { plot, options: {
      provenance: { kind: 'time', column: cfg.key, source_mode: 'full',
        annotations: { visible: annotationsVisible, items: annotationItems.current(plot) },
        sources: [{ test, request: loadedQuery,
          original: hasOriginal ? loadedAnalysis(win) : null,
          filtered: hasFiltered ? loadedAnalysis(filteredWindow) : null,
          visible_series_indices: visibleIndices,
        }],
      },
      filename: `${test}_${cfg.key}_full-test_${hasOriginal && hasFiltered ? 'both' : hasFiltered ? 'filtered' : 'original'}.png`,
      title: `${cfg.label} · Full test`,
      scope: [`Source test: ${test}. Time (s) uses stored timestamps; current X/Y view.`,
        `Source rows [${win.i0}, ${win.i1}); shaded intervals identify selected TPs.`],
      details: [...plotFilterDetails(hasFiltered ? filterSpec : null),
        ...annotationImageDetails(annotationItems.current(plot), annotationsVisible),
        `Display: ${win.mode}, level ${win.level}. CSV exports full-resolution samples.`,
        ...(hasFiltered ? [`Filter request: ${loadedQuery.t0 ?? 'start'} to ${loadedQuery.t1 ?? 'end'} s; ${loadedQuery.display}, ${loadedQuery.px} px. Envelope context can extend outside the requested range.`, `Complete requested-window result: ${filterSummary}`] : []),
        ...(hasFiltered && filteredWindow?.boundary_warning ? ['Dataset edge: filter transients possible.'] : []),
        ...(hasFiltered && (filteredWindow?.time_gap_count || filteredWindow?.gap_segment_warning) ? ['Known gaps are processing boundaries; short segments may have no filtered trace.'] : [])],
    } };
  };
  const exportScope = `Full test: ${range ? 'zoomed X range' : 'complete source rows'} in stored time (seconds). Filtering retains the displayed window's processing context before cropping. Selected-TP shading does not restrict CSV rows.`;
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
    <div
      className={containerClass}
      style={{ ...noSelect }}
      role="group"
      aria-label={`${cfg.label} full test plot`}
      data-filter-display={displayedWindow
        ? showingOverlay ? 'overlay' : showingFiltered ? 'filtered' : 'original'
        : undefined}
    >
      <PlotHeader label={cfg.label} isExpanded={isExpanded} onToggleExpand={onToggleExpand}

        summary={displayedWindow && <span title="Display resolution">
          {displayedWindow.mode === 'raw' ? displayedWindow.level === 1 ? 'raw' : `line 1:${displayedWindow.level}` : `env 1:${displayedWindow.level}`}
        </span>}
        status={<>
          {showingFiltered && <span className={styles.filteredStatus} title={showingOverlay ? 'Original: thin dashed · filtered: solid' : 'Filtered signal'}>
            {showingOverlay ? 'original + filtered' : 'filtered'}
          </span>}
          {(ferror || filterNeedsAttention) && <span title={ferror || 'Open Filter settings in the plot menu'} style={{ color: '#e7c16f' }}>Filter needs attention</span>}
        </>}
        actions={<>
          <PlotActionMenu label={cfg.label} targetRef={chartRef} contextKey={JSON.stringify([contextKey, windowRequestKey, filterSpec, showOriginal])}
            getPlot={() => plotRef.current} exportActions={exportActions} annotationActions={annotationActions}
            canAnnotate={!!annotationSources.length} resetLabel="Reset linked time zoom" onReset={() => onZoomReset?.()}
            onEditFilter={filterUi && onFilterUiChange ? () => setShowFilter(true) : undefined}
            filterStatus={ferror || (filterNeedsAttention ? "Filter settings need attention" : undefined)}
            overlay={onShowOriginalChange ? { checked: showOriginal, enabled: !!filterSpec, onChange: onShowOriginalChange } : undefined}
            notes={{ visible: annotationsVisible, onChange: onAnnotationsVisibleChange }} />

          <PlotAnnotations hideTrigger actionsRef={annotationActions} label={cfg.label} sources={annotationSources} manager={annotations}
            visible={annotationsVisible} onVisibleChange={onAnnotationsVisibleChange} getPlot={() => plotRef.current} />
          <PlotExportControls hideTrigger actionsRef={exportActions} label={cfg.label}
            contextKey={JSON.stringify([test, cfg.key, filterSpec, showOriginal, displayMode])}
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
          {filteredWindow?.boundary_warning && (
            <span style={{ fontSize: 10, color: '#dcdcaa' }}>
              ⚠ range touches data edge — filter transients possible
            </span>
          )}
          {(filteredWindow?.time_gap_count ?? 0) > 0 && (
            <span style={{ fontSize: 10, color: '#dcdcaa' }}>
              ⚠ filtered separately around {filteredWindow?.time_gap_count} missing-data
              {' '}gap{filteredWindow?.time_gap_count === 1 ? '' : 's'}
            </span>
          )}
          {filteredWindow?.gap_segment_warning && (
            <span style={{ fontSize: 10, color: '#dcdcaa' }}>
              short continuous regions have no filtered trace
            </span>
          )}
          {ferror && <span style={{ color: '#f48771', fontSize: 10 }}>{ferror}</span>}
          {filterSpec && <span style={{ flexBasis: '100%', fontSize: 10, color: '#a5b0b8' }}>
            Filters the viewed range at full resolution; zoom applies it again. Original is stored data before this filter, including prior edits.
          </span>}
        </div>
        </PlotFilterDialog>
      )}
      <div className={styles.plotViewport} onDoubleClick={onZoomReset}>
        <div ref={chartRef} tabIndex={0} aria-label={`Plot canvas for ${cfg.label}`} className={styles.plotCanvas} />
        <PlotStateOverlay
          loading={loading || filterPending}
          hasData={hasData}
          error={visibleError}
          dataStatus={showingRawFallback ? 'Showing original data.' : undefined}
          emptyState={{
            title: filterSpec ? 'No filtered samples' : 'No samples in this view',
            detail: range
              ? 'Reset the zoom or choose a wider time range.'
              : `The ${cfg.label} ${
                  filterSpec ? 'filter result' : 'signal'
                } has no plottable values.`,
          }}
          onRetry={retry}
          loadingLabel={filterSpec ? 'Applying filter' : 'Loading time series'}
          updatingLabel={filterSpec ? 'Applying filter' : 'Updating time range'}
          errorTitle={error ? 'Could not load the time series' : 'Could not apply the filter'}
        />
      </div>
    </div>
  );
};
