import { FormEvent, useEffect, useId, useRef, useState } from 'react';
import { fetchSplitCandidates, previewAutoSplit } from '../../services/api';
import type { AutoSplitProposal, IdCandidate, TestPoint } from '../../types';
import { SearchableSelect } from '../controls/SearchableSelect';
import styles from './AutoSplitPanel.module.css';

interface Props {
  test: string;
  columns: string[];
  draft: TestPoint[];
  disabled: boolean;
  onApply: (proposal: AutoSplitProposal) => void;
  onClose: () => void;
}

interface Settings {
  columns: string[];
  ignoreZero: boolean;
  minDuration: string;
}

interface VariableRow {
  id: number;
  column: string;
}

const MAX_VARIABLES = 9;
const PAGE_SIZE = 50;
const STORAGE_PREFIX = 'ptt.auto-split.v1:';

function readSettings(test: string, available: string[]): Settings {
  const fallback = { columns: [''], ignoreZero: true, minDuration: '1' };
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(STORAGE_PREFIX + test) ?? 'null');
    if (!raw || typeof raw !== 'object') return fallback;
    const stored = raw as Partial<Settings>;
    const columns = Array.isArray(stored.columns)
      ? [...new Set(stored.columns.filter((column): column is string =>
        typeof column === 'string' && available.includes(column)))].slice(0, MAX_VARIABLES)
      : [];
    const duration = typeof stored.minDuration === 'string' ? Number(stored.minDuration) : NaN;
    return {
      columns: columns.length ? columns : fallback.columns,
      ignoreZero: typeof stored.ignoreZero === 'boolean' ? stored.ignoreZero : true,
      minDuration: stored.minDuration?.trim() && Number.isFinite(duration) && duration >= 0
        ? stored.minDuration : '1',
    };
  } catch {
    return fallback;
  }
}

function saveSettings(test: string, settings: Settings): void {
  try {
    window.localStorage.setItem(STORAGE_PREFIX + test, JSON.stringify(settings));
  } catch {
    // Browser preferences are optional; restricted storage must not block splitting.
  }
}

function seconds(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 9 });
}

function runDuration(tp: TestPoint, sampleRate: number): number | null {
  return typeof tp.start_idx === 'number' && typeof tp.end_idx === 'number' &&
    Number.isFinite(sampleRate) && sampleRate > 0
    ? (tp.end_idx - tp.start_idx) / sampleRate : null;
}

