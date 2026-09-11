import { useEffect, useMemo, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import type { SelectedTestPoint, TimePlotConfig, WaterfallData, WaterfallExportRequest } from '../../types';
import { fetchWaterfall, isAbortError } from '../../services/api';
import { AXIS_STYLE } from '../../constants/uplotTheme';
import { xPanZoomPlugin } from '../../utils/uplotPanZoom';
import { usePlotViewport, type ViewportProps } from '../../utils/plotViewport';
import { capturePlotPng, encodeAndDownload, type PlotPngCapture } from '../../utils/plotPngExport';
import { imageMetadata } from '../../utils/analysisMetadata';
import { downloadPlotCsv } from '../../utils/plotExport';
import { usePlotExportRegistration, type RegisterPlotExport } from '../../utils/plotExportRegistry';
import { PlotExportControls, type PlotExportActions } from '../controls/PlotExportControls';
import { SearchableSelect } from '../controls/SearchableSelect';
import { PlotActionMenu } from './PlotActionMenu';
import { PlotHeader } from './PlotHeader';
import common from './TimePlot.module.css';
import styles from './WaterfallPlot.module.css';

interface Props extends ViewportProps {
  test: string; cfg: TimePlotConfig; source: 'full' | 'tp';
  selectedTPs: SelectedTestPoint[]; hiddenTPs: Set<string>; columnsByTest: Record<string, string[]>;
  range: [number, number] | null; nperseg: number; overlap: number; logColor: boolean;
  resolutionHz: number | null; frequencyBand: 'low' | 'full';
  isExpanded: boolean; onToggleExpand: () => void; registerExport?: RegisterPlotExport;
  isEditMode?: boolean; allConfigs?: TimePlotConfig[]; onConfigChange?: (value: string) => void;
}
interface Source { test: string; tpId?: number; label: string }
interface Trace extends Source { data: WaterfallData }
const EMPTY: Trace[] = [];
const fmt = (v: number) => Number(v.toPrecision(5)).toString();
const stops = [[10, 14, 90], [22, 58, 185], [0, 160, 210], [32, 192, 125], [231, 221, 50], [244, 119, 26], [192, 22, 30]];
function color(value: number): number[] {
  const pos = Math.max(0, Math.min(1, value)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(pos));
  return stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * (pos - i)));
}
function cellAt(edges: number[], value: number) {
  if (value < edges[0] || value > edges[edges.length - 1]) return -1;
  let low = 0, high = edges.length - 1;
  while (low + 1 < high) { const mid = (low + high) >> 1; if (edges[mid] <= value) low = mid; else high = mid; }
  return Math.min(low, edges.length - 2);
}

