import { useEffect, useId, useRef, useState } from 'react';
import { buildFilterSpec, DEFAULT_FILTER_UI, FILTER_LABELS, type FilterUi } from '../../constants/filters';
import { exportCsvUrl, getJson, isAbortError, rawCsvUrl } from '../../services/api';
import { createPreprocessedTest, describePreprocessingFilter, fetchPreprocessing, matchesPreprocessingRequest, preprocessingFilterError,
  preprocessingNameError, serializePreprocessingFilter, suggestPreprocessingName,
  type PreprocessingSnapshot } from '../../services/preprocessing';
import type { TestInfo } from '../../types';
import { FilterRow } from '../controls/FilterRow';
import styles from './PreprocessDialog.module.css';

interface Props {
  test: TestInfo;
  existingNames: string[];
  onClose: () => void;
  onCreated: (name: string) => void;
  onOpenTest: (name: string) => void;
}

const HELP: Record<string, string> = {
  '': 'Choose a filter for this parameter. Parameters set to None are copied unchanged.',
  lowpass: 'Reduce frequencies above the cutoff with a zero-phase Butterworth filter.',
  highpass: 'Reduce frequencies below the cutoff with a zero-phase Butterworth filter.',
  bandpass: 'Keep the frequency band between the two cutoffs with a zero-phase Butterworth filter.',
  bandstop: 'Reduce the frequency band between the two cutoffs with a zero-phase Butterworth filter.',
  moving_avg: 'Smooth this parameter using a centered moving average over the selected duration.',
  despike: 'Replace short spikes relative to the local median. Longer events remain in the data.',
  detrend: 'Remove the linear trend from this parameter over the full recording, respecting acquisition gaps.',
};
const displayNumber = (value: number) => Number(value.toPrecision(7)).toLocaleString();
const errorText = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
// CSV parameter names are arbitrary, including Object.prototype property names.
const filterDraft = (drafts: Record<string, FilterUi>, column: string): FilterUi =>
  Object.prototype.hasOwnProperty.call(drafts, column) ? drafts[column] : DEFAULT_FILTER_UI;
const sampleCount = (counts: Record<string, number> | undefined, column: string): number =>
  typeof counts?.[column] === 'number' ? counts[column] : 0;

