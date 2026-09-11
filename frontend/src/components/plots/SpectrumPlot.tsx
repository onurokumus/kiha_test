import { usePlotViewport, ViewportProps } from '../../utils/plotViewport';
import { loadedAnalysis } from '../../utils/analysisMetadata';
import React, { useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchSpectrum, isAbortError } from '../../services/api';
import {
  SelectedTestPoint,
  SpectrumData,
  SpectrumXAxis,
  TimePlotConfig,
  SpectrumExportRequest,
  SpectrumExportSource,
} from '../../types';
import { noSelect } from '../../constants/styles';
import { ACCENT, AXIS_STYLE, safeRange } from '../../constants/uplotTheme';
import { xPanZoomPlugin } from '../../utils/uplotPanZoom';
import { visibleYAutoFitPlugin, visibleYRange } from '../../utils/visibleYRange';
import { syncPlot, clearPlot, facetedSeriesValue, sortedFacetedDataIdx } from '../../utils/uplotSync';
import { PlotStateOverlay, PlotEmptyState } from './PlotState';
import { SpectrumAnalysisDetails, type SpectrumAnalysisActions, type SpectrumAnalysisTrace, type SpectrumFailure } from './SpectrumAnalysisDetails';
import { SearchableSelect } from '../controls/SearchableSelect';
import { PlotExportControls, type PlotExportActions } from '../controls/PlotExportControls';
import { downloadPlotCsv, plotExportRange } from '../../utils/plotExport';
import { capturePlotPng, downloadPlotPng } from '../../utils/plotPngExport';
import { usePlotExportRegistration, type RegisterPlotExport } from '../../utils/plotExportRegistry';
import { PlotActionMenu } from './PlotActionMenu';
import { PlotHeader } from './PlotHeader';
import styles from './TimePlot.module.css';

export type PanelSource = 'tp' | 'full';

interface SpectrumPlotProps extends ViewportProps {
  test: string;
  cfg: TimePlotConfig;
  /** Data source: spectra of the selected TPs, or of the active test. */
  source: PanelSource;
  selectedTPs: SelectedTestPoint[];
  hiddenTPs: Set<string>;
  columnsByTest: Record<string, string[]>;
  /** Time range for source='full' (null = whole test). */
  range: [number, number] | null;
  specMode: 'fft' | 'welch';
  axisMode: SpectrumXAxis;
  rpmColumn: string;
  logY: boolean;
  isExpanded: boolean;
  onToggleExpand: () => void;
  isEditMode?: boolean;
  allConfigs?: TimePlotConfig[];
  onConfigChange?: (newKey: string) => void;
  registerExport?: RegisterPlotExport;
}

interface SpectrumTrace extends SpectrumAnalysisTrace {
  source: Pick<SpectrumExportSource, 'test' | 'tp_id' | 't0' | 't1'>;
  color: string;
  x: (number | null)[];
  mag: (number | null)[];
  meanRpm?: number;
  minRpm?: number;
  maxRpm?: number;
}

const EMPTY_TRACES: SpectrumTrace[] = [];

interface SpectrumResultState {
  key: string;
  traces: SpectrumTrace[];
  failures: SpectrumFailure[];
  error: string;
  partial: string;
}

function resolveColumn(columns: string[], requested: string): string | null {
  if (columns.includes(requested)) return requested;
  const normalized = requested.toLocaleLowerCase();
  return columns.find((column) => column.toLocaleLowerCase() === normalized) ?? null;
}

function traceFromSpectrum(
  data: SpectrumData,
  axisMode: SpectrumXAxis,
  label: string,
  color: string,
  source: SpectrumTrace['source'],
): SpectrumTrace {
  const meanRpm = data.mean_rpm;
  if (
    axisMode === 'per_rev' &&
    (meanRpm === undefined || !Number.isFinite(meanRpm) || meanRpm <= 0)
  ) {
    throw new Error(
      'The backend did not return a usable mean RPM. Restart the backend and retry.'
    );
  }
  const x =
    axisMode === 'per_rev'
      ? data.freqs.map((frequency) =>
          frequency === null ? null : (frequency * 60) / (meanRpm as number)
        )
      : data.freqs;
  return {
    source,
    label,
    data,
    color,
    x,
    mag: data.mag,
    meanRpm,
    minRpm: data.min_rpm,
    maxRpm: data.max_rpm,
  };
}

