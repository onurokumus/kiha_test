import React, { useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchSpectrum, isAbortError } from '../../services/api';
import {
  SelectedTestPoint,
  SpectrumData,
  SpectrumXAxis,
  TimePlotConfig,
} from '../../types';
import { noSelect } from '../../constants/styles';
import { ACCENT, AXIS_STYLE, safeRange } from '../../constants/uplotTheme';
import { xPanZoomPlugin } from '../../utils/uplotPanZoom';
import { syncPlot, clearPlot, facetedSeriesValue, sortedFacetedDataIdx } from '../../utils/uplotSync';
import { PlotStateOverlay, PlotEmptyState } from './PlotState';
import { SearchableSelect } from '../controls/SearchableSelect';
import styles from './TimePlot.module.css';

export type PanelSource = 'tp' | 'full';

interface SpectrumPlotProps {
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
}

interface SpectrumTrace {
  label: string;
  color: string;
  x: (number | null)[];
  mag: (number | null)[];
  meanRpm?: number;
  minRpm?: number;
  maxRpm?: number;
}

const EMPTY_TRACES: SpectrumTrace[] = [];

interface RpmStats {
  mean: number;
  min: number;
  max: number;
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
  color: string
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
    label,
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
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const structKeyRef = useRef('');
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [loadedTraces, setTraces] = useState<SpectrumTrace[]>([]);
  const [loadedContext, setLoadedContext] = useState('');
  const [loadedMeta, setMeta] = useState<{
    mode: string;
    n: number;
    nan: number;
    rpm: RpmStats[];
  } | null>(null);
  const [loading, setLoading] = useState(
    Boolean(cfg.key && (source === 'full' ? test : selectedTPs.length))
  );
  const [error, setError] = useState('');
  const [partialMessage, setPartialMessage] = useState('');
  const [retryVersion, setRetryVersion] = useState(0);