export default function PreprocessDialog({ test, existingNames, onClose, onCreated, onOpenTest }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const alive = useRef(true);
  const submitting = useRef(false);
  const createdCallback = useRef(onCreated);
  createdCallback.current = onCreated;
  const id = useId();
  const [snapshot, setSnapshot] = useState<PreprocessingSnapshot | null>(null);
  const [loadKey, setLoadKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [drafts, setDrafts] = useState<Record<string, FilterUi>>({});
  const [selected, setSelected] = useState('');
  const [bulkSelection, setBulkSelection] = useState<string[]>([]);
  const [bulkEditing, setBulkEditing] = useState(false);
  const [bulkUi, setBulkUi] = useState<FilterUi>({ ...DEFAULT_FILTER_UI });
  const [bulkDirty, setBulkDirty] = useState(false);
  const [bulkNote, setBulkNote] = useState('');
  const selectAllRef = useRef<HTMLInputElement>(null);
  const editorRef = useRef<HTMLElement>(null);
  const [query, setQuery] = useState('');
  const [configuredOnly, setConfiguredOnly] = useState(false);
  const [name, setName] = useState(() => suggestPreprocessingName(test.name, existingNames));
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [createdName, setCreatedName] = useState('');
  const [job, setJob] = useState<TestInfo | null>(null);
  const [statusError, setStatusError] = useState('');
  const [pollKey, setPollKey] = useState(0);

  useEffect(() => {
    alive.current = true;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const modal = dialog.current;
    modal?.showModal();
    return () => {
      alive.current = false;
      modal?.close();
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setLoadError('');
    fetchPreprocessing(test.name, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      setSnapshot(data);
      setSelected(current => data.meta.columns.includes(current) && current !== data.meta.time_column
        ? current : data.meta.columns.find(column => column !== data.meta.time_column) ?? '');
    }).catch(reason => {
      if (!controller.signal.aborted && !isAbortError(reason)) setLoadError(errorText(reason));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [test.name, loadKey]);

  useEffect(() => {
    if (!createdName) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setStatusError('');
    const poll = async () => {
      try {
        const tests = await getJson<TestInfo[]>('/tests', controller.signal);
        if (controller.signal.aborted) return;
        const current = tests.find(entry => entry.name === createdName);
        if (!current) throw new Error('The filtered copy is not in the test library. Refresh status or check Uploads.');
        setJob(current);
        if (current.status === 'ready' || current.status === 'error') {
          createdCallback.current(createdName);
          return;
        }
        timer = setTimeout(() => void poll(), 1200);
      } catch (reason) {
        if (!controller.signal.aborted && !isAbortError(reason)) setStatusError(errorText(reason));
      }
    };
    void poll();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [createdName, pollKey]);

  const meta = snapshot?.meta;
  const recipe = createdName ? job?.preprocessing : snapshot?.preprocessing;
  const readonly = !!snapshot?.preprocessing;
  const parameters = meta?.columns.filter(column => column !== meta.time_column) ?? [];
  const configured = parameters.filter(column => !!filterDraft(drafts, column).kind);
  const unavailable = Object.keys(drafts).filter(column => !!drafts[column]?.kind && !parameters.includes(column));
  const errors = new Map(configured.map(column => [column,
    preprocessingFilterError(drafts[column], meta!.fs_hz, meta!.n_rows)]));
  const invalid = configured.filter(column => !!errors.get(column));
  const visible = parameters.filter(column => (!configuredOnly || filterDraft(drafts, column).kind)
    && column.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const bulkTargets = parameters.filter(column => bulkSelection.includes(column));
  const hiddenSelected = bulkTargets.filter(column => !visible.includes(column)).length;
  const allSelected = parameters.length > 0 && bulkTargets.length === parameters.length;
  const isBulk = bulkEditing && bulkTargets.length > 0;
  const currentUi = isBulk ? bulkUi : filterDraft(drafts, selected);
  const currentError = isBulk && meta ? preprocessingFilterError(bulkUi, meta.fs_hz, meta.n_rows) : errors.get(selected);
  const missingCount = sampleCount(meta?.nan_counts, selected) + sampleCount(meta?.inf_counts, selected);
  const nameError = preprocessingNameError(name, existingNames);
  const sizeError = snapshot && snapshot.meta.n_rows > snapshot.max_samples
    ? `This recording has ${snapshot.meta.n_rows.toLocaleString()} samples. Pre-processing supports up to ${snapshot.max_samples.toLocaleString()} samples per recording.` : '';
  const done = job?.status === 'ready';
  const failed = job?.status === 'error';
  const busy = !!createdName && !done && !failed;
  const hasJob = !!createdName;
  const displayedName = createdName || test.name;
  const readyRecipe = readonly || done;
  const canSave = !!snapshot && !readonly && !loading && !loadError && !saving && !hasJob && !sizeError
    && !nameError && configured.length > 0 && invalid.length === 0 && unavailable.length === 0 && !bulkDirty;
  const progress = job?.preprocessing_progress;
  const progressText = progress?.stage || 'Processing the full recording';

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = bulkTargets.length > 0 && !allSelected;
  }, [bulkTargets.length, allSelected, loading, hasJob, readonly]);

  const selectBulk = (columns: string[]) => {
    const template = !bulkTargets.length ? filterDraft(drafts, selected) : bulkUi;
    if (!bulkTargets.length && columns.length) setBulkUi({ ...template });
    // Changing the target set can introduce unapplied settings even when the
    // template was already applied to the previous group. Never let Save imply
    // that newly checked parameters received those settings automatically.
    const intended = template.kind ? JSON.stringify(buildFilterSpec(template)) : null;
    const differs = columns.some(column =>
      (filterDraft(drafts, column).kind ? JSON.stringify(buildFilterSpec(filterDraft(drafts, column))) : null) !== intended);
    setBulkDirty(columns.length > 0 && (bulkDirty || differs || (!!template.kind && !buildFilterSpec(template))));
    setBulkSelection(columns);
    setBulkEditing(columns.length > 0);
    setBulkNote('');
  };
  const applyBulk = () => {
    if (!meta || !bulkTargets.length || preprocessingFilterError(bulkUi, meta.fs_hz, meta.n_rows) || saving) return;
    setDrafts(current => {
      const next = new Map(Object.entries(current));
      for (const column of bulkTargets) {
        if (bulkUi.kind) next.set(column, { ...bulkUi });
        else next.delete(column);
      }
      return Object.fromEntries(next);
    });
    setBulkDirty(false); setSaveError('');
    setBulkNote(`${bulkUi.kind ? `Applied ${FILTER_LABELS[bulkUi.kind]} to` : 'Removed filters from'} ${bulkTargets.length} parameter${bulkTargets.length === 1 ? '' : 's'}.`);
  };
  const discardBulk = () => { setBulkUi({ ...DEFAULT_FILTER_UI }); setBulkDirty(false); setBulkNote(''); setBulkEditing(false); };
  const focusFilterEditor = () => requestAnimationFrame(() => editorRef.current?.querySelector('select')?.focus());

  const save = async () => {
    if (!canSave || !snapshot || submitting.current) return;
    submitting.current = true; setSaving(true); setSaveError('');
    const request = {
      name: name.trim(), source_id: snapshot.source.id, source_revision: snapshot.source.revision,
      filters: configured.map(column => ({ column, filter: serializePreprocessingFilter(buildFilterSpec(drafts[column])!) })),
    };
    const accepted = (result: TestInfo) => {
      createdCallback.current(result.name);
      if (alive.current) { setJob(result); setCreatedName(result.name); }
    };
    try {
      accepted(await createPreprocessedTest(test.name, request));
    } catch (reason) {
      try {
        const tests = await getJson<TestInfo[]>('/tests');
        const recovered = tests.find(entry => entry.name === request.name && matchesPreprocessingRequest(entry.preprocessing, request));
        if (recovered) { accepted(recovered); return; }
      } catch { /* Preserve the original request error when recovery is offline. */ }
      if (alive.current) setSaveError(errorText(reason));
    } finally {
      submitting.current = false;
      if (alive.current) setSaving(false);
    }
  };
  const retryAsNew = () => {
    setName(suggestPreprocessingName(test.name, [...existingNames, createdName]));
    setCreatedName(''); setJob(null); setStatusError(''); setSaveError('');
    setLoadKey(value => value + 1);
  };

  return <dialog ref={dialog} className={styles.dialog} aria-labelledby={`${id}-title`}
    aria-describedby={`${id}-description`} onCancel={event => { event.preventDefault(); if (!saving) onClose(); }}>
    <div className={styles.shell}>
      <header className={styles.header}>
        <div className={styles.heading}>
          <span className={styles.headerIcon} aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 7h16M4 17h16M8 4v6m8 4v6" /></svg></span>
          <div><h2 id={`${id}-title`}>{readonly ? 'Pre-processing details' : 'Pre-process test'}</h2>
            <p className={styles.sourceName} title={test.name}>{test.name}</p></div>
        </div>
        <button type="button" className={styles.close} aria-label="Close pre-process" disabled={saving} onClick={onClose}>×</button>
      </header>

      <div className={styles.body}>
        <p id={`${id}-description`} className={styles.intro}>{readonly
          ? 'This test is a saved filtered copy. Its source and processing settings are recorded below.'
          : 'Apply a filter to each chosen parameter across the full recording. Save a separate filtered test and keep the source.'}</p>
        {loading && <div className={styles.state} role="status">Loading native data details…</div>}
        {loadError && <div className={styles.error} role="alert"><p>Could not load pre-processing: {loadError}</p>
          <button type="button" className="btn" onClick={() => setLoadKey(value => value + 1)}>Retry loading</button></div>}

        {meta && !loading && !loadError && <>
          <div className={styles.scope} aria-label="Processing scope">
            <span><strong>{meta.n_rows.toLocaleString()}</strong> native samples</span>
            <span><strong>{displayNumber(meta.fs_hz)}</strong> Hz</span>
            <span><strong>{displayNumber(meta.duration_s)}</strong> seconds</span>
            <span className={styles.scopeEnd}>Full recording</span>
          </div>

          {hasJob && <section className={`${styles.job} ${failed ? styles.jobFailed : done ? styles.jobDone : ''}`} aria-live="polite">
            <div className={styles.jobHeading}><strong>{done ? 'Filtered copy saved' : failed ? 'Filtered copy failed' : 'Creating filtered copy'}</strong>
              <span title={createdName}>{createdName}</span></div>
            {busy && <><p>{progressText}. You can close this box; processing continues in Uploads.</p>
              {progress && <p>{progress.completed_columns} of {progress.total_columns} parameters filtered</p>}
              <progress aria-label="Pre-processing progress" max={Math.max(1, progress?.total_columns ?? 1)}
                value={progress && progress.completed_columns < progress.total_columns ? progress.completed_columns : undefined} /></>}
            {failed && <><p role="alert">{job?.error || 'The copy could not be processed.'} Your source is unchanged.</p>
              <button type="button" className="btn" onClick={retryAsNew}>Retry with a new copy</button></>}
            {done && <p>The filtered test is available for analysis. Plot filters remain independent and can be applied on top.</p>}
            {statusError && <div className={styles.statusError} role="alert"><p>Could not refresh status: {statusError}</p>
              <button type="button" className="btn" onClick={() => setPollKey(value => value + 1)}>Refresh status</button></div>}
          </section>}

          {readyRecipe ? <section className={styles.saved} aria-label="Saved preprocessing recipe">
            <div className={styles.savedHeader}><h3>Saved filters</h3><span>{recipe?.filters.length ?? 0} {recipe?.filters.length === 1 ? 'parameter' : 'parameters'}</span></div>
            {recipe && <><dl className={styles.provenance}>
              <div><dt>Source test</dt><dd>{recipe.source.name}</dd></div>
              <div><dt>Saved</dt><dd>{new Date(recipe.completed_at ?? recipe.created_at).toLocaleString()}</dd></div>
            </dl>
              <ul className={styles.recipe}>{recipe.filters.map(entry => <li key={entry.column}>
                <strong>{entry.column}</strong><span>{describePreprocessingFilter(entry.filter)}</span>
              </li>)}</ul>
              {!!recipe.warnings.length && <div className={styles.warning}><strong>Processing notes</strong>
                <ul>{recipe.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></div>}
            </>}
            <p className={styles.muted}>To use different settings, create another filtered copy from the source test.</p>
          </section> : !hasJob && <>
            <fieldset className={styles.workspace} disabled={saving} aria-label="Pre-processing filters">
              <section className={styles.parameterPane} aria-label="Parameters">
                <div className={styles.parameterHeading}><h3>Parameters</h3><span>{configured.length} / {parameters.length} filtered</span></div>
                <input className={`input ${styles.search}`} type="search" aria-label="Search parameters" placeholder="Search parameters…"
                  value={query} onChange={event => setQuery(event.target.value)} />
                <label className={styles.configuredOnly}><input type="checkbox" checked={configuredOnly}
                  onChange={event => setConfiguredOnly(event.target.checked)} />Configured only</label>
                <div className={styles.selectionTools}>
                  <label className={styles.selectAll}><input ref={selectAllRef} type="checkbox" checked={allSelected}
                    aria-label="Select all parameters" disabled={!parameters.length}
                    onChange={() => selectBulk(allSelected ? [] : [...parameters])} />Select all</label>
                  {visible.length < parameters.length && <button type="button" className={styles.textButton}
                    disabled={!visible.length} onClick={() => selectBulk([...new Set([...bulkTargets, ...visible])])}>Select visible</button>}
                </div>
                {bulkTargets.length > 0 && <div className={styles.selectionSummary}>
                  <button type="button" className={styles.selectionCount} aria-label="Edit filter for selected parameters"
                    aria-pressed={isBulk} onClick={() => setBulkEditing(true)}>
                    {bulkTargets.length} selected{hiddenSelected > 0 && <small>{hiddenSelected} hidden</small>}
                  </button>
                  <button type="button" className={styles.textButton} onClick={() => { selectBulk([]); selectAllRef.current?.focus(); }}>Clear selection</button>
                </div>}
                <div className={styles.parameterList}>
                  {visible.map(column => <div key={column} className={styles.parameterRow} data-checked={bulkTargets.includes(column) || undefined}>
                    <input type="checkbox" className={styles.parameterCheck} aria-label={`Select ${column} for bulk filtering`}
                      checked={bulkTargets.includes(column)} onChange={event => selectBulk(event.target.checked
                        ? [...bulkTargets, column] : bulkTargets.filter(target => target !== column))} />
                    <button type="button" className={styles.parameter}
                      aria-label={`Configure ${column}`} aria-pressed={!isBulk && selected === column} data-invalid={!!errors.get(column) || undefined}
                      onClick={() => { setSelected(column); setBulkEditing(false); }}>
                      <span title={column}>{column}</span><small>{filterDraft(drafts, column).kind ? FILTER_LABELS[filterDraft(drafts, column).kind as keyof typeof FILTER_LABELS] : 'None'}
                        {errors.get(column) && <span className={styles.invalidMarker} aria-label="Invalid settings">!</span>}</small>
                    </button>
                  </div>)}
                  {!visible.length && <p className={styles.empty}>{configuredOnly ? 'No configured parameters match.' : 'No matching parameters.'}</p>}
                </div>
                <div className={styles.timeLocked}><svg viewBox="0 0 16 16" aria-hidden="true"><rect x="4" y="7" width="8" height="6" rx="1" /><path d="M6 7V5a2 2 0 0 1 4 0v2" /></svg>
                  <span title={meta.time_column}>{meta.time_column}</span><small>Time preserved</small></div>
              </section>

              <section ref={editorRef} className={styles.editor} aria-label="Parameter filter settings">
                {selected || isBulk ? <>
                  <div className={styles.editorHeading}><h3 title={isBulk ? bulkTargets.join(', ') : selected}>
                    {isBulk ? `Filter ${bulkTargets.length} parameter${bulkTargets.length === 1 ? '' : 's'}` : selected}</h3>
                    <span>{isBulk ? 'Bulk settings' : currentUi.kind ? 'Filter configured' : 'Copied unchanged'}</span></div>
                  {isBulk && <p className={styles.bulkScope}>Choose settings, then apply to all {bulkTargets.length} selected.
                    {hiddenSelected > 0 && ` Includes ${hiddenSelected} hidden by the current list filters.`}</p>}
                  <p className={styles.filterHelp}>{isBulk ? HELP[currentUi.kind].replace(/this parameter/g, 'the selected parameters') : HELP[currentUi.kind]}</p>
                  <FilterRow ui={currentUi} fs={meta.fs_hz} title={isBulk ? 'Filter the full native recording for every selected parameter' : 'Filter the full native recording for this parameter'}
                    clearTitle={isBulk ? 'Reset the bulk draft; apply to remove selected filters' : 'Remove this parameter’s preprocessing filter'}
                    onChange={patch => {
                      if (isBulk) { setBulkUi(current => ({ ...current, ...patch })); setBulkDirty(true); setBulkNote(''); }
                      else setDrafts(current => ({ ...current, [selected]: { ...filterDraft(current, selected), ...patch } }));
                      setSaveError('');
                    }} />
                  {currentError && <p className={styles.fieldError} role="status">{currentError}</p>}
                  {isBulk && <div className={styles.bulkApply}>
                    <p>{currentUi.kind ? 'Replaces existing filters on the selected parameters.' : 'Removes filters only from the selected parameters.'} Unselected parameters keep their settings.</p>
                    <button type="button" className={`btn ${styles.applyButton}`} disabled={!!currentError || (!currentUi.kind && !bulkDirty && !bulkTargets.some(column => filterDraft(drafts, column).kind))}
                      onClick={applyBulk}>{currentUi.kind ? 'Apply filter to selected' : 'Remove filters from selected'}</button>
                  </div>}
                  {!isBulk && missingCount > 0 && <p className={styles.missing}>
                    {missingCount.toLocaleString()} missing or invalid samples. Missing positions remain missing after filtering.</p>}
                </> : <p className={styles.empty}>No signal parameters are available in this test.</p>}
              </section>
            </fieldset>
            {bulkNote && <p className={styles.bulkNotice} role="status">{bulkNote}</p>}
            {bulkDirty && <div className={styles.pendingBulk} role="status"><span>Bulk changes have not been applied.</span>
              {!isBulk && <button type="button" className={styles.textButton} disabled={!bulkTargets.length} onClick={() => { setBulkEditing(true); focusFilterEditor(); }}>Review bulk settings</button>}
              <button type="button" className={styles.textButton} onClick={() => { discardBulk(); focusFilterEditor(); }}>Discard bulk changes</button></div>}
            <div className={styles.recipeSummary}><span>{configured.length ? `${configured.length} parameter${configured.length === 1 ? '' : 's'} will be filtered; ${parameters.length - configured.length} copied unchanged.` : 'Choose at least one parameter filter.'}</span>
              <button type="button" className={styles.textButton} disabled={!configured.length || saving}
                onClick={() => { setDrafts({}); setSaveError(''); discardBulk(); }}>Clear all filters</button></div>
            {!!invalid.length && <p className={styles.fieldError} role="status">Review {invalid.length} parameter{invalid.length === 1 ? '' : 's'} with invalid settings: {invalid.join(', ')}.</p>}
            {!!unavailable.length && <div className={styles.error} role="alert"><p>The source changed. These configured parameters are no longer available: {unavailable.join(', ')}.</p>
              <button type="button" className="btn" onClick={() => setDrafts(current => Object.fromEntries(Object.entries(current).filter(([column]) => parameters.includes(column))))}>Remove unavailable filters</button></div>}

            <div className={styles.destination}>
              <label htmlFor={`${id}-name`}>Filtered test name</label>
              <input id={`${id}-name`} className="input" value={name} autoComplete="off" spellCheck={false} maxLength={200}
                disabled={saving} aria-invalid={!!nameError} aria-describedby={nameError ? `${id}-name-error` : `${id}-name-help`}
                onChange={event => { setName(event.target.value); setSaveError(''); }} />
              {nameError ? <p id={`${id}-name-error`} className={styles.fieldError}>{nameError}</p>
                : <p id={`${id}-name-help`}>Saved alongside {test.name} in Uploads.</p>}
            </div>
            {sizeError && <p className={styles.error} role="alert">{sizeError}</p>}
            {saveError && <div className={styles.error} role="alert"><p>{saveError}</p>
              <button type="button" className="btn" onClick={() => { setLoadKey(value => value + 1); setSaveError(''); }}>Reload source details</button></div>}
          </>}

          <details className={styles.details}><summary>Processing and data retention</summary>
            <p>Filters use every native sample in the current stored source test. Time values, row count, test-point boundaries and parameters without a filter are preserved. Existing calculated parameters are copied as stored unless selected for filtering.</p>
            <p>The source test stays in the library. The filtered copy also retains the original uploaded CSV separately when available; that file may precede edits to the stored source. Known acquisition gaps remain boundaries for processing.</p>
            <p>Plot filters are separate analysis settings. On a filtered test they apply additional processing to the saved filtered data.</p>
          </details>
        </>}
      </div>

      <footer className={styles.footer}>
        <div className={styles.footerNote}>{readyRecipe ? 'Saved recipe · full recording' : 'Source retained · plot filters stay separate'}</div>
        <div className={styles.footerActions}>
          {readyRecipe && <><a className="btn" href={exportCsvUrl(displayedName)} download>Filtered CSV</a>
            {recipe?.original_raw_available && <a className="btn" href={rawCsvUrl(displayedName)} download>Original uploaded CSV</a>}</>}
          <button type="button" className="btn" disabled={saving} onClick={onClose}>{hasJob || readonly ? 'Close' : 'Cancel'}</button>
          {readyRecipe ? <button type="button" className={`btn ${styles.primary}`} onClick={() => { onClose(); onOpenTest(displayedName); }}>Analyze filtered test</button>
            : !hasJob && <button type="button" className={`btn ${styles.primary}`} disabled={!canSave} onClick={() => void save()}>{saving ? 'Starting…' : 'Save filtered copy'}</button>}
        </div>
      </footer>
    </div>
  </dialog>;
}
