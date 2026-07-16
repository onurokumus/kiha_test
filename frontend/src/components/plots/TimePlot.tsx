import React, { useEffect, useRef, useState, useMemo } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { SelectedTestPoint, TimePlotConfig } from '../../types';
import { noSelect } from '../../constants/styles';
import { AXIS_STYLE, TP_SYNC_KEY } from '../../constants/uplotTheme';
import styles from './TimePlot.module.css';

interface TimePlotProps {
  cfg: TimePlotConfig;
  selectedTPs: SelectedTestPoint[];
  hiddenTPs: Set<string>;
  isExpanded: boolean;
  onToggleExpand: () => void;
  zoomDomain: [number, number] | null;
  onZoomChange: (domain: [number, number]) => void;
  onZoomReset?: () => void;
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

/** TP-overlay time plot: one line per selected test point, relative time
 *  from TP start. uPlot mode 2 (facets) — each series keeps its own time
 *  array, so TPs of different lengths overlay without resampling. */
export const TimePlot: React.FC<TimePlotProps> = ({
  cfg,
  selectedTPs,
  hiddenTPs,
  isExpanded,
  onToggleExpand,
  zoomDomain,
  onZoomChange,
  onZoomReset,
  isEditMode = false,
  allConfigs = [],
  onConfigChange,
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });

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

  useEffect(() => {
    const el = chartRef.current;
    plotRef.current?.destroy();
    plotRef.current = null;
    if (!el || traces.length === 0 || box.w < 40 || box.h < 40) return;

    const opts: uPlot.Options = {
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
      axes: [{ ...AXIS_STYLE }, { ...AXIS_STYLE, scale: 'y' }],
      legend: { show: isExpanded, live: true },
      cursor: {
        drag: { x: true, y: false },
        sync: { key: TP_SYNC_KEY, scales: ['x', null] },
      },
      series: [
        {},
        ...traces.map(
          (trace) =>
            ({
              label: trace.label,
              stroke: trace.color,
              width: 1.5,
              spanGaps: false,
              facets: [
                { scale: 'x', auto: true },
                { scale: 'y', auto: true },
              ],
            }) as uPlot.Series
        ),
      ],
      hooks: {
        setSelect: [
          (u) => {
            if (u.select.width > 10) {
              const t0 = u.posToVal(u.select.left, 'x');
              const t1 = u.posToVal(u.select.left + u.select.width, 'x');
              u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
              onZoomChange([t0, t1]);
            }
          },
        ],
      },
    };

    const data = [null, ...traces.map((tr) => [tr.t, tr.y])] as unknown as uPlot.AlignedData;
    const u = new uPlot(opts, data, el);
    plotRef.current = u;

    // The legend renders inside the container and eats chart height.
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [traces, zoomDomain, box, isExpanded]);

  const containerClass = `${styles.plotContainer} ${
    isExpanded ? styles.plotContainerExpanded : styles.plotContainerCollapsed
  }`;

  const buttonClass = `${styles.expandButton} ${
    isExpanded ? styles.expandButtonExpanded : styles.expandButtonCollapsed
  }`;

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
        <button onClick={onToggleExpand} className={buttonClass}>
          <span className={styles.expandButtonIcon}>{isExpanded ? '▪' : '▣'}</span>
        </button>
      </div>
      <div
        ref={chartRef}
        style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}
        onDoubleClick={onZoomReset}
        title={traces.length === 0 ? 'Select test points on the scatter plot' : undefined}
      >
        {traces.length === 0 && (
          <div style={{ color: '#555', fontSize: 11, padding: 8 }}>no selection</div>
        )}
      </div>
    </div>
  );
};
