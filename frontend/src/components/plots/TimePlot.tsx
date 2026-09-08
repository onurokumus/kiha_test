import React, { useEffect, useRef, useState, useMemo } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchFiltered, isAbortError } from '../../services/api';
import { FilterSpec, SelectedTestPoint, TimePlotConfig } from '../../types';
import { noSelect } from '../../constants/styles';
import {
  AXIS_STYLE,
  TIME_AXIS_STYLE,
  TP_SYNC_KEY,
} from '../../constants/uplotTheme';
import { xPanZoomPlugin } from '../../utils/uplotPanZoom';
import {
  clearPlot,
  facetedSeriesValue,
  sortedFacetedDataIdx,
  syncPlot,
} from '../../utils/uplotSync';
import { FILTER_LABELS, FilterUi } from '../../constants/filters';
import { FilterRow } from '../controls/FilterRow';
import { SearchableSelect } from '../controls/SearchableSelect';
import { PlotStateOverlay, PlotEmptyState } from './PlotState';
import styles from './TimePlot.module.css';

const FILTER_COLOR = '#dcdcaa';

interface TimePlotProps {
  cfg: TimePlotConfig;
  selectedTPs: SelectedTestPoint[];
  hiddenTPs: Set<string>;
  /** Trace-fetch failures keyed by `${selection.id}|${column}`. */
  traceErrors?: Record<string, string>;
  onRetryTraces?: () => void;
  /** Per-test plottable columns — filtered traces are only fetched for TPs
   *  whose OWN test has this plot's column (cross-test selections would
   *  otherwise 400 with 'unknown columns'; same rule as SpectrumPlot). */
  columnsByTest: Record<string, string[]>;
  isExpanded: boolean;
  onToggleExpand: () => void;
  zoomDomain: [number, number] | null;
  onZoomChange: (domain: [number, number]) => void;
  onZoomReset?: () => void;
  /** THIS plot's DSP filter, computed over each TP's own range in its own
   *  test. Per-plot only (toggled by the ≈ header button; also shown when
   *  expanded). */
  filterSpec?: FilterSpec | null;
  filterUi?: FilterUi;
  onFilterUiChange?: (patch: Partial<FilterUi>) => void;
  fs?: number | null;
  isEditMode?: boolean;
  allConfigs?: TimePlotConfig[];
  onConfigChange?: (newKey: string) => void;
}

interface OverlayTrace {
  label: string;
  color: string;
  t: number[];
  y: (number | null)[];
}

/** Filtered result for one TP: 1 segment (line) or 2 (envelope min/max). */
interface FilteredTpTrace {
  id: string;
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
  cfg,
  selectedTPs,
  hiddenTPs,
  traceErrors = {},
  onRetryTraces,
  columnsByTest,
  isExpanded,
  onToggleExpand,
  zoomDomain,
  onZoomChange,
  onZoomReset,
  filterSpec = null,
  filterUi,
  onFilterUiChange,
  fs = null,
  isEditMode = false,
  allConfigs = [],
  onConfigChange,
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const structKeyRef = useRef('');
  // Latest zoom-commit callback — a reused uPlot keeps its build-time closures.
  const onZoomChangeRef = useRef(onZoomChange);
  onZoomChangeRef.current = onZoomChange;
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [filterResult, setFilterResult] =
    useState<FilterResultState>(EMPTY_FILTER_RESULT);
  const [fbusy, setFbusy] = useState(false);
  const [filterRetryVersion, setFilterRetryVersion] = useState(0);
  // per-cell filter row visibility, toggled by the ≈ header button
  const [showFilter, setShowFilter] = useState(false);

  const visibleTPs = selectedTPs.filter((s) => !hiddenTPs.has(s.id));
  const tpFingerprint = visibleTPs
    .map((s) => `${s.id}:${s.tp.start_s}:${s.endS}`)
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
        out.push({ label: `${s.name} · ${s.test}`, color: s.color, t, y });
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

