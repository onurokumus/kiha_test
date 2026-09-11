import { useEffect, useId, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { useExportTask } from '../../utils/useExportTask';
import { ExportTaskStatus } from './ExportTaskStatus';
import type { PlotExportData } from '../../types';
import { downloadPlotCsvBundle } from '../../utils/plotExport';
import { downloadPlotLayoutPng } from '../../utils/plotPngExport';
import type { PlotExportHandle, PlotExportRegistry } from '../../utils/plotExportRegistry';
import base from './PlotExportControls.module.css';
import styles from './MultiPlotExportControls.module.css';

interface Props {
  registry: PlotExportRegistry;
  contextKey: string;
  defaultColumns: 2 | 3;
  disabledReason: string | null;
  title: string;
  kind?: 'time' | 'spectrum' | 'xy' | 'waterfall';
}
const csvReason = (handle: PlotExportHandle, data: PlotExportData) => data === 'original'
  ? handle.originalReason : data === 'filtered' ? handle.filteredReason
    : handle.originalReason || handle.filteredReason;
const choices = [['original', 'Original'], ['filtered', 'Filtered'], ['both', 'Original and filtered']] as const;

export function MultiPlotExportControls({ registry, contextKey, defaultColumns, disabledReason, title, kind = 'time' }: Props) {
  const fixedChoice = kind === 'waterfall' ? 'Waterfall grid magnitudes' : kind === 'spectrum' ? 'Native spectral bins' : kind === 'xy' ? 'Original finite pairs' : null;
  const plots = useSyncExternalStore(registry.subscribe, registry.getSnapshot);
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const task = useExportTask();
  const resetTask = task.reset;
  const busy = task.busy;
  const context = useRef(contextKey);
  const [open, setOpen] = useState(false);
  const [columns, setColumns] = useState<2 | 3>(defaultColumns);
  const [selected, setSelected] = useState<number[]>([]);
  const [data, setData] = useState<Record<number, PlotExportData>>({});
  const [includeMetadata, setIncludeMetadata] = useState(true);

  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);
  useEffect(() => {
    if (context.current === contextKey) return;
    context.current = contextKey;
    resetTask();
    dialog.current?.close();
    setOpen(false);
  }, [contextKey, resetTask]);

  const close = () => {
    resetTask();
    dialog.current?.close();
    setOpen(false); trigger.current?.focus();
  };
  const clearFeedback = resetTask;
  const selectedPlots = plots.filter((entry) => selected.includes(entry.slot));
  const selectionReason = selected.length === 0 ? 'Select at least one plot.'
    : selected.length > columns * columns ? `Select at most ${columns * columns} plots for a ${columns} × ${columns} layout.`
      : selectedPlots.length !== selected.length ? 'A selected plot is no longer available. Close and reopen export.' : null;
  const csvBlocked = selectionReason || selectedPlots.map(({ slot, handle }) => {
    const reason = csvReason(handle, data[slot] ?? handle.defaultData);
    return reason ? `Plot ${slot}: ${reason}` : null;
  }).find(Boolean);
  const pngBlocked = selectionReason || selectedPlots.map(({ slot, handle }) => handle.pngReason
    ? `Plot ${slot}: ${handle.pngReason}` : null).find(Boolean);

  const run = async (format: 'CSV ZIP' | 'PNG') => {
    if (busy || selectionReason) return;
    await task.run(format, async (signal) => {
      // Take one current registry snapshot; never silently drop an unmounted slot.
      const current = registry.getSnapshot().filter((entry) => selected.includes(entry.slot));
      if (current.length !== selected.length) throw new Error('A selected plot is no longer available.');
      if (format === 'CSV ZIP') {
        const requests = current.map(({ slot, handle }) => {
          try { return { slot, request: handle.buildCsvRequest(data[slot] ?? handle.defaultData) }; }
          catch (cause) { throw new Error(`Plot ${slot}: ${cause instanceof Error ? cause.message : 'Export is unavailable.'}`); }
        });
        await downloadPlotCsvBundle({ layout: columns === 2 ? '2x2' : '3x3', plots: requests, include_metadata: includeMetadata }, signal);
      } else {
        await downloadPlotLayoutPng({ filename: `${kind}-plots_${columns}x${columns}.png`,
          title: `${title} · ${columns} × ${columns}`, columns, signal, includeMetadata,
          plots: current.map(({ slot, handle }) => ({ slot, capture: handle.capturePng })),
        });
      }
    });
  };

  return <>
    <button ref={trigger} type="button" className={`${base.trigger} ${styles.trigger}`}
      disabled={!!disabledReason || plots.length === 0} title={disabledReason ?? undefined}
      aria-haspopup="dialog" aria-expanded={open} onClick={() => {
        setSelected(plots.map((entry) => entry.slot));
        setData(Object.fromEntries(plots.map(({ slot, handle }) => [slot, handle.defaultData])));
        setColumns(defaultColumns); clearFeedback(); setOpen(true);
      }}>Export selected plots</button>
    {open && createPortal(<dialog ref={dialog} className={`${base.dialog} ${styles.dialog}`}
      aria-label="Export selected plots" aria-describedby={`${id}-scope`}
      onCancel={(event) => { event.preventDefault(); close(); }} onClose={() => setOpen(false)}>
      <div className={base.heading}>
        <strong>Export selected plots</strong>
        <button type="button" onClick={close}>Close</button>
      </div>
      <p id={`${id}-scope`}>Choose visible grid slots. Each CSV keeps its own variable, sources and analysis settings.
        Plots are packed in grid order, with their original slot numbers.</p>
      <label><input type="checkbox" checked={includeMetadata} disabled={!!busy}
        onChange={(event) => setIncludeMetadata(event.target.checked)} /> Include analysis metadata (ZIP)</label>
      <p className={base.hint}>Includes analysis.json for the selected plots. PNG and metadata download together in one ZIP.</p>
      <fieldset disabled={!!busy}>
        <legend>Layout</legend>
        <div className={styles.layoutChoice}>
          {([2, 3] as const).map((count) => <label key={count}>
            <input type="radio" name={`${id}-layout`} checked={columns === count}
              onChange={() => { setColumns(count); clearFeedback(); }} />{count} × {count}
          </label>)}
        </div>
        <div className={styles.preview} style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }} aria-label="Export layout preview">
          {Array.from({ length: columns * columns }, (_, index) => <span key={index}>
            {selectedPlots[index] ? `Plot ${selectedPlots[index].slot}` : 'Empty'}
          </span>)}
        </div>
      </fieldset>
      <fieldset disabled={!!busy}>
        <legend>Plots and CSV data</legend>
        <div className={styles.selectionActions}>
          <button type="button" onClick={() => { setSelected(plots.map((entry) => entry.slot)); clearFeedback(); }}>Select all</button>
          <button type="button" onClick={() => { setSelected([]); clearFeedback(); }}>Clear selection</button>
          <span>{selected.length} selected</span>
        </div>
        {plots.map(({ slot, handle }) => <div key={slot} className={styles.plot}>
          <div className={styles.plotChoice}>
            <label><input type="checkbox" checked={selected.includes(slot)}
              aria-label={`Select plot ${slot}: ${handle.label}`} onChange={(event) => {
                setSelected((previous) => event.target.checked ? [...previous, slot] : previous.filter((value) => value !== slot));
                clearFeedback();
              }} /><span>Plot {slot} · {handle.label}</span></label>
            {fixedChoice ? <span>{fixedChoice}</span> : <select aria-label={`CSV data for plot ${slot}`} disabled={!selected.includes(slot)}
              value={data[slot] ?? handle.defaultData} onChange={(event) => {
                setData((previous) => ({ ...previous, [slot]: event.target.value as PlotExportData })); clearFeedback();
              }}>
              {choices.map(([value, label]) => <option key={value} value={value}
                disabled={!!csvReason(handle, value)}>{label}</option>)}
            </select>}
          </div>
          {selected.includes(slot) && <>
            <p className={styles.scope}>{handle.scope}</p>
            {(handle.originalReason || handle.filteredReason || handle.pngReason) && <p className={base.hint}>
              {[handle.originalReason, handle.filteredReason, handle.pngReason].filter((value, index, all) => value && all.indexOf(value) === index).join(' ')}
            </p>}
          </>}
        </div>)}
      </fieldset>
      <p>{kind === 'waterfall' ? 'CSV ZIP contains one waterfall grid per selected plot. Each row records linear magnitude and the explicit time/frequency bounds of a cell intersecting the viewport. Aggregated cells contain maxima across windows and bins. Source and FFT settings are included; log color does not transform CSV values.'
        : kind === 'xy' ? 'CSV ZIP contains one full-resolution paired-data CSV per plot. Only finite X/Y pairs are included, in original sample order, without interpolation or temporary filters. Default views include every pair; zoom and pan crop both axes. Each source keeps its own sample/time identifiers.'
        : kind === 'spectrum' ? 'CSV ZIP contains one native-bin CSV per selected plot. Independent source grids retain Hz, linear values and method details. Frequency zoom crops bin centers after estimation; order maps X using each source’s mean RPM. Log Y never transforms CSV values.'
        : 'CSV ZIP contains one full-resolution CSV per selected plot. Unzoomed plots use complete source rows; X zoom crops sample centers after filtering. Y zoom does not remove rows. Original means stored data, including prior edits.'}</p>
      <button type="button" className={base.action} disabled={!!busy || !!csvBlocked}
        title={csvBlocked ?? undefined} onClick={() => void run('CSV ZIP')}>Download CSV ZIP</button>
      {csvBlocked && <p className={base.hint}>{csvBlocked}</p>}
      <div className={base.imageSection}>
        <strong>Combined PNG</strong>
        <p>Captures current traces and X/Y ranges, with source labels, settings and legends for every plot.
          CSV data choices above do not change the image. Unused cells remain empty.</p>
        <button type="button" className={base.action} disabled={!!busy || !!pngBlocked}
          title={pngBlocked ?? undefined} onClick={() => void run('PNG')}>Download combined PNG</button>
        {pngBlocked && <p className={base.hint}>{pngBlocked}</p>}
      </div>
      <ExportTaskStatus task={task} />
    </dialog>, document.body)}
  </>;
}
