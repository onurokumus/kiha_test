import { useCallback, useEffect, useRef, useState } from 'react';
import type { ComponentCatalogState } from '../../hooks/useComponentCatalog';
import { componentSetsError, MAX_COMPONENT_SETS, newComponentSet, type ComponentSet } from '../../utils/components';
import { ComponentPicker } from './ComponentPicker';
import styles from './ComponentSetsPicker.module.css';

export function ComponentSetsPicker({ value, onChange, catalog, onDraftChange, columns }: {
  value: ComponentSet[]; onChange: (sets: ComponentSet[]) => void;
  catalog: ComponentCatalogState; onDraftChange: (dirty: boolean) => void;
  /** Omitted before ingestion: the source schema is not yet authoritative. */
  columns?: string[];
}) {
  const [draftIds, setDraftIds] = useState<Set<string>>(new Set());
  const names = useRef(new Map<string, HTMLInputElement>());
  const addButton = useRef<HTMLButtonElement>(null);
  // Component creation can resolve after edits to another set. Merge into the
  // latest draft instead of the render that started the asynchronous request.
  const latest = useRef({ value, onChange });
  latest.current = { value, onChange };
  const reportDraft = useCallback((id: string, dirty: boolean) => setDraftIds(previous => {
    if (previous.has(id) === dirty) return previous;
    const next = new Set(previous); if (dirty) next.add(id); else next.delete(id); return next;
  }), []);
  useEffect(() => { onDraftChange(value.some(set => draftIds.has(set.id))); }, [value, draftIds, onDraftChange]);
  const problem = componentSetsError(value);
  const update = (id: string, changes: Partial<ComponentSet>) => latest.current.onChange(
    latest.current.value.map(set => set.id === id ? { ...set, ...changes } : set));
  const columnPicker = (set: ComponentSet, key: 'rpm_column' | 'motor_temperature_column' | 'power_column', label: string) =>
    <label>{label}<select value={set[key] ?? ''} onChange={event => update(set.id, { [key]: event.target.value || null })}>
      <option value="">Unassigned</option>
      {set[key] && !columns?.includes(set[key]) && <option value={set[key]}>{set[key]} (unavailable)</option>}
      {columns?.map(column => <option key={column} value={column}>{column}</option>)}
    </select></label>;
  return <div className={styles.picker}>
    <p>Each set represents one propeller, motor and ESC with its own signals. A physical component can appear in only one set per test.</p>
    {!value.length && <p>No component sets assigned.</p>}
    {value.map((set, index) => <fieldset key={set.id} data-component-set-id={set.id}
      className={styles.set} aria-label={`Component set ${index + 1}`}>
      <legend>Set {index + 1}</legend>
      <div className={styles.heading}>
        <label>Set name<input value={set.name} ref={node => { if (node) names.current.set(set.id, node); else names.current.delete(set.id); }}
          onChange={event => update(set.id, { name: event.target.value })} /></label>
        <button type="button" className="btn" disabled={draftIds.has(set.id)} onClick={() => {
          onChange(value.filter(item => item.id !== set.id));
          requestAnimationFrame(() => addButton.current?.focus());
        }} aria-label={`Remove ${set.name || 'set'}`}>Remove set</button>
      </div>
      <SetHardware value={set} onChange={components => update(set.id, { components })} catalog={catalog} onDraftChange={reportDraft} />
      {columns && <div className={styles.signals}>
        {columnPicker(set, 'rpm_column', 'RPM column')}
        {columnPicker(set, 'motor_temperature_column', 'Motor temperature column')}
        <label>Motor temperature unit<select value={set.motor_temperature_unit}
          onChange={event => update(set.id, { motor_temperature_unit: event.target.value as ComponentSet['motor_temperature_unit'] })}>
          <option value="C">°C</option><option value="F">°F</option><option value="K">K</option>
        </select></label>
        {columnPicker(set, 'power_column', 'Power column')}
        <label>Power unit<select value={set.power_unit}
          onChange={event => update(set.id, { power_unit: event.target.value as ComponentSet['power_unit'] })}>
          <option value="W">W</option><option value="kW">kW</option>
        </select></label>
      </div>}
    </fieldset>)}
    <button ref={addButton} type="button" className="btn" disabled={value.length >= MAX_COMPONENT_SETS} onClick={() => {
      let index = value.length + 1;
      while (value.some(set => set.name.trim().toLocaleLowerCase() === `set ${index}`)) index += 1;
      const set = newComponentSet(`Set ${index}`); onChange([...value, set]);
      requestAnimationFrame(() => names.current.get(set.id)?.focus());
    }}>Add component set</button>
    {problem && <p role="alert">{problem}</p>}
    {columns ? <p>RPM must already be in revolutions per minute. Finite RPM &gt; 0 defines running for each set.
      Optional temperature and power statistics use that set’s running samples only; select the recorded units explicitly.
      Temperature contributes to the assigned motor, and power to each assigned component. Results use °C and W.
      Power retains the meaning of your selected source signal. Save, then open Components for totals across active tests.</p>
      : <p>Applies to all selected files. Choose each set’s RPM, motor temperature and power columns in Edit after import.</p>}
  </div>;
}

function SetHardware({ value, onChange, catalog, onDraftChange }: {
  value: ComponentSet; onChange: (components: ComponentSet['components']) => void;
  catalog: ComponentCatalogState; onDraftChange: (id: string, dirty: boolean) => void;
}) {
  const report = useCallback((dirty: boolean) => onDraftChange(value.id, dirty), [value.id, onDraftChange]);
  return <ComponentPicker value={value.components} onChange={onChange} catalog={catalog} onDraftChange={report} />;
}
