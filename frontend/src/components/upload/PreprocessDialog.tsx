import { useEffect, useId, useRef, useState } from 'react';
import { buildFilterSpec, DEFAULT_FILTER_UI, FILTER_LABELS, type FilterUi } from '../../constants/filters';
import { getJson, isAbortError } from '../../services/api';
import { applyPreprocessing, fetchPreprocessing, matchesPreprocessingRequest, newPreprocessingRequestId,
  preprocessingFilterError, preprocessingFiltersEqual, preprocessingFilterUi, serializePreprocessingFilter,
  type PreprocessingRequest, type PreprocessingSnapshot } from '../../services/preprocessing';
import type { TestInfo } from '../../types';
import { FilterRow } from '../controls/FilterRow';
import PreprocessComparison from './PreprocessComparison';
import styles from './PreprocessDialog.module.css';

interface Props {
  test: TestInfo;
  initialView?: 'filters' | 'compare';
  onClose: () => void;
  onPreprocessed: (name: string, requestId: string) => void;
  onOpenTest: (name: string) => void;
}

const HELP: Record<string, string> = {
  '': 'Choose a filter for this parameter. None uses its original data.',
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

export default function PreprocessDialog({ test, initialView = 'filters', onClose, onPreprocessed, onOpenTest }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const alive = useRef(true);
  const submitting = useRef(false);
  const changedCallback = useRef(onPreprocessed);
  changedCallback.current = onPreprocessed;
  const lastRequest = useRef<PreprocessingRequest | null>(null);
  const keepDraftsOnReload = useRef(false);
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
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [pendingRequest, setPendingRequest] = useState<PreprocessingRequest | null>(null);
  const [job, setJob] = useState<TestInfo | null>(null);
  const [statusError, setStatusError] = useState('');
  const [pollKey, setPollKey] = useState(0);
  const [view, setView] = useState<'filters' | 'compare'>(initialView);
  const [comparisonExpanded, setComparisonExpanded] = useState(false);

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
      if (data.preprocessing?.version !== 2 || !data.preprocessing.filters.length) setView('filters');
      if (!keepDraftsOnReload.current) {
        setDrafts(Object.fromEntries((data.preprocessing?.filters ?? []).map(entry => [entry.column, preprocessingFilterUi(entry.filter)])));
        setBulkSelection([]); setBulkEditing(false); setBulkDirty(false); setBulkNote('');
        setBulkUi({ ...DEFAULT_FILTER_UI });
      }
      keepDraftsOnReload.current = false;
      setSaveError(''); setPendingRequest(null); setStatusError('');
      setJob(data.preprocessing_operation?.state === 'failed'
        ? { name: test.name, status: 'ready', preprocessing_operation: data.preprocessing_operation } : null);
      lastRequest.current = null;
      setSelected(current => data.meta.columns.includes(current) && current !== data.meta.time_column
        ? current : data.meta.columns.find(column => column !== data.meta.time_column) ?? '');
    }).catch(reason => {
      if (!controller.signal.aborted && !isAbortError(reason)) setLoadError(errorText(reason));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [test.name, loadKey]);

  useEffect(() => {
    if (!pendingRequest) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setStatusError('');
    const poll = async () => {
      try {
        const tests = await getJson<TestInfo[]>('/tests', controller.signal);
        if (controller.signal.aborted) return;
        const current = tests.find(entry => entry.name === test.name);
        if (!current) throw new Error('This flight is no longer in the library. Close this box and check Uploads.');
        if (!matchesPreprocessingRequest(current.preprocessing_operation, pendingRequest))
          throw new Error('This flight was updated by another request. Reload its saved filters before making more changes.');
        setJob(current);
        if (current.preprocessing_operation?.state !== 'running') {
          // A ready flight can mean either a committed update or a rolled-back
          // failure. Refresh the active guard token before enabling another save.
          const updated = await fetchPreprocessing(test.name, controller.signal);
          if (controller.signal.aborted) return;
          setSnapshot(updated);
          if (current.preprocessing_operation?.state === 'completed') {
            setDrafts(Object.fromEntries((updated.preprocessing?.filters ?? []).map(entry => [entry.column, preprocessingFilterUi(entry.filter)])));
            setBulkSelection([]); setBulkEditing(false); setBulkDirty(false); setBulkNote('');
            setBulkUi({ ...DEFAULT_FILTER_UI });
          }
          setPendingRequest(null);
          lastRequest.current = null;
          changedCallback.current(test.name, pendingRequest.request_id);
          return;
        }
        timer = setTimeout(() => void poll(), 1200);
      } catch (reason) {
        if (!controller.signal.aborted && !isAbortError(reason)) setStatusError(errorText(reason));
      }
    };
    void poll();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [pendingRequest, pollKey, test.name]);

  const meta = snapshot?.meta;
  const recipe = snapshot?.preprocessing;
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
  const sizeError = configured.length > 0 && snapshot && snapshot.meta.n_rows > snapshot.max_samples
    ? `This recording has ${snapshot.meta.n_rows.toLocaleString()} samples. Pre-processing supports up to ${snapshot.max_samples.toLocaleString()} samples per recording.` : '';
  const done = !pendingRequest && job?.preprocessing_operation?.state === 'completed';
  const failed = !pendingRequest && job?.preprocessing_operation?.state === 'failed';
  const busy = !!pendingRequest;
  const hasJob = !!job || busy;
  const filters = configured.flatMap(column => {
    const spec = buildFilterSpec(drafts[column]);
    return spec ? [{ column, filter: serializePreprocessingFilter(spec) }] : [];
  });
  const savedFilters = recipe?.filters ?? [];
  const changed = invalid.length > 0 || unavailable.length > 0 || !preprocessingFiltersEqual(filters, savedFilters);
  const restoring = !configured.length && savedFilters.length > 0;
  const canSave = !!snapshot && !loading && !loadError && !saving && !busy && !sizeError
    && changed && invalid.length === 0 && unavailable.length === 0 && !bulkDirty;
  const progress = job?.preprocessing_progress;
  const progressText = progress?.stage || 'Processing the full recording';
  const canCompare = !!snapshot && recipe?.version === 2 && savedFilters.length > 0 && !loading && !loadError && !saving && !busy;
  const changeView = (next: 'filters' | 'compare', focusTab = false) => {
    setView(next);
    if (focusTab) requestAnimationFrame(() => document.getElementById(`${id}-tab-${next}`)?.focus());
  };

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = bulkTargets.length > 0 && !allSelected;
  }, [bulkTargets.length, allSelected, loading, busy, view]);

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
    if (!meta || !bulkTargets.length || preprocessingFilterError(bulkUi, meta.fs_hz, meta.n_rows) || saving || busy) return;
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
    const previous = lastRequest.current;
    const request: PreprocessingRequest = previous && previous.source_id === snapshot.source.id
      && previous.source_revision === snapshot.source.revision && preprocessingFiltersEqual(previous.filters, filters)
      ? previous : { request_id: newPreprocessingRequestId(), source_id: snapshot.source.id,
        source_revision: snapshot.source.revision, filters };
    lastRequest.current = request;
    const accepted = (result: TestInfo) => {
      changedCallback.current(test.name, request.request_id);
      if (alive.current) { setJob(result); setPendingRequest(request); }
    };
    try {
      accepted(await applyPreprocessing(test.name, request));
    } catch (reason) {
      try {
        const tests = await getJson<TestInfo[]>('/tests');
        const recovered = tests.find(entry => entry.name === test.name && matchesPreprocessingRequest(entry.preprocessing_operation, request));
        if (recovered) { accepted(recovered); return; }
      } catch { /* Preserve the original request error when recovery is offline. */ }
      if (alive.current) setSaveError(errorText(reason));
    } finally {
      submitting.current = false;
      if (alive.current) setSaving(false);
    }
  };

  return <dialog ref={dialog} className={`${styles.dialog} ${view === 'compare' ? styles.comparisonDialog : ''} ${view === 'compare' && comparisonExpanded ? styles.expandedDialog : ''}`} aria-labelledby={`${id}-title`}
    aria-describedby={`${id}-description`} onCancel={event => { event.preventDefault(); if (!saving) onClose(); }}>
    <div className={styles.shell}>
      <header className={styles.header}>
        <div className={styles.heading}>
          <span className={styles.headerIcon} aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 7h16M4 17h16M8 4v6m8 4v6" /></svg></span>
          <div><h2 id={`${id}-title`}>Pre-process flight</h2>
            <p className={styles.sourceName} title={test.name}>{test.name}</p></div>
        </div>
        <div className={styles.headerActions}>
          {view === 'compare' && <button type="button" className={styles.expand} aria-label={comparisonExpanded ? 'Restore comparison size' : 'Expand comparison'}
            title={comparisonExpanded ? 'Restore comparison size' : 'Expand comparison'} aria-pressed={comparisonExpanded}
            onClick={() => setComparisonExpanded(value => !value)}>
            <svg viewBox="0 0 20 20" aria-hidden="true">{comparisonExpanded
              ? <path d="M3 7h4V3m6 0v4h4M3 13h4v4m6 0v-4h4" />
              : <path d="M7 3H3v4m10-4h4v4M3 13v4h4m6 0h4v-4" />}</svg>
          </button>}
          <button type="button" className={styles.close} aria-label="Close pre-process" disabled={saving} onClick={onClose}>×</button>
        </div>
      </header>

      <div className={styles.tabs} role="tablist" aria-label="Pre-processing views"
        onKeyDown={event => {
          if (!canCompare || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          changeView(event.key === 'Home' ? 'filters' : event.key === 'End' ? 'compare' : view === 'filters' ? 'compare' : 'filters', true);
        }}>
        <button type="button" role="tab" id={`${id}-tab-filters`} aria-controls={`${id}-panel-filters`}
          aria-selected={view === 'filters'} tabIndex={view === 'filters' ? 0 : -1} disabled={saving || busy}
          onClick={() => changeView('filters')}>Filters</button>
        <button type="button" role="tab" id={`${id}-tab-compare`} aria-controls={`${id}-panel-compare`}
          aria-selected={view === 'compare'} tabIndex={view === 'compare' ? 0 : -1} disabled={!canCompare}
          title={canCompare ? 'Compare saved filtered data with the retained original' : 'Apply preprocessing filters to compare with the original'}
          onClick={() => changeView('compare')}>Compare data</button>
      </div>
      <div className={styles.body} role="tabpanel" id={`${id}-panel-${view}`} aria-labelledby={`${id}-tab-${view}`} tabIndex={0}>
        <p id={`${id}-description`} className={styles.intro}>{view === 'compare'
          ? 'Compare the saved filtered recording with its original samples. Plot filters are not applied here.'
          : 'Apply filters to the full recording and update this flight under the same name. Every update starts from the original data, kept safely in the background.'}</p>
        {loading && <div className={styles.state} role="status">Loading native data details…</div>}
        {loadError && <div className={styles.error} role="alert"><p>Could not load pre-processing: {loadError}</p>
          <button type="button" className="btn" onClick={() => setLoadKey(value => value + 1)}>Retry loading</button></div>}

        {meta && !loading && !loadError && <>
          {view === 'filters' && <div className={styles.scope} aria-label="Processing scope">
            <span><strong>{meta.n_rows.toLocaleString()}</strong> native samples</span>
            <span><strong>{displayNumber(meta.fs_hz)}</strong> Hz</span>
            <span><strong>{displayNumber(meta.duration_s)}</strong> seconds</span>
            <span className={styles.scopeEnd}>Full recording</span>
          </div>}

          {hasJob && (view === 'filters' || busy) && <section className={`${styles.job} ${failed ? styles.jobFailed : done ? styles.jobDone : ''}`} aria-live="polite">
            <div className={styles.jobHeading}><strong>{done ? savedFilters.length ? 'Pre-processing saved' : 'Original data restored' : failed ? 'Pre-processing failed' : 'Updating flight'}</strong>
              <span title={test.name}>{test.name}</span></div>
            {busy && <><p>{progressText}. You can close this box; processing continues in Uploads.</p>
              {progress && <p>{progress.completed_columns} of {progress.total_columns} parameters filtered</p>}
              <progress aria-label="Pre-processing progress" max={Math.max(1, progress?.total_columns ?? 1)}
                value={progress && progress.completed_columns < progress.total_columns ? progress.completed_columns : undefined} /></>}
            {failed && <p role="alert">{job?.preprocessing_operation?.error || job?.error || 'The update could not be completed.'} The previous data remains available. Review or change the filters below before applying again.</p>}
            {done && <p>{savedFilters.length ? 'This flight now uses the saved filtered data.' : 'This flight now uses its original data.'} Plot filters remain separate.</p>}
            {statusError && <div className={styles.statusError} role="alert"><p>Could not refresh status: {statusError}</p>
              <button type="button" className="btn" onClick={() => setPollKey(value => value + 1)}>Refresh status</button>
              <button type="button" className="btn" onClick={() => { setJob(null); setLoadKey(value => value + 1); }}>Reload saved filters</button></div>}
          </section>}

          {!busy && view === 'compare' && snapshot && <PreprocessComparison name={test.name} snapshot={snapshot}
            expanded={comparisonExpanded} hasDraftChanges={changed || bulkDirty}
            onReload={() => { keepDraftsOnReload.current = changed || bulkDirty; setJob(null); setLoadKey(value => value + 1); }} />}

          {!busy && view === 'filters' && <>
            <div className={styles.savedState} aria-label="Saved preprocessing state">
              <span className={styles.stateBadge}>{savedFilters.length ? 'Filtered data' : 'Original data'}</span>
              <span>{savedFilters.length ? `${savedFilters.length} saved parameter filter${savedFilters.length === 1 ? '' : 's'} · edit below` : 'No preprocessing filters applied'}</span>
              {recipe?.completed_at && <time dateTime={recipe.completed_at}>{new Date(recipe.completed_at).toLocaleString()}</time>}
            </div>
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
                    <span>{isBulk ? 'Bulk settings' : currentUi.kind ? 'Filter configured' : 'Original data'}</span></div>
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
            <div className={styles.recipeSummary}><span>{configured.length ? `${configured.length} parameter${configured.length === 1 ? '' : 's'} will be filtered; ${parameters.length - configured.length} use original data.` : restoring ? 'All filters removed. Apply to restore the original recording.' : 'Choose a parameter filter to update this flight.'}</span>
              <button type="button" className={styles.textButton} disabled={(!configured.length && !bulkDirty) || saving}
                onClick={() => { setDrafts({}); setSaveError(''); discardBulk(); }}>Clear all filters</button></div>
            {!!invalid.length && <p className={styles.fieldError} role="status">Review {invalid.length} parameter{invalid.length === 1 ? '' : 's'} with invalid settings: {invalid.join(', ')}.</p>}
            {!!unavailable.length && <div className={styles.error} role="alert"><p>The source changed. These configured parameters are no longer available: {unavailable.join(', ')}.</p>
              <button type="button" className="btn" onClick={() => setDrafts(current => Object.fromEntries(Object.entries(current).filter(([column]) => parameters.includes(column))))}>Remove unavailable filters</button></div>}

            {restoring && <div className={styles.restoreNotice} role="status"><strong>Restore original data</strong>
              <p>Applying with no filters restores this flight’s original samples. Its name and saved test points stay the same.</p></div>}
            {sizeError && <p className={styles.error} role="alert">{sizeError}</p>}
            {saveError && <div className={styles.error} role="alert"><p>{saveError}</p>
              <button type="button" className="btn" onClick={() => { setJob(null); setLoadKey(value => value + 1); }}>Reload saved filters</button></div>}
            {!!recipe?.warnings.length && <div className={styles.warning}><strong>Saved processing notes</strong>
              <ul>{recipe.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></div>}
          </>}

          {view === 'filters' && <details className={styles.details}><summary>Processing and data retention</summary>
            <p>Every update uses every native sample from the original recording saved before its first preprocessing update. Time values, row count and test-point boundaries are preserved. Parameters with no filter use their original values. Known acquisition gaps remain boundaries for processing.</p>
            <p>The original stays out of the flight library. Use Compare data to inspect it alongside the saved filtered data, or clear all filters and apply to restore it. Analysis and CSV exports use this flight’s current data.</p>
            <p>Plot filters are separate analysis settings and apply additional processing to the current data.</p>
            {snapshot?.legacy_preprocessing && <p>This flight was created as a filtered copy by an earlier version. Its existing samples are the original for these updates; its earlier source remains a separate flight.</p>}
          </details>}
        </>}
      </div>

      <footer className={styles.footer}>
        <div className={styles.footerNote}>{view === 'compare' ? 'Saved data comparison · same flight' : busy ? 'Full recording · processing in background' : changed || bulkDirty ? 'Unapplied changes · same flight' : 'Original retained · plot filters stay separate'}</div>
        <div className={styles.footerActions}>
          <button type="button" className="btn" disabled={saving} onClick={onClose}>{view === 'compare' || hasJob || !changed ? 'Close' : 'Cancel'}</button>
          {done && !changed && !bulkDirty && <button type="button" className="btn" onClick={() => { onClose(); onOpenTest(test.name); }}>Analyze flight</button>}
          {view === 'compare' && <button type="button" className="btn" onClick={() => changeView('filters', true)}>Back to filters</button>}
          {!busy && view === 'filters' && <button type="button" className={`btn ${styles.primary}`} disabled={!canSave} onClick={() => void save()}>{saving ? 'Starting…' : restoring ? 'Restore original data' : 'Apply preprocessing'}</button>}
        </div>
      </footer>
    </div>
  </dialog>;
}
