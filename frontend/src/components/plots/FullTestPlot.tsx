import React, { useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchFiltered, fetchWindow, isAbortError } from '../../services/api';
import { DataWindow, FilteredWindow, FilterKind, FilterSpec, TimePlotConfig } from '../../types';
import { noSelect } from '../../constants/styles';
import { ACCENT, AXIS_STYLE, FULL_SYNC_KEY } from '../../constants/uplotTheme';
import styles from './TimePlot.module.css';

const FILTER_LABELS: Record<FilterKind, string> = {
  lowpass: 'low-pass',
  highpass: 'high-pass',
  bandpass: 'band-pass',
  bandstop: 'band-stop',
  moving_avg: 'moving avg',
  detrend: 'detrend',
};

const FILTER_COLOR = '#dcdcaa';

interface FullTestPlotProps {
  test: string;
  cfg: TimePlotConfig;
  range: [number, number] | null;
  onRangeChange: (range: [number, number]) => void;
  onZoomReset?: () => void;
  /** Sample rate, for the Nyquist hint in the filter row. */
  fs?: number | null;
  isExpanded: boolean;
  onToggleExpand: () => void;
  isEditMode?: boolean;
  allConfigs?: TimePlotConfig[];
  onConfigChange?: (newKey: string) => void;
}

/** Full-test time plot: one column over the whole test, served by windowed
 *  pyramid reads. Zoomed out the backend sends a min/max envelope (drawn as
 *  a band); zoomed in far enough it sends raw 2 kHz samples. Every zoom is
 *  a re-fetch, so spikes can never be lost to client-side decimation. */
