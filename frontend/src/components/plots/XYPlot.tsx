import React, { useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchXY, isAbortError } from '../../services/api';
import { SelectedTestPoint, TimePlotConfig } from '../../types';
import { noSelect } from '../../constants/styles';
import { ACCENT, AXIS_STYLE } from '../../constants/uplotTheme';
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
}

interface XYTrace {
  label: string;
  color: string;
  x: number[];
  y: number[];
  stride: number;
}

/** Variable-vs-variable scatter (x = shared column, y = this cell's column):
 *  either the active test over its zoom range, or one point cloud per
 *  selected test point (each over its own time range, in TP colors).
 *  Rendered as points via uPlot mode 2 — trajectories are not x-sorted. */
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
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [traces, setTraces] = useState<XYTrace[]>([]);
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
    if (!xCol || !cfg.key) return;
    let dead = false;
    const controller = new AbortController();
    setLoading(true);

    const load = async () => {
      try {
        if (source === 'full') {
          if (!test) return;
          const testCols = columnsByTest[test] ?? [];
          if (testCols.length && (!testCols.includes(xCol) || !testCols.includes(cfg.key))) {
            if (!dead) {
              setTraces([]);
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
                return null;
              }
            })
          );
          if (dead) return;
          setTraces(results.filter((r): r is XYTrace => r !== null));
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
  }, [test, xCol, cfg.key, range, source, tpFingerprint, columnsByTest]);

  useEffect(() => {
    const el = chartRef.current;
    plotRef.current?.destroy();
    plotRef.current = null;
    if (!el || traces.length === 0 || box.w < 40 || box.h < 40) return;

    const pointsPaths = uPlot.paths.points!();
    const opts: uPlot.Options = {
      mode: 2,
      width: box.w,
      height: box.h,
      scales: { x: { time: false }, y: {} },
      axes: [{ ...AXIS_STYLE }, { ...AXIS_STYLE }],
      legend: { show: isExpanded, live: true },
      cursor: { drag: { x: false, y: false } },
      series: [
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
      ],
    };

    const data = [
      null,
      ...traces.map((tr) => [tr.x, tr.y]),
    ] as unknown as uPlot.AlignedData;
    const u = new uPlot(opts, data, el);
    plotRef.current = u;

    if (isExpanded) {
      const legend = u.root.querySelector('.u-legend') as HTMLElement | null;
      const legendH = legend?.offsetHeight ?? 0;
      if (legendH > 0) {
        u.setSize({ width: box.w, height: Math.max(60, box.h - legendH) });
      }
    }

    return () => {
      plotRef.current?.destroy();
      plotRef.current = null;
    };
  }, [traces, box, isExpanded]);

  const containerClass = `${styles.plotContainer} ${
    isExpanded ? styles.plotContainerExpanded : styles.plotContainerCollapsed
  }`;

  const buttonClass = `${styles.expandButton} ${
    isExpanded ? styles.expandButtonExpanded : styles.expandButtonCollapsed
  }`;

  const maxStride = traces.reduce((m, tr) => Math.max(m, tr.stride), 0);
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
          {cfg.label} <span style={{ color: '#909090' }}>vs {xCol}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          {traces.length > 0 && (
            <span style={{ fontSize: 10, color: '#909090' }}>
              {source === 'tp' ? `${traces.length} TP${traces.length === 1 ? '' : 's'} · ` : ''}
              1:{maxStride}
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
