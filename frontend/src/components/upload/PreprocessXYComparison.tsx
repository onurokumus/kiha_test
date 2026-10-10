import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import uPlot from 'uplot';
import { AXIS_STYLE, PLOT_PADDING, plotSeriesColor, safeRange, themeSeriesColor } from '../../constants/uplotTheme';
import { isAbortError } from '../../services/api';
import { describePreprocessingFilter, type PreprocessingSnapshot } from '../../services/preprocessing';
import { comparisonXYCursorValues, fetchPreprocessingXYComparison,
  type PreprocessingXYComparisonData } from '../../services/preprocessingXYComparison';
import { usePlotTheme } from '../../utils/usePlotTheme';
import { axisTitlesPlugin } from '../../utils/uplotAxisTitle';
import { boxZoomPlugin, xyPanZoomPlugin } from '../../utils/uplotPanZoom';
import { nearestXYDataIdx } from '../../utils/plotHoverValues';
import { usePlotViewport, type ViewportProps } from '../../utils/plotViewport';
import { validAxisRange } from '../../utils/timePlotRanges';
import { pointAppearancePlugin } from '../../utils/uplotPointAppearance';
import { downloadPlotPng } from '../../utils/plotPngExport';
import styles from './PreprocessComparison.module.css';

interface Props extends ViewportProps {
  name: string;
  snapshot: PreprocessingSnapshot;
  x: string;
  y: string;
  display: 'both' | 'original' | 'filtered';
  range: [number, number] | null;
  expanded: boolean;
  onResetInterval: () => void;
  onReload?: () => void;
}
const ORIGINAL = '#ae673b';
const FILTERED = '#263685';
const number = (value: number | null | undefined) => value == null || !Number.isFinite(value)
  ? '—' : Number(value.toPrecision(7)).toLocaleString(undefined, { maximumSignificantDigits: 7 });
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

