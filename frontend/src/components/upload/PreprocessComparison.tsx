import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { AXIS_STYLE, PLOT_PADDING, TIME_AXIS_STYLE, plotSeriesColor, themeSeriesColor } from '../../constants/uplotTheme';
import { isAbortError } from '../../services/api';
import { describePreprocessingFilter, type PreprocessingSnapshot } from '../../services/preprocessing';
import { comparisonCursorValues, constrainComparisonRange, fetchPreprocessingComparison,
  type ComparisonEnvelope, type ComparisonSamples, type PreprocessingComparisonData } from '../../services/preprocessingComparison';
import { usePlotTheme } from '../../utils/usePlotTheme';
import { axisTitlesPlugin } from '../../utils/uplotAxisTitle';
import { xPanZoomPlugin, type PanZoomControl } from '../../utils/uplotPanZoom';
import { visibleYAutoFitPlugin, visibleYRange } from '../../utils/visibleYRange';
import { downloadPlotPng } from '../../utils/plotPngExport';
import { SearchableSelect } from '../controls/SearchableSelect';
import styles from './PreprocessComparison.module.css';

interface Props {
  name: string;
  snapshot: PreprocessingSnapshot;
  hasDraftChanges?: boolean;
  onReload?: () => void;
  expanded?: boolean;
}
type Display = 'both' | 'original' | 'filtered';
type Range = [number, number];
const ORIGINAL = '#ae673b';
const FILTERED = '#263685';
const number = (value: number | null | undefined) => value == null || !Number.isFinite(value)
  ? '—' : Number(value.toPrecision(7)).toLocaleString(undefined, { maximumSignificantDigits: 7 });
const sampleValue = (values: (number | null)[] | undefined) => !values ? '—' : values.map(number).join(' to ');
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

/** A new saved revision starts a new comparison, never a mixture of two recipes. */
export default function PreprocessComparison(props: Props) {
  return <SavedComparison key={JSON.stringify([props.name, props.snapshot.source.id, props.snapshot.source.revision])} {...props} />;
}

