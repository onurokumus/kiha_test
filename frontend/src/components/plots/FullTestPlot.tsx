import React, { useEffect, useRef, useState } from 'react';
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
import { SearchableSelect } from '../controls/SearchableSelect';
import {
  ACCENT,
  AXIS_STYLE,
  FULL_SYNC_KEY,
  TIME_AXIS_STYLE,
} from '../../constants/uplotTheme';
import { xPanZoomPlugin } from '../../utils/uplotPanZoom';
import {
  XRangeHighlight,
  xRangeHighlightsPlugin,
} from '../../utils/uplotRangeHighlights';
import { syncPlot, clearPlot } from '../../utils/uplotSync';
import { PlotStateOverlay } from './PlotState';
import styles from './TimePlot.module.css';

const FILTER_COLOR = '#dcdcaa';

interface FullTestPlotProps {
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
  /** Sample rate, for the Nyquist hint in the expanded filter row. */
  fs?: number | null;
  isExpanded: boolean;
  onToggleExpand: () => void;
  isEditMode?: boolean;
  allConfigs?: TimePlotConfig[];
  onConfigChange?: (newKey: string) => void;
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

/** Full-test time plot: one column served by windowed reads. Auto switches
 *  between a line and a min/max band from sample density; displayMode can
 *  force either representation. Every zoom re-fetches the chosen form. */
export const FullTestPlot: React.FC<FullTestPlotProps> = ({
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
  fs = null,
  isExpanded,
  onToggleExpand,
  isEditMode = false,
  allConfigs = [],
  onConfigChange,
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
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
  const win = loadedContext === contextKey ? loadedWindow : null;
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
  const ferror = currentFilterResult.error;
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
  }, [test, cfg.key, range, displayMode, retryVersion, contextKey]);

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

  // Use filtered data only when it is for the CURRENT raw window: same
  // mode and length (arrays must align 1:1 for uPlot bands) AND the same
  // [i0, i1) range. Without the range check a pan at constant zoom keeps the
  // same mode+length, so the OLD range's result must not be shown while the
  // 300 ms-debounced refetch is pending (1.18).
  const filteredWindow =
    fwin &&
    win &&
    !filterPending &&
    !ferror &&
    fwin.mode === win.mode &&
    fwin.i0 === win.i0 &&
    fwin.i1 === win.i1 &&
    fwin.t.length === win.t.length
      ? fwin
      : null;
  const showingFiltered = Boolean(filterSpec && filteredWindow);
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
    const traceColor = showingFiltered ? FILTER_COLOR : ACCENT;
    const traceLabel = showingFiltered ? `${cfg.key} filtered` : cfg.key;