function Heatmap({ trace, scale, logColor, expanded, frequencyBand, viewport, viewportContext, onViewportChange, register }: {
  trace: Trace; scale: [number, number]; logColor: boolean; expanded: boolean;
  frequencyBand: 'low' | 'full';
  register: (plot: uPlot | null) => void;
} & ViewportProps) {
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });
  const [hover, setHover] = useState('Hover to inspect frequency, elapsed time and magnitude.');
  const control = usePlotViewport(plot, { viewport, viewportContext, onViewportChange }, true);
  const reg = useRef(register); reg.current = register;
  const data = trace.data;
  // Reset domains describe the source, never the most recently refined tiles.
  const fullFrequency = data.grid!.full_frequency_range_hz;
  const defaultXMax = frequencyBand === 'low' ? Math.min(200, fullFrequency[1]) : fullFrequency[1];
  const [defaultYMin, defaultYMax] = data.grid!.full_time_range_s;
  const lo = scale[0], hi = scale[1];
  useEffect(() => {
    const el = host.current!;
    const observer = new ResizeObserver(() => setBox({ width: el.clientWidth, height: el.clientHeight }));
    observer.observe(el); return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!host.current || box.width < 100 || box.height < 100) return;
    setHover('Hover to inspect frequency, elapsed time and magnitude.');
    const f = data.frequency_edges_hz, t = data.time_edges_s;
    const rows = data.magnitude.length, cols = data.magnitude[0]?.length ?? 0;
    const bitmap = document.createElement('canvas'); bitmap.width = Math.max(1, cols); bitmap.height = Math.max(1, rows);
    const ctx = bitmap.getContext('2d')!;
    const pixels = ctx.createImageData(bitmap.width, bitmap.height);
    data.magnitude.forEach((row, y) => row.forEach((v, x) => {
      const value = logColor ? v > 0 ? Math.log10(v) : lo : v;
      const rgb = color((value - lo) / (hi - lo));
      const offset = ((rows - 1 - y) * cols + x) * 4;
      pixels.data.set([...rgb, 255], offset);
    }));
    ctx.putImageData(pixels, 0, 0);
    const draw = (u: uPlot) => {
      const c = u.ctx, b = u.bbox;
      c.save(); c.beginPath(); c.rect(b.left, b.top, b.width, b.height); c.clip(); c.imageSmoothingEnabled = false;
      // Interior cells are equal-width. First/last frequency bins and the last
      // aggregate frame can be shorter; draw their strips with exact edges.
      const xs = [...new Set([0, 1, cols - 1, cols])].sort((a,b) => a-b);
      const ys = [...new Set([0, rows - 1, rows])].sort((a,b) => a-b);
      for (let a = 0; rows && cols && a < xs.length - 1; a++) for (let b = 0; b < ys.length - 1; b++) {
        const x0 = xs[a], x1 = xs[a+1], y0 = ys[b], y1 = ys[b+1];
        const left = u.valToPos(f[x0], 'x', true), right = u.valToPos(f[x1], 'x', true);
        const top = u.valToPos(t[y1], 'y', true), bottom = u.valToPos(t[y0], 'y', true);
        c.drawImage(bitmap, x0, rows-y1, x1-x0, y1-y0, left, top, right-left, bottom-top);
      }
      c.restore();
      const ratio = u.ctx.canvas.width / u.width, x = b.left + b.width + 10 * ratio, w = 9 * ratio;
      const gradient = c.createLinearGradient(0, b.top + b.height, 0, b.top);
      stops.forEach((rgb, i) => gradient.addColorStop(i / (stops.length-1), `rgb(${rgb.join(',')})`));
      c.fillStyle = gradient; c.fillRect(x, b.top, w, b.height);
      c.fillStyle = '#bbc3cc'; c.font = `${9 * ratio}px Segoe UI`; c.textAlign = 'left';
      [0, .5, 1].forEach(v => c.fillText(fmt(lo + v*(hi-lo)), x+w+3*ratio, b.top + b.height*(1-v) + (v===0 ? 0 : 7*ratio)));
    };
    control.sync(() => {
      const u = new uPlot({ width: box.width, height: box.height,
        padding: [12, 66, 0, 0], legend: { show: false },
        scales: { x: { time: false, range: (_u, min, max) => [min ?? 0, max ?? defaultXMax] }, y: { range: () => [defaultYMin, defaultYMax] } },
        axes: [{ ...AXIS_STYLE, label: 'Frequency (Hz)', size: 35, labelSize: 16 },
          { ...AXIS_STYLE, label: 'Elapsed time (s)', size: 48, labelSize: 16 }],
        series: [{}, { label: trace.label, paths: () => null, points: { show: false } }],
        cursor: { drag: { x: true, y: true } },
        plugins: [xPanZoomPlugin(undefined, () => {}), control.plugin],
        hooks: { draw: [draw], setCursor: [(u) => {
          if (!rows || !cols) return;
          if (u.cursor.left == null || u.cursor.top == null || u.cursor.left < 0 || u.cursor.top < 0) return;
          const x = cellAt(f, u.posToVal(u.cursor.left, 'x')), y = cellAt(t, u.posToVal(u.cursor.top, 'y'));
          if (x < 0 || y < 0) return;
          setHover(`${fmt(f[x])}–${fmt(f[x+1])} Hz · ${fmt(t[y])}–${fmt(t[y+1])} s · ${fmt(data.magnitude[y][x])} U${data.reduction.method === 'max' ? ' (cell max)' : ''} · source frame centers ${fmt(data.source_frame_start_s[y])}–${fmt(data.source_frame_end_s[y])} s`);
        }] },
      }, [[0, defaultXMax], [defaultYMin, defaultYMax]], host.current!);
      plot.current = u; reg.current(u);
    });
    return () => { reg.current(null); plot.current?.destroy(); plot.current = null; bitmap.width = bitmap.height = 0; };
  }, [data, trace.label, box, expanded, lo, hi, logColor, control, defaultXMax, defaultYMin, defaultYMax]);
  useEffect(() => {
    if (!plot.current) return;
    control.sync(() => {
      if (!viewport || viewport.context !== viewportContext) plot.current!.batch(() => {
        plot.current!.setScale('x', { min: 0, max: defaultXMax });
        plot.current!.setScale('y', { min: defaultYMin, max: defaultYMax });
      });
    });
  }, [viewport, viewportContext, control, data, defaultXMax, defaultYMin, defaultYMax]);
  return <section className={styles.facet} aria-label={`Waterfall source ${trace.label}`}>
    <div className={styles.source} title={trace.label}>{trace.label}</div>
    <div ref={host} className={styles.chart} tabIndex={0} aria-label={`Waterfall canvas for ${trace.label}`}
      onKeyDown={e => { if (e.key === 'Home') { e.preventDefault(); control.reset(); } }} />
    <div className={styles.readout}>{data.magnitude.length ? hover : 'No FFT cells in this view. Press Home to reset axes.'}</div>
  </section>;
}

