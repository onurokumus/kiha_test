import React, { useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchWindow, isAbortError } from '../../services/api';
import { DataWindow, TestPoint } from '../../types';
import { AXIS_STYLE, colorFor } from '../../constants/uplotTheme';
import { round3 } from '../../utils/formatters';

export type TimeRange = [number, number] | null;

interface Props {
  test: string;
  cols: string[];
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

/** end of a TP for display: own end, else next TP start, else data end */
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
  const { test, cols, range, onRangeChange, tps, selectedId, onSelect,
          onChangeTp, dataStart, dataEnd } = props;
  const wrapperRef = useRef<HTMLDivElement>(null); // positioning reference
  const containerRef = useRef<HTMLDivElement>(null); // uPlot mount target
  const plotRef = useRef<uPlot | null>(null);
  const [win, setWin] = useState<DataWindow | null>(null);
  const [box, setBox] = useState<OverlayBox | null>(null);
  const [chartTick, setChartTick] = useState(0); // bumps when chart rebuilt
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const dragRef = useRef<{ id: number; which: 'start' | 'end'; overLeft: number } | null>(null);

  // measure plot area after layout settles (sync measure returns 0x0)
  const measure = () => {
    const u = plotRef.current;
    const w = wrapperRef.current;
    if (!u || !w) return;
    const o = u.over.getBoundingClientRect();
    const wr = w.getBoundingClientRect();
    if (o.width === 0) return;
    setBox({ left: o.left - wr.left, top: o.top - wr.top,
             width: o.width, height: o.height });
  };

  // fetch window
  useEffect(() => {
    if (!test || cols.length === 0) return;
    let dead = false;
    const controller = new AbortController();
    setLoading(true);
    const px = containerRef.current?.clientWidth ?? 1500;
    fetchWindow(test, cols, range?.[0] ?? null, range?.[1] ?? null, px, controller.signal)
      .then((w) => { if (!dead) { setWin(w); setError(''); } })
      .catch((e) => {
        if (!dead && !isAbortError(e)) setError(String(e instanceof Error ? e.message : e));
      })
      .finally(() => !dead && setLoading(false));
    return () => {
      dead = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [test, cols.join(','), range?.[0], range?.[1]]);

  // (re)build chart
  useEffect(() => {
    if (!win || !containerRef.current) return;
    plotRef.current?.destroy();
    const width = containerRef.current.clientWidth;

    const series: uPlot.Series[] = [{}];
    const bands: uPlot.Band[] = [];
    const scales: uPlot.Scales = { x: { time: false } };
    const axes: uPlot.Axis[] = [{ ...AXIS_STYLE }];
    const data: (number | null)[][] = [win.t];

    cols.forEach((c, i) => {
      // offset the palette: colorFor(0) is the accent blue used by the TP
      // region overlays — the data trace must stand apart (orange first)
      const color = colorFor(i + 1);
      scales[c] = { auto: true };
      if (i === 0) axes.push({ ...AXIS_STYLE, scale: c, stroke: color });
      if (win.mode === 'envelope') {
        const s = win.series[c] as { min: (number | null)[]; max: (number | null)[] };
        const base = series.length;
        series.push(
          { label: `${c} max`, stroke: color, width: 1, scale: c, spanGaps: false },
          { label: `${c} min`, stroke: color, width: 1, scale: c, spanGaps: false }
        );
        bands.push({ series: [base, base + 1], fill: color + '40' });
        data.push(s.max, s.min);
      } else {
        series.push({ label: c, stroke: color, width: 1.5, scale: c, spanGaps: false });
        data.push(win.series[c] as (number | null)[]);
      }
    });

    // A NaN in the time column serializes as null in win.t; uPlot's x array
    // must be ascending numbers, so drop those samples across every parallel
    // array (t + each series column). Matches TimePlot / FullTestPlot (1.19).
    if (win.t.some((v) => v == null)) {
      const keep: number[] = [];
      for (let i = 0; i < win.t.length; i++) if (win.t[i] != null) keep.push(i);
      for (let c = 0; c < data.length; c++) data[c] = keep.map((i) => data[c][i]);
    }

    const u = new uPlot(
      {
        width, height: 340, series, bands, scales, axes,
        legend: { live: false },
        cursor: { drag: { x: true, y: false } },
        hooks: {
          setSelect: [
            (u2) => {
              if (u2.select.width > 10) {
                const t0 = u2.posToVal(u2.select.left, 'x');
                const t1 = u2.posToVal(u2.select.left + u2.select.width, 'x');
                u2.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
                onRangeChange([t0, t1]);
              }
            },
          ],
        },
      },
      data as uPlot.AlignedData,
      containerRef.current
    );
    plotRef.current = u;

    const raf = requestAnimationFrame(() => {
      measure();
      setChartTick((n) => n + 1);
    });

    return () => {
      cancelAnimationFrame(raf);
      plotRef.current?.destroy();
      plotRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [win]);

  // remeasure on container resize
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const u = plotRef.current;
      if (u && el.clientWidth > 0) {
        u.setSize({ width: el.clientWidth, height: 340 });
        requestAnimationFrame(measure);
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // drag handles
  useEffect(() => {
    const move = (e: PointerEvent) => {
      const d = dragRef.current;
      const u = plotRef.current;
      if (!d || !u) return;
      let v = u.posToVal(e.clientX - d.overLeft, 'x');
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
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
  }, [tps, onChangeTp, dataStart, dataEnd]);

  const startDrag = (id: number, which: 'start' | 'end') => (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const u = plotRef.current;
    if (!u) return;
    dragRef.current = { id, which, overLeft: u.over.getBoundingClientRect().left };
  };

  const u = plotRef.current;
  const xToPx = (v: number): number | null => {
    if (!u || !box) return null;
    return u.valToPos(v, 'x');
  };
  void chartTick; // overlay depends on chart rebuild

  return (
    <div className="panel" style={{ position: 'relative' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>{cols.join(', ')}</span>
        {win && (
          <span className="badge">
            {win.mode === 'raw' ? 'raw' : `envelope 1:${win.level}`}
          </span>
        )}
        {loading && <span style={{ color: '#569cd6', fontSize: 11 }}>⟳</span>}
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={() => onRangeChange(null)}>reset zoom</button>
      </div>
      {error && <div style={{ color: '#f48771', fontSize: 11 }}>{error}</div>}
      <div ref={wrapperRef} style={{ position: 'relative' }}>
        <div ref={containerRef} onDoubleClick={() => onRangeChange(null)} />
        {u && box && (
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
                <div
                  onClick={() => onSelect(sel ? null : tp.id)}
                  style={{
                    position: 'absolute', left: x0, top: 0,
                    maxWidth: Math.max(x1 - x0, 40),
                    overflow: 'hidden', whiteSpace: 'nowrap',
                    pointerEvents: 'auto', cursor: 'pointer',
                    fontSize: 10, fontWeight: 600,
                    color: sel ? '#569cd6' : '#a0a0a0',
                    background: sel ? '#1e3a52' : '#25252699',
                    padding: '1px 4px', borderRadius: 2,
                  }}
                >
                  {tp.name}
                </div>
                {sel && (
                  <>
                    <div
                      onPointerDown={startDrag(tp.id, 'start')}
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
      </div>
      <div style={{ fontSize: 10, color: '#909090' }}>
        drag to zoom · double-click reset · click TP label to select · drag handles to move edges
      </div>
    </div>
  );
}
