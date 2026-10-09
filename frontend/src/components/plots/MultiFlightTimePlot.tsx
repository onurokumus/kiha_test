import { useEffect, useMemo, useRef, useState } from 'react';
import uPlot from 'uplot';
import type { FilteredWindow, PlotExportData, PlotExportRequest } from '../../types';
import type { FullFlightSource } from '../../utils/fullFlightComparison';
import type { FullTestPlotProps } from './FullTestPlot';
import { fetchFiltered, fetchWindow, isAbortError } from '../../services/api';
import { AXIS_STYLE, FULL_SYNC_KEY, PLOT_PADDING, TIME_AXIS_STYLE, plotSeriesColor } from '../../constants/uplotTheme';
import { noSelect } from '../../constants/styles';
import { FILTER_LABELS } from '../../constants/filters';
import { FullTestVariables } from './FullTestVariables';
import { FullFlightWindow, FullFlightTimeTrace, FULL_FLIGHT_VARIABLE_DASHES, flightWindowTraces, windowColumnsAlign, windowsAlign } from '../../utils/fullFlightTime';
import { clearPlot, facetedSeriesValue, sortedFacetedDataIdx, syncPlot } from '../../utils/uplotSync';
import { visibleYAutoFitPlugin, visibleYRange } from '../../utils/visibleYRange';
import { xPanZoomPlugin } from '../../utils/uplotPanZoom';
import { xRangeHighlightsPlugin } from '../../utils/uplotRangeHighlights';
import { usePlotTheme } from '../../utils/usePlotTheme';
import { lineAppearancePlugin } from '../../utils/uplotAppearance';
import { usePlotHoverGroup } from '../../utils/usePlotHoverGroup';
import { plotHoverPlugin } from '../../utils/uplotHover';
import { sampleHoverRows } from '../../utils/plotHoverValues';
import { loadedAnalysis } from '../../utils/analysisMetadata';
import { downloadPlotCsv, plotCsvRange, plotFilterDetails } from '../../utils/plotExport';
import { capturePlotPng, downloadPlotPng } from '../../utils/plotPngExport';
import { usePlotExportRegistration } from '../../utils/plotExportRegistry';
import { FilterRow } from '../controls/FilterRow';
import { PlotExportControls, type PlotExportActions } from '../controls/PlotExportControls';
import { PlotFilterDialog } from './PlotFilterDialog';
import { PlotHeader } from './PlotHeader';
import { PlotActionMenu } from './PlotActionMenu';
import { PlotStateOverlay } from './PlotState';
import styles from './TimePlot.module.css';

interface LoadedWindows { context: string; key: string; entries: FullFlightWindow[]; errors: string[] }
interface FilterWindows { key: string; windows: Record<string, FilteredWindow>; errors: Record<string, string> }
const EMPTY_LOADED: LoadedWindows = { context: '', key: '', entries: [], errors: [] };
const EMPTY_FILTERED: FilterWindows = { key: '', windows: {}, errors: {} };
const NEUTRAL_VARIABLE_COLOR = '#626f83';
const neutralColors = (columns: readonly string[]) => columns.map(() => NEUTRAL_VARIABLE_COLOR);