  // Filtered trace per visible TP: /filter over the TP's own absolute
  // range in its own test, shifted to relative time. Fetched once per
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
    setFbusy(true);
    const timer = window.setTimeout(() => {
      Promise.all(
        eligible.map(async (s): Promise<FilteredTpTrace | null> => {
          try {
            const w = await fetchFiltered(
              s.test, [cfg.key], filterSpec, s.tp.start_s, s.endS, px,
              controller.signal
            );
            const sr = w.series[cfg.key];
            if (!sr) return null;
            const ys: (number | null)[][] =
              w.mode === 'envelope'
                ? [(sr as { max: (number | null)[] }).max, (sr as { min: (number | null)[] }).min]
                : [sr as (number | null)[]];
            const segs = ys.map((yArr) => {
              const t: number[] = [];
              const y: (number | null)[] = [];
              w.t.forEach((tv, i) => {
                if (tv === null) return;
                t.push(tv - s.tp.start_s);
                y.push(yArr[i]);
              });
              return { t, y };
            });
            return {
              id: s.id,
              color: s.color,
              name: s.name,
              segs,
              warning: !!w.boundary_warning,
              gapWarning: (w.time_gap_count ?? 0) > 0,
              segmentWarning: !!w.gap_segment_warning,
              replacementCount: w.replacement_counts?.[cfg.key],
              spikeEventCount: w.spike_event_counts?.[cfg.key],
            };
          } catch (e) {
            if (isAbortError(e)) throw e;
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
              }.`,
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
                    } could not be updated.`
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
          label: `${filtered.name} · filtered${
            filtered.segs.length > 1 ? (index === 0 ? ' max' : ' min') : ''
          }`,
          color: filtered.color,
          t: segment.t,
          y: segment.y,
        }))
      ),
    [fovers]
  );
  const showingFiltered =
    Boolean(filterSpec) && !filterPending && !ferror && fovers.length > 0;
  const showingRawFallback = Boolean(filterSpec && ferror);
  const plottedTraces = useMemo(
    () =>
      showingFiltered
        ? filteredTraces
        : !filterSpec || showingRawFallback
          ? traces
          : [],
    [
      showingFiltered,
      filteredTraces,
      filterSpec,
      showingRawFallback,
      traces,
    ]
  );

  useEffect(() => {
    const el = chartRef.current;
    if (!el || plottedTraces.length === 0 || box.w < 40 || box.h < 40) {
      clearPlot(plotRef, structKeyRef);
      return;
    }

    const series: uPlot.Series[] = [
      {},
      ...plottedTraces.map(
        (trace) =>
          ({
            label: trace.label,
            stroke: trace.color,
            width: showingFiltered ? 2 : 1.5,
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
          ...(zoomDomain ? { range: [zoomDomain[0], zoomDomain[1]] as [number, number] } : {}),
        },
        y: {},
      },
      axes: [{ ...TIME_AXIS_STYLE }, { ...AXIS_STYLE, scale: 'y' }],
      legend: { show: isExpanded, live: true },
      cursor: {
        dataIdx: sortedFacetedDataIdx,
        drag: { x: true, y: false },
        sync: { key: TP_SYNC_KEY, scales: ['x', null] },
      },
      plugins: [xPanZoomPlugin((r) => onZoomChangeRef.current(r))],
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

    // TP zoom is a scale range (client-side), NOT part of the data, so it is
    // kept OUT of the struct key: a zoom change reuses the instance. On reuse
    // with an active zoom, don't let setData auto-range (it would flash the
    // full extent) — swap data without resetting scales, then re-apply the
    // zoom via setScale. With no zoom, auto-range is the reset-to-fit view.
    const structKey = [
      JSON.stringify(series.map((s) => [s.label, s.stroke, s.width])), box.w, box.h, isExpanded,
    ].join('|');
    syncPlot({
      plotRef,
      structKeyRef,
      el,
      structKey,
      makeOpts,
      data,
      resetScales: !zoomDomain,
      onUpdate: (u) => {
        if (zoomDomain) u.setScale('x', { min: zoomDomain[0], max: zoomDomain[1] });
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
  }, [plottedTraces, showingFiltered, zoomDomain, box, isExpanded]);

  const containerClass = `${styles.plotContainer} ${
    isExpanded ? styles.plotContainerExpanded : styles.plotContainerCollapsed
  }`;

  const buttonClass = `${styles.expandButton} ${
    isExpanded ? styles.expandButtonExpanded : styles.expandButtonCollapsed
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
      filterSpec && ferror ? rawPartialMessage : '',
    ]
      .filter(Boolean)
      .join(' ');
  const retry = filterSpec && (ferror || filterPartialMessage)
    ? () => setFilterRetryVersion((version) => version + 1)
    : onRetryTraces;
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

  return (
    <div className={containerClass} style={{ ...noSelect }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 4,
          position: 'relative',
        }}
      >
        <div style={{ fontSize: 12, color: '#c0c0c0' }}>{cfg.label}</div>
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
              short continuous regions were left unfiltered
            </span>
          )}
          {ferror && <span style={{ color: '#f48771', fontSize: 10 }}>{ferror}</span>}
        </div>
      )}
      <div className={styles.plotViewport} onDoubleClick={onZoomReset}>
        <div ref={chartRef} className={styles.plotCanvas} />
        <PlotStateOverlay
          loading={filterSpec ? filterPending : loadingTraces}
          hasData={hasData}
          error={visibleError}
          emptyState={emptyState}
          onRetry={retry}
          loadingLabel={filterSpec ? 'Applying filter' : 'Loading test-point traces'}
          updatingLabel={filterSpec ? 'Applying filter' : 'Loading remaining traces'}
          errorTitle={
            ferror ? 'Could not apply the filter' : 'Could not load test-point traces'
          }
          partialMessage={partialMessage}
        />
      </div>
    </div>
  );
};
