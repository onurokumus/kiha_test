import { useEffect, useRef, useState } from 'react';
import type { ComponentCatalogState } from '../../hooks/useComponentCatalog';
import { COMPONENT_KINDS, COMPONENT_LABELS, type ComponentIds, type ComponentKind } from '../../utils/components';
import { testTextError } from '../../utils/testNotes';
import styles from './ComponentPicker.module.css';

export function ComponentPicker({ value, onChange, catalog, onDraftChange }: {
  value: ComponentIds; onChange: (value: ComponentIds) => void;
  catalog: ComponentCatalogState; onDraftChange: (dirty: boolean) => void;
}) {
  const [adding, setAdding] = useState<ComponentKind | null>(null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const selects = useRef<Partial<Record<ComponentKind, HTMLSelectElement | null>>>({});
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { onDraftChange(adding !== null || busy); }, [adding, busy, onDraftChange]);
  useEffect(() => { if (adding) input.current?.focus(); }, [adding]);
  const normalized = name.normalize('NFC').trim();
  const problem = !normalized ? 'Enter a component name.' : /[\r\n\t]/.test(name)
    ? 'Use a single-line name without tabs.' : testTextError(normalized, 120, 'Component name');
  const create = async () => {
    if (!adding || busy || problem) return;
    setBusy(true); setError('');
    try {
      const item = await catalog.add(adding, normalized);
      if (alive.current) {
        onChange({ ...value, [adding]: item.id }); setAdding(null); setName('');
        requestAnimationFrame(() => { if (alive.current) selects.current[adding]?.focus(); });
      }
    } catch (e) { if (alive.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (alive.current) setBusy(false); }
  };
  return <div className={styles.picker} role="group" aria-label="Component associations">
    <p>Choose the individual hardware used in this test. Unassigned is allowed.</p>
    <div className={styles.fields}>
      {COMPONENT_KINDS.map((kind) => {
        const items = catalog.items.filter((item) => item.kind === kind).sort((a, b) => a.name.localeCompare(b.name));
        const missing = value[kind] && !items.some((item) => item.id === value[kind]);
        return <label key={kind}>{COMPONENT_LABELS[kind]}
          <select ref={(element) => { selects.current[kind] = element; }} value={adding === kind ? '__new' : value[kind] ?? ''} disabled={busy || catalog.loading || !!catalog.error || (adding !== null && adding !== kind)}
            onChange={(event) => {
              setError('');
              if (event.target.value === '__new') { setAdding(kind); setName(''); }
              else { setAdding(null); setName(''); onChange({ ...value, [kind]: event.target.value || null }); }
            }}>
            <option value="">Unassigned</option>
            {missing && <option value={value[kind]!}>Unavailable component · {value[kind]}</option>}
            {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            <option value="__new">Add new…</option>
          </select>
        </label>;
      })}
    </div>
    {catalog.loading && <span role="status">Loading components…</span>}
    {catalog.error && <div role="alert">Component list unavailable: {catalog.error}
      <button type="button" className="btn" onClick={() => void catalog.reload()}>Reload components</button></div>}
    {!catalog.error && !catalog.loading && COMPONENT_KINDS.some((kind) => value[kind] && !catalog.items.some((item) => item.id === value[kind] && item.kind === kind)) &&
      <p role="alert">An assigned component is unavailable. Its ID is retained; choose a listed component or Unassigned to correct it.</p>}
    {adding && <div className={styles.creation}>
      <label>New {COMPONENT_LABELS[adding].toLowerCase()} name
        <input ref={input} className="input" value={name} disabled={busy} aria-invalid={!!name && !!problem}
          onChange={(event) => { setName(event.target.value); setError(''); }}
          onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void create(); } }} /></label>
      <p>Adding saves a reusable component to the shared list. Matching names of the same type reuse the existing component.</p>
      {name && problem && <span role="status">{problem}</span>}
      {error && <span role="alert">{error}</span>}
      <div className={styles.actions}>
        <button type="button" className="btn" disabled={busy || !!problem} onClick={() => void create()}>{busy ? 'Adding…' : 'Add component'}</button>
        <button type="button" className="btn" disabled={busy} onClick={() => {
          selects.current[adding]?.focus(); setAdding(null); setName(''); setError('');
        }}>Cancel new component</button>
      </div>
    </div>}
  </div>;
}