/** Every flight owns its time array, envelope bands, filter context and source rows. */
export function MultiFlightTimePlot({ fullFlights, timeBasis = 'elapsed', cfg, additionalConfigs = [], allConfigs = [],
  selectedTPs, hiddenTPs, range, displayMode, onRangeChange, onZoomReset, onConfigChange, onAdditionalColumnsChange,
  filterSpec = null, filterUi, onFilterUiChange, showOriginal = false, onShowOriginalChange,
  isExpanded, onToggleExpand, viewport, viewportContext, onViewportChange, registerExport, onDisplayDetailChange,
}: FullTestPlotProps & { fullFlights: FullFlightSource[] }) {
  const configs = [cfg, ...additionalConfigs].filter((config, index, entries) =>
    entries.findIndex(candidate => candidate.key === config.key) === index).slice(0, 6);
  const columnsKey = JSON.stringify(configs.map(config => config.key));
  const columns = useMemo<string[]>(() => JSON.parse(columnsKey), [columnsKey]);
  const sourcesKey = JSON.stringify(fullFlights);
  const sources = useMemo<FullFlightSource[]>(() => JSON.parse(sourcesKey), [sourcesKey]);
  const eligible = useMemo(() => sources.map(source => ({ source, columns: columns.filter(column => source.columns.includes(column)) }))
    .filter(entry => entry.columns.length > 0), [sources, columns]);
  const plotLabel = configs.map(config => config.label).join(' + ');
  const timeAxisLabel = timeBasis === 'elapsed' ? 'Elapsed time (s)'
    : sources.some(source => source.timeOffset !== 0) ? 'Aligned stored time (s)' : 'Stored time (s)';
  const rangeKey = JSON.stringify(range);
  const requestRange = useMemo<[number, number] | null>(() => JSON.parse(rangeKey), [rangeKey]);
  const [retryVersion, setRetryVersion] = useState(0);
  const contextKey = JSON.stringify([sources, columns]);
  const requestKey = JSON.stringify([contextKey, requestRange, displayMode, retryVersion]);
  const [loaded, setLoaded] = useState<LoadedWindows>(EMPTY_LOADED);
  const current = loaded.context === contextKey ? loaded : EMPTY_LOADED;
  const loading = eligible.length > 0 && loaded.key !== requestKey;
  const [filterResults, setFilterResults] = useState<FilterWindows>(EMPTY_FILTERED);
  const filterKey = filterSpec && current.entries.length ? JSON.stringify([current.key, filterSpec]) : '';
  const filtered = filterResults.key === filterKey ? filterResults : EMPTY_FILTERED;
  const filterPending = Boolean(filterKey && filtered.key !== filterKey);
  const [showFilter, setShowFilter] = useState(false);
  const [hiddenColumns, setHiddenColumns] = useState<Set<string>>(new Set());
  const chartRef = useRef<HTMLDivElement>(null);
  const exportActions = useRef<PlotExportActions>(null);
  const plotRef = useRef<uPlot | null>(null);
  const structKeyRef = useRef('');
  const [box, setBox] = useState({ w: 0, h: 0 });
  const yRange = viewport?.context === viewportContext ? viewport?.y ?? null : null;
  const yRangeRef = useRef(yRange);
  yRangeRef.current = yRange;
  const rangeRef = useRef(range);
  rangeRef.current = range;
  const handlersRef = useRef({ onRangeChange, onViewportChange, viewportContext });
  handlersRef.current = { onRangeChange, onViewportChange, viewportContext };
  const hoverGroup = usePlotHoverGroup();
  usePlotTheme(plotRef);
  const highlights = selectedTPs.filter(selection => !hiddenTPs.has(selection.id)).flatMap(selection => {
    const source = sources.find(candidate => candidate.test === selection.test);
    return source ? [{ start: selection.tp.start_s + source.timeOffset,
      end: selection.endS + source.timeOffset, color: source.color }] : [];
  });
  const highlightsRef = useRef(highlights);
  highlightsRef.current = highlights;
  const highlightsKey = JSON.stringify(highlights);

  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!eligible.length) return;
    const controller = new AbortController();
    let dead = false;
    const px = Math.max(200, Math.round(chartRef.current?.clientWidth || 1200));
    const timer = window.setTimeout(async () => {
      const responses = await Promise.all(eligible.map(async ({ source, columns: available }) => {
        const query = { t0: requestRange ? requestRange[0] - source.timeOffset : null,
          t1: requestRange ? requestRange[1] - source.timeOffset : null, px, display: displayMode };
        try {
          const result = await fetchWindow(source.test, available, query.t0, query.t1, px, controller.signal, displayMode);
          if (!windowColumnsAlign(result, available)) throw new Error('The returned variables do not match their time samples.');
          return { entry: { source, columns: available, window: result, query } as FullFlightWindow };
        } catch (error) {
          return { error: isAbortError(error) ? '' : `${source.test}: ${error instanceof Error ? error.message : String(error)}` };
        }
      }));
      if (dead) return;
      setLoaded({ context: contextKey, key: requestKey,
        entries: responses.flatMap(response => response.entry ? [response.entry] : []),
        errors: responses.flatMap(response => response.error ? [response.error] : []) });
    }, 100);
    return () => { dead = true; window.clearTimeout(timer); controller.abort(); };
  }, [eligible, requestRange, displayMode, contextKey, requestKey]);

  useEffect(() => {
    if (!filterSpec || !current.entries.length || loading) return;
    const controller = new AbortController();
    let dead = false;
    const timer = window.setTimeout(async () => {
      const responses = await Promise.all(current.entries.map(async entry => {
        try {
          const { query, source } = entry;
          const result = await fetchFiltered(source.test, entry.columns, filterSpec,
            query.t0, query.t1, query.px, controller.signal, query.display);
          if (!windowsAlign(entry.window, result, entry.columns)) {
            throw new Error('The filtered result does not align with the original samples. Showing original data.');
          }
          return { test: source.test, window: result };
        } catch (error) {
          return { test: entry.source.test, error: isAbortError(error) ? '' : String(error instanceof Error ? error.message : error) };
        }
      }));
      if (dead) return;
      setFilterResults({ key: filterKey,
        windows: Object.fromEntries(responses.flatMap(response => response.window ? [[response.test, response.window]] : [])),
        errors: Object.fromEntries(responses.flatMap(response => response.error ? [[response.test, response.error]] : [])) });
    }, 300);
    return () => { dead = true; window.clearTimeout(timer); controller.abort(); };
  }, [current.entries, filterSpec, loading, filterKey]);

  const traces = useMemo<FullFlightTimeTrace[]>(() => {
    const originals: FullFlightTimeTrace[] = [], processed: FullFlightTimeTrace[] = [];
    for (const entry of current.entries) {
      const filteredWindow = filterSpec ? filtered.windows[entry.source.test] : undefined;
      if (!filterSpec || showOriginal || filtered.errors[entry.source.test]) {
        originals.push(...flightWindowTraces(entry, entry.window, columns, 'original', Boolean(filteredWindow)));
      }
      if (filteredWindow) processed.push(...flightWindowTraces(entry, filteredWindow, columns, 'filtered', false));
    }
    return [...originals, ...processed].filter(trace => !hiddenColumns.has(trace.column));
  }, [current.entries, filtered, filterSpec, showOriginal, columns, hiddenColumns]);
  const showingFiltered = traces.some(trace => trace.kind === 'filtered');
  const showingOriginal = traces.some(trace => trace.kind === 'original');
  const showingOverlay = showingFiltered && showingOriginal;
  const hasData = traces.some(trace => trace.y.some(value => value !== null && Number.isFinite(value)));
  const missing = sources.flatMap(source => {
    const unavailable = columns.filter(column => !source.columns.includes(column));
    return unavailable.length ? [`${source.test}: missing ${unavailable.join(', ')}`] : [];
  });
  const filterErrors = Object.entries(filtered.errors).map(([test, error]) => `${test}: ${error}`);
  const partial = [...current.errors, ...filterErrors, ...missing];
  const fullCoverage = sources.filter(source => columns.every(column => source.columns.includes(column))).length;
  const partialSummary = [
    current.errors.length ? `${current.errors.length} flight${current.errors.length === 1 ? '' : 's'} failed to load` : '',
    filterErrors.length ? `${filterErrors.length} flight filter${filterErrors.length === 1 ? '' : 's'} failed; originals shown` : '',
    missing.length ? `${fullCoverage} of ${sources.length} flights have all variables` : '',
  ].filter(Boolean).join(' · ');
  const error = !hasData && !loading && !filterPending ? [...current.errors, ...filterErrors].join(' ') : '';
  const filterNeedsAttention = Boolean(filterUi?.kind && !filterSpec) || filterErrors.length > 0;

  useEffect(() => () => clearPlot(plotRef, structKeyRef), []);
  useEffect(() => {
    const el = chartRef.current;
    if (!el || !traces.length || box.w < 40 || box.h < 40) { clearPlot(plotRef, structKeyRef); return; }
    const series: uPlot.Series[] = [{}, ...traces.map(trace => ({
      label: trace.label,
      stroke: (plot: uPlot) => plotSeriesColor(plot, trace.subdued ? `${trace.color}80` : trace.color),
      width: trace.subdued ? 1 : trace.kind === 'filtered' ? 2 : trace.edge ? 1 : 1.5,
      dash: trace.dash, spanGaps: false, value: facetedSeriesValue,
      facets: [{ scale: 'x', auto: true }, { scale: 'y', auto: true }],
    }))];
    const bands: uPlot.Band[] = traces.flatMap((trace, index) => trace.edge === 'max' &&
      traces[index + 1]?.band === trace.band && traces[index + 1]?.edge === 'min'
      ? [{ series: [index + 1, index + 2] as [number, number],
        fill: (plot: uPlot) => plotSeriesColor(plot, `${trace.color}${trace.subdued ? '16' : '32'}`) }] : []);
    const makeOpts = (): uPlot.Options => ({ mode: 2, width: box.w, height: box.h,
      padding: PLOT_PADDING, series, bands,
      scales: { x: { time: false, range: (_u, min, max) => rangeRef.current ?? [min, max] },
        y: { range: (u, min, max) => yRangeRef.current ?? visibleYRange(u, min, max) } },
      axes: [{ ...TIME_AXIS_STYLE, label: timeAxisLabel }, { ...AXIS_STYLE }], legend: { show: isExpanded, live: true },
      cursor: { dataIdx: sortedFacetedDataIdx, drag: { x: true, y: false },
        points: { size: 6 }, sync: { key: FULL_SYNC_KEY, scales: ['x', null] } },
      plugins: [lineAppearancePlugin(), plotHoverPlugin(u => ({ heading: 'Full flights', columns: [timeAxisLabel, 'Value'], units: ['s', ''],
        rows: sampleHoverRows(u, true) }), hoverGroup, timeBasis === 'elapsed' ? 'time:elapsed-flight' : 'time:absolute'),
        visibleYAutoFitPlugin(() => yRangeRef.current != null), xRangeHighlightsPlugin(() => highlightsRef.current),
        xPanZoomPlugin(value => handlersRef.current.onRangeChange(value), value => {
          yRangeRef.current = value;
          const { viewportContext: context, onViewportChange: change } = handlersRef.current;
          const plot = plotRef.current;
          if (plot && context) change?.({ context, x: [plot.scales.x.min!, plot.scales.x.max!], y: value });
        })],
      hooks: { setSelect: [u => {
        if (u.select.width <= 10) return;
        const bounds: [number, number] = [u.posToVal(u.select.left, 'x'), u.posToVal(u.select.left + u.select.width, 'x')];
        u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
        handlersRef.current.onRangeChange(bounds);
      }] },
    });
    const structKey = JSON.stringify([traces.map(trace => [trace.label, trace.color, trace.dash, trace.subdued, trace.edge]),
      box, isExpanded, timeBasis, timeAxisLabel]);
    syncPlot({ plotRef, structKeyRef, el, structKey, makeOpts,
      data: [null, ...traces.map(trace => [trace.t, trace.y])] as unknown as uPlot.AlignedData,
      onCreate: plot => {
        if (isExpanded) {
          const legendHeight = (plot.root.querySelector('.u-legend') as HTMLElement | null)?.offsetHeight ?? 0;
          if (legendHeight) plot.setSize({ width: box.w, height: Math.max(60, box.h - legendHeight) });
        }
      } });
  }, [traces, box, isExpanded, timeBasis, timeAxisLabel, hoverGroup]);
  useEffect(() => { plotRef.current?.redraw(); }, [highlightsKey]);
  useEffect(() => {
    plotRef.current?.setScale('y', { min: yRange?.[0] ?? null, max: yRange?.[1] ?? null } as unknown as { min: number; max: number });
  }, [yRange]);
  useEffect(() => {
    if (range) plotRef.current?.setScale('x', { min: range[0], max: range[1] });
  }, [range]);
  const windows = current.entries.map(entry => filtered.windows[entry.source.test] ?? entry.window);
  const detailLevel = hasData ? Math.max(...windows.map(window => window.level)) : null;
  const detailMode = windows.some(window => window.mode === 'envelope') ? 'envelope' : 'raw';
  useEffect(() => {
    onDisplayDetailChange?.(detailLevel === null ? null : { mode: detailMode, level: detailLevel });
    return () => onDisplayDetailChange?.(null);
  }, [detailLevel, detailMode, onDisplayDetailChange]);

  const originalExportReason = !current.entries.length || loading ? 'Wait for flight samples to load.'
    : !hasData ? 'Show at least one variable with samples.' : null;
  const filteredExportReason = originalExportReason || (!filterSpec ? 'Apply a valid filter first.'
    : filterPending ? 'Wait for each flight filter to finish.' : filterErrors.length ? 'Retry failed flight filters before exporting filtered data.' : null);
  const pngExportReason = originalExportReason || (filterPending ? 'Wait for each flight filter to finish.' : null);
  const visibleColumns = (test: string) => columns.filter(column => traces.some((trace, index) =>
    trace.test === test && trace.column === column && plotRef.current?.series[index + 1]?.show !== false));
  const buildCsvRequest = (data: PlotExportData): PlotExportRequest => {
    const reason = data === 'original' ? originalExportReason : filteredExportReason;
    if (reason) throw new Error(reason);
    const exportSources = current.entries.flatMap(entry => {
      const available = visibleColumns(entry.source.test);
      return available.length ? [{ test: entry.source.test, ...entry.query, columns: available,
        time_offset: entry.source.timeOffset, expected_i0: entry.window.i0, expected_i1: entry.window.i1 }] : [];
    });
    if (!exportSources.length) throw new Error('Show at least one trace before exporting.');
    return { column: cfg.key, columns, data, sources: exportSources, filter: data === 'original' ? null : filterSpec,
      x_range: plotCsvRange(plotRef.current, range !== null, true) };
  };
  const getPngSource = () => {
    if (pngExportReason) throw new Error(pngExportReason);
    const plot = plotRef.current;
    if (!plot) throw new Error('Wait for the current plot.');
    const visible = traces.filter((_trace, index) => plot.series[index + 1]?.show !== false);
    if (!visible.length) throw new Error('Show at least one trace before exporting.');
    const hasFiltered = visible.some(trace => trace.kind === 'filtered');
    return { plot, options: {
      filename: `${sources.map(source => source.test).join('_')}_${columns.join('_')}_full-flights.png`,
      title: `${plotLabel} · ${sources.length} flight${sources.length === 1 ? '' : 's'}`,
      provenance: { kind: 'time', source_mode: 'full', column: cfg.key, columns, time_basis: timeBasis,
        variable_line_styles: Object.fromEntries(columns.map((column, index) => [column, FULL_FLIGHT_VARIABLE_DASHES[index]])),
        time_axis_label: timeAxisLabel, flight_colors: Object.fromEntries(sources.map(source => [source.test, source.color])),
        sources: current.entries.map(entry => ({ test: entry.source.test, time_offset: entry.source.timeOffset,
          request: entry.query, columns: entry.columns,
          original: visible.some(trace => trace.test === entry.source.test && trace.kind === 'original') ? loadedAnalysis(entry.window) : null,
          filtered: visible.some(trace => trace.test === entry.source.test && trace.kind === 'filtered') ? loadedAnalysis(filtered.windows[entry.source.test]) : null })),
        visible_traces: visible.map(({ test, column, kind, edge }) => ({ test, column, kind, edge })), partial_results: partial },
      scope: [`Full flights; ${timeBasis === 'elapsed' ? 'time since recording start' : 'stored time'} plus per-flight alignment offsets. Current X/Y view.`,
        'Flight color and variable line pattern identify each trace. Native samples retain independent time arrays.',
        ...sources.map(source => `${source.test}: displayed time = stored time + ${source.timeOffset} s.`),
        ...(columns.length > 1 ? ['Variables share one Y axis in stored units; values are not normalized.'] : [])],
      details: [...plotFilterDetails(hasFiltered ? filterSpec : null),
        'Envelope edges are independent per flight and variable. CSV contains full-resolution samples and native timestamps.',
        ...partial],
    } };
  };
  const exportScope = `Full flights: ${range ? 'zoomed displayed time range' : 'complete source rows'}. CSV preserves native timestamps and adds displayed time with alignment offsets. Each flight is filtered independently at its own sample rate.`;
  const defaultData = showingOverlay ? 'both' : showingFiltered ? 'filtered' : 'original';
  usePlotExportRegistration(registerExport, { label: plotLabel, scope: exportScope, defaultData,
    originalReason: originalExportReason, filteredReason: filteredExportReason, pngReason: pngExportReason,
    buildCsvRequest, capturePng: () => { const { plot, options } = getPngSource(); return capturePlotPng(plot, options); } });
  const sampleRates = eligible.map(entry => entry.source.fs).filter((rate): rate is number => rate !== null && Number.isFinite(rate) && rate > 0);
  const fs = sampleRates.length ? Math.min(...sampleRates) : null;

  return <div className={`${styles.plotContainer} ${isExpanded ? styles.plotContainerExpanded : styles.plotContainerCollapsed}`}
    style={noSelect} role="group" aria-label={`${plotLabel} full test plot`} data-full-flight-count={sources.length}
    data-filter-display={showingOverlay ? 'overlay' : showingFiltered ? 'filtered' : showingOriginal ? 'original' : undefined}>
    <PlotHeader label={plotLabel} isExpanded={isExpanded} onToggleExpand={onToggleExpand}
      identityControl={<FullTestVariables configs={configs} allConfigs={allConfigs} colors={neutralColors(columns)} resolveColors={neutralColors}
        variableDashes={FULL_FLIGHT_VARIABLE_DASHES} hiddenColumns={hiddenColumns} onToggleVariable={column => setHiddenColumns(previous => {
          const next = new Set(previous); if (next.has(column)) next.delete(column); else next.add(column); return next;
        })} showingOverlay={showingOverlay} onPrimaryChange={onConfigChange} onAdditionalColumnsChange={onAdditionalColumnsChange} />}
      status={filterNeedsAttention && <span title={filterErrors.join(' ') || 'Open Filter settings in the plot menu'} style={{ color: 'var(--warning, #806b20)' }}>Filter needs attention</span>}
      actions={<>
        <PlotActionMenu label={plotLabel} targetRef={chartRef} contextKey={JSON.stringify([requestKey, filterSpec, showOriginal])}
          exportActions={exportActions} resetLabel="Reset linked time / Y axes" onReset={() => onZoomReset?.()}
          onEditFilter={filterUi && onFilterUiChange ? () => setShowFilter(true) : undefined}
          filterStatus={filterNeedsAttention ? 'Filter settings need attention' : undefined}
          overlay={onShowOriginalChange ? { checked: showOriginal, enabled: Boolean(filterSpec), onChange: onShowOriginalChange } : undefined} />
        <PlotExportControls hideTrigger actionsRef={exportActions} label={plotLabel} contextKey={JSON.stringify([requestKey, filterSpec, showOriginal, [...hiddenColumns]])}
          scope={exportScope} defaultData={defaultData} originalReason={originalExportReason} filteredReason={filteredExportReason} pngReason={pngExportReason}
          onCsv={(data, signal, includeMetadata) => downloadPlotCsv({ ...buildCsvRequest(data), include_metadata: includeMetadata }, signal)}
          onPng={(signal, includeMetadata) => { const { plot, options } = getPngSource(); return downloadPlotPng(plot, { ...options, signal, includeMetadata }); }} />
      </>} />
    {showFilter && filterUi && onFilterUiChange && <PlotFilterDialog label={plotLabel} onClose={() => setShowFilter(false)}>
      <FilterRow ui={filterUi} onChange={onFilterUiChange} fs={fs} title="Filter every flight and variable in this plot" />
      <p style={{ fontSize: 11, color: 'var(--muted)' }}>Each flight uses its own sample rate. The frequency limit is the lowest participating Nyquist frequency.</p>
      {filterSpec && <span className="badge">{FILTER_LABELS[filterSpec.kind]} · {Object.keys(filtered.windows).length} of {current.entries.length} flights</span>}
      {current.entries.map(entry => {
        const result = filtered.windows[entry.source.test];
        const warnings = [result?.boundary_warning ? 'data edge; filter transients possible' : '',
          result?.time_gap_count ? `${result.time_gap_count} missing-data gaps processed separately` : '',
          result?.gap_segment_warning ? 'short continuous regions have no filtered trace' : '', filtered.errors[entry.source.test]].filter(Boolean);
        if (filterSpec?.kind === 'despike' && result?.replacement_counts && result.spike_event_counts) {
          const repaired = entry.columns.reduce((count, column) => count + (result.replacement_counts?.[column] ?? 0), 0);
          const events = entry.columns.reduce((count, column) => count + (result.spike_event_counts?.[column] ?? 0), 0);
          warnings.unshift(`${events} spike event${events === 1 ? '' : 's'}; ${repaired} samples repaired`);
        }
        return warnings.length ? <p key={entry.source.test} style={{ fontSize: 11 }}>{entry.source.test}: {warnings.join('; ')}</p> : null;
      })}
    </PlotFilterDialog>}
    <div className={styles.plotViewport} onDoubleClick={onZoomReset}>
      <div ref={chartRef} tabIndex={0} aria-label={`Plot canvas for ${plotLabel}`} className={styles.plotCanvas} />
      <PlotStateOverlay loading={loading || filterPending} hasData={hasData} error={error}
        partialMessage={partialSummary} partialTitle={partial.join('\n')}
        onRetry={current.errors.length || filterErrors.length ? () => setRetryVersion(value => value + 1) : undefined}
        loadingLabel={filterPending ? 'Applying flight filters' : 'Loading flights'} updatingLabel="Updating flight comparison"
        dataStatus={filterErrors.length ? 'Failed flight filters show original data.' : undefined}
        emptyState={{ title: sources.length ? columns.every(column => hiddenColumns.has(column)) ? 'All variables hidden' : 'No samples in this view' : 'No visible flights',
          detail: !sources.length ? 'Select or show a flight above the plots.' : !eligible.length ? missing.join(' · ') : 'Choose a wider time range or show a variable in the legend.' }} />
    </div>
  </div>;
}
