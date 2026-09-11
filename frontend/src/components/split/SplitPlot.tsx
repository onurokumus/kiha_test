import React, { useCallback, useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchWindow, isAbortError } from '../../services/api';
import { DataWindow, TestPoint } from '../../types';
import {
  AXIS_STYLE,
  colorFor,
  TIME_AXIS_STYLE,
} from '../../constants/uplotTheme';
import { round3 } from '../../utils/formatters';
import { visibleYRange } from '../../utils/visibleYRange';
import { SearchableSelect } from '../controls/SearchableSelect';
import { PlotStateOverlay } from '../plots/PlotState';
import styles from './SplitPlot.module.css';

export type TimeRange = [number, number] | null;

interface Props {
  test: string;
  column: string;
  columns: string[];
  plotNumber: number;
  canRemove: boolean;
  onColumnChange: (column: string) => void;
  onRemove: () => void;
  syncKey: string;
  range: TimeRange;
  onRangeChange: (r: TimeRange) => void;
  tps: TestPoint[];
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  onChangeTp: (id: number, patch: Partial<TestPoint>) => void;
  dataStart: number;
  dataEnd: number;
}

interface OverlayBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface WindowState {
  key: string;
  status: 'loading' | 'ready' | 'error';
  data: DataWindow | null;
  error: string;
}

const PLOT_HEIGHT = 300;

/** end of a TP for display: own end, else next TP start, else data end */
// This shared geometry helper intentionally lives beside the component that
// owns the TP overlay semantics.
// eslint-disable-next-line react-refresh/only-export-components
export function effectiveEnd(tp: TestPoint, tps: TestPoint[], dataEnd: number): number {
  if (tp.end_s !== null) return tp.end_s;
  const nexts = tps
    .filter((o) => o.id !== tp.id && o.start_s > tp.start_s)
    .map((o) => o.start_s);
  return nexts.length ? Math.min(...nexts) : dataEnd;
}

/** Windowed test plot with test-point regions drawn as an HTML overlay:
 *  click a TP label to select it, then drag its start/end handles. */
