import { useEffect, useId, useImperativeHandle, useRef, useState, type Ref } from 'react';
import { createPortal } from 'react-dom';
import { useExportTask } from '../../utils/useExportTask';
import { ExportTaskStatus } from './ExportTaskStatus';
import type { PlotExportData } from '../../types';
import styles from './PlotExportControls.module.css';

interface Props {
  actionsRef?: Ref<PlotExportActions>;
  hideTrigger?: boolean;
  label: string;
  contextKey: string;
  scope: string;
  defaultData: PlotExportData;
  originalReason: string | null;
  filteredReason: string | null;
  pngReason: string | null;
  /** Spectrum/XY have one numerical result, without time-domain filter choices. */
  csvLabel?: string;
  csvDescription?: string;
  onCsv: (data: PlotExportData, signal: AbortSignal, includeMetadata: boolean) => Promise<void>;
  onPng: (signal: AbortSignal, includeMetadata: boolean) => Promise<void>;
}
export interface PlotExportActions { open: () => void }

/** Native modal sits outside grid clipping, traps keyboard focus and leaves
 * plot state untouched. Each action captures its own current export context. */
export function PlotExportControls({ label, contextKey, scope, defaultData,
  originalReason, filteredReason, pngReason, csvLabel, csvDescription, onCsv, onPng, actionsRef, hideTrigger = false }: Props) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const task = useExportTask();
  const resetTask = task.reset;
  const busy = task.busy;
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<PlotExportData>(defaultData);
  const [includeMetadata, setIncludeMetadata] = useState(true);
  const context = useRef(contextKey);
  const openDialog = () => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : trigger.current;
    setData(defaultData); resetTask(); setOpen(true);
  };
  useImperativeHandle(actionsRef, () => ({ open: openDialog }));

  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);
  useEffect(() => {
    if (context.current !== contextKey) {
      context.current = contextKey;
      resetTask();
      dialog.current?.close();
      setOpen(false);
    }
  }, [contextKey, resetTask]);

  const close = () => {
    resetTask();
    // A modal makes its trigger inert until close() runs; focus after closing.
    dialog.current?.close();
    setOpen(false);
    (opener.current?.isConnected ? opener.current : trigger.current)?.focus({ preventScroll: true });
  };
  const csvReason = data === 'original' ? originalReason
    : data === 'filtered' ? filteredReason : originalReason || filteredReason;
  const run = (format: 'CSV' | 'PNG') => task.run(format, (signal) =>
    format === 'CSV' ? onCsv(data, signal, includeMetadata) : onPng(signal, includeMetadata));

  return <>
    {!hideTrigger && <button ref={trigger} type="button" className={styles.trigger}
      aria-label={`Export ${label} plot`} aria-haspopup="dialog" aria-expanded={open}
      onClick={openDialog}>Export</button>}
    {open && createPortal(<dialog ref={dialog} className={styles.dialog}
      aria-label={`Export ${label} plot`} aria-describedby={`${id}-scope`}
      onCancel={(event) => { event.preventDefault(); close(); }}
      onClose={() => setOpen(false)}>
      <div className={styles.heading}>
        <strong>Export · {label}</strong>
        <button type="button" aria-label="Close export" onClick={close}>×</button>
      </div>
      <p id={`${id}-scope`}>{scope}</p>
      <label><input type="checkbox" checked={includeMetadata} disabled={!!busy}
        onChange={(event) => setIncludeMetadata(event.target.checked)} /> Include analysis metadata (ZIP)</label>
      <p className={styles.hint}>Includes analysis.json with sources, equations and the settings used. Uncheck for the file alone.</p>
      {csvLabel ? <p><strong>CSV data · {csvLabel}</strong></p> : <fieldset disabled={!!busy}>
        <legend>CSV data</legend>
        {([
          ['original', 'Original', originalReason],
          ['filtered', 'Filtered', filteredReason],
          ['both', 'Original and filtered', originalReason || filteredReason],
        ] as const).map(([value, text, reason]) => <label key={value} title={reason ?? undefined}>
          <input type="radio" name={`${id}-data`} value={value} checked={data === value}
            disabled={!!reason} onChange={() => { setData(value); resetTask(); }} />
          {text}
        </label>)}
      </fieldset>}
      <p>{csvDescription ?? 'CSV contains complete source rows before X zoom, or full-resolution samples in the zoomed X range. Y zoom does not remove rows. Original means stored data, including prior edits.'}</p>
      {filteredReason && <p className={styles.hint}>{filteredReason}</p>}
      <button type="button" className={styles.action} disabled={!!busy || !!csvReason}
        title={csvReason ?? undefined} onClick={() => void run('CSV')}>Download CSV</button>
      {csvReason && csvReason !== filteredReason && <p className={styles.hint}>{csvReason}</p>}
      <div className={styles.imageSection}>
        <strong>PNG image</strong>
        <p>Current traces, axis ranges, labels and analysis settings. Uses the current display,
          independently of the CSV choice above.</p>
        <button type="button" className={styles.action} disabled={!!busy || !!pngReason}
          title={pngReason ?? undefined} onClick={() => void run('PNG')}>Download PNG</button>
        {pngReason && <p className={styles.hint}>{pngReason}</p>}
      </div>
      <ExportTaskStatus task={task} />
    </dialog>, document.body)}
  </>;
}