export function WaterfallPlot(props: Props) {
  const { cfg, test, source, selectedTPs, hiddenTPs, columnsByTest, range, nperseg, overlap,
    resolutionHz, frequencyBand, logColor, isExpanded, onToggleExpand, viewport, viewportContext, onViewportChange } = props;
  const body = useRef<HTMLDivElement>(null), dialog = useRef<HTMLDialogElement>(null);
  const plots = useRef(new Map<string, uPlot>()), exportActions = useRef<PlotExportActions>(null);
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<{ key: string; baseKey: string; traces: Trace[]; errors: string[] } | null>(null);
  const sources: Source[] = source === 'full' ? test ? [{ test, label: test }] : [] : selectedTPs
    .filter(p => !hiddenTPs.has(p.id)).map(p => ({ test: p.test, tpId: p.tpId, label: `${p.name} · ${p.test} · TP ${p.tpId}` }));
  const activeViewport = viewport?.context === viewportContext ? viewport : null;
  const frequencyRange: [number, number] | null = activeViewport?.x ?? (frequencyBand === 'low' ? [0, 200] : null);
  const timeRange = activeViewport?.y ?? null;
  const baseKey = JSON.stringify([source, sources, cfg.key, nperseg, overlap, resolutionHz, frequencyBand, source === 'full' ? range :
    selectedTPs.filter(p => !hiddenTPs.has(p.id)).map(p => [p.id, p.tp.start_idx, p.tp.end_idx, p.tp.start_s, p.endS]), viewportContext,
    sources.map(s => columnsByTest[s.test]?.includes(cfg.key))]);
  const key = JSON.stringify([baseKey, frequencyRange, timeRange]);
  const current = result?.key === key, pending = !current;
  // Keep the last grid during refinement so wheel/pan gestures remain usable.
  // Only same-source/settings results may remain visible; exports wait for parity.
  const traces = result?.baseKey === baseKey ? result.traces : EMPTY;
  const errors = current ? result.errors : [];
  useEffect(() => {
    const controller = new AbortController(); let dead = false;
    const run = async () => {
      const loaded: Trace[] = [], failures: string[] = [];
      // Bound concurrent requests per grid slot; each request runs under the
      // shared native-read gate. Never relabel a late result after source edits.
      for (const item of sources) {
        if (dead) return;
        try {
          if (!columnsByTest[item.test]?.includes(cfg.key)) throw new Error('variable unavailable in this source');
          const data = await fetchWaterfall(item.test, cfg.key, nperseg, overlap, source === 'full' ? range : null, item.tpId, controller.signal,
            { resolutionHz, frequencyRange, timeRange });
          if (data.method?.version !== 'kiha-waterfall-v2' || !data.grid || (item.tpId !== undefined && data.tp_id !== item.tpId)) throw new Error('Backend did not confirm the detailed waterfall method and source. Reload and retry.');
          loaded.push({ ...item, data });
        } catch (cause) { if (isAbortError(cause)) return; failures.push(`${item.label}: ${cause instanceof Error ? cause.message : String(cause)}`); }
      }
      if (!dead) setResult({ key, baseKey, traces: loaded, errors: failures });
    };
    const timer = window.setTimeout(run, 180);
    return () => { dead = true; controller.abort(); clearTimeout(timer); };
    // Refinement selects bins/anchored frames from the same source interval.
    // Debounce pointer gestures; color alone never recalculates the FFT.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, retry]);
  const scale = useMemo<[number, number]>(() => {
    let max = 0;
    traces.forEach(tr => tr.data.magnitude.forEach(row => row.forEach(v => { max = Math.max(max, v); })));
    return logColor ? [max > 0 ? Math.log10(max)-6 : -6, max > 0 ? Math.log10(max) : 0] : [0, max || 1];
  }, [traces, logColor]);
  const reason = pending ? 'Wait for the waterfall calculation.' : errors.length ? 'Retry failed sources before exporting.' : !traces.length ? 'Select a source to export.' : traces.some(tr => !tr.data.magnitude.length) ? 'No FFT cells for one or more sources in this view. Reset axes or choose an overlapping range.' : null;
  const reset = () => onViewportChange?.(null);
  const scope = 'Windowed FFT of stored samples. Frequency in Hz; elapsed time from each selected interval. Shared color range across sources of this variable. U is the signal unit.';
  const request = (): WaterfallExportRequest => {
    if (reason) throw new Error(reason);
    return { kind: 'waterfall', column: cfg.key, method_version: 'kiha-waterfall-v2', nperseg, overlap,
      resolution_hz: resolutionHz, grid_frequency_range_hz: frequencyRange, grid_time_range_s: timeRange,
      x_range: activeViewport?.x ?? null, y_range: activeViewport?.y ?? null,
      sources: traces.map(tr => ({ test: tr.test, tp_id: tr.tpId,
        ...(source === 'full' ? { t0: range?.[0], t1: range?.[1] } : {}),
        expected_i0: tr.data.i0, expected_i1: tr.data.i1, expected_fs_hz: tr.data.fs_hz })) };
  };
  const capture = (): PlotPngCapture => {
    if (reason) throw new Error(reason);
    const captures: PlotPngCapture[] = [];
    try {
      traces.forEach(tr => {
        const u = plots.current.get(tr.label); if (!u) throw new Error('Wait for the waterfall canvas.');
        const metadata = Object.fromEntries(Object.entries(tr.data).filter(([key]) => key !== 'magnitude'));
        captures.push(capturePlotPng(u, { title: `${cfg.label} · Waterfall FFT · ${tr.label}`, scope: [scope],
          details: [`Color: ${logColor ? 'log10(U), 6-decade floor; zero at floor' : 'linear amplitude U'}; range ${scale.map(fmt).join(' to ')}.`,
            `Hann window ${tr.data.method.nperseg} samples (${fmt(tr.data.method.window_seconds)} s), overlap ${overlap}%; Δf ${fmt(tr.data.method.bin_spacing_hz)} Hz.`,
            `Grid ${tr.data.reduction.method}; maximum over ${tr.data.reduction.time_factor} frames × ${tr.data.reduction.frequency_factor} bins per full cell. ${tr.data.nan_count} missing samples interpolated; ${tr.data.method.trailing_samples} trailing samples unused.`],
          provenance: { kind: 'waterfall', column: cfg.key, test: tr.test, tp_id: tr.tpId, loaded: metadata,
            color_transform: logColor ? 'log10' : 'linear', color_range: scale } }));
        if (captures.reduce((sum, c) => sum + c.canvas.width*c.canvas.height, 0) > 16_000_000) {
          throw new Error('Too many sources for one image. Select fewer test points.');
        }
      });
      const columns = captures.length > 1 ? 2 : 1, rows = Math.ceil(captures.length/columns);
      const width = Math.max(...captures.map(c => c.canvas.width)), height = Math.max(...captures.map(c => c.canvas.height));
      if (width*columns > 8192 || height*rows > 8192 || width*columns*height*rows > 16_000_000) throw new Error('Too many sources for one image. Select fewer test points.');
      const canvas = document.createElement('canvas'); canvas.width = width*columns; canvas.height = height*rows;
      const context = canvas.getContext('2d')!; context.fillStyle = '#252526'; context.fillRect(0,0,canvas.width,canvas.height);
      captures.forEach((c,i) => context.drawImage(c.canvas, i%columns*width, Math.floor(i/columns)*height));
      const ratio = captures[0].pixelRatio;
      return { canvas, cssWidth: canvas.width/ratio, cssHeight: canvas.height/ratio, pixelRatio: ratio,
        metadata: { kind: 'waterfall', sources: captures.map(c => c.metadata) }, dispose: () => { canvas.width = canvas.height = 0; } };
    } finally { captures.forEach(c => c.dispose()); }
  };
  usePlotExportRegistration(props.registerExport, { label: cfg.label, scope, defaultData: 'original',
    originalReason: reason, filteredReason: null, pngReason: reason, buildCsvRequest: request, capturePng: capture });
  const closeDetails = () => { dialog.current?.close(); body.current?.focus(); };
  useEffect(() => { dialog.current?.close(); }, [key]);
  return <div className={`${common.plotContainer} ${isExpanded ? styles.expanded : ''}`} role="group" aria-label={`${cfg.label} waterfall plot`}>
    <PlotHeader label={cfg.label} isExpanded={isExpanded} onToggleExpand={onToggleExpand}
      summary={`Waterfall FFT${traces.length > 1 ? ` · ${traces.length} selected TPs` : ''} · ${traces.length ? `Δf ${[...new Set(traces.map(tr => fmt(tr.data.method.bin_spacing_hz)))].join(' / ')} Hz` : resolutionHz ? `Target Δf ${resolutionHz} Hz` : `Hann ${nperseg}`} · ${overlap}% overlap${traces.some(tr => tr.data.reduction.method === 'max') ? ' · reduced grid' : ''}${traces.some(tr => tr.data.nan_count) ? ' · missing samples interpolated' : ''}`}
      actions={<>
        <PlotActionMenu label={cfg.label} targetRef={body} contextKey={key} onReset={reset}
          exportActions={exportActions} onAnalysisDetails={() => dialog.current?.showModal()} />
        <PlotExportControls hideTrigger actionsRef={exportActions} label={cfg.label} contextKey={`${key}:${logColor}`} scope={scope}
          defaultData="original" originalReason={reason} filteredReason={null} pngReason={reason}
          csvLabel="Waterfall grid · linear magnitude" csvDescription="One row per displayed cell intersecting the viewport. Includes time/frequency bounds and source identity; aggregated cells contain maxima, not native FFT bins."
          onCsv={(_data, signal, includeMetadata) => downloadPlotCsv({ ...request(), include_metadata: includeMetadata }, signal)}
          onPng={async (signal, includeMetadata) => { const c = capture(); try { await encodeAndDownload(c.canvas, `${cfg.key}_waterfall.png`, signal, includeMetadata ? imageMetadata([c.metadata]) : undefined); } finally { c.dispose(); } }} />
      </>}>
      {props.isEditMode && <SearchableSelect value={cfg.key} options={(props.allConfigs ?? []).map(c => ({ value:c.key,label:c.label }))}
        onChange={v => props.onConfigChange?.(v)} ariaLabel="Plot variable" appearance="plot" size="compact" />}
    </PlotHeader>
    <div className={styles.body} ref={body} tabIndex={0}>
      {pending && !traces.length && <p role="status" className={styles.failure}>Calculating waterfall FFT…</p>}
      {!pending && !sources.length && <p className={styles.failure}>Select test points to compare, or choose Full test.</p>}
      {errors.map(message => <p role="alert" className={styles.failure} key={message}>{message}</p>)}
      {!!errors.length && <button className="btn" onClick={() => setRetry(v=>v+1)}>Retry waterfall</button>}
      {!!traces.length && <div className={styles.hint} role="status">{pending ? 'Refining visible grid… · ' : ''}Color: {logColor ? 'log10(U), 6-decade floor' : 'magnitude (U)'} · {traces.length > 1 ? 'shared ' : ''}{scale.map(fmt).join(' to ')} · drag to zoom · Shift-drag to pan · Home / double-click to reset</div>}
      <div className={styles.facets}>
        {traces.map(tr => <Heatmap key={`${baseKey}:${tr.label}`} trace={tr} scale={scale} logColor={logColor} expanded={isExpanded} frequencyBand={frequencyBand}
          viewport={viewport} viewportContext={viewportContext} onViewportChange={onViewportChange}
          register={u => { if (u) plots.current.set(tr.label,u); else plots.current.delete(tr.label); }} />)}
      </div>
    </div>
    <dialog ref={dialog} className={styles.dialog} aria-label={`Waterfall analysis for ${cfg.label}`}
      onCancel={e => { e.preventDefault(); closeDetails(); }}>
      <h3>Waterfall FFT · {cfg.label}</h3>
      <p>{scope}</p>
      <p>Each complete window is mean-centered, multiplied by a periodic Hann window, then transformed. One-sided magnitude is divided by the window sum; interior frequency bins are doubled. No padding or time-plot filtering. Known acquisition gaps prevent analysis. Missing signal values are interpolated by sample index.</p>
      <p>Time marks nominal window centers relative to the selected interval. Source timestamps are retained in hover and CSV. Log color is log10(U), with a floor six decades below the shared maximum; it is not dB. Reduced cells report maxima over explicit ranges. Smaller windows improve time resolution; larger windows give finer frequency bins.</p>
      <p>Zoom reloads the visible FFT bins and time frames before reduction. Frame origins remain anchored to the selected source interval. Bin spacing is not a guarantee that two equally close tones can be separated: the Hann window broadens peaks. Higher overlap adds correlated frames without undoing the time blur of a longer window. Each window has its mean removed, so 0 Hz does not represent the original DC level.</p>
      {traces.map(tr => <section key={tr.label}><h4>{tr.label}</h4><p>Rows [{tr.data.i0}, {tr.data.i1}) · {tr.data.fs_hz} Hz · {tr.data.nan_count} missing values interpolated.</p>
        <p>Native bin spacing {fmt(tr.data.method.bin_spacing_hz)} Hz · typical displayed cell {fmt(tr.data.method.bin_spacing_hz * tr.data.reduction.frequency_factor)} Hz × {fmt(tr.data.method.step_seconds * tr.data.reduction.time_factor)} s · window {fmt(tr.data.method.window_seconds)} s.</p>
        <pre>{JSON.stringify({ method: tr.data.method, grid: tr.data.grid, reduction: tr.data.reduction, quality: tr.data.quality }, null, 2)}</pre></section>)}
      {errors.map(e=><p key={e}>{e}</p>)}
      <button className="btn" onClick={closeDetails}>Close</button>
    </dialog>
  </div>;
}
