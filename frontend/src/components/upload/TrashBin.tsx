import { useCallback, useEffect, useRef, useState } from 'react';
import { deleteTrash, fetchTrash, isAbortError, restoreTrash } from '../../services/api';
import type { TrashEntry } from '../../types';
import { COMPONENT_KINDS, COMPONENT_LABELS, type HardwareComponent } from '../../utils/components';
import { useConfirm } from '../feedback/confirm';
import styles from './TrashBin.module.css';

export function TrashBin({ refreshKey, onRestored, components }: {
  refreshKey: number; onRestored: (name: string) => void; components: HardwareComponent[];
}) {
  const confirm = useConfirm();
  const [entries, setEntries] = useState<TrashEntry[]>([]);
  const [retention, setRetention] = useState<number | null>();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [readError, setReadError] = useState('');
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const alive = useRef(true);
  const operating = useRef(false);
  const generation = useRef(0);
  const reload = useCallback(async (signal?: AbortSignal) => {
    const version = ++generation.current;
    setLoading(true); setReadError('');
    try {
      const data = await fetchTrash(signal);
      if (alive.current && generation.current === version) { setEntries(data.entries); setRetention(data.retention_seconds); }
    } catch (e) {
      if (alive.current && generation.current === version && !isAbortError(e)) setReadError(e instanceof Error ? e.message : String(e));
    } finally { if (alive.current && generation.current === version) setLoading(false); }
  }, []);
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void reload(controller.signal);
    if (refreshKey) setOpen(true);
    return () => { alive.current = false; controller.abort(); };
  }, [refreshKey, reload]);
  useEffect(() => { if (editing) input.current?.focus(); }, [editing]);
  const locked = busy || loading || !!readError;
  const nameError = !/^[A-Za-z0-9._-]{1,200}$/.test(name) || !/[A-Za-z0-9]/.test(name) || name.endsWith('.')
    ? 'Use 1–200 letters, digits, dots, underscores or hyphens; no trailing dot.' : '';
  const restore = async (entry: TrashEntry) => {
    if (operating.current || locked || nameError) return;
    operating.current = true; setBusy(true); setError(''); setNote('');
    try {
      const result = await restoreTrash(entry.id, name);
      onRestored(result.restored);
      if (alive.current) {
        setEditing(null); setNote(`Restored ${result.restored}.`);
        await reload(); toggle.current?.focus();
      }
    } catch (e) { if (alive.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { operating.current = false; if (alive.current) setBusy(false); }
  };
  const remove = async (selected: TrashEntry[], all: boolean) => {
    if (operating.current || locked) return;
    // Capture IDs before confirmation: newly trashed tests are not authorized.
    const ids = selected.map((entry) => entry.id);
    operating.current = true; setBusy(true);
    try {
      if (!(await confirm({ title: all ? `Permanently delete all ${ids.length} trashed tests?` : `Permanently delete '${selected[0].name}'?`,
        description: 'The selected data, notes and time annotations will be removed permanently. This cannot be undone.',
        detail: all ? 'Only the entries currently listed in this trash snapshot will be deleted. Active tests and reusable components are kept.'
          : `Deleted ${selected[0].deleted_at ? new Date(selected[0].deleted_at).toLocaleString() : 'at an unknown time'} · ID ${ids[0]}. Active tests and reusable components are kept.`,
        confirmLabel: all ? 'Delete all permanently' : 'Delete permanently', tone: 'danger' }))) return;
      setError(''); setNote('');
      const result = await deleteTrash(ids);
      if (alive.current) {
        if (result.failures.length) setError(result.failures.map((failure) => `${failure.id.slice(0, 8)}: ${failure.error}`).join('\n'));
        if (result.deleted_ids.length) setNote(`Permanently deleted ${result.deleted_ids.length} trash ${result.deleted_ids.length === 1 ? 'entry' : 'entries'}.`);
        if (editing && result.deleted_ids.includes(editing)) setEditing(null);
        await reload(); toggle.current?.focus();
      }
    } catch (e) { if (alive.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { operating.current = false; if (alive.current) setBusy(false); }
  };
  return <section className={styles.bin} aria-label="Trash bin">
    <div className={styles.header}>
      <h2><button ref={toggle} type="button" className="btn" aria-expanded={open} aria-controls="trash-entries"
        onClick={() => setOpen((value) => !value)}>Trash ({entries.length})</button></h2>
      <button type="button" className="btn" disabled={busy || loading} onClick={() => void reload()}>Refresh trash</button>
    </div>
    {loading && <p role="status">Loading trash…</p>}
    {readError && <p role="alert">Trash list unavailable: {readError}. Use Refresh trash to retry.</p>}
    {error && <p className={styles.error} role="alert">{error}</p>}
    {note && <p role="status">{note}</p>}
    <div id="trash-entries" hidden={!open}>
      <div className={styles.policy}>
        <p>{retention === undefined ? 'Loading retention policy…' : retention === null ? 'Tests stay here until permanently deleted.' : `Tests may expire after ${Math.round(retention / 60)} minutes, on the next test deletion.`} Notes, time annotations and component associations remain with each stored test.</p>
        <button type="button" className="btn" disabled={locked || !entries.length} onClick={() => void remove(entries, true)}>Delete all</button>
      </div>
      {!loading && !readError && !entries.length && <p>The trash is empty.</p>}
      <ul className={styles.entries}>
        {entries.map((entry) => <li key={entry.id} data-trash-id={entry.id} className={styles.entry}>
          <div className={styles.identity}>
            <strong>{entry.name}</strong>
            <span>Deleted {entry.deleted_at ? new Date(entry.deleted_at).toLocaleString() : 'at an unknown time'}{entry.legacy_time_estimated ? ' (estimated)' : ''}</span>
            <span title={entry.id}>ID {entry.id.slice(0, 8)} · {entry.status}{entry.source_file ? ` · ${entry.source_file}` : ''}{entry.uploader_name ? ` · ${entry.uploader_name}` : ''}</span>
            {entry.description && <p className={styles.description} title={entry.description}>{entry.description}</p>}
            {COMPONENT_KINDS.some((kind) => entry.components?.[kind]) && <span>{COMPONENT_KINDS.filter((kind) => entry.components?.[kind]).map((kind) =>
              `${COMPONENT_LABELS[kind]}: ${components.find((item) => item.id === entry.components?.[kind])?.name ?? entry.components?.[kind]}`).join(' · ')}</span>}
            {!entry.restorable && <span className={styles.error}>{entry.state === 'deleting' ? 'Permanent deletion incomplete. Retry deletion to finish.' : entry.error || 'Restore unavailable.'}</span>}
          </div>
          <div className={styles.actions}>
            <button type="button" className="btn" disabled={locked || !entry.restorable} onClick={() => { setEditing(entry.id); setName(entry.name); setError(''); setNote(''); }}>Restore…</button>
            <button type="button" className="btn" disabled={locked} onClick={() => void remove([entry], false)}>{entry.state === 'deleting' ? 'Retry deletion' : 'Delete permanently'}</button>
          </div>
          {editing === entry.id && <form className={styles.restore} onSubmit={(event) => { event.preventDefault(); void restore(entry); }}>
            <label>Restore name<input ref={input} className="input" value={name} disabled={busy} aria-invalid={!!nameError} onChange={(event) => setName(event.target.value)} /></label>
            <p>Use another name if an active test already has this name. Both datasets are preserved.</p>
            {nameError && <p role="status">{nameError}</p>}
            <div className={styles.actions}>
              <button className="btn" disabled={locked || !!nameError || !entry.restorable}>Restore test</button>
              <button type="button" className="btn" disabled={busy} onClick={() => { setEditing(null); toggle.current?.focus(); }}>Cancel restore</button>
            </div>
          </form>}
        </li>)}
      </ul>
    </div>
  </section>;
}