function SavedComparison({ name, snapshot, hasDraftChanges = false, onReload, expanded = false }: Props) {
  const helpId = useId();
  const columns = snapshot.meta.columns.filter(column => column !== snapshot.meta.time_column);
  const savedFilters = snapshot.preprocessing?.filters ?? [];
  const [column, setColumn] = useState(() => savedFilters.find(entry => columns.includes(entry.column))?.column ?? columns[0] ?? '');
  const [display, setDisplay] = useState<Display>('both');
  const [range, setRange] = useState<Range | null>(null);
  const [retry, setRetry] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; data: PreprocessingComparisonData | null; error: string } | null>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });
  const [cursor, setCursor] = useState<{ key: string; index: number | null } | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const navigation = useRef<PanZoomControl | null>(null);
  const exportingController = useRef<AbortController | null>(null);
  const theme = usePlotTheme(plot);
  const supported = snapshot.preprocessing?.version === 2 && savedFilters.length > 0;
  const px = Math.max(400, Math.round(box.width / 160) * 160);
  const requestKey = JSON.stringify([snapshot.source, column, range, px, retry]);
  const data = loaded?.key === requestKey ? loaded.data : null;
  const error = loaded?.key === requestKey ? loaded.error : '';
  const loading = supported && !!column && (!loaded || loaded.key !== requestKey);
  const currentFilter = savedFilters.find(entry => entry.column === column)?.filter;
  const fullRange: Range = data ? [data.range.start, data.range.end]
    : [snapshot.meta.t_start ?? 0, (snapshot.meta.t_start ?? 0) + snapshot.meta.duration_s];
  const domainRef = useRef(fullRange);
  domainRef.current = fullRange;
  const cursorValue = data && cursor?.key === requestKey ? comparisonCursorValues(data, cursor.index) : null;

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
    if (!supported || !column) return;
    const controller = new AbortController();
    // Delay only navigation/resize churn; stale data is removed immediately by key.
    const timer = window.setTimeout(() => {
      fetchPreprocessingComparison(name, snapshot.source, column, range, px, controller.signal)
        .then(result => { if (!controller.signal.aborted) setLoaded({ key: requestKey, data: result, error: '' }); })
        .catch(reason => { if (!controller.signal.aborted && !isAbortError(reason))
          setLoaded({ key: requestKey, data: null, error: errorText(reason) }); });
    }, 80);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [name, snapshot.source, column, range, px, requestKey, supported]);

  useEffect(() => () => exportingController.current?.abort(), []);

  const commitRange = (next: Range) => {
    const constrained = constrainComparisonRange(next, domainRef.current);
    if (!constrained) return;
    setRange(current => current?.[0] === constrained[0] && current?.[1] === constrained[1] ? current : constrained);
  };
  const commitRef = useRef(commitRange);
  commitRef.current = commitRange;
  const reset = () => { navigation.current?.cancel(); setRange(null); };
  const resetRef = useRef(reset);
  resetRef.current = reset;

  useEffect(() => {
    const element = host.current;
    if (!element || !data || !data.t.length || box.width < 40 || box.height < 40) return;
    const series: uPlot.Series[] = [{}];
    const bands: uPlot.Band[] = [];
    const values: (number | null)[][] = [data.t];
    const addTrace = (kind: 'original' | 'filtered') => {
      const color = kind === 'original' ? ORIGINAL : FILTERED;
      const label = kind === 'original' ? 'Original' : 'Filtered';
      const style: uPlot.Series = { stroke: u => plotSeriesColor(u, color),
        width: kind === 'original' ? 1.3 : 2, dash: kind === 'original' ? [5, 4] : [],
        spanGaps: false, points: { show: false } };
      if (data.mode === 'envelope') {
        const envelope = data[kind] as ComparisonEnvelope;
        const upper = series.length;
        series.push({ ...style, label: `${label} max` }, { ...style, label: `${label} min` });
        bands.push({ series: [upper, upper + 1], fill: u => plotSeriesColor(u, `${color}20`) });
        values.push(envelope.max, envelope.min);
      } else {
        series.push({ ...style, label });
        values.push(data[kind] as ComparisonSamples);
      }
    };
    if (display !== 'filtered') addTrace('original');
    if (display !== 'original') addTrace('filtered');
    const view = range ?? [data.range.start, data.range.end];
    const instance = new uPlot({ width: box.width, height: box.height, padding: PLOT_PADDING,
      series, bands, scales: { x: { time: false, range: () => view }, y: { range: visibleYRange } },
      axes: [{ ...TIME_AXIS_STYLE }, { ...AXIS_STYLE, label: column }],
      legend: { show: false }, cursor: { drag: { x: true, y: false, setScale: false }, points: { show: false },
        bind: { dblclick: () => () => { resetRef.current(); return null; } } },
      plugins: [axisTitlesPlugin(column), visibleYAutoFitPlugin(),
        xPanZoomPlugin(next => commitRef.current(next), undefined, navigation, false, 'scroll')],
      hooks: {
        setSelect: [u => {
          if (u.select.width > 8) {
            const next: Range = [u.posToVal(u.select.left, 'x'), u.posToVal(u.select.left + u.select.width, 'x')];
            u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
            commitRef.current(next);
          }
        }],
        setCursor: [u => setCursor({ key: requestKey, index: u.cursor.left === undefined || u.cursor.left < 0 ? null : u.cursor.idx ?? null })],
      },
    }, values as uPlot.AlignedData, element);
    plot.current = instance;
    const panZoom = navigation.current;
    return () => { panZoom?.cancel(); instance.destroy(); if (plot.current === instance) plot.current = null; };
  }, [data, column, display, range, requestKey, box.width, box.height]);

  const navigate = (factor: number, shift = 0) => {
    const instance = plot.current;
    if (!instance || loading || error) return;
    const start = instance.scales.x.min!, end = instance.scales.x.max!, span = end - start;
    const middle = (start + end) / 2 + span * shift;
    commitRange([middle - span * factor / 2, middle + span * factor / 2]);
  };
  const onPlotKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === 'Home') { event.preventDefault(); reset(); }
    if (event.key === '+' || event.key === '=') { event.preventDefault(); navigate(.5); }
    if (event.key === '-') { event.preventDefault(); navigate(2); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); navigate(1, -.1); }
    if (event.key === 'ArrowRight') { event.preventDefault(); navigate(1, .1); }
  };
  const exportPng = async () => {
    if (!plot.current || !data || exporting) return;
    setExportError(''); setExporting(true);
    const controller = new AbortController();
    exportingController.current = controller;
    try {
      await downloadPlotPng(plot.current, { filename: `${name}_${column}_preprocessing-comparison.png`,
        title: `${name} · ${column} · Original / filtered`, scope: ['Saved preprocessing comparison'],
        details: [currentFilter ? describePreprocessingFilter(currentFilter) : 'No preprocessing filter on this parameter.',
          data.mode === 'envelope' ? `Min/max envelope · ${data.level} native samples per bucket` : 'Native sample pairs',
          ...data.warnings], provenance: { source: data.source, preprocessing: snapshot.preprocessing,
          column, display, mode: data.mode, level: data.level, native_samples: data.n_raw }, signal: controller.signal });
    } catch (reason) { if (!controller.signal.aborted) setExportError(errorText(reason)); }
    finally { if (!controller.signal.aborted) setExporting(false); }
  };
  const changedParameter = (next: string) => { navigation.current?.cancel(); setColumn(next); setExportError(''); };
  const empty = !!data && ![data.original, data.filtered].some(values =>
    (data.mode === 'raw' ? values as ComparisonSamples : [...(values as ComparisonEnvelope).min, ...(values as ComparisonEnvelope).max])
      .some(value => value !== null));

  if (!supported) return <p className={styles.state}>Apply preprocessing filters to compare the saved result with its original data.</p>;

  return <section className={styles.comparison} aria-label="Saved preprocessing comparison">
    {hasDraftChanges && <p className={styles.draftNotice} role="status">You have unapplied filter changes. This comparison shows the currently saved result.</p>}
    <div className={styles.controls}>
      <div className={styles.parameter}>
        <span>Parameter</span>
        <SearchableSelect value={column} onChange={changedParameter} ariaLabel="Comparison parameter"
          options={columns.map(value => ({ value, label: value,
            description: savedFilters.some(entry => entry.column === value) ? 'Preprocessed' : 'Unchanged',
            group: savedFilters.some(entry => entry.column === value) ? 'Preprocessed parameters' : 'Other parameters' }))}
          optionNoun="parameter" searchPlaceholder="Find a parameter…" menuMaxWidth={560} />
      </div>
      <div className={styles.display} role="group" aria-label="Comparison traces">
        {(['both', 'original', 'filtered'] as const).map(mode => <button type="button" key={mode}
          aria-pressed={display === mode} onClick={() => setDisplay(mode)}>{mode === 'both' ? 'Both' : mode === 'original' ? 'Original' : 'Filtered'}</button>)}
      </div>
    </div>
    <div className={styles.recipe}>
      <span className={styles.savedBadge}>Saved data</span>
      <span className={currentFilter ? styles.recipeBadge : styles.unchangedBadge}>{currentFilter ? 'Applied filter' : 'Unchanged'}</span>
      <span>{currentFilter ? describePreprocessingFilter(currentFilter) : 'This parameter has no preprocessing filter; both traces are identical.'}</span>
    </div>
    <div className={styles.plotFrame}>
      <div className={styles.plotToolbar}>
        <span className={styles.range}>{range ? `${number(range[0])} – ${number(range[1])} s` : 'Full recording'}</span>
        <div className={styles.plotActions}>
          <button type="button" aria-label="Zoom in comparison" title="Zoom in (+)" disabled={!data || empty} onClick={() => navigate(.5)}>+</button>
          <button type="button" aria-label="Zoom out comparison" title="Zoom out (−)" disabled={!data || empty} onClick={() => navigate(2)}>−</button>
          <button type="button" aria-label="Reset comparison zoom" disabled={!range} onClick={reset}>Reset zoom</button>
          <button type="button" disabled={!data || empty || exporting} onClick={() => void exportPng()} aria-label="Export comparison PNG">{exporting ? 'Exporting…' : 'Export PNG'}</button>
        </div>
      </div>
      <div className={styles.plotArea}>
        <div ref={host} className={`${styles.plot} ${expanded ? styles.expandedPlot : ''}`} tabIndex={0}
          role="region" aria-label="Original and filtered comparison plot" onKeyDown={onPlotKeyDown}
          aria-describedby={helpId} aria-busy={loading} />
        {(loading || error || empty) && <div className={styles.plotState} role={error ? 'alert' : 'status'}>
          {loading ? <p>Loading original and filtered data…</p> : error ? <>
            <p>{error}</p><div className={styles.errorActions}><button type="button" onClick={() => setRetry(value => value + 1)}>Retry comparison</button>
              {onReload && <button type="button" onClick={onReload}>Reload saved data</button>}</div>
          </> : <p>No finite samples in this time range. Reset zoom or choose another parameter.</p>}
        </div>}
      </div>
      <div className={styles.readout} aria-label="Comparison cursor values">
        <span className={styles.timeValue}>{cursorValue ? `${number(cursorValue.time)} s` : 'Move over the plot to inspect'}</span>
        <span className={styles.traceValue} data-hidden={display === 'filtered' || undefined}>
          <i className={styles.originalSwatch} style={{ borderColor: themeSeriesColor(ORIGINAL, theme === 'dark') }} />Original <strong>{sampleValue(cursorValue?.original)}</strong></span>
        <span className={styles.traceValue} data-hidden={display === 'original' || undefined}>
          <i className={styles.filteredSwatch} style={{ borderColor: themeSeriesColor(FILTERED, theme === 'dark') }} />Filtered <strong>{sampleValue(cursorValue?.filtered)}</strong></span>
        <span className={styles.difference} title="Filtered minus original at the same native sample">Difference <strong>{data?.mode === 'envelope' ? 'Zoom in for samples' : number(cursorValue?.difference)}</strong></span>
      </div>
    </div>
    {exportError && <p className={styles.error} role="alert">{exportError}</p>}
    <div className={styles.detailRow}>
      <p id={helpId}>Drag to zoom · Shift-drag to pan · Double-click to reset. Keyboard: + / −, arrows, Home.</p>
      {data && <span>{data.mode === 'envelope' ? `Min/max envelope · ${data.level.toLocaleString()} samples / bucket` : 'Native samples'} · {data.n_raw.toLocaleString()} in view</span>}
    </div>
    {data?.mode === 'raw' && data.summary && <dl className={styles.summary} aria-label="Native sample differences">
      <div><dt>RMS difference</dt><dd>{number(data.summary.rms_difference)}</dd></div>
      <div><dt>Maximum absolute difference</dt><dd>{number(data.summary.max_abs_difference)}</dd></div>
      <div><dt>Valid sample pairs</dt><dd>{data.summary.finite_pairs.toLocaleString()}</dd></div>
      {!!data.summary.missing_pairs && <div><dt>Missing pairs excluded</dt><dd>{data.summary.missing_pairs.toLocaleString()}</dd></div>}
    </dl>}
    {data?.mode === 'envelope' && <p className={styles.envelopeNote}>Each band shows its own minimum and maximum. Zoom in to inspect paired samples and exact differences.</p>}
    {!!data?.warnings.length && <details className={styles.warnings}><summary>Comparison details ({data.warnings.length})</summary>
      <ul>{data.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details>}
  </section>;
}
