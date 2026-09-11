import { ComponentProps, useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  loadSplitColumns,
  MAX_SPLIT_PLOTS,
  normalizeSplitColumns,
  saveSplitColumns,
} from '../../utils/splitPlotPreferences';
import SplitPlot from './SplitPlot';

type Props = Omit<ComponentProps<typeof SplitPlot>,
  'column' | 'plotNumber' | 'canRemove' | 'onColumnChange' | 'onRemove' | 'syncKey'>;

/** Mounted per test by SplitView. Every card edits the same unsaved TP draft. */
export default function SplitPlotStack({ columns, ...shared }: Props) {
  const [choices, setChoices] = useState(() => loadSplitColumns(shared.test, columns));
  const selected = useMemo(() => normalizeSplitColumns(choices, columns), [choices, columns]);
  const syncKey = useId();
  const stackRef = useRef<HTMLDivElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const focusColumn = useRef<string | null>(null);
  const nextColumn = columns.find(column => !selected.includes(column));
  const canAdd = selected.length < MAX_SPLIT_PLOTS && nextColumn !== undefined;

  useEffect(() => {
    // An empty schema may be transient while metadata is refreshed.
    if (columns.length) saveSplitColumns(shared.test, selected);
  }, [columns.length, shared.test, selected]);

  useEffect(() => {
    if (focusColumn.current === null) return;
    const index = selected.indexOf(focusColumn.current);
    focusColumn.current = null;
    const picker = stackRef.current?.querySelector<HTMLButtonElement>(
      `[aria-label="Variable for split plot ${index + 1}"]`
    );
    (picker ?? addRef.current)?.focus();
  }, [selected]);

  const addPlot = () => {
    if (!canAdd || nextColumn === undefined) return;
    focusColumn.current = nextColumn;
    setChoices([...selected, nextColumn]);
  };

  const removePlot = (column: string) => {
    if (selected.length <= 1) return;
    const index = selected.indexOf(column);
    const remaining = selected.filter(value => value !== column);
    focusColumn.current = remaining[Math.min(index, remaining.length - 1)];
    setChoices(remaining);
  };

  const changeColumn = (previous: string, column: string) => {
    if (!columns.includes(column) || selected.includes(column)) return;
    focusColumn.current = column;
    setChoices(selected.map(value => value === previous ? column : value));
  };

  return (
    <div ref={stackRef} style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '6px 12px', padding: '2px 4px' }}>
        <span className="section-title" style={{ margin: 0 }}>Line plots</span>
        <span className="badge" role="status" aria-live="polite">
          {selected.length} / {Math.min(columns.length, MAX_SPLIT_PLOTS)}
        </span>
        <span style={{ fontSize: 11, color: '#a0a0a0', flex: 1 }}>
          Time range and test-point boundaries are linked across plots.
        </span>
        <button ref={addRef} className="btn" aria-label="Add line plot" onClick={addPlot}
          disabled={!canAdd}
          title={canAdd ? 'Add another variable below' : selected.length >= MAX_SPLIT_PLOTS
            ? `Up to ${MAX_SPLIT_PLOTS} line plots` : 'All available variables are plotted'}>
          + Add line plot
        </button>
      </div>
      {selected.map((column, index) => (
        <SplitPlot key={column} {...shared} column={column}
          columns={columns.filter(value => value === column || !selected.includes(value))}
          plotNumber={index + 1} canRemove={selected.length > 1} syncKey={syncKey}
          onColumnChange={value => changeColumn(column, value)}
          onRemove={() => removePlot(column)} />
      ))}
      {selected.length === 0 && (
        <div className="panel" role="status">This test has no plottable variables.</div>
      )}
    </div>
  );
}
