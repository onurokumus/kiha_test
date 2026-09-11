import { useEffect, useState } from 'react';
import { isBusyStatus } from '../../constants/status';
import { fetchMeta, isAbortError } from '../../services/api';
import type { SourceTimeStep, TestInfo, TestMeta } from '../../types';
import styles from './DataQuality.module.css';

const count = (value: number) => value.toLocaleString();
const seconds = (value: number) =>
  value.toLocaleString(undefined, {
    maximumFractionDigits: 9,
  });
const sum = (values?: Record<string, number>) =>
  values ? Object.values(values).reduce((total, value) => total + value, 0) : null;

function qualityIndicator(test: TestInfo) {
  if (test.status === 'error') return { tone: 'blocked', label: 'Analysis unavailable' };
  if (isBusyStatus(test.status)) return { tone: 'pending', label: 'Checks pending' };
  if (test.status !== 'ready' || !test.data_quality) {
    return { tone: 'unknown', label: 'Checks incomplete' };
  }
  const warnings = test.data_quality.warnings.length;
  return warnings
    ? {
        tone: 'warning',
        label: `${warnings} warning${warnings === 1 ? '' : 's'}${
          test.data_quality.partial ? ' · partial checks' : ''
        }`,
      }
    : test.data_quality.partial
      ? { tone: 'unknown', label: 'Checks incomplete' }
      : { tone: 'ok', label: 'No issues found' };
}

export function DataQualityButton({
  test,
  expanded,
  onClick,
  detailsId,
}: {
  test: TestInfo;
  expanded: boolean;
  onClick: () => void;
  detailsId: string;
}) {
  const indicator = qualityIndicator(test);
  return (
    <button
      id={`${detailsId}-toggle`}
      className={styles.summary}
      data-tone={indicator.tone}
      aria-label={`Data quality for ${test.name}: ${indicator.label}`}
      aria-expanded={expanded}
      aria-controls={expanded ? detailsId : undefined}
      onClick={onClick}
    >
      <span aria-hidden="true">{expanded ? '▾' : '▸'}</span> Quality: {indicator.label}
    </button>
  );
}

function StepExamples({
  examples,
  total,
  label,
}: {
  examples: SourceTimeStep[];
  total: number;
  label: string;
}) {
  if (!examples.length) return null;
  return (
    <details className={styles.examples}>
      <summary>
        {label}: {count(total)} · show {examples.length} example{examples.length === 1 ? '' : 's'}
      </summary>
      <ul>
        {examples.map((step) => (
          <li key={step.row}>
            Rows {count(step.row - 1)} → {count(step.row)}: {seconds(step.previous_s)} →{' '}
            {seconds(step.time_s)} s
            {step.missing_rows != null && ` (${count(step.missing_rows)} inserted rows)`}
          </li>
        ))}
      </ul>
    </details>
  );
}