/** Frequency-domain view of one column: either one spectrum over the active
 *  test's zoom range, or one spectrum per selected test point (each computed
 *  over that TP's own time range in its own test, overlaid in TP colors).
 *  Drag = client-side frequency zoom; double-click resets. */
export const SpectrumPlot: React.FC<SpectrumPlotProps> = ({
  test,
  cfg,
  source,
  selectedTPs,
  hiddenTPs,
  columnsByTest,
  range,
  specMode,
  axisMode,
  rpmColumn,
  logY,
  isExpanded,
  onToggleExpand,
  isEditMode = false,
  allConfigs = [],
  onConfigChange,
  registerExport,
  viewport, viewportContext, onViewportChange,
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const analysisActions = useRef<SpectrumAnalysisActions>(null);
  const exportActions = useRef<PlotExportActions>(null);
  const plotRef = useRef<uPlot | null>(null);
  const viewportControl = usePlotViewport(plotRef, {viewport, viewportContext, onViewportChange}, false);
  const structKeyRef = useRef('');
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [result, setResult] = useState<SpectrumResultState>({
    key: '', traces: [], failures: [], error: '', partial: '',
  });
  const [loading, setLoading] = useState(false);
  const [retryVersion, setRetryVersion] = useState(0);
  const visibleTPs = selectedTPs.filter((point) => !hiddenTPs.has(point.id));
  const eligibleTPs = visibleTPs.filter((point) => (columnsByTest[point.test] ?? []).includes(cfg.key));
  // Include exact saved bounds and schema eligibility; a changed Full interval
  // must never expose a previous interval's values or analysis details.
  const contextKey = JSON.stringify([
    source, cfg.key, specMode, axisMode, rpmColumn,
    source === 'full'
      ? [test, range, resolveColumn(columnsByTest[test] ?? [], rpmColumn)]
      : eligibleTPs.map((point) => [point.id, point.tp.start_idx, point.tp.end_idx,
          point.tp.start_s, point.endS, point.name, point.color,
          resolveColumn(columnsByTest[point.test] ?? [], rpmColumn)]),
  ]);
  const current = result.key === contextKey;
  const traces = current ? result.traces : EMPTY_TRACES;
  const failures = current ? result.failures : [];
  const error = current ? result.error : '';
  const partialMessage = current ? result.partial : '';
  const pending = loading || !current;
  const needsRpmColumn = axisMode === 'per_rev' && !rpmColumn;

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
    const empty: SpectrumResultState = { key: contextKey, traces: [], failures: [], error: '', partial: '' };
    if (!cfg.key || needsRpmColumn || (source === 'full' ? !test : !eligibleTPs.length)) {
      setResult(empty);
      setLoading(false);
      return;
    }
    let dead = false;
    const controller = new AbortController();
    setLoading(true);
    setResult((previous) => ({ ...previous, error: '', partial: '' }));
    const load = async () => {
      try {
        const requests = source === 'full'
          ? [{ test, tpId: undefined, label: test, color: ACCENT }]
          : eligibleTPs.map((point) => ({ test: point.test, tpId: point.tpId,
              label: `${point.name} · ${point.test} · TP ${point.tpId}`, color: point.color }));
        const results = await Promise.all(requests.map(async (item) => {
          try {
            const resolvedRpmColumn = axisMode === 'per_rev'
              ? resolveColumn(columnsByTest[item.test] ?? [], rpmColumn) : null;
            if (axisMode === 'per_rev' && !resolvedRpmColumn) {
              throw new Error(`RPM variable '${rpmColumn}' is not available in ${item.test}.`);
            }
            const data = await fetchSpectrum(item.test, cfg.key, specMode,
              source === 'full' ? range?.[0] ?? null : null,
              source === 'full' ? range?.[1] ?? null : null,
              resolvedRpmColumn, controller.signal, item.tpId);
            if (item.tpId !== undefined && data.tp_id !== item.tpId) {
              throw new Error('The backend did not confirm the requested test-point interval. Update the backend and retry.');
            }
            return { trace: traceFromSpectrum(data, axisMode, item.label, item.color, {
              test: item.test, tp_id: item.tpId,
              ...(source === 'full' ? { t0: range?.[0] ?? null, t1: range?.[1] ?? null } : {}),
            }) };
          } catch (cause) {
            if (isAbortError(cause)) throw cause;
            return { failure: { label: item.label, message: cause instanceof Error ? cause.message : String(cause) } };
          }
        }));
        if (dead) return;
        const loaded = results.flatMap((item) => item.trace ? [item.trace] : []);
        const failed = results.flatMap((item) => item.failure ? [item.failure] : []);
        setResult({ key: contextKey, traces: loaded, failures: failed,
          error: !loaded.length && failed.length ? Array.from(new Set(failed.map((failure) => failure.message))).join(' ') : '',
          partial: loaded.length && failed.length ? `${failed.length} of ${requests.length} selected spectra could not be loaded. Open Analysis for source details.` : '',
        });
      } catch (cause) {
        if (!dead && !isAbortError(cause)) {
          const message = cause instanceof Error ? cause.message : String(cause);
          setResult({ ...empty, error: message, failures: [{ label: source === 'full' ? test : cfg.label, message }] });
        }
      } finally {
        if (!dead) setLoading(false);
      }
    };
    const timer = window.setTimeout(load, 100);
    return () => { dead = true; window.clearTimeout(timer); controller.abort(); };
    // The key includes every request input, source identity and rendered label.
    // Relative TP time zoom and unrelated schema fetches must not recompute it.
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

    const transform = (mag: (number | null)[]) =>
      logY ? mag.map((v) => (v !== null && v > 0 ? Math.log10(v) : null)) : mag;

    const series: uPlot.Series[] = [
      {},
      ...traces.map(
        (tr) =>
          ({
            label: tr.label,
            stroke: tr.color,
            width: 1,
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
        x: { time: false, range: safeRange as uPlot.Scale.Range },
        y: { range: visibleYRange },
      },
      axes: [
        {
          ...AXIS_STYLE,
          label: axisMode === 'per_rev' ? 'Order (cycles/rev)' : 'Frequency (Hz)',
        },
        { ...AXIS_STYLE, label: `${specMode === 'welch' ? 'PSD (U²/Hz)' : 'Magnitude (U)'}${logY ? ' · log10' : ''}` },
      ],
      legend: { show: isExpanded, live: true },
      // uPlot default drag = client-side x zoom; dblclick resets it.
      // Wheel-zoom / shift-drag pan are client-side too (no commit target).
      cursor: { dataIdx: sortedFacetedDataIdx, drag: { x: true, y: false } },
      plugins: [visibleYAutoFitPlugin(), xPanZoomPlugin(), viewportControl.plugin],
      series,
    });

    const data = [
      null,
      ...traces.map((tr) => [tr.x, transform(tr.mag)]),
    ] as unknown as uPlot.AlignedData;

    // Axis labels and series colors are structural, so log mode and colors
    // must participate in the key used to reuse the existing canvas.
    const structKey = [
      JSON.stringify(series.map((s) => [s.label, s.stroke])), box.w, box.h, isExpanded, axisMode, logY, specMode,
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
  }, [viewportControl, viewportContext, traces, logY, box, isExpanded, axisMode, specMode]);

  const containerClass = `${styles.plotContainer} ${
    isExpanded ? styles.plotContainerExpanded : styles.plotContainerCollapsed
  }`;

  const eligibleTpCount = visibleTPs.filter((selected) =>
    (columnsByTest[selected.test] ?? []).includes(cfg.key)
  ).length;
  const hasData = traces.some((trace) =>
    trace.x.some(
      (xValue, index) =>
        xValue !== null &&
        Number.isFinite(xValue) &&
        trace.mag[index] !== null &&
        Number.isFinite(trace.mag[index]) &&
        (!logY || (trace.mag[index] as number) > 0)
    )
  );
  let emptyState: PlotEmptyState;
  if (needsRpmColumn) {
    emptyState = {
      title: 'Choose an RPM variable',
      detail: 'Per-revolution spectra use mean RPM over each FFT/Welch interval.',
    };
  } else if (source === 'tp' && visibleTPs.length === 0) {
    emptyState = {
      title: 'Select test points to compare',
      detail: 'Choose one or more points on the scatter plot to calculate their spectra.',
    };
  } else if (source === 'tp' && eligibleTpCount === 0) {
    emptyState = {
      title: `${cfg.label} is not available`,
      detail: 'None of the visible test points contain this signal.',
    };
  } else if (logY && traces.length > 0 && !hasData) {
    emptyState = {
      title: 'No positive spectrum values',
      detail: 'Switch off Log Y to view zero or non-positive magnitudes.',
    };
  } else {
    emptyState = {
      title: 'No spectrum samples',
      detail:
        source === 'full' && range
          ? 'Reset the time zoom or choose a wider range.'
          : 'The selected signal has no plottable frequency data.',
    };
  }

  const sampleCount = traces.reduce((total, trace) => total + trace.data.n_samples, 0);
  const missingCount = traces.reduce((total, trace) => total + trace.data.nan_count, 0);
  const reduced = traces.some((trace) => trace.data.reduction?.method === 'max-bin');
  const summary = traces.length ? `${specMode.toUpperCase()} · ${source === 'tp'
    ? `${traces.length} TP${traces.length === 1 ? '' : 's'}` : `${sampleCount.toLocaleString()} samples`}${missingCount ? ` · ${missingCount.toLocaleString()} missing` : ''}${reduced ? ' · reduced' : ''}` : '';

  const exportScope = `${specMode.toUpperCase()} from stored data, including saved edits; no time-plot filter. Only visible legend traces are exported. ${source === 'tp' ? 'Each saved TP uses its own complete interval.' : 'Uses the loaded Full-test time interval.'}`;
  const exportReason = pending ? 'Wait for Spectrum to finish.' : error || partialMessage ||
    (!traces.length ? 'Load a spectrum before exporting.' : null);
  const csvReason = exportReason || (traces.some(({ data }) => data.method?.version !== 'kiha-spectrum-v2' ||
    data.i0 == null || data.i1 == null) ? 'Reload with a current backend to export exact Spectrum context.' : null);
  const pngReason = exportReason || (!hasData ? 'No visible spectrum values to capture.' : null);
  const visibleExportTraces = () => {
    if (exportReason) throw new Error(exportReason);
    const plot = plotRef.current;
    if (!plot) throw new Error('Wait for the spectrum canvas.');
    const visible = traces.filter((_trace, index) => plot.series[index + 1]?.show !== false);
    if (!visible.length) throw new Error('Show at least one spectrum in the legend.');
    return { plot, visible };
  };
  const buildCsvRequest = (): SpectrumExportRequest => {
    if (csvReason) throw new Error(csvReason);
    const { plot, visible } = visibleExportTraces();
    const axis = plotExportRange(plot);
    // The default display is reduced and padded. It must still export DC and
    // Nyquist even when neither native endpoint won a display bucket.
    let min = Infinity, max = -Infinity;
    for (const trace of visible) for (const value of trace.x) {
      if (value != null && Number.isFinite(value)) { min = Math.min(min, value); max = Math.max(max, value); }
    }
    const automatic = safeRange(plot, min, max);
    const isDefault = axis.every((value, index) => value === automatic[index]);
    return { kind: 'spectrum', column: cfg.key, mode: specMode, axis: axisMode,
      method_version: 'kiha-spectrum-v2', x_range: isDefault ? null : axis,
      sources: visible.map(({ source: identity, data }) => ({ ...identity,
        expected_i0: data.i0!, expected_i1: data.i1!, expected_fs_hz: data.fs_hz,
        nperseg: data.method!.nperseg ?? 4096,
        ...(axisMode === 'per_rev' ? { rpm_col: data.rpm_col, expected_mean_rpm: data.mean_rpm } : {}),
      })),
    };
  };
  const getPngSource = () => {
    if (pngReason) throw new Error(pngReason);
    const { plot, visible } = visibleExportTraces();
    return { plot, options: {
      provenance: { kind: 'spectrum', column: cfg.key, source_mode: source,
        x_axis: axisMode, y_transform: logY ? 'log10_linear_values_not_db' : 'linear',
        sources: visible.map((trace) => ({ ...trace.source, loaded: loadedAnalysis(trace.data) })),
      },
      filename: `${cfg.key}_${specMode}_${axisMode}.png`, title: `${cfg.label} · ${specMode.toUpperCase()}`,
      scope: [exportScope, ...visible.map(({ label, data }) => `${label}: rows [${data.i0 ?? '?'}, ${data.i1 ?? '?'}); sample times ${data.time_start_s ?? '?'} to ${data.time_end_s ?? '?'} s; ${data.fs_hz} Hz; N=${data.n_samples}; missing=${data.nan_count}.`)],
      details: [
        `${axisMode === 'per_rev' ? 'X = Hz × 60 / each trace’s mean absolute RPM; no order tracking. PSD remains per Hz.' : 'X = frequency in Hz.'} ${logY ? 'Y displays log10(linear value), not dB; zero values are not drawn.' : 'Y displays linear values.'}`,
        ...visible.flatMap(({ label, data }) => [
          `${label} method: ${data.method ? Object.entries(data.method).map(([key, value]) => `${key}=${value ?? 'n/a'}`).join('; ') : 'unavailable (legacy response)'}.`,
          `${label} display: ${data.reduction ? `${data.reduction.n_bins_returned}/${data.reduction.n_bins_original} native bins; ${data.reduction.method}` : 'reduction unavailable'}. ${data.rpm_col ? `RPM ${data.rpm_col}: mean=${data.mean_rpm}, range=${data.min_rpm}..${data.max_rpm}, finite=${data.rpm_finite_count}, missing=${data.rpm_nan_count}.` : ''}`,
          `${label} timing: ${data.quality ? Object.entries(data.quality).map(([key, value]) => `${key}=${value ?? 'unknown'}`).join('; ') : 'unavailable'}.`,
        ]),
      ],
    } };
  };
  usePlotExportRegistration(registerExport, { label: cfg.label, scope: exportScope,
    defaultData: 'original', originalReason: csvReason, filteredReason: null, pngReason,
    buildCsvRequest, capturePng: () => { const { plot, options } = getPngSource(); return capturePlotPng(plot, options); },
  });

  return (
    <div className={containerClass} style={{ ...noSelect }} role="group" aria-label={`${cfg.label} spectrum plot`}>
      <PlotHeader label={cfg.label} isExpanded={isExpanded} onToggleExpand={onToggleExpand}

        summary={summary && <span title={summary} style={{ color: missingCount ? '#dcdcaa' : undefined }}>{summary}</span>}

        actions={<>
          <PlotActionMenu label={cfg.label} targetRef={chartRef} contextKey={contextKey}
            getPlot={() => plotRef.current} exportActions={exportActions} onAnalysisDetails={() => analysisActions.current?.open()} onReset={viewportControl.reset} />


          <PlotExportControls hideTrigger actionsRef={exportActions} label={cfg.label} contextKey={`${contextKey}:${logY}`} scope={exportScope}
            defaultData="original" originalReason={csvReason} filteredReason={null} pngReason={pngReason}
            csvLabel={`${specMode.toUpperCase()} · native bins`}
            csvDescription="Complete native bins with Hz, linear values and source/method details. Frequency X zoom crops bin centers after estimation. Order adds each trace’s Hz × 60 / mean RPM. Log Y never transforms CSV values or removes zeros."
            onCsv={(_data, signal, includeMetadata) => downloadPlotCsv({ ...buildCsvRequest(), include_metadata: includeMetadata }, signal)}
            onPng={(signal, includeMetadata) => { const { plot, options } = getPngSource(); return downloadPlotPng(plot, { ...options, signal, includeMetadata }); }} />
          <SpectrumAnalysisDetails hideTrigger actionsRef={analysisActions} label={cfg.label} traces={traces} failures={failures}
            contextKey={contextKey} axisMode={axisMode} logY={logY} loading={pending} />
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
      <div className={styles.plotViewport}>
        <div ref={chartRef} tabIndex={0} aria-label={`Plot canvas for ${cfg.label}`} className={styles.plotCanvas} />
        <PlotStateOverlay
          loading={pending}
          hasData={hasData}
          error={error}
          emptyState={emptyState}
          onRetry={() => setRetryVersion((version) => version + 1)}
          loadingLabel="Calculating spectrum"
          updatingLabel="Recalculating spectrum"
          errorTitle="Could not calculate the spectrum"
          partialMessage={partialMessage}
        />
      </div>
    </div>
  );
};
