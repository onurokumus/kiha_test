import { useEffect, useImperativeHandle, useRef, useState, type Ref } from 'react';
import { createPortal } from 'react-dom';
import type uPlot from 'uplot';
import type { AnnotationManager } from '../../hooks/useAnnotations';
import { annotationTimeLabel, type AnnotationDocument, type AnnotationSource, type TimeAnnotation } from '../../utils/plotAnnotations';
import { normalizeTestText, testTextError, textLength } from '../../utils/testNotes';
import styles from './PlotAnnotations.module.css';

interface Props {
  actionsRef?: Ref<PlotAnnotationActions>;
  hideTrigger?: boolean;
  label: string; sources: AnnotationSource[]; manager: AnnotationManager;
  visible: boolean; onVisibleChange: (visible: boolean) => void;
  getPlot: () => uPlot | null;
}
export interface AnnotationStart { kind: 'marker' | 'interval'; value: number; range: [number, number] }
export interface PlotAnnotationActions { open: (start?: AnnotationStart) => void }
interface Draft { id: string; start: string; end: string; interval: boolean; text: string }
const blank = (): Draft => ({ id: '', start: '', end: '', interval: false, text: '' });
const fromItem = (item: TimeAnnotation): Draft => ({ id: item.id, start: String(item.start_s),
  end: item.end_s === null ? '' : String(item.end_s), interval: item.end_s !== null, text: item.text });

function draftAtView(source: AnnotationSource | undefined, document: AnnotationDocument, start?: AnnotationStart): Draft {
  if (!source || !start) return blank();
  const low = Math.max(document.data_bounds[0], source.bounds?.[0] ?? -Infinity);
  const high = Math.min(document.data_bounds[1], source.bounds?.[1] ?? Infinity);
  const min = Math.max(low, start.range[0] + source.origin);
  const max = Math.min(high, start.range[1] + source.origin);
  const interval = start.kind === 'interval';
  const value = start.value + source.origin;
  // Do not silently move a click outside this TP to its boundary. The user may
  // select another source or enter a stored time in the same guarded editor.
  if (max < min || (!interval && (value < low || value > high))) return { ...blank(), interval };
  return { ...blank(), interval, start: String(interval ? min : value), end: interval ? String(max) : '' };
}

/** The dialog stays outside clipping and uses native keyboard focus trapping.
 * Its document is a snapshot: background refreshes cannot replace a draft. */