  const visibleTPs = selectedTPs.filter((s) => !hiddenTPs.has(s.id));
  const tpFingerprint = JSON.stringify(
    visibleTPs.map((s) => [s.id, s.tp.start_s, s.endS, s.name, s.color])
  );
  const contextKey = JSON.stringify([
    source, cfg.key, specMode, axisMode, rpmColumn,
    source === 'full' ? test : tpFingerprint,
  ]);
  const traces = loadedContext === contextKey ? loadedTraces : EMPTY_TRACES;
  const meta = loadedContext === contextKey ? loadedMeta : null;
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
    if (!cfg.key || needsRpmColumn || (source === 'full' && !test)) {
      setTraces([]);
      setMeta(null);
      setLoading(false);
      setError('');
      setPartialMessage('');
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
          const resolvedRpmColumn =
            axisMode === 'per_rev'
              ? resolveColumn(columnsByTest[test] ?? [], rpmColumn)
              : null;
          if (axisMode === 'per_rev' && !resolvedRpmColumn) {
            throw new Error(`RPM variable '${rpmColumn}' is not available in ${test}.`);
          }
          const d = await fetchSpectrum(
            test, cfg.key, specMode, range?.[0] ?? null, range?.[1] ?? null,
            resolvedRpmColumn,
            controller.signal
          );
          if (dead) return;
          const trace = traceFromSpectrum(d, axisMode, cfg.key, ACCENT);
          setLoadedContext(contextKey);
          setTraces([trace]);
          setMeta({
            mode: d.mode,
            n: d.n_samples,
            nan: d.nan_count,
            rpm:
              trace.meanRpm === undefined
                ? []
                : [{
                    mean: trace.meanRpm,
                    min: trace.minRpm ?? trace.meanRpm,
                    max: trace.maxRpm ?? trace.meanRpm,
                  }],
          });
          setPartialMessage('');
        } else {
          const eligible = visibleTPs.filter((s) =>
            (columnsByTest[s.test] ?? []).includes(cfg.key)
          );
          let failed = 0;
          const failureMessages: string[] = [];
          const results = await Promise.all(
            eligible.map(async (s) => {
              try {
                const resolvedRpmColumn =
                  axisMode === 'per_rev'
                    ? resolveColumn(columnsByTest[s.test] ?? [], rpmColumn)
                    : null;
                if (axisMode === 'per_rev' && !resolvedRpmColumn) {
                  throw new Error(
                    `RPM variable '${rpmColumn}' is not available in ${s.test}.`
                  );
                }
                const d: SpectrumData = await fetchSpectrum(
                  s.test, cfg.key, specMode, s.tp.start_s, s.endS,
                  resolvedRpmColumn,
                  controller.signal
                );
                return traceFromSpectrum(
                  d, axisMode, `${s.name} · ${s.test}`, s.color
                );
              } catch (e) {
                if (isAbortError(e)) throw e;
                console.error(`spectrum failed for ${s.id}/${cfg.key}:`, e);
                failed += 1;
                failureMessages.push(String(e instanceof Error ? e.message : e));
                return null;
              }
            })
          );
          if (dead) return;
          const ok = results.filter((r): r is SpectrumTrace => r !== null);
          if (failed > 0 && ok.length === 0) {
            const uniqueMessages = Array.from(new Set(failureMessages.filter(Boolean)));
            if (uniqueMessages.length === 1) throw new Error(uniqueMessages[0]);
            throw new Error(
              `Spectrum data was unavailable for ${failed} selected test point${failed === 1 ? '' : 's'}.`
            );
          }
          setLoadedContext(contextKey);
          setTraces(ok);
          setMeta(ok.length ? {
            mode: specMode,
            n: ok.length,
            nan: 0,
            rpm: ok.flatMap((trace) =>
              trace.meanRpm === undefined
                ? []
                : [{
                    mean: trace.meanRpm,
                    min: trace.minRpm ?? trace.meanRpm,
                    max: trace.maxRpm ?? trace.meanRpm,
                  }]
            ),
          } : null);
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
  }, [
    test,
    cfg.key,
    specMode,
    axisMode,
    rpmColumn,
    range,
    source,
    tpFingerprint,
    columnsByTest,
    retryVersion,
    needsRpmColumn,
    contextKey,
  ]);

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
        y: { range: safeRange as uPlot.Scale.Range },
      },
      axes: [
        {
          ...AXIS_STYLE,
          label: axisMode === 'per_rev' ? 'Order (cycles/rev)' : 'Frequency (Hz)',
        },
        { ...AXIS_STYLE, label: `${specMode === 'welch' ? 'Power spectral density' : 'Magnitude'}${logY ? ' (log10)' : ''}` },
      ],
      legend: { show: isExpanded, live: true },
      // uPlot default drag = client-side x zoom; dblclick resets it.
      // Wheel-zoom / shift-drag pan are client-side too (no commit target).
      cursor: { dataIdx: sortedFacetedDataIdx, drag: { x: true, y: false } },
      plugins: [xPanZoomPlugin()],
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
  }, [traces, logY, box, isExpanded, axisMode, specMode]);

  const containerClass = `${styles.plotContainer} ${
    isExpanded ? styles.plotContainerExpanded : styles.plotContainerCollapsed
  }`;

  const buttonClass = `${styles.expandButton} ${
    isExpanded ? styles.expandButtonExpanded : styles.expandButtonCollapsed
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

  const rpmMeans = meta?.rpm.map((stats) => stats.mean) ?? [];
  const formatRpm = (value: number) =>
    value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  const rpmSummary =
    rpmMeans.length === 0
      ? ''
      : rpmMeans.length === 1
        ? `mean ${formatRpm(rpmMeans[0])} rpm`
        : `mean ${formatRpm(Math.min(...rpmMeans))}-${formatRpm(Math.max(...rpmMeans))} rpm`;
  const rpmDetail = meta?.rpm.length
    ? meta.rpm
        .map(
          (stats, index) =>
            `${traces[index]?.label ?? `Trace ${index + 1}`}: mean ${formatRpm(stats.mean)} rpm ` +
            `(range ${formatRpm(stats.min)}-${formatRpm(stats.max)} rpm)`
        )
        .join('\n')
    : undefined;

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
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          {meta && (
            <span style={{ fontSize: 10, color: '#909090' }} title={rpmDetail}>
              {source === 'full'
                ? `${meta.mode} · ${meta.n.toLocaleString()} pts${meta.nan > 0 ? ` · ⚠${meta.nan} NaN` : ''}${rpmSummary ? ` · ${rpmSummary}` : ''}`
                : `${meta.mode} · ${meta.n} TP${meta.n === 1 ? '' : 's'}${rpmSummary ? ` · ${rpmSummary}` : ''}`}
            </span>
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
      <div className={styles.plotViewport}>
        <div ref={chartRef} className={styles.plotCanvas} />
        <PlotStateOverlay
          loading={loading}
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
