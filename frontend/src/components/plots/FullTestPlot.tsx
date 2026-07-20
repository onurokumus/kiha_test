import React, { useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchFiltered, fetchWindow, isAbortError } from '../../services/api';
import { DataWindow, FilteredWindow, FilterSpec, TimePlotConfig } from '../../types';
import { noSelect } from '../../constants/styles';
import { FilterUi } from '../../constants/filters';
import { FilterRow } from '../controls/FilterRow';
import { ACCENT, AXIS_STYLE, FULL_SYNC_KEY } from '../../constants/uplotTheme';
import { xPanZoomPlugin } from '../../utils/uplotPanZoom';
import { syncPlot, clearPlot } from '../../utils/uplotSync';
import styles from './TimePlot.module.css';

const FILTER_COLOR = '#dcdcaa';

interface FullTestPlotProps {
  test: string;
  cfg: TimePlotConfig;
  range: [number, number] | null;
  onRangeChange: (range: [number, number]) => void;
  onZoomReset?: () => void;
  /** THIS plot's DSP filter (null = none/incomplete params); drawn as a
   *  dashed overlay. Per-plot only (toggled by the ≈ header button; also
   *  shown when the cell is expanded). */
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
  const [win, setWin] = useState<DataWindow | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // filter overlay state (spec comes from this plot's own filter row)
  const [fwin, setFwin] = useState<FilteredWindow | null>(null);
  const [ferror, setFerror] = useState('');
  const [fbusy, setFbusy] = useState(false);
  // per-cell filter row visibility, toggled by the ≈ header button
  const [showFilter, setShowFilter] = useState(false);

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
    if (!filterSpec || !win) {
      setFwin(null);
      setFerror('');
      return;
    }
    let dead = false;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setFbusy(true);
      fetchFiltered(test, [cfg.key], filterSpec, range?.[0] ?? null, range?.[1] ?? null,
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
  }, [win, filterSpec]);

  // Overlay only when the filtered window is for the CURRENT raw window: same
  // mode and length (arrays must align 1:1 for uPlot bands) AND the same
  // [i0, i1) range. Without the range check a pan at constant zoom keeps the
  // same mode+length, so the OLD range's filtered curve would be drawn over the
  // new window until the 300 ms-debounced refetch lands (1.18).
  const overlay =
    fwin &&
    win &&
    fwin.mode === win.mode &&
    fwin.i0 === win.i0 &&
    fwin.i1 === win.i1 &&
    fwin.t.length === win.t.length
      ? fwin
      : null;

  // Destroy the uPlot instance only on unmount; data/structure changes reuse or
  // rebuild it in place via syncPlot (perf 2.4), so there is no per-run cleanup.
  useEffect(() => () => clearPlot(plotRef, structKeyRef), []);

  useEffect(() => {
    const el = chartRef.current;
    const base = win?.series[cfg.key];
    if (!el || !win || !base || box.w < 40 || box.h < 40) {
      clearPlot(plotRef, structKeyRef);
      return;
    }

    const series: uPlot.Series[] = [{}];
    const bands: uPlot.Band[] = [];
    const data: (number | null)[][] = [win.t];

    if (win.mode === 'envelope') {
      const s = base as { min: (number | null)[]; max: (number | null)[] };
      series.push(
        { label: `${cfg.key} max`, stroke: ACCENT, width: 1, spanGaps: false },
        { label: `${cfg.key} min`, stroke: ACCENT, width: 1, spanGaps: false }
      );
      bands.push({ series: [1, 2], fill: ACCENT + '40' });
      data.push(s.max, s.min);
    } else {
      series.push({ label: cfg.key, stroke: ACCENT, width: 1.5, spanGaps: false });
      data.push(base as (number | null)[]);
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

    // A NaN in the time column serializes as null in win.t; uPlot's x array
    // must be ascending numbers, so a single null corrupts the whole window.
    // Drop those samples across every parallel array (t + each series/overlay
    // column, all the same length). TimePlot filters null t the same way (1.19).
    if (win.t.some((v) => v == null)) {
      const keep: number[] = [];
      for (let i = 0; i < win.t.length; i++) if (win.t[i] != null) keep.push(i);
      for (let c = 0; c < data.length; c++) data[c] = keep.map((i) => data[c][i]);
    }

    const makeOpts = (): uPlot.Options => ({
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
      plugins: [xPanZoomPlugin((r) => onRangeChangeRef.current(r))],
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
      win.mode, overlay ? overlay.mode : 'none', cfg.key, box.w, box.h, isExpanded,
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
        {showFilter && !isExpanded && filterUi && onFilterUiChange && (
          <div
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
              title="filter for THIS plot only"
            />
          </div>
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          {win && (
            <span style={{ fontSize: 10, color: '#909090' }}>
              {win.mode === 'raw' ? 'raw' : `env 1:${win.level}`}
            </span>
          )}
          {(loading || fbusy) && <span style={{ fontSize: 10, color: '#569cd6' }}>⟳</span>}
          {filterUi && onFilterUiChange && (
            <button
              onClick={() => setShowFilter((v) => !v)}
              className={buttonClass}
              title={
                ferror ||
                (filterSpec ? 'filter active — click to edit' : 'filter this plot')
              }
            >
              <span
                className={styles.expandButtonIcon}
                style={{
                  color: ferror ? '#f48771' : filterSpec ? FILTER_COLOR : undefined,
                }}
              >
                ≈
              </span>
            </button>
          )}
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
          {filterUi && onFilterUiChange && (
            <>
              <span style={{ fontSize: 11, color: '#909090' }}>filter:</span>
              <FilterRow
                ui={filterUi}
                onChange={onFilterUiChange}
                fs={fs}
                title="filter for THIS plot only"
              />
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