export default function SplitPlot(props: Props) {
  const { test, column, columns, plotNumber, canRemove, onColumnChange, onRemove,
          syncKey, range, onRangeChange, tps, selectedId, onSelect,
          onChangeTp, dataStart, dataEnd } = props;
  const wrapperRef = useRef<HTMLDivElement>(null); // positioning reference
  const containerRef = useRef<HTMLDivElement>(null); // uPlot mount target
  const plotRef = useRef<uPlot | null>(null);
  const [windowState, setWindowState] = useState<WindowState | null>(null);
  const [box, setBox] = useState<OverlayBox | null>(null);
  const [chartTick, setChartTick] = useState(0); // bumps when chart rebuilt
  const [retryVersion, setRetryVersion] = useState(0);
  const rangeStart = range?.[0] ?? null;
  const rangeEnd = range?.[1] ?? null;
  const requestKey = JSON.stringify([test, column, rangeStart, rangeEnd, retryVersion]);
  const currentWindow = windowState?.key === requestKey ? windowState : null;
  const win = currentWindow?.status === 'ready' ? currentWindow.data : null;
  const loading = Boolean(test && column && (!currentWindow || currentWindow.status === 'loading'));
  const error = currentWindow?.error ?? '';
  const contextRef = useRef({ requestKey, ready: Boolean(win), onRangeChange });
  contextRef.current = { requestKey, ready: Boolean(win), onRangeChange };
  const chartContextRef = useRef<string | null>(null);
  const dragRef = useRef<{
    id: number;
    which: 'start' | 'end';
    pointerId: number;
    requestKey: string;
  } | null>(null);

  // All cards use the same exact bounds, including windows between samples.
  // Deriving X from each response's first/last sample misaligns envelopes and
  // raw traces. A zero-duration source still needs a usable display interval.
  const xMin = rangeStart ?? dataStart;
  const requestedMax = rangeEnd ?? dataEnd;
  const xMax = requestedMax > xMin ? requestedMax : xMin + Math.max(1, Math.abs(xMin) * 1e-6);

  // measure plot area after layout settles (sync measure returns 0x0)
  const measure = useCallback(() => {
    const u = plotRef.current;
    const w = wrapperRef.current;
    if (!u || !w) return;
    const o = u.over.getBoundingClientRect();
    const wr = w.getBoundingClientRect();
    if (o.width === 0) return;
    const next = { left: o.left - wr.left, top: o.top - wr.top,
                   width: o.width, height: o.height };
    setBox((previous) => previous && previous.left === next.left && previous.top === next.top &&
      previous.width === next.width && previous.height === next.height ? previous : next);
  }, []);

  // fetch window
  useEffect(() => {
    if (!test || !column) return;
    let dead = false;
    const controller = new AbortController();
    setWindowState({ key: requestKey, status: 'loading', data: null, error: '' });
    const px = Math.max(1, containerRef.current?.clientWidth ?? 1500);
    fetchWindow(test, [column], rangeStart, rangeEnd, px, controller.signal)
      .then((data) => {
        if (!dead) setWindowState({ key: requestKey, status: 'ready', data, error: '' });
      })
      .catch((e) => {
        if (!dead && !isAbortError(e)) {
          setWindowState({ key: requestKey, status: 'error', data: null,
            error: String(e instanceof Error ? e.message : e) });
        }
      });
    return () => {
      dead = true;
      controller.abort();
    };
  }, [test, column, rangeStart, rangeEnd, requestKey]);

  // (re)build chart
  useEffect(() => {
    dragRef.current = null;
    setBox(null);
    if (!win || !containerRef.current) return;
    const width = Math.max(1, containerRef.current.clientWidth);

    const series: uPlot.Series[] = [{}];
    const bands: uPlot.Band[] = [];
    const data: (number | null)[][] = [win.t];
    // Keep the trace distinct from the blue TP overlays on every card.
    const color = colorFor(1);
    const empty = () => win.t.map(() => null);
    if (win.mode === 'envelope') {
      const envelope = win.series[column];
      series.push(
        { label: `${column} max`, stroke: color, width: 1, spanGaps: false },
        { label: `${column} min`, stroke: color, width: 1, spanGaps: false }
      );
      bands.push({ series: [1, 2], fill: color + '40' });
      data.push(envelope?.max ?? empty(), envelope?.min ?? empty());
    } else {
      series.push({ label: column, stroke: color, width: 1.5, spanGaps: false });
      data.push(win.series[column] ?? empty());
    }

    // A NaN in the time column serializes as null in win.t; uPlot's x array
    // must be ascending numbers, so drop those samples across every parallel
    // array (t + each series column). Matches TimePlot / FullTestPlot (1.19).
    if (win.t.some((v) => v === null || !Number.isFinite(v))) {
      const keep: number[] = [];
      for (let i = 0; i < win.t.length; i++) {
        if (win.t[i] !== null && Number.isFinite(win.t[i])) keep.push(i);
      }
      for (let c = 0; c < data.length; c++) data[c] = keep.map((i) => data[c][i]);
    }

    const u = new uPlot(
      {
        width, height: PLOT_HEIGHT, series, bands,
        scales: {
          x: { time: false, min: xMin, max: xMax, range: [xMin, xMax] },
          y: { range: visibleYRange },
        },
        // Fixed axis and outer padding keep absolute X/TP positions aligned
        // even when variables have very different numerical magnitudes.
        axes: [{ ...TIME_AXIS_STYLE, size: 44 }, { ...AXIS_STYLE, size: 84, stroke: color }],
        padding: [20, 16, 0, 0],
        legend: { show: false },
        cursor: {
          y: false,
          drag: { x: true, y: false, setScale: false },
          sync: {
            key: syncKey,
            scales: ['x', null],
            setSeries: false,
            // Only cursor motion is synchronized. The parent owns range
            // updates so selection events cannot trigger duplicate requests.
            filters: { pub: (type) => type === 'mousemove', sub: (type) => type === 'mousemove' },
          },
        },
        hooks: {
          setSelect: [
            (u2) => {
              const context = contextRef.current;
              if (context.ready && context.requestKey === requestKey && u2.select.width > 10) {
                const t0 = u2.posToVal(u2.select.left, 'x');
                const t1 = u2.posToVal(u2.select.left + u2.select.width, 'x');
                u2.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
                context.onRangeChange([t0, t1]);
              }
            },
          ],
        },
      },
      data as uPlot.AlignedData,
      containerRef.current
    );
    plotRef.current = u;
    chartContextRef.current = requestKey;
    // Explicitly seed X for a successful but empty response as well.
    u.setScale('x', { min: xMin, max: xMax });

    const raf = requestAnimationFrame(() => {
      measure();
      setChartTick((n) => n + 1);
    });

    return () => {
      cancelAnimationFrame(raf);
      u.destroy();
      if (plotRef.current === u) {
        plotRef.current = null;
        chartContextRef.current = null;
      }
      dragRef.current = null;
    };
  }, [win, column, syncKey, requestKey, xMin, xMax, measure]);

  // remeasure on container resize
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let frame = 0;
    const ro = new ResizeObserver(() => {
      const u = plotRef.current;
      if (u && el.clientWidth > 0) {
        u.setSize({ width: el.clientWidth, height: PLOT_HEIGHT });
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(measure);
      }
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [measure]);

  // drag handles
  useEffect(() => {
    const move = (e: PointerEvent) => {
      const d = dragRef.current;
      const u = plotRef.current;
      if (!d || !u) return;
      const context = contextRef.current;
      if (!context.ready || d.requestKey !== context.requestKey || (e.buttons & 1) === 0) {
        dragRef.current = null;
        return;
      }
      if (e.pointerId !== d.pointerId) return;
      let v = u.posToVal(e.clientX - u.over.getBoundingClientRect().left, 'x');
      if (!Number.isFinite(v)) return;
      const tp = tps.find((x) => x.id === d.id);
      if (!tp) return;
      if (d.which === 'start') {
        const hi = tp.end_s !== null ? tp.end_s - 0.01 : dataEnd - 0.01;
        v = Math.min(Math.max(v, dataStart), hi);
        onChangeTp(d.id, { start_s: round3(v) });
      } else {
        v = Math.min(Math.max(v, tp.start_s + 0.01), dataEnd);
        onChangeTp(d.id, { end_s: round3(v) });
      }
    };
    const up = () => { dragRef.current = null; };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
    window.addEventListener('blur', up);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      window.removeEventListener('blur', up);
    };
  }, [tps, onChangeTp, dataStart, dataEnd]);

  const startDrag = (id: number, which: 'start' | 'end') => (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const u = plotRef.current;
    const context = contextRef.current;
    if (!u || !context.ready || chartContextRef.current !== context.requestKey) return;
    dragRef.current = { id, which, pointerId: e.pointerId, requestKey: context.requestKey };
  };

  const u = plotRef.current;
  const xToPx = (v: number): number | null => {
    if (!u || !box) return null;
    return u.valToPos(v, 'x');
  };
  void chartTick; // overlay depends on chart rebuild

  const hasValues = (values: (number | null)[] | undefined) =>
    Boolean(values?.some((value, index) => value !== null && Number.isFinite(value) &&
      win?.t[index] !== null && Number.isFinite(win?.t[index])));
  const hasData = Boolean(win && (win.mode === 'envelope'
    ? hasValues(win.series[column]?.min) || hasValues(win.series[column]?.max)
    : hasValues(win.series[column])));
  const showRegions = Boolean(win && !loading && !error && chartContextRef.current === requestKey);

  return (
    <section className={`panel ${styles.card}`} role="region" aria-label={`Split plot ${plotNumber}`} data-split-plot>
      <div className={styles.header}>
        <span className={styles.plotNumber}>Plot {plotNumber}</span>
        <SearchableSelect
          value={column}
          options={columns.map((value) => ({ value, label: value }))}
          onChange={onColumnChange}
          ariaLabel={`Variable for split plot ${plotNumber}`}
          placeholder="Choose a variable"
          optionNoun="variable"
          size="compact"
          appearance="plot"
          className={styles.variable}
        />
        {win && (
          <span className="badge">
            {win.mode === 'raw' ? 'raw' : `envelope 1:${win.level}`}
          </span>
        )}
        <div className={styles.actions}>
          <button type="button" className="btn" title="Reset time zoom for all split plots"
            onClick={() => onRangeChange(null)}>Reset zoom</button>
          <button type="button" className="btn" aria-label={`Remove split plot ${plotNumber}`}
            title={canRemove ? 'Remove this plot' : 'Keep at least one split plot'}
            disabled={!canRemove} onClick={onRemove}>Remove</button>
        </div>
      </div>
      <div ref={wrapperRef} className={styles.viewport} style={{ height: PLOT_HEIGHT }}>
        <div
          ref={containerRef}
          style={{ width: '100%', height: PLOT_HEIGHT, pointerEvents: win ? undefined : 'none' }}
          onDoubleClick={() => onRangeChange(null)}
        />
        {showRegions && u && box && (
          <div
            style={{
              position: 'absolute',
              left: box.left,
              top: box.top,
              width: box.width,
              height: box.height,
              overflow: 'hidden',
              pointerEvents: 'none',
            }}
          >
          {tps.map((tp) => {
            const x0v = xToPx(tp.start_s);
            const x1v = xToPx(effectiveEnd(tp, tps, dataEnd));
            if (x0v === null || x1v === null) return null;
            const x0 = Math.max(x0v, 0);
            const x1 = Math.min(x1v, box.width);
            if (x1 < 0 || x0 > box.width || x1 <= x0) return null;
            const sel = tp.id === selectedId;
            return (
              <div key={tp.id}>
                <div
                  style={{
                    position: 'absolute', left: x0, top: 0,
                    width: x1 - x0, height: '100%',
                    background: sel ? '#569cd630' : '#569cd614',
                    borderLeft: '1px solid #569cd6',
                    borderRight: tp.end_s !== null ? '1px solid #569cd6' : '1px dashed #4a6b8a',
                  }}
                />
                <button
                  type="button"
                  onClick={() => onSelect(sel ? null : tp.id)}
                  aria-pressed={sel}
                  aria-label={`Select ${tp.name} in split plot ${plotNumber}`}
                  title={`${tp.name}: ${tp.start_s}–${effectiveEnd(tp, tps, dataEnd)} s`}
                  className={styles.tpLabel}
                  style={{
                    position: 'absolute', left: x0, top: 0,
                    maxWidth: Math.max(x1 - x0, 40),
                    overflow: 'hidden', whiteSpace: 'nowrap',
                    pointerEvents: 'auto', cursor: 'pointer',
                    fontSize: 10, fontWeight: 600,
                    color: sel ? '#569cd6' : '#a0a0a0',
                    background: sel ? '#1e3a52' : '#25252699',
                    padding: '1px 4px', borderRadius: 2, border: 0,
                  }}
                >
                  {tp.name}
                </button>
                {sel && (
                  <>
                    <div
                      onPointerDown={startDrag(tp.id, 'start')}
                      role="img"
                      aria-label={`Start handle for TP ${tp.id} in split plot ${plotNumber}`}
                      style={{
                        position: 'absolute', left: x0v - 4, top: 0,
                        width: 9, height: '100%',
                        cursor: 'ew-resize', pointerEvents: 'auto',
                        display: 'flex', justifyContent: 'center',
                      }}
                    >
                      <div style={{ width: 3, height: '100%', background: '#569cd6' }} />
                    </div>
                    {tp.end_s !== null && (
                      <div
                        onPointerDown={startDrag(tp.id, 'end')}
                        role="img"
                        aria-label={`End handle for TP ${tp.id} in split plot ${plotNumber}`}
                        style={{
                          position: 'absolute', left: x1v - 4, top: 0,
                          width: 9, height: '100%',
                          cursor: 'ew-resize', pointerEvents: 'auto',
                          display: 'flex', justifyContent: 'center',
                        }}
                      >
                        <div style={{ width: 3, height: '100%', background: '#569cd6' }} />
                      </div>
                    )}
                  </>
                )}
              </div>
            );
          })}
          </div>
        )}
        <PlotStateOverlay
          loading={loading}
          hasData={hasData}
          error={error}
          emptyState={{
            title: 'No samples in this range',
            detail: range
              ? 'Reset the zoom or choose a wider time range.'
              : 'The selected signals contain no plottable values.',
          }}
          onRetry={() => setRetryVersion((version) => version + 1)}
          loadingLabel="Loading split preview"
          updatingLabel="Updating split preview"
          errorTitle="Could not load the split preview"
        />
      </div>
      <div className={styles.hint}>
        drag to zoom · double-click reset · click TP label to select · drag handles to move edges
      </div>
    </section>
  );
}