export const FullTestPlot: React.FC<FullTestPlotProps> = ({
  test,
  cfg,
  range,
  onRangeChange,
  onZoomReset,
  fs,
  isExpanded,
  onToggleExpand,
  isEditMode = false,
  allConfigs = [],
  onConfigChange,
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const pxRef = useRef(1200);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [win, setWin] = useState<DataWindow | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // filter overlay state (controls shown when expanded)
  const [fkind, setFkind] = useState<'' | FilterKind>('');
  const [order, setOrder] = useState('4');
  const [f1, setF1] = useState('');
  const [f2, setF2] = useState('');
  const [winS, setWinS] = useState('1');
  const [fwin, setFwin] = useState<FilteredWindow | null>(null);
  const [ferror, setFerror] = useState('');
  const [fbusy, setFbusy] = useState(false);

  const spec: FilterSpec | null = (() => {
    if (!fkind) return null;
    if (fkind === 'detrend') return { kind: fkind };
    if (fkind === 'moving_avg') {
      const w = Number(winS);
      return w > 0 ? { kind: fkind, windowS: w } : null;
    }
    const o = Math.min(Math.max(Math.round(Number(order) || 4), 1), 10);
    const a = Number(f1);
    if (!(a > 0)) return null;
    if (fkind === 'lowpass' || fkind === 'highpass')
      return { kind: fkind, order: o, f1: a };
    const b = Number(f2);
    return b > a ? { kind: fkind, order: o, f1: a, f2: b } : null;
  })();

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
    if (!test || !cfg.key) return;
    let dead = false;
    const controller = new AbortController();
    setLoading(true);
    const px = chartRef.current?.clientWidth || 1200;
    pxRef.current = px;
    const timer = window.setTimeout(() => {
      fetchWindow(test, [cfg.key], range?.[0] ?? null, range?.[1] ?? null, px, controller.signal)
        .then((w) => {
          if (!dead) {
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
  }, [test, cfg.key, range]);

  // fetch the filtered overlay; keyed on win so it reuses the same px + range
  useEffect(() => {
    if (!spec || !win) {
      setFwin(null);
      setFerror('');
      return;
    }
    let dead = false;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setFbusy(true);
      fetchFiltered(test, [cfg.key], spec, range?.[0] ?? null, range?.[1] ?? null,
        pxRef.current, controller.signal)
        .then((w) => {
          if (!dead) {
            setFwin(w);
            setFerror('');
          }
        })
        .catch((e) => {
          if (!dead && !isAbortError(e)) {
            setFwin(null);
            setFerror(String(e instanceof Error ? e.message : e));
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
  }, [win, fkind, order, f1, f2, winS]);

  // overlay only when the server produced the identical time axis
  const overlay =
    fwin && win && fwin.mode === win.mode && fwin.t.length === win.t.length
      ? fwin
      : null;

  useEffect(() => {
    const el = chartRef.current;
    plotRef.current?.destroy();
    plotRef.current = null;
    if (!el || !win || box.w < 40 || box.h < 40) return;

    const series: uPlot.Series[] = [{}];
    const bands: uPlot.Band[] = [];
    const data: (number | null)[][] = [win.t];

    if (win.mode === 'envelope') {
      const s = win.series[cfg.key];
      if (!s) return;
      series.push(
        { label: `${cfg.key} max`, stroke: ACCENT, width: 1, spanGaps: false },
        { label: `${cfg.key} min`, stroke: ACCENT, width: 1, spanGaps: false }
      );
      bands.push({ series: [1, 2], fill: ACCENT + '40' });
      data.push(s.max, s.min);
    } else {
      const s = win.series[cfg.key];
      if (!s) return;
      series.push({ label: cfg.key, stroke: ACCENT, width: 1.5, spanGaps: false });
      data.push(s);
    }

    if (overlay) {
      if (overlay.mode === 'envelope') {
        const s = overlay.series[cfg.key] as { min: (number | null)[]; max: (number | null)[] };
        series.push(
          { label: `${cfg.key} filt max`, stroke: FILTER_COLOR, width: 1.5, dash: [6, 4], spanGaps: false },
          { label: `${cfg.key} filt min`, stroke: FILTER_COLOR, width: 1.5, dash: [6, 4], spanGaps: false }
        );
        data.push(s.max, s.min);
      } else {
        const s = overlay.series[cfg.key] as (number | null)[];
        series.push({
          label: `${cfg.key} filt`,
          stroke: FILTER_COLOR,
          width: 2,
          dash: [6, 4],
          spanGaps: false,
        });
        data.push(s);
      }
    }

    const opts: uPlot.Options = {
      width: box.w,
      height: box.h,
      series,
      bands,
      scales: { x: { time: false } },
      axes: [{ ...AXIS_STYLE }, { ...AXIS_STYLE }],
      legend: { show: isExpanded, live: true },
      cursor: {
        drag: { x: true, y: false },
        points: { size: 6 },
        sync: { key: FULL_SYNC_KEY, scales: ['x', null] },
      },
      hooks: {
        setSelect: [
          (u) => {
            if (u.select.width > 10) {
              const t0 = u.posToVal(u.select.left, 'x');
              const t1 = u.posToVal(u.select.left + u.select.width, 'x');
              u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
              onRangeChange([t0, t1]);
            }
          },
        ],
      },
    };

    const u = new uPlot(opts, data as uPlot.AlignedData, el);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [win, overlay, box, isExpanded]);

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
          gap: 6,
        }}
      >
        <div style={{ fontSize: 12, color: '#c0c0c0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {cfg.label}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          {win && (
            <span style={{ fontSize: 10, color: '#909090' }}>
              {win.mode === 'raw' ? 'raw' : `env 1:${win.level}`}
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
      {isExpanded && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 4 }}>
          <span style={{ fontSize: 11, color: '#909090' }}>filter:</span>
          <select
            className="input"
            style={{ width: 110 }}
            value={fkind}
            onChange={(e) => setFkind(e.target.value as '' | FilterKind)}
          >
            <option value="">— none —</option>
            {(Object.keys(FILTER_LABELS) as FilterKind[]).map((k) => (
              <option key={k} value={k}>
                {FILTER_LABELS[k]}
              </option>
            ))}
          </select>
          {(fkind === 'lowpass' || fkind === 'highpass' || fkind === 'bandpass' || fkind === 'bandstop') && (
            <>
              <span style={{ fontSize: 11, color: '#909090' }}>order</span>
              <input className="input" style={{ width: 40 }} value={order}
                     onChange={(e) => setOrder(e.target.value)} />
              <input className="input" style={{ width: 65 }} placeholder="f1 Hz"
                     value={f1} onChange={(e) => setF1(e.target.value)} />
              {(fkind === 'bandpass' || fkind === 'bandstop') && (
                <input className="input" style={{ width: 65 }} placeholder="f2 Hz"
                       value={f2} onChange={(e) => setF2(e.target.value)} />
              )}
              {fs && (
                <span style={{ fontSize: 10, color: '#909090' }}>Nyquist {fs / 2} Hz</span>
              )}
            </>
          )}
          {fkind === 'moving_avg' && (
            <>
              <input className="input" style={{ width: 50 }} value={winS}
                     onChange={(e) => setWinS(e.target.value)} />
              <span style={{ fontSize: 11, color: '#909090' }}>s window</span>
            </>
          )}
          {fbusy && <span style={{ fontSize: 10, color: '#569cd6' }}>⟳</span>}
          {overlay && <span className="badge">filtered overlay (dashed)</span>}
          {fwin?.boundary_warning && (
            <span style={{ fontSize: 10, color: '#dcdcaa' }}>
              ⚠ range touches data edge — filter transients possible
            </span>
          )}
          {ferror && <span style={{ color: '#f48771', fontSize: 10 }}>{ferror}</span>}
        </div>
      )}
      <div
        ref={chartRef}
        style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}
        onDoubleClick={onZoomReset}
      >
        {error && <div style={{ color: '#f48771', fontSize: 11, padding: 8 }}>{error}</div>}
      </div>
    </div>
  );
};