export function PlotAnnotations({ label, sources, manager, visible, onVisibleChange, getPlot, actionsRef, hideTrigger = false }: Props) {
  const [open, setOpen] = useState(false);
  const { reload } = manager;
  const [sourceKey, setSourceKey] = useState('');
  const [document, setDocument] = useState<AnnotationDocument>();
  const [draft, setDraft] = useState<Draft>(blank);
  const [baseline, setBaseline] = useState<Draft>(blank);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pending, setPending] = useState<{ text: string; action: () => void } | null>(null);
  const [loadVersion, setLoadVersion] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const textInput = useRef<HTMLTextAreaElement>(null);
  const source = sources.find((item) => item.key === sourceKey) ?? sources[0];
  const test = source?.test;
  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline);
  const initialSignature = useRef('');
  const signature = JSON.stringify(sources.map((s) => [s.key, s.origin, s.bounds]));
  const sourcesRef = useRef(sources);
  sourcesRef.current = sources;
  const creation = useRef<AnnotationStart>();

  const reset = (next = blank()) => { setDraft(next); setBaseline(next); setNotice(''); setPending(null); };
  const close = () => {
    dialog.current?.close(); setOpen(false);
    (opener.current?.isConnected ? opener.current : trigger.current)?.focus({ preventScroll: true });
  };
  const guard = (text: string, action: () => void) => {
    if (busy) return;
    if (dirty) setPending({ text, action }); else action();
  };
  const openDialog = (start?: AnnotationStart) => {
    if (!sources.length) return;
    opener.current = window.document.activeElement instanceof HTMLElement ? window.document.activeElement : trigger.current;
    creation.current = start;
    initialSignature.current = signature; setSourceKey(sources[0].key); reset(); setError(''); setOpen(true);
  };
  useImperativeHandle(actionsRef, () => ({ open: openDialog }));
  useEffect(() => {
    if (open) dialog.current?.showModal(); else dialog.current?.close();
  }, [open]);
  useEffect(() => {
    if (!open || !test) return;
    let alive = true;
    setDocument(undefined); setError(''); setBusy(true);
    void reload(test).then((value) => {
      if (alive) {
        const next = draftAtView(sourcesRef.current.find((s) => s.key === sourceKey), value, creation.current);
        setDocument(value); setDraft(next); setBaseline(next);
      }
    }).catch((e) => { if (alive) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (alive) setBusy(false); });
    return () => { alive = false; };
  }, [open, test, sourceKey, loadVersion, reload]);
  useEffect(() => {
    if (open && initialSignature.current !== signature) {
      // Preserve text for copying rather than silently moving it to a new test/TP.
      setError('The plotted source changed. Close and reopen Notes for the new source.');
    }
  }, [signature, open]);
  useEffect(() => {
    if (!open || (!dirty && !busy)) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [open, dirty, busy]);

  const start = Number(draft.start), end = Number(draft.end);
  const old = document?.annotations.find((item) => item.id === draft.id);
  const sameTimes = old && old.start_s === start && old.end_s === (draft.interval ? end : null);
  const problem = !document ? 'Load saved notes first.'
    : !draft.start.trim() || !Number.isFinite(start) || start < 0 ? 'Enter a finite time in seconds.'
    : draft.interval && (!draft.end.trim() || !Number.isFinite(end) || end <= start) ? 'End must be greater than start.'
    : !sameTimes && (start < document.data_bounds[0] || (draft.interval ? end : start) > document.data_bounds[1])
      ? 'New or moved notes must be within the current test bounds.'
    : !draft.text.trim() ? 'Enter a note.' : testTextError(draft.text, 2000, 'Note');
  const sourceChanged = open && initialSignature.current !== signature;
  const loadFailed = sources.some((s) => manager.states[s.test]?.error);
  const loading = sources.some((s) => manager.states[s.test]?.loading);
  const count = [...new Set(sources.map((s) => s.test))].reduce((n, name) => n + (manager.states[name]?.document?.annotations.length ?? 0), 0);

  const fillFromView = (interval: boolean) => {
    const plot = getPlot();
    if (!source || !document || plot?.scales.x.min == null || plot.scales.x.max == null) return;
    const range: [number, number] = [plot.scales.x.min, plot.scales.x.max];
    const min = Math.max(document.data_bounds[0], source.bounds?.[0] ?? -Infinity, range[0] + source.origin);
    const max = Math.min(document.data_bounds[1], source.bounds?.[1] ?? Infinity, range[1] + source.origin);
    if (max < min) return;
    const next = draftAtView(source, document, { kind: interval ? 'interval' : 'marker', range, value: (min + max) / 2 - source.origin });
    setDraft((d) => ({ ...d, interval: next.interval, start: next.start, end: next.end }));
    textInput.current?.focus();
  };
  const commit = async (items: TimeAnnotation[], message: string) => {
    if (!document || busy || sourceChanged) return;
    setBusy(true); setError(''); setNotice(''); setPending(null);
    try {
      const saved = await manager.save(document, items);
      setDocument(saved); reset(); setNotice(message);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const save = () => {
    if (problem || !document) return;
    const item = { id: draft.id || crypto.randomUUID(), start_s: start,
      end_s: draft.interval ? end : null, text: normalizeTestText(draft.text) };
    void commit(draft.id ? document.annotations.map((a) => a.id === draft.id ? item : a)
      : [...document.annotations, item], 'Annotation saved');
  };

  return <>
    {!hideTrigger && <button ref={trigger} type="button" className={styles.trigger} aria-label={`Time notes for ${label}`}
      aria-haspopup="dialog" aria-expanded={open} disabled={!sources.length}
      title={sources.length ? 'Manage time markers and interval notes' : 'Wait for a time trace with its source timing'}
      onClick={() => openDialog()}>
      Notes{loadFailed ? ' !' : loading ? ' …' : count ? ` ${count}` : ''}
    </button>}
    {open && createPortal(<dialog ref={dialog} className={styles.dialog} aria-label={`Time notes for ${label}`}
      onCancel={(event) => { event.preventDefault(); guard('Close and discard this unsaved note?', close); }}>
      <div className={styles.heading}><strong>Time notes · {label}</strong>
        <button type="button" disabled={busy} onClick={() => guard('Close and discard this unsaved note?', close)} aria-label="Close time notes">×</button></div>
      <label className={styles.toggle}><input type="checkbox" checked={visible} onChange={(e) => onVisibleChange(e.target.checked)} />Show annotations on time plots</label>
      <fieldset disabled={busy || sourceChanged}>
        <label>Source<select value={source?.key ?? ''} onChange={(e) => {
          const next = e.target.value;
          guard('Switch source and discard this unsaved note?', () => { reset(); setSourceKey(next); });
        }}>{sources.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select></label>
        <p>All note times use <strong>stored test seconds</strong>. {source && source.origin !== 0 && <>This TP starts at {annotationTimeLabel(source.origin)} s; its plot shows time relative to that sample.</>}</p>
        {creation.current && <p>Check the source before saving. Times are filled from the clicked time or visible interval for the selected source; a blank time means that view does not intersect this source.</p>}
        {document && <p>Current test bounds: {document.data_bounds.map(annotationTimeLabel).join('–')} s. Notes outside trimmed data are retained.</p>}
        <div className={styles.actions}>
          <button type="button" onClick={() => guard('Discard this draft and start a new note?', () => reset())}>New note</button>
          <button type="button" onClick={() => fillFromView(false)}>Use view center</button>
          <button type="button" onClick={() => fillFromView(true)}>Use visible interval</button>
        </div>
        <div className={styles.times}>
          <label>Kind<select value={draft.interval ? 'interval' : 'marker'} onChange={(e) => setDraft({ ...draft, interval: e.target.value === 'interval' })}>
            <option value="marker">Time marker</option><option value="interval">Interval</option></select></label>
          <label>{draft.interval ? 'Start (test seconds)' : 'Time (test seconds)'}<input type="number" step="any" value={draft.start}
            onChange={(e) => { setDraft({ ...draft, start: e.target.value }); setNotice(''); }} /></label>
          {draft.interval && <label>End (test seconds)<input type="number" step="any" value={draft.end}
            onChange={(e) => { setDraft({ ...draft, end: e.target.value }); setNotice(''); }} /></label>}
        </div>
        <label>Note<textarea ref={textInput} rows={3} value={draft.text}
          onChange={(e) => { setDraft({ ...draft, text: e.target.value }); setNotice(''); }} /></label>
        <p>{textLength(draft.text).toLocaleString()} / 2,000 characters · Marker tags match the saved list below.</p>
        <div className={styles.actions}>
          <button type="button" onClick={save} disabled={!!problem || !dirty || (!draft.id && (document?.annotations.length ?? 0) >= 200)}>Save annotation</button>
          {draft.id && <button type="button" onClick={() => setPending({ text: 'Delete this saved annotation?', action: () => {
            if (document) void commit(document.annotations.filter((a) => a.id !== draft.id), 'Annotation deleted');
          } })}>Delete annotation</button>}
        </div>
        {dirty && problem && <p role="status">{problem}</p>}
        {(document?.annotations.length ?? 0) >= 200 && <p>Limit: 200 annotations per test. Edit or delete an existing note.</p>}
      </fieldset>
      {busy && <p role="status">Loading or saving annotations…</p>}
      {error && <p role="alert" className={styles.error}>{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {pending && <div role="alert" className={styles.confirm}>
        <p>{pending.text}</p><button type="button" onClick={() => { const action = pending.action; setPending(null); action(); }}>Confirm</button>
        <button type="button" onClick={() => setPending(null)}>Keep editing</button>
      </div>}
      <div className={styles.heading}><strong>Saved notes · {document?.annotations.length ?? '—'}</strong>
        <button type="button" disabled={busy || sourceChanged} onClick={() => guard('Reload saved notes and discard this draft?', () => {
          reset(); setLoadVersion((v) => v + 1);
        })}>Reload saved notes</button></div>
      <div className={styles.list}>
        {document?.annotations.map((item, index) => <button type="button" key={item.id} disabled={busy || sourceChanged}
          className={styles.note} aria-label={`Edit annotation A${index + 1}`}
          onClick={() => guard('Discard this draft and edit another note?', () => { reset(fromItem(item)); textInput.current?.focus(); })}>
          <strong>A{index + 1} · {annotationTimeLabel(item.start_s)}{item.end_s === null ? '' : `–${annotationTimeLabel(item.end_s)}`} s</strong>
          {(item.start_s < document.data_bounds[0] || (item.end_s ?? item.start_s) > document.data_bounds[1]) && <span>Outside or partly outside current data</span>}
          <span>{item.text}</span>
        </button>)}
        {document?.annotations.length === 0 && <p>No saved annotations for this test.</p>}
      </div>
    </dialog>, window.document.body)}
  </>;
}
