import type { useExportTask } from '../../utils/useExportTask';
import styles from './PlotExportControls.module.css';

export function ExportTaskStatus({ task }: { task: ReturnType<typeof useExportTask> }) {
  if (task.state === 'idle') return null;
  const { progress } = task;
  const count = progress.completed ?? 0;
  const known = progress.total != null && progress.total > 0;
  const number = (value: number) => progress.unit === 'bytes' ? `${(value / 1048576).toFixed(1)} MB` : value.toLocaleString();
  return <div className={styles.progress} data-export-state={task.state}>
    {task.busy ? <>
      <p role="status">{task.state === 'canceling' ? 'Canceling; waiting for the current block to finish' : progress.stage}
        {task.state !== 'canceling' && progress.plot != null ? ` · Plot ${progress.plot}` : ''}
        {task.state !== 'canceling' && progress.source ? ` · ${progress.source}` : ''}</p>
      <progress aria-label="Export progress" max={known ? progress.total! : 1}
        value={known && task.state !== 'canceling' ? Math.min(count, progress.total!) : undefined} />
      {progress.unit && task.state !== 'canceling' && <p>{number(count)}{known ? ` / ${number(progress.total!)}` : ''}{progress.unit !== 'bytes' ? ` ${progress.unit}` : ''}</p>}
      <button type="button" onClick={task.cancel} disabled={task.state === 'canceling'}>Cancel export</button>
    </> : <p role={task.state === 'failed' ? 'alert' : 'status'} className={task.state === 'failed' ? styles.error : undefined}>{task.message}</p>}
  </div>;
}