/** Auto-split always stages a proposal before it can replace the current TP draft. */
export default function AutoSplitPanel({ test, columns, draft, disabled, onApply, onClose }: Props) {
  const [initial] = useState(() => readSettings(test, columns));
  const [rows, setRows] = useState<VariableRow[]>(() =>
    initial.columns.map((column, id) => ({ id, column })));
  const [ignoreZero, setIgnoreZero] = useState(initial.ignoreZero);
  const [minDuration, setMinDuration] = useState(initial.minDuration);
  const [candidates, setCandidates] = useState<IdCandidate[]>([]);
  const [candidateState, setCandidateState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [candidateRetry, setCandidateRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [preview, setPreview] = useState<{
    proposal: AutoSplitProposal;
    context: string;
    rules: string;
  } | null>(null);
  const [page, setPage] = useState(0);
  const panelRef = useRef<HTMLElement>(null);
  const nextRowId = useRef(rows.length);
  const changedByUser = useRef(false);
  const requestRef = useRef<AbortController | null>(null);
  const headingId = useId();
  const explanationId = useId();
  const durationId = useId();
  const durationErrorId = useId();
  const exclusionsId = useId();
  const selectedColumns = rows.map((row) => row.column);
  const duration = Number(minDuration);
  const validDuration = minDuration.trim() !== '' && Number.isFinite(duration) && duration >= 0;
  const validColumns = rows.length > 0 && rows.length <= MAX_VARIABLES &&
    selectedColumns.every((column) => columns.includes(column)) &&
    new Set(selectedColumns).size === rows.length;
  const context = JSON.stringify([test, columns, draft, disabled]);
  const rules = JSON.stringify([selectedColumns, ignoreZero, minDuration]);
  const currentRef = useRef({ context, rules, disabled });
  currentRef.current = { context, rules, disabled };
  const proposal = preview?.context === context && preview.rules === rules
    ? preview.proposal : null;
  const candidateMap = new Map(candidates.map((candidate) => [candidate.col, candidate]));
  const available = [...new Set(columns)];
  const orderedColumns = [
    ...available.filter((column) => candidateMap.has(column)),
    ...available.filter((column) => !candidateMap.has(column)),
  ];
  const canAdd = rows.length < MAX_VARIABLES && selectedColumns.filter(Boolean).length < available.length;

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      panelRef.current?.querySelector<HTMLButtonElement>('[data-variable-row] button')?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setCandidateState('loading');
    fetchSplitCandidates(test, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      setCandidates(result);
      setCandidateState('ready');
      if (!changedByUser.current) {
        setRows((current) => {
          if (current.length !== 1 || current[0].column) return current;
          const column = result.find((candidate) => columns.includes(candidate.col))?.col ?? columns[0] ?? '';
          return [{ ...current[0], column }];
        });
      }
    }).catch(() => {
      if (!controller.signal.aborted) setCandidateState('error');
    });
    return () => controller.abort();
  }, [test, columns, candidateRetry]);

  useEffect(() => {
    return () => {
      requestRef.current?.abort();
      requestRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (requestRef.current || preview) {
      requestRef.current?.abort();
      requestRef.current = null;
      setBusy(false);
      setPreview(null);
      setError('');
      setNotice('Test-point definitions or test data changed. Preview again before applying.');
    }
    // The serialized context changes only when actual draft/data/loading values change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [context]);

  useEffect(() => {
    if (!validColumns || !validDuration) return;
    saveSettings(test, { columns: selectedColumns, ignoreZero, minDuration });
    // Serialized rules include every configurable value without unstable array dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [test, rules, validColumns, validDuration]);

  const invalidate = () => {
    changedByUser.current = true;
    requestRef.current?.abort();
    requestRef.current = null;
    setBusy(false);
    setPreview(null);
    setError('');
    setNotice(preview || busy ? 'Rules changed. Preview again before applying.' : '');
    setPage(0);
  };

  const focusRow = (id: number) => {
    requestAnimationFrame(() => {
      panelRef.current?.querySelector<HTMLButtonElement>(`[data-variable-row="${id}"] button`)?.focus();
    });
  };

  const addVariable = () => {
    if (!canAdd || disabled) return;
    const column = orderedColumns.find((item) => !selectedColumns.includes(item));
    if (!column) return;
    invalidate();
    const id = nextRowId.current++;
    setRows((current) => [...current, { id, column }]);
    focusRow(id);
  };

  const removeVariable = (index: number) => {
    if (rows.length <= 1 || disabled) return;
    invalidate();
    const remaining = rows.filter((_, rowIndex) => rowIndex !== index);
    setRows(remaining);
    focusRow(remaining[Math.min(index, remaining.length - 1)].id);
  };

  const runPreview = async (event: FormEvent) => {
    event.preventDefault();
    if (!validColumns || !validDuration || disabled || busy) return;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    const requestedContext = context;
    const requestedRules = rules;
    setPreview(null);
    setError('');
    setNotice('');
    setBusy(true);
    setPage(0);
    const isCurrent = () => requestRef.current === controller && !controller.signal.aborted &&
      currentRef.current.context === requestedContext && currentRef.current.rules === requestedRules &&
      !currentRef.current.disabled;
    try {
      const result = await previewAutoSplit(test, {
        columns: selectedColumns,
        ignore_zero: ignoreZero,
        min_len_s: duration,
      }, controller.signal);
      if (!isCurrent()) return;
      setPreview({ proposal: result, context: requestedContext, rules: requestedRules });
    } catch (cause) {
      if (!isCurrent()) return;
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        setBusy(false);
      }
    }
  };

  const apply = () => {
    if (!proposal?.test_points.length || disabled || busy ||
      currentRef.current.context !== preview?.context || currentRef.current.rules !== preview.rules) return;
    requestRef.current?.abort();
    onApply(proposal);
  };

  const close = () => {
    requestRef.current?.abort();
    requestRef.current = null;
    onClose();
  };

  const pageCount = proposal ? Math.ceil(proposal.test_points.length / PAGE_SIZE) : 0;
  const visiblePoints = proposal?.test_points.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE) ?? [];

  return (
    <section ref={panelRef} className={`panel ${styles.panel}`} aria-label="Auto-split">
      <header className={styles.header}>
        <div>
          <h2 id={headingId}>Auto-split by variable changes</h2>
          <p id={explanationId}>
            Create a test point for each continuous run. A new run starts when <strong>any</strong>{' '}
            selected variable changes its exact value.
          </p>
        </div>
        <button type="button" className="btn" onClick={close} aria-label="Close auto-split">Close</button>
      </header>

      <div className={styles.content}>
        <form className={styles.rules} onSubmit={runPreview} aria-describedby={explanationId}>
          <fieldset className={styles.variables} disabled={disabled}>
            <legend>Split variables <span>{rows.length} / {MAX_VARIABLES}</span></legend>
            <div className={styles.variableList}>
              {rows.map((row, index) => (
                <div key={row.id} className={styles.variableRow} data-variable-row={row.id}>
                  <span className={styles.rowLabel}>Variable {index + 1}</span>
                  <SearchableSelect
                    value={row.column}
                    onChange={(column) => {
                      invalidate();
                      setRows((current) => current.map((item) => item.id === row.id ? { ...item, column } : item));
                    }}
                    ariaLabel={`Auto-split variable ${index + 1}`}
                    placeholder="Choose a variable"
                    searchPlaceholder="Search split variables..."
                    optionNoun="variable"
                    disabled={disabled}
                    className={styles.select}
                    options={orderedColumns.map((column) => {
                      const candidate = candidateMap.get(column);
                      const alreadySelected = column !== row.column && selectedColumns.includes(column);
                      return {
                        value: column,
                        label: column,
                        group: candidate ? 'Recommended ID-like variables' : 'Other variables',
                        description: alreadySelected ? 'Already selected' : candidate
                          ? `${candidate.n_unique.toLocaleString()} sampled unique values` : undefined,
                        disabled: alreadySelected,
                      };
                    })}
                  />
                  <button
                    type="button"
                    className={`btn ${styles.remove}`}
                    aria-label={`Remove auto-split variable ${index + 1}`}
                    title={rows.length === 1 ? 'At least one variable is required' : `Remove variable ${index + 1}`}
                    disabled={disabled || rows.length === 1}
                    onClick={() => removeVariable(index)}
                  >Remove</button>
                </div>
              ))}
            </div>
            <div className={styles.addRow}>
              <button type="button" className="btn" disabled={disabled || !canAdd} onClick={addVariable}>
                + Add variable
              </button>
              {rows.length === MAX_VARIABLES && <span>Maximum of {MAX_VARIABLES} variables.</span>}
              {!columns.length && <span>No numeric variables available.</span>}
            </div>
          </fieldset>

          <div className={styles.candidateStatus} aria-live="polite">
            {candidateState === 'loading' && 'Finding recommended ID-like variables… You can choose any variable now.'}
            {candidateState === 'ready' && (candidates.length
              ? 'ID-like variables are recommended in the pickers; all numeric variables are available.'
              : 'No ID-like variables found. Choose a numeric variable to inspect its runs.')}
            {candidateState === 'error' && <>
              <span>Recommendations could not load. You can still choose any variable.</span>
              <button type="button" className="btn" disabled={disabled}
                onClick={() => setCandidateRetry((value) => value + 1)}>Retry recommendations</button>
            </>}
          </div>

          <p className={styles.methodNote}>
            Best for IDs, modes and stepped setpoints. Continuously changing or noisy signals may create
            many short runs; this method does not detect thresholds or stable ranges.
          </p>

          <div className={styles.filters}>
            <label className={styles.checkbox}>
              <input type="checkbox" checked={ignoreZero} disabled={disabled}
                aria-describedby={exclusionsId}
                onChange={(event) => { invalidate(); setIgnoreZero(event.target.checked); }} />
              Exclude zero values
            </label>
            <div className={styles.duration}>
              <label htmlFor={durationId}>Minimum duration (s)</label>
              <input id={durationId} className="input" type="number" step="any" min="0"
                value={minDuration} disabled={disabled} aria-invalid={!validDuration || undefined}
                aria-describedby={!validDuration ? durationErrorId : undefined}
                onChange={(event) => { invalidate(); setMinDuration(event.target.value); }} />
            </div>
          </div>
          {!validDuration && <p id={durationErrorId} className={styles.error} role="alert">
            Enter a finite duration of 0 seconds or more.
          </p>}
          <p id={exclusionsId} className={styles.help}>
            Missing or non-finite values in any selected variable always break a run and are excluded.
            {ignoreZero && ' Any zero in a selected variable is excluded too.'}
            {' '}Runs shorter than the minimum duration are discarded.
            {' '}Duration is the number of samples in a run divided by the sample rate.
          </p>
          <div className={styles.previewAction}>
            <button type="submit" className="btn-toggle active"
              disabled={disabled || busy || !validColumns || !validDuration}>
              {busy ? 'Generating preview…' : 'Preview test points'}
            </button>
            {busy && <button type="button" className="btn" onClick={() => {
              requestRef.current?.abort();
              requestRef.current = null;
              setBusy(false);
              setNotice('Preview canceled. The current test points are unchanged.');
            }}>Cancel preview</button>}
            <span>Preview leaves your current test points unchanged.</span>
          </div>
        </form>

        <section className={styles.proposal} aria-label="Auto-split preview" aria-busy={busy}>
          <h3>Proposed test points {proposal && <span className="badge">{proposal.test_points.length}</span>}</h3>
          <div className={styles.resultStatus} role="status" aria-live="polite">
            {busy && 'Reading full-resolution data and finding continuous runs…'}
            {notice && !busy && notice}
            {!proposal && !busy && !error && !notice && 'Choose variables and preview the test points before using them.'}
          </div>
          {error && <div className={styles.error} role="alert">
            <strong>Preview failed.</strong> {error}
            <p>Review the variables and duration, then select Preview test points to retry.</p>
          </div>}
          {proposal && <>
            <p className={styles.sampleSummary}>
              Checked {proposal.sample_count.toLocaleString()} samples across {proposal.columns.length}{' '}
              {proposal.columns.length === 1 ? 'variable' : 'variables'} at {seconds(proposal.fs_hz)} Hz.
              {' '}Start and end use source time; duration uses the sample count.
            </p>
            <dl className={styles.excluded} aria-label="Excluded from proposal">
              <div><dt>Missing / non-finite samples</dt><dd>{proposal.excluded.missing_samples.toLocaleString()}</dd></div>
              <div><dt>Zero-value samples</dt><dd>{proposal.excluded.zero_samples.toLocaleString()}</dd></div>
              <div><dt>Runs below {seconds(proposal.min_len_s)} s</dt><dd>{proposal.excluded.short_runs.toLocaleString()}</dd></div>
            </dl>
            {proposal.test_points.length ? <>
              <div className={styles.tableScroll} tabIndex={0} role="region" aria-label="Proposed test-point intervals">
                <table className={styles.table}>
                  <thead><tr><th>Test point</th><th>Start (s)</th><th>End (s)</th>
                    <th title="Samples in the run divided by sample rate; the same duration used for the minimum-duration rule.">Duration (s)</th>
                    <th>Values</th></tr></thead>
                  <tbody>{visiblePoints.map((tp) => {
                    const intervalDuration = runDuration(tp, proposal.fs_hz);
                    return <tr key={tp.id}>
                    <th scope="row">{tp.name}</th>
                    <td title={String(tp.start_s)}>{seconds(tp.start_s)}</td>
                    <td title={String(tp.end_s)}>{tp.end_s === null ? 'To end' : seconds(tp.end_s)}</td>
                    <td>{intervalDuration === null ? 'Unavailable' : seconds(intervalDuration)}</td>
                    <td className={styles.values} title={tp.label}>{tp.label}</td>
                  </tr>;
                  })}</tbody>
                </table>
              </div>
              <div className={styles.pagination}>
                <span>Showing {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, proposal.test_points.length)} of{' '}
                  {proposal.test_points.length} test points</span>
                {pageCount > 1 && <div>
                  <button type="button" className="btn" disabled={page === 0}
                    aria-label="Previous preview page" onClick={() => setPage((value) => value - 1)}>Previous</button>
                  <button type="button" className="btn" disabled={page + 1 >= pageCount}
                    aria-label="Next preview page" onClick={() => setPage((value) => value + 1)}>Next</button>
                </div>}
              </div>
            </> : <p className={styles.empty}>
              No test points match these rules. Try a shorter minimum duration, include zeros,
              or choose variables with longer constant runs. Your current test points are unchanged.
            </p>}
          </>}
          <div className={styles.applyArea}>
            <p>
              {draft.length ? <>Using a proposal <strong>replaces the current {draft.length} test-point definitions</strong>{' '}
                in your unsaved draft.</> : 'Using a proposal adds its test points to your unsaved draft.'}
              {' '}Saved data changes only when you select Save.
            </p>
            <button type="button" className="btn-toggle active" disabled={disabled || busy || !proposal?.test_points.length}
              onClick={apply}>
              {proposal ? `Use ${proposal.test_points.length} test points` : 'Use test points'}
            </button>
          </div>
        </section>
      </div>
    </section>
  );
}
