import { useEffect, useState } from 'react';
import { fetchTestPoints, testPointCsvUrl } from '../../services/api';
import type { TestPoint } from '../../types';
import styles from './SplitCsvDownloads.module.css';

/** Mount only while open: fresh saved definitions on each disclosure, without
 *  fetching every uploaded test's TP file on each history poll. */
export function SplitCsvDownloads({ test, id, onClose }: {
  test: string;
  id: string;
  onClose: () => void;
}) {
  const [points, setPoints] = useState<TestPoint[] | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let dead = false;
    setPoints(null);
    setError('');
    fetchTestPoints(test).then((file) => {
      if (!dead) setPoints([...file.test_points].sort((a, b) => a.start_s - b.start_s));
    }).catch((reason: unknown) => {
      if (!dead) setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => { dead = true; };
  }, [test, attempt]);

  const close = () => {
    document.getElementById(`${id}-toggle`)?.focus();
    onClose();
  };
  return (
    <section id={id} className={styles.details} role="region"
      aria-label={`Split CSV downloads for ${test}`}
      onKeyDown={(event) => {
        if (event.key === 'Escape') { event.stopPropagation(); close(); }
      }}>
      <div className={styles.heading}>
        <strong>Saved test points</strong>
        <button className="btn" onClick={close} aria-label={`Close split downloads for ${test}`}>Close</button>
      </div>
      <p>Each CSV contains one complete saved TP, full-resolution stored signals and a
        test_point_id column. Plot filters are excluded. A source column with that name is
        retained under a unique source_ name.</p>
      {error ? <div role="alert">Could not load test points. {error}{' '}
        <button className="btn" onClick={() => setAttempt((n) => n + 1)}>Retry</button>
      </div> : points === null ? <p role="status">Loading test points…</p> : points.length === 0 ?
        <p role="status">No saved test points. Define and save them in Split first.</p> :
        <ul className={styles.list}>
          {points.map((tp) => <li key={tp.id}>
            <span><b>TP {tp.id}</b> · {tp.name}{tp.label ? ` · ${tp.label}` : ''}</span>
            <a className="btn" href={testPointCsvUrl(test, tp.id)} download
              aria-label={`Download CSV for ${test} TP ${tp.id}`}>CSV</a>
          </li>)}
        </ul>}
    </section>
  );
}
