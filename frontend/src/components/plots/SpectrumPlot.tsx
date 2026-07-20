import React, { useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchSpectrum, isAbortError } from '../../services/api';
import { SelectedTestPoint, SpectrumData, TimePlotConfig } from '../../types';
import { noSelect } from '../../constants/styles';
import { ACCENT, AXIS_STYLE, safeRange } from '../../constants/uplotTheme';
import { xPanZoomPlugin } from '../../utils/uplotPanZoom';
import { syncPlot, clearPlot } from '../../utils/uplotSync';
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
  freqs: (number | null)[];
  mag: (number | null)[];
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
  const [traces, setTraces] = useState<SpectrumTrace[]>([]);
  const [meta, setMeta] = useState<{ mode: string; n: number; nan: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

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
    if (!cfg.key) return;
    let dead = false;
    const controller = new AbortController();
    setLoading(true);

    const load = async () => {
      try {
        if (source === 'full') {
          if (!test) return;
          const d = await fetchSpectrum(
            test, cfg.key, specMode, range?.[0] ?? null, range?.[1] ?? null,
            controller.signal
          );
          if (dead) return;
          setTraces([
            { label: cfg.key, color: ACCENT, freqs: d.freqs, mag: d.mag },
          ]);
          setMeta({ mode: d.mode, n: d.n_samples, nan: d.nan_count });
        } else {
          const eligible = visibleTPs.filter((s) =>
            (columnsByTest[s.test] ?? []).includes(cfg.key)
          );
          const results = await Promise.all(
            eligible.map(async (s) => {
              try {
                const d: SpectrumData = await fetchSpectrum(
                  s.test, cfg.key, specMode, s.tp.start_s, s.endS,
                  controller.signal
                );
                return {
                  label: `${s.name} · ${s.test}`,
                  color: s.color,
                  freqs: d.freqs,
                  mag: d.mag,
                };
              } catch (e) {
                if (isAbortError(e)) throw e;
                console.error(`spectrum failed for ${s.id}/${cfg.key}:`, e);
                return null;
              }
            })
          );
          if (dead) return;
          const ok = results.filter((r): r is SpectrumTrace => r !== null);
          setTraces(ok);
          setMeta(ok.length ? { mode: specMode, n: ok.length, nan: 0 } : null);
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
  }, [test, cfg.key, specMode, range, source, tpFingerprint, columnsByTest]);

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
      // uPlot default drag = client-side x zoom; dblclick resets it.
      // Wheel-zoom / shift-drag pan are client-side too (no commit target).
      cursor: { drag: { x: true, y: false } },
      plugins: [xPanZoomPlugin()],
      series,
    });

    const data = [
      null,
      ...traces.map((tr) => [tr.freqs, transform(tr.mag)]),
    ] as unknown as uPlot.AlignedData;

    // logY is a data transform, not a structure change → excluded from the key,
    // so toggling it re-ranges (setData) instead of rebuilding.
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
  }, [traces, logY, box, isExpanded]);

  const containerClass = `${styles.plotContainer} ${
    isExpanded ? styles.plotContainerExpanded : styles.plotContainerCollapsed
  }`;

  const buttonClass = `${styles.expandButton} ${
    isExpanded ? styles.expandButtonExpanded : styles.expandButtonCollapsed
  }`;

  const emptyHint =
    source === 'tp' && visibleTPs.length === 0
      ? 'Select test points on the scatter plot'
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
            <span style={{ fontSize: 10, color: '#909090' }}>
              {source === 'full'
                ? `${meta.mode} · ${meta.n.toLocaleString()} pts${meta.nan > 0 ? ` · ⚠${meta.nan} NaN` : ''}`
                : `${meta.mode} · ${meta.n} TP${meta.n === 1 ? '' : 's'}`}
            </span>
          )}
          {loading && <span style={{ fontSize: 10, color: '#569cd6' }}>⟳</span>}
          <button onClick={onToggleExpand} className={buttonClass}>
            <span className={styles.expandButtonIcon}>{isExpanded ? '▪' : '▣'}</span>
          </button>
        </div>
        {isEditMode && !isExpanded && allConfigs.length > 0 && (
          <select
            value={cfg.key}
            onChange={(e) => onConfigChange?.(e.target.value)}
            style={{
              position: 'absolute',
              left: 0,
              top: -2,
              background: '#1e1e1e',
              color: '#e0e0e0',
              border: '1px solid #3c3c3c',
              borderRadius: 3,
              padding: '2px 6px',
              fontSize: 11,
              cursor: 'pointer',
              outline: 'none',
              fontFamily: 'Segoe UI, sans-serif',
              zIndex: 5,
            }}
          >
            {allConfigs.map((config) => (
              <option key={config.key} value={config.key}>
                {config.label}
              </option>
            ))}
          </select>
        )}
      </div>
      <div ref={chartRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }} title={emptyHint}>
        {error && <div style={{ color: '#f48771', fontSize: 11, padding: 8 }}>{error}</div>}
        {!error && emptyHint && (
          <div style={{ color: '#555', fontSize: 11, padding: 8 }}>no selection</div>
        )}
      </div>
    </div>
  );
};
