import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { SelectedTestPoint, StatsCache } from '../../types';
import styles from './TimePlotStatistics.module.css';

interface Props {
  column: string;
  selectedTPs: SelectedTestPoint[];
  hiddenTPs: Set<string>;
  columnsByTest: Record<string, string[]>;
  statsCache: StatsCache;
  errors: Record<string, string>;
  onRetry: (keys: string[]) => void;
  filterActive: boolean;
}

function formatStatistic(value: number | null | undefined, precision = 5): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? Number(value.toPrecision(precision)).toString()
    : '—';
}

export function TimePlotStatistics({
  column,
  selectedTPs,
  hiddenTPs,
  columnsByTest,
  statsCache,
  errors,
  onRetry,
  filterActive,
}: Props) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0 });
  const rows = selectedTPs
    .filter((s) => !hiddenTPs.has(s.id))
    .map((point) => {
      const available = (columnsByTest[point.test] ?? []).includes(column);
      const cached = statsCache[point.test]?.[column];
      const stat = available ? cached?.[point.tpId] : undefined;
      const summary = stat?.summary?.method === 'finite-population-v1' ? stat.summary : undefined;
      const error = available ? errors[`${point.test}|${column}`] : undefined;
      const mean = summary ? summary.mean : stat?.mean;
      return {
        point,
        available,
        stat,
        summary,
        mean,
        error,
        missingPoint: available && Boolean(cached) && !stat,
        loading: available && !cached && !error,
      };
    });
  const finiteMeans = rows
    .map((row) => row.mean)
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  const hasFailure = rows.some((row) => row.error || row.missingPoint);
  const pending = rows.some((row) => row.loading);
  const prefix = `${filterActive ? 'Original ' : ''}${rows.length > 1 ? 'means' : 'mean'}`;
  const values =
    finiteMeans.length > 1
      ? `${formatStatistic(Math.min(...finiteMeans))}…${formatStatistic(Math.max(...finiteMeans))}`
      : finiteMeans.length === 1
        ? formatStatistic(finiteMeans[0])
        : pending
          ? 'loading…'
          : hasFailure
            ? 'unavailable'
            : '—';
  const partial = finiteMeans.length > 0 && finiteMeans.length < rows.length;
  const caption = `${prefix[0].toUpperCase()}${prefix.slice(1)} ${values}${partial ? ' · partial' : ''}`;
  const retryKeys = Array.from(
    new Set(
      rows
        .filter((row) => row.error || row.missingPoint)
        .map((row) => `${row.point.test}|${column}`)
    )
  );
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      if (!trigger.current || !panel.current) return;
      const anchor = trigger.current.getBoundingClientRect(),
        box = panel.current.getBoundingClientRect();
      setPosition({
        left: Math.max(8, Math.min(anchor.left, window.innerWidth - box.width - 8)),
        top: Math.max(8, Math.min(anchor.bottom + 6, window.innerHeight - box.height - 8)),
      });
    };
    place();
    const observer = new ResizeObserver(place);
    if (panel.current) observer.observe(panel.current);
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    closeButton.current?.focus();
    const outside = (event: MouseEvent | FocusEvent) => {
      if (
        !panel.current?.contains(event.target as Node) &&
        !trigger.current?.contains(event.target as Node)
      )
        setOpen(false);
    };
    document.addEventListener('mousedown', outside);
    document.addEventListener('focusin', outside);
    return () => {
      document.removeEventListener('mousedown', outside);
      document.removeEventListener('focusin', outside);
    };
  }, [open]);

  if (!rows.length) return null;
  return (
    <>
      <button
        ref={trigger}
        className={styles.trigger}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={`Statistics for ${column}: ${caption}`}
        title={`${caption}. Original data, complete test points. Open per-point means and population standard deviations.`}
        onClick={() => (open ? close() : setOpen(true))}
      >
        {caption}
      </button>
      {open &&
        createPortal(
          <div
            ref={panel}
            id={id}
            role="dialog"
            aria-label={`Statistics for ${column}`}
            aria-describedby={`${id}-scope`}
            className={styles.panel}
            style={position}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                close();
              }
            }}
          >
            <div className={styles.heading}>
              <strong>{column}</strong>
              <button ref={closeButton} type="button" onClick={close} aria-label="Close statistics">
                ×
              </button>
            </div>
            <p id={`${id}-scope`}>
              Original data · complete test points. Zoom and plot filters do not change these
              statistics.
            </p>
            {filterActive && (
              <p className={styles.notice}>
                The plot is filtered; these values describe the original signal.
              </p>
            )}
            {rows.length > 1 && (
              <p>
                The header spans individual TP means; test points are not pooled. Hidden points are
                excluded.
              </p>
            )}
            <div
              className={styles.tableScroll}
              tabIndex={0}
              role="region"
              aria-label="Per-test-point statistics"
            >
              <table>
                <thead>
                  <tr>
                    <th scope="col">Test point</th>
                    <th scope="col">Mean</th>
                    <th scope="col">Population SD</th>
                    <th scope="col">Finite / total</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(
                    ({ point, available, stat, summary, mean, error, missingPoint, loading }) => (
                      <tr key={point.id}>
                        <th scope="row">
                          <span className={styles.point} style={{ borderColor: point.color }}>
                            {point.name}
                          </span>
                          <span className={styles.context}>
                            {point.test} · TP {point.tpId}
                          </span>
                          <span className={styles.context}>
                            {formatStatistic(point.tp.start_s, 12)}–
                            {formatStatistic(point.endS, 12)} s
                          </span>
                          {summary?.i0 != null && summary.i1 != null && (
                            <span className={styles.context}>
                              Rows [{summary.i0}, {summary.i1})
                            </span>
                          )}
                        </th>
                        {!available || error || missingPoint || loading ? (
                          <td
                            colSpan={3}
                            className={error || missingPoint ? styles.notice : undefined}
                          >
                            {!available
                              ? 'Variable not in this test'
                              : error
                                ? `Could not load statistics: ${error}`
                                : missingPoint
                                  ? 'Statistics for this saved point are unavailable'
                                  : 'Loading statistics…'}
                          </td>
                        ) : (
                          <>
                            <td title={mean == null ? undefined : String(mean)}>
                              {formatStatistic(mean, 12)}
                            </td>
                            <td
                              title={
                                summary?.std_population == null
                                  ? undefined
                                  : String(summary.std_population)
                              }
                            >
                              {formatStatistic(summary?.std_population, 12)}
                            </td>
                            <td>
                              {stat?.n_valid ?? '—'} / {stat?.n ?? '—'}
                              {stat && stat.n > stat.n_valid && (
                                <span className={styles.context}>
                                  {stat.n - stat.n_valid} excluded
                                </span>
                              )}
                              {stat?.n_valid === 0 && (
                                <span className={styles.context}>
                                  {stat.n === 0 ? 'No samples in range' : 'No finite samples'}
                                </span>
                              )}
                            </td>
                          </>
                        )}
                      </tr>
                    )
                  )}
                </tbody>
              </table>
            </div>
            {retryKeys.length > 0 && (
              <button
                type="button"
                className={styles.retry}
                onClick={() => {
                  onRetry(retryKeys);
                  closeButton.current?.focus();
                }}
              >
                Retry statistics
              </button>
            )}
            {rows.some((row) => row.stat && !row.summary) && (
              <p className={styles.notice}>
                Older statistics response: mean is rounded to six decimal places; standard deviation
                and exact row bounds are unavailable.
              </p>
            )}
            <p>
              Each finite sample has equal weight. Missing values and infinities are excluded.
              Population SD divides by the finite sample count (N); one finite sample has SD 0. Both
              values use the variable’s stored units.
            </p>
          </div>,
          document.body
        )}
    </>
  );
}