function QualityFacts({ meta }: { meta: TestMeta }) {
  const source = meta.source_time_quality?.version === 1 ? meta.source_time_quality : undefined;
  const missing = sum(meta.nan_counts);
  const infinite = sum(meta.inf_counts);
  const affected = [
    ...new Set([...Object.keys(meta.nan_counts ?? {}), ...Object.keys(meta.inf_counts ?? {})]),
  ]
    .filter((col) => (meta.nan_counts?.[col] ?? 0) + (meta.inf_counts?.[col] ?? 0) > 0)
    .sort((a, b) => a.localeCompare(b));
  const finiteCount = (col: string) =>
    meta.n_rows - (meta.nan_counts?.[col] ?? 0) - (meta.inf_counts?.[col] ?? 0);
  const spectrumUnavailable = affected.filter((col) => finiteCount(col) < 2);
  const gaps = meta.time_gap_ranges ?? [];
  const skipped = Object.entries(meta.skipped_columns ?? {});
  return (
    <div className={styles.facts}>
      <section>
        <h4>Current working data</h4>
        <p>
          {count(meta.n_rows)} rows · {seconds(meta.fs_hz)} Hz · time column{' '}
          <b>{meta.time_column}</b>
        </p>
        <p>
          Missing cells: <b>{missing === null ? 'not recorded' : count(missing)}</b>. Infinite
          cells: <b>{infinite === null ? 'not recorded' : count(infinite)}</b>.
        </p>
        <p className={styles.note}>
          Missing means null or NaN, including inserted gap rows. Counts reflect stored data after
          edits; temporary plot filters are excluded.
        </p>
        {affected.length > 0 && (
          <div className={styles.counts} role="region" aria-label="Affected columns" tabIndex={0}>
            <table aria-label="Missing and infinite values by column">
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Missing</th>
                  <th>Infinite</th>
                </tr>
              </thead>
              <tbody>
                {affected.map((col) => (
                  <tr key={col}>
                    <th scope="row">
                      {col}
                      {spectrumUnavailable.includes(col) && (
                        <small>
                          {finiteCount(col) === 0
                            ? 'No finite samples'
                            : missing === null || infinite === null
                              ? 'At most 1 finite sample'
                              : 'Only 1 finite sample'}
                        </small>
                      )}
                    </th>
                    <td>{missing === null ? '—' : count(meta.nan_counts?.[col] ?? 0)}</td>
                    <td>{infinite === null ? '—' : count(meta.inf_counts?.[col] ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p>
          Current time gaps:{' '}
          <b>{meta.time_gap_count == null ? 'not recorded' : count(meta.time_gap_count)}</b>
          {meta.missing_rows_inserted != null &&
            ` · ${count(meta.missing_rows_inserted)} inserted rows`}
          .
        </p>
        {gaps.length > 0 && (
          <details className={styles.examples}>
            <summary>
              Show current gap intervals ({Math.min(gaps.length, 8)} of {gaps.length})
            </summary>
            <p className={styles.note}>
              Zero-based working rows [start, end), end excluded. Times are nominal, from sample
              rate.
            </p>
            <ul>
              {gaps.slice(0, 8).map(([start, end]) => (
                <li key={`${start}:${end}`}>
                  [{count(start)}, {count(end)}) ·{' '}
                  {seconds((meta.t_start ?? 0) + start / meta.fs_hz)}–
                  {seconds((meta.t_start ?? 0) + end / meta.fs_hz)} s
                </li>
              ))}
            </ul>
          </details>
        )}
        {(meta.time_gap_count ?? 0) > 0 && !gaps.length && <p>Gap intervals were not recorded.</p>}
        {meta.nan_policy && (
          <p className={styles.note}>
            Stored missing-value policy:{' '}
            {(
              {
                keep_gaps: 'Keep gaps',
                zero_fill: 'Fill with zero',
                interpolate: 'Interpolate',
              } as Record<string, string>
            )[meta.nan_policy] ?? meta.nan_policy}
            .
          </p>
        )}
      </section>
      <section>
        <h4>Source timestamps at upload</h4>
        <p className={styles.note}>
          Original findings stay unchanged after edits. Source row numbers start at 1, excluding the
          header; times below are parsed source seconds.
        </p>
        {!source && (
          <p>
            Source timestamp checks were not recorded for this older test. Original CSV has not been
            rescanned.
          </p>
        )}
        {source && !source.checked && (
          <p>
            {source.axis_reason === 'generated_requested'
              ? 'Generated time was selected at upload; source timestamps were not checked.'
              : 'No source time column was detected; time was generated from the upload sample rate.'}
          </p>
        )}
        {source?.checked && (
          <>
            <p>
              Column <b>{source.column}</b> · {count(source.n_rows)} source rows checked.
            </p>
            <p>
              Duplicate steps: <b>{count(source.duplicate_steps?.count ?? 0)}</b> (equal neighbors).{' '}
              Backward steps: <b>{count(source.backward_steps?.count ?? 0)}</b>. Missing / invalid
              timestamps: <b>{count(source.invalid_timestamps?.count ?? 0)}</b>.
            </p>
            {source.axis_reason === 'invalid_source_time' && (
              <p className={styles.warning}>
                Source time was unusable. A generated axis replaced it at the upload sample rate.
              </p>
            )}
            {source.duplicate_steps && (
              <StepExamples
                label="Duplicate steps"
                total={source.duplicate_steps.count}
                examples={source.duplicate_steps.examples}
              />
            )}
            {source.backward_steps && (
              <StepExamples
                label="Backward steps"
                total={source.backward_steps.count}
                examples={source.backward_steps.examples}
              />
            )}
            {(source.invalid_timestamps?.count ?? 0) > 0 && (
              <p>
                Invalid timestamp example rows:{' '}
                {source.invalid_timestamps?.example_rows.map(count).join(', ')}.
              </p>
            )}
            <p>
              Source time gaps:{' '}
              <b>
                {source.gap_count == null
                  ? 'not assessed — unusable clock'
                  : count(source.gap_count)}
              </b>
              .
            </p>
            <StepExamples
              label="Source gaps"
              total={source.gap_count ?? 0}
              examples={source.gap_examples ?? []}
            />
          </>
        )}
        {meta.time_quantized && (
          <p>
            Coarse timestamp resolution was detected. Repeated steps alone do not block analysis.
          </p>
        )}
        {meta.jitter_warning && (
          <p>Import timing warning: irregular sample spacing or acquisition gaps were detected.</p>
        )}
        {skipped.length > 0 && (
          <details className={styles.examples}>
            <summary>
              {skipped.length} non-numeric column{skipped.length === 1 ? '' : 's'} skipped at upload
            </summary>
            <ul>
              {skipped.map(([col, type]) => (
                <li key={col}>
                  {col} ({type})
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>
      <section>
        <h4>Analysis availability</h4>
        <p>The test is ready. These findings do not disable the Analyze action.</p>
        {(meta.time_gap_count ?? 0) > 0 && (
          <p className={styles.warning}>
            Spectrum cannot cross a known time gap. Choose a continuous interval or test point;
            filters process continuous regions separately.
          </p>
        )}
        {spectrumUnavailable.length > 0 && (
          <p className={styles.warning}>
            Columns with fewer than two finite samples cannot produce a spectrum. Use another
            variable or review the source data.
          </p>
        )}
        {meta.time_source === 'generated' && (
          <p className={styles.warning}>
            Time is generated at {seconds(meta.fs_hz)} Hz. Confirm that rate before interpreting
            durations or frequencies. A generated axis cannot reveal acquisition gaps.
          </p>
        )}
        {(missing ?? 0) + (infinite ?? 0) > 0 && (
          <p>
            Missing or infinite signal values may be omitted or interpolated by an analysis method.
            Review the selected variable and interval.
          </p>
        )}
        <p className={styles.note}>
          Checks describe detected input issues, not instrument calibration or the validity of every
          analysis range.
        </p>
      </section>
    </div>
  );
}

export function DataQualityDetails({
  test,
  id,
  onClose,
}: {
  test: TestInfo;
  id: string;
  onClose: () => void;
}) {
  const [meta, setMeta] = useState<TestMeta | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setMeta(null);
    setError('');
    if (test.status === 'ready') {
      void fetchMeta(test.name, controller.signal)
        .then((value) => {
          if (!controller.signal.aborted) setMeta(value);
        })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted && !isAbortError(reason)) {
            setError(reason instanceof Error ? reason.message : String(reason));
          }
        });
    }
    return () => controller.abort();
  }, [test.name, test.status, test.edited_at, test.n_rows, test.quality_revision, retry]);

  const close = () => {
    onClose();
    document.getElementById(`${id}-toggle`)?.focus();
  };
  return (
    <div
      id={id}
      className={styles.details}
      role="region"
      aria-label={`Data quality details for ${test.name}`}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          close();
        }
      }}
    >
      <div className={styles.heading}>
        <h3>Data quality · {test.name}</h3>
        <button className="btn" onClick={close} aria-label={`Close data quality for ${test.name}`}>
          Close
        </button>
      </div>
      {test.status === 'error' ? (
        <div>
          <p className={styles.warning}>
            Analysis is unavailable because upload or rebuild failed.
          </p>
          <p>{test.error || 'No failure details were recorded.'}</p>
          <p>
            Review the source CSV and upload settings before retrying. Existing analysis controls
            remain unavailable until the test is ready.
          </p>
        </div>
      ) : isBusyStatus(test.status) ? (
        <p role="status">
          Quality checks are pending while the test is {test.status}. Details will update when
          processing finishes.
        </p>
      ) : test.status !== 'ready' ? (
        <p>Quality checks and analysis readiness are unknown for this test.</p>
      ) : error ? (
        <div role="alert">
          <p>Could not load data-quality details: {error}</p>
          <button className="btn" onClick={() => setRetry((value) => value + 1)}>
            Retry quality details
          </button>
        </div>
      ) : meta ? (
        <QualityFacts meta={meta} />
      ) : (
        <p role="status">Loading data-quality details…</p>
      )}
    </div>
  );
}