export default function PreprocessXYComparison({ name, snapshot, x, y, display, range, expanded,
  onResetInterval, onReload, viewport, onViewportChange }: Props) {
  const helpId = useId();
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const exportController = useRef<AbortController | null>(null);
  const [retry, setRetry] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; data: PreprocessingXYComparisonData | null; error: string } | null>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });
  const [cursor, setCursor] = useState<{ key: string; index: number | null } | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const context = JSON.stringify([snapshot.source, x, y, range]);
  const requestKey = JSON.stringify([context, retry]);
  const data = loaded?.key === requestKey ? loaded.data : null;
  const error = loaded?.key === requestKey ? loaded.error : '';
  const loading = loaded?.key !== requestKey;
  const theme = usePlotTheme(plot);
  const viewportControl = usePlotViewport(plot, { viewport, viewportContext: context, onViewportChange }, true);
  const cursorValue = data && cursor?.key === requestKey ? comparisonXYCursorValues(data, cursor.index) : null;
  const label = (column: string) => column === snapshot.meta.time_column ? `${column} (s)` : column;
  const xLabel = label(x), yLabel = label(y);
  const empty = !!data && (display === 'original' ? !data.summary.original.finite_pairs
    : display === 'filtered' ? !data.summary.filtered.finite_pairs
    : !data.summary.original.finite_pairs && !data.summary.filtered.finite_pairs);
  const filterDescription = (column: string) => {
    const filter = snapshot.preprocessing?.filters.find(entry => entry.column === column)?.filter;
    return filter ? describePreprocessingFilter(filter) : column === snapshot.meta.time_column ? 'Recorded time' : 'Unchanged';
  };

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const observer = new ResizeObserver(entries => {
      const rect = entries[0]?.contentRect;
      if (rect) setBox({ width: Math.floor(rect.width), height: Math.floor(rect.height) });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setExporting(false); setExportError('');
    const timer = window.setTimeout(() => {
      fetchPreprocessingXYComparison(name, snapshot.source, x, y, range, controller.signal)
        .then(result => { if (!controller.signal.aborted) setLoaded({ key: requestKey, data: result, error: '' }); })
        .catch(reason => { if (!controller.signal.aborted && !isAbortError(reason))
          setLoaded({ key: requestKey, data: null, error: errorText(reason) }); });
    }, 80);
    return () => { window.clearTimeout(timer); controller.abort(); exportController.current?.abort(); };
  }, [name, snapshot.source, x, y, range, requestKey]);

  useEffect(() => {
    const element = host.current;
    if (!element || !data || empty || box.width < 40 || box.height < 40) return;
    const kinds = (['original', 'filtered'] as const).filter(kind => display === 'both' || display === kind);
    const pointsPaths = uPlot.paths.points!();
    const series: uPlot.Series[] = [{}, ...kinds.map(kind => {
      const color = kind === 'original' ? ORIGINAL : FILTERED;
      return { label: kind === 'original' ? 'Original' : 'Filtered',
        stroke: (u: uPlot) => plotSeriesColor(u, color),
        fill: kind === 'original' ? 'transparent' : (u: uPlot) => plotSeriesColor(u, `${color}b3`),
        width: kind === 'original' ? 1.15 : .8,
        paths: pointsPaths, points: { show: false, size: kind === 'original' ? 6 : 4, width: 1 },
        facets: [{ scale: 'x', auto: true }, { scale: 'y', auto: true }],
      } as uPlot.Series;
    })];
    const values = [null, ...kinds.map(kind => [data[kind].x, data[kind].y])] as unknown as uPlot.AlignedData;
    viewportControl.sync(() => {
      const instance = new uPlot({ mode: 2, width: box.width, height: box.height, padding: PLOT_PADDING,
        series, legend: { show: false },
        scales: { x: { time: false, range: safeRange as uPlot.Scale.Range }, y: { range: safeRange as uPlot.Scale.Range } },
        axes: [{ ...AXIS_STYLE, label: xLabel }, { ...AXIS_STYLE, label: yLabel }],
        cursor: { dataIdx: nearestXYDataIdx, drag: { x: true, y: true }, points: { show: false } },
        plugins: [axisTitlesPlugin(yLabel), pointAppearancePlugin(), xyPanZoomPlugin('scroll'),
          boxZoomPlugin(), viewportControl.plugin],
        hooks: { setCursor: [u => {
          let nearest: number | null = null, distance = Infinity;
          if (u.cursor.left != null && u.cursor.top != null && u.cursor.left >= 0 && u.cursor.top >= 0) {
            kinds.forEach((kind, index) => {
              const candidate = nearestXYDataIdx(u, index + 1);
              if (candidate === null) return;
              const dx = u.valToPos(data[kind].x[candidate]!, 'x') - u.cursor.left!;
              const dy = u.valToPos(data[kind].y[candidate]!, 'y') - u.cursor.top!;
              if (dx * dx + dy * dy < distance) { distance = dx * dx + dy * dy; nearest = candidate; }
            });
          }
          setCursor({ key: requestKey, index: nearest });
        }] },
      }, values, element);
      plot.current = instance;
    });
    const instance = plot.current;
    return () => { instance?.destroy(); if (plot.current === instance) plot.current = null; };
  }, [data, display, empty, box.width, box.height, xLabel, yLabel, requestKey, viewportControl]);

  const navigate = (factor: number, shiftX = 0, shiftY = 0) => {
    const instance = plot.current;
    if (!instance || loading || empty || error) return;
    const next = (axis: 'x' | 'y', shift: number): [number, number] => {
      const { min, max } = instance.scales[axis];
      const span = max! - min!, middle = (min! + max!) / 2 + span * shift;
      return [middle - span * factor / 2, middle + span * factor / 2];
    };
    const nextX = next('x', shiftX), nextY = next('y', shiftY);
    if (!validAxisRange(nextX) || !validAxisRange(nextY)) return;
    instance.batch(() => {
      instance.setScale('x', { min: nextX[0], max: nextX[1] });
      instance.setScale('y', { min: nextY[0], max: nextY[1] });
    });
  };
  const onPlotKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === 'Home') { event.preventDefault(); viewportControl.reset(); }
    if (event.key === '+' || event.key === '=') { event.preventDefault(); navigate(.5); }
    if (event.key === '-') { event.preventDefault(); navigate(2); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); navigate(1, -.1); }
    if (event.key === 'ArrowRight') { event.preventDefault(); navigate(1, .1); }
    if (event.key === 'ArrowDown') { event.preventDefault(); navigate(1, 0, -.1); }
    if (event.key === 'ArrowUp') { event.preventDefault(); navigate(1, 0, .1); }
  };
  const exportPng = async () => {
    if (!plot.current || !data || empty || exporting) return;
    setExportError(''); setExporting(true);
    const controller = new AbortController();
    exportController.current = controller;
    try {
      await downloadPlotPng(plot.current, { filename: `${name}_${y}_vs_${x}_preprocessing-comparison.png`,
        title: `${name} · ${yLabel} (Y) vs ${xLabel} (X)`,
        scope: [`Saved XY comparison · ${display === 'both' ? 'Original and filtered' : display === 'original' ? 'Original only' : 'Filtered only'}`,
          range ? `Recording interval ${number(range[0])} to ${number(range[1])} s` : 'Full recording'],
        details: [`X: ${xLabel} · ${filterDescription(x)}`, `Y: ${yLabel} · ${filterDescription(y)}`,
          'Open circles: original. Filled circles: filtered. Native X/Y pairs at the same shared sample rows; no interpolation.',
          `${data.n_sampled} of ${data.n_raw} native rows displayed; stride 1:${data.stride}. XY zoom changes the value axes only.`,
          ...data.warnings],
        provenance: { source: data.source, preprocessing: snapshot.preprocessing, x, y, display, range,
          mode: data.mode, stride: data.stride, i0: data.i0, i1: data.i1, native_samples: data.n_raw,
          sampled_rows: data.n_sampled, fallback_indices: data.fallback_indices, summary: data.summary,
          x_range: [plot.current.scales.x.min, plot.current.scales.x.max],
          y_range: [plot.current.scales.y.min, plot.current.scales.y.max] }, signal: controller.signal });
    } catch (reason) { if (!controller.signal.aborted) setExportError(errorText(reason)); }
    finally { if (!controller.signal.aborted) setExporting(false); }
  };

  return <>
    <div className={`${styles.recipe} ${styles.xyRecipe}`}>
      <span className={styles.savedBadge}>Saved data</span>
      <span><b>X</b> {filterDescription(x)}</span><span><b>Y</b> {filterDescription(y)}</span>
    </div>
    <div className={styles.plotFrame}>
      <div className={styles.plotToolbar}>
        <span className={styles.range}>Recording interval: {range ? `${number(range[0])} – ${number(range[1])} s` : 'Full recording'}</span>
        <div className={styles.plotActions}>
          {range && <button type="button" onClick={onResetInterval}>Reset interval</button>}
          <button type="button" aria-label="Zoom in XY comparison" title="Zoom in (+)" disabled={!data || empty} onClick={() => navigate(.5)}>+</button>
          <button type="button" aria-label="Zoom out XY comparison" title="Zoom out (−)" disabled={!data || empty} onClick={() => navigate(2)}>−</button>
          <button type="button" aria-label="Reset XY comparison zoom" disabled={!data || empty} onClick={viewportControl.reset}>Reset zoom</button>
          <button type="button" aria-label="Export XY comparison PNG" disabled={!data || empty || exporting} onClick={() => void exportPng()}>{exporting ? 'Exporting…' : 'Export PNG'}</button>
        </div>
      </div>
      <div className={styles.plotArea}>
        <div ref={host} className={`${styles.plot} ${expanded ? styles.expandedPlot : ''}`} tabIndex={0}
          role="region" aria-label="Original and filtered XY comparison plot" onKeyDown={onPlotKeyDown}
          aria-describedby={helpId} aria-busy={loading} />
        {(loading || error || empty) && <div className={styles.plotState} role={error ? 'alert' : 'status'}>
          {loading ? <p>Loading original and filtered XY pairs…</p> : error ? <>
            <p>{error}</p><div className={styles.errorActions}><button type="button" onClick={() => setRetry(value => value + 1)}>Retry XY comparison</button>
              {onReload && <button type="button" onClick={onReload}>Reload saved data</button>}</div>
          </> : <p>No finite X/Y pairs for the visible data. Choose other variables, show both versions, or reset the recording interval.</p>}
        </div>}
      </div>
      <div className={`${styles.readout} ${styles.xyReadout}`} aria-label="XY comparison cursor values">
        <span className={styles.timeValue}>{cursorValue ? `${number(cursorValue.time)} s · Row ${cursorValue.index.toLocaleString()}` : 'Move over a point to inspect'}</span>
        <span className={styles.traceValue} data-hidden={display === 'filtered' || undefined}>
          <i className={styles.originalPoint} style={{ borderColor: themeSeriesColor(ORIGINAL, theme === 'dark') }} />
          Original <strong>X {number(cursorValue?.original.x)} · Y {number(cursorValue?.original.y)}</strong></span>
        <span className={styles.traceValue} data-hidden={display === 'original' || undefined}>
          <i className={styles.filteredPoint} style={{ background: themeSeriesColor(FILTERED, theme === 'dark') }} />
          Filtered <strong>X {number(cursorValue?.filtered.x)} · Y {number(cursorValue?.filtered.y)}</strong></span>
        <span className={styles.difference} title="Filtered minus original at the same native sample row">Difference
          <strong>ΔX {number(cursorValue?.difference.x)} · ΔY {number(cursorValue?.difference.y)}</strong></span>
      </div>
    </div>
    {exportError && <p className={styles.error} role="alert">{exportError}</p>}
    <div className={styles.detailRow}>
      <p id={helpId}>Drag to zoom · Shift-drag to pan · Shift-wheel to zoom · Double-click to reset. Keyboard: + / −, arrows, Home.</p>
      {data && <span>{data.mode === 'sampled' ? `${data.n_sampled.toLocaleString()} of ${data.n_raw.toLocaleString()} native rows · Stride 1:${data.stride}` : `${data.n_raw.toLocaleString()} native rows`}</span>}
    </div>
    <p className={styles.envelopeNote}>XY zoom changes the value axes. Use Time view to choose the recording interval. Both readouts use the same sample row.</p>
    {data?.mode === 'sampled' && <p className={styles.envelopeNote}>This view shows a shared subset of native rows and may omit brief features. Narrow the interval in Time view for more detail.</p>}
    {data && <dl className={styles.summary} aria-label="XY paired sample counts">
      <div><dt>Original pairs shown</dt><dd>{data.summary.original.finite_pairs.toLocaleString()}</dd></div>
      <div><dt>Filtered pairs shown</dt><dd>{data.summary.filtered.finite_pairs.toLocaleString()}</dd></div>
      {!!(data.summary.original.native_missing_pairs || data.summary.filtered.native_missing_pairs) &&
        <div><dt>Missing pairs in interval</dt><dd>{data.summary.original.native_missing_pairs.toLocaleString()} original / {data.summary.filtered.native_missing_pairs.toLocaleString()} filtered</dd></div>}
    </dl>}
    {!!data?.warnings.length && <details className={styles.warnings}><summary>Comparison details ({data.warnings.length})</summary>
      <ul>{data.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details>}
  </>;
}