    if (displayedWindow.mode === 'envelope') {
      const s = base as { min: (number | null)[]; max: (number | null)[] };
      series.push(
        {
          label: `${traceLabel} max`,
          stroke: traceColor,
          width: showingFiltered ? 1.5 : 1,
          spanGaps: false,
        },
        {
          label: `${traceLabel} min`,
          stroke: traceColor,
          width: showingFiltered ? 1.5 : 1,
          spanGaps: false,
        }
      );
      bands.push({
        series: [1, 2],
        fill: traceColor + (showingFiltered ? '32' : '40'),
      });
      data.push(s.max, s.min);
    } else {
      series.push({
        label: traceLabel,
        stroke: traceColor,
        width: showingFiltered ? 2 : 1.5,
        spanGaps: false,
      });
      data.push(base as (number | null)[]);
    }

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
      scales: { x: { time: false } },
      axes: [{ ...TIME_AXIS_STYLE }, { ...AXIS_STYLE }],
      legend: { show: isExpanded, live: true },
      cursor: {
        drag: { x: true, y: false },
        points: { size: 6 },
        sync: { key: FULL_SYNC_KEY, scales: ['x', null] },
      },
      plugins: [
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
      showingFiltered ? 'filtered' : 'raw',
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
  }, [displayedWindow, showingFiltered, box, isExpanded]);

  // Selection changes do not alter the trace data or uPlot structure. Redraw
  // the reused canvas so newly selected (or revealed) test-point bars appear
  // immediately without triggering a full-test refetch.
  useEffect(() => {
    plotRef.current?.redraw();
  }, [highlightsKey]);

  const containerClass = `${styles.plotContainer} ${
    isExpanded ? styles.plotContainerExpanded : styles.plotContainerCollapsed
  }`;

  const buttonClass = `${styles.expandButton} ${
    isExpanded ? styles.expandButtonExpanded : styles.expandButtonCollapsed
  }`;

  const baseSeries = displayedWindow?.series[cfg.key];
  const hasData =
    Boolean(displayedWindow && displayedWindow.t.length > 0 && baseSeries) &&
    (displayedWindow?.mode === 'envelope'
      ? (baseSeries as { min: (number | null)[]; max: (number | null)[] }).min.some(
          (value) => value !== null && Number.isFinite(value)
        ) ||
        (baseSeries as { min: (number | null)[]; max: (number | null)[] }).max.some(
          (value) => value !== null && Number.isFinite(value)
        )
      : (baseSeries as (number | null)[]).some(
          (value) => value !== null && Number.isFinite(value)
        ));
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
          {cfg.label}
        </div>
        {showFilter && !isExpanded && filterUi && onFilterUiChange && (
          <div
            role="group"
            aria-label={`Filter settings for ${cfg.label}`}
            style={{
              position: 'absolute',
              left: 0,
              right: 58,
              top: -4,
              zIndex: 6,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              flexWrap: 'wrap',
              background: '#2d2d2d',
              border: '1px solid #3c3c3c',
              borderRadius: 3,
              padding: '2px 4px',
              boxShadow: '0 2px 8px rgba(0, 0, 0, 0.5)',
            }}
          >
            <FilterRow
              ui={filterUi}
              onChange={onFilterUiChange}
              fs={fs}
              title="Filter this plot only"
            />
          </div>
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          {displayedWindow && (
            <span style={{ fontSize: 10, color: '#909090' }}>
              {displayedWindow.mode === 'raw'
                ? displayedWindow.level === 1
                  ? 'raw'
                  : `line 1:${displayedWindow.level}`
                : `env 1:${displayedWindow.level}`}
            </span>
          )}
          {showingFiltered && (
            <span className={styles.filteredStatus}>filtered</span>
          )}
          {filterUi && onFilterUiChange && (
            <button
              type="button"
              onClick={() => setShowFilter((v) => !v)}
              className={buttonClass}
              aria-label={`Filter ${cfg.label}`}
              aria-expanded={showFilter}
              aria-pressed={Boolean(filterSpec)}
              title={
                ferror ||
                (filterNeedsAttention
                  ? 'Filter settings need attention — click to edit'
                  : filterSpec
                    ? 'Filtered data shown — click to edit'
                    : 'Filter this plot')
              }
            >
              <span
                className={styles.expandButtonIcon}
                style={{
                  color: ferror
                    ? '#f48771'
                    : filterNeedsAttention
                      ? '#e7c16f'
                      : filterSpec
                        ? FILTER_COLOR
                        : undefined,
                }}
              >
                ≈
              </span>
            </button>
          )}
          <button
            type="button"
            onClick={onToggleExpand}
            className={buttonClass}
            aria-label={`${isExpanded ? 'Minimize' : 'Expand'} ${cfg.label}`}
            title={`${isExpanded ? 'Minimize' : 'Expand'} this plot`}
          >
            <span className={styles.expandButtonIcon}>{isExpanded ? '▪' : '▣'}</span>
          </button>
        </div>
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
      </div>
      {isExpanded && (
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
              short continuous regions were left unfiltered
            </span>
          )}
          {ferror && <span style={{ color: '#f48771', fontSize: 10 }}>{ferror}</span>}
        </div>
      )}
      <div className={styles.plotViewport} onDoubleClick={onZoomReset}>
        <div ref={chartRef} className={styles.plotCanvas} />
        <PlotStateOverlay
          loading={loading || filterPending}
          hasData={hasData}
          error={visibleError}
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
