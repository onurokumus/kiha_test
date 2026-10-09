import { useEffect, useRef, useState, type CSSProperties, type Ref } from 'react';
import type { TestInfo } from '../../types';
import { nextFlightColor, type FullFlightComparison } from '../../utils/fullFlightComparison';
import { SearchableSelect } from './SearchableSelect';
import { NumericField } from './NumericField';
import { useTheme } from '../../hooks/useTheme';
import { themeSeriesColor } from '../../constants/uplotTheme';
import styles from './FlightSelection.module.css';

interface Props {
  tests: TestInfo[];
  comparison: FullFlightComparison;
  onChange: (value: FullFlightComparison) => void;
  comparing: boolean;
}
export function FlightSelect({ tests, comparison, onChange, comparing }: Props) {
  const dark = useTheme() === 'dark';
  const names = comparison.flights.map(flight => flight.test);
  const visibleCount = comparison.flights.filter(flight => !flight.hidden).length;
  const options = tests.map(test => ({ value: test.name, label: test.name,
    description: test.status !== 'ready' ? test.status : [test.duration_s != null ? `${test.duration_s.toLocaleString()} s` : '',
      test.fs_hz != null ? `${test.fs_hz.toLocaleString()} Hz` : '', test.description].filter(Boolean).join(' · '),
    disabled: test.status !== 'ready' || (!names.includes(test.name) && names.length >= 20),
    color: comparing && comparison.flights.some(flight => flight.test === test.name)
      ? themeSeriesColor(comparison.flights.find(flight => flight.test === test.name)!.color, dark) : undefined,
  }));
  return <SearchableSelect ariaLabel="Flights" value="" multipleValues={names} options={options}
    className={styles.picker} optionNoun="flight" searchPlaceholder="Search flights..."
    menuMinWidth={320} title="" onChange={test => {
      const exists = names.includes(test);
      onChange({ ...comparison,
        timeBasis: !comparing && !exists && names.length > 0 ? 'elapsed' : comparison.timeBasis,
        flights: exists ? comparison.flights.filter(flight => flight.test !== test)
          : [...comparison.flights, { test, color: nextFlightColor(comparison.flights), hidden: false, offset: 0 }] });
    }} triggerContent={<><span className={styles.pickerLabel}>Flights</span>
      <span className={styles.count} aria-hidden="true">{visibleCount < names.length ? `${visibleCount}/${names.length}` : names.length}</span>
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg></>} />;
}

interface TrayProps extends Props {
  onFit: () => void;
  exportTargetRef: Ref<HTMLDivElement>;
  sourceErrors?: string[];
}
export function FlightSelectionTray({ tests, comparison, comparing, onChange, onFit, exportTargetRef, sourceErrors = [] }: TrayProps) {
  const dark = useTheme() === 'dark';
  const [alignOpen, setAlignOpen] = useState(false);
  const [draftTimeBasis, setDraftTimeBasis] = useState(comparison.timeBasis);
  const [offsets, setOffsets] = useState<Record<string, string>>({});
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const close = () => {
    dialog.current?.close();
    setAlignOpen(false);
    opener.current?.focus({ preventScroll: true });
  };
  useEffect(() => {
    if (!alignOpen) return;
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, [alignOpen]);
  const valid = comparison.flights.every(flight => offsets[flight.test]?.trim() && Number.isFinite(Number(offsets[flight.test])));
  return <div className={styles.tray} role="group" aria-label="Flight comparison controls">
    <FlightSelect tests={tests} comparison={comparison} comparing={comparing} onChange={onChange} />
    <span className={styles.selectionStatus} role="status" aria-live="polite">
      {comparison.flights.filter(flight => !flight.hidden).length} of {comparison.flights.length} flights visible
    </span>
    <ul className={styles.flights} aria-label="Selected flights">
      {comparison.flights.map(flight => <li key={flight.test} className={styles.flight} data-hidden={flight.hidden || undefined}
        style={{ '--flight-color': comparing ? themeSeriesColor(flight.color, dark) : 'var(--muted, #626f83)' } as CSSProperties}>
        <button type="button" className={styles.visibility} aria-pressed={!flight.hidden}
          aria-label={`${flight.hidden ? 'Show' : 'Hide'} flight ${flight.test}`} title={flight.test}
          onClick={() => onChange({ ...comparison, flights: comparison.flights.map(item => item.test === flight.test ? { ...item, hidden: !item.hidden } : item) })}>
          {comparing && <span className={styles.swatch} aria-hidden="true" />}<span className={styles.flightName}>{flight.test}</span>
          {flight.offset !== 0 && <small className={styles.offset}>{flight.offset > 0 ? '+' : ''}{flight.offset} s</small>}
          {tests.find(test => test.name === flight.test)?.status !== 'ready' && <small className={styles.unavailable}>Unavailable</small>}
          <svg className={styles.visibilityIcon} viewBox="0 0 16 16" aria-hidden="true">
            {flight.hidden ? <path d="m2 2 12 12M6.2 3.8A7 7 0 0 1 8 3.5c3.5 0 6 4.5 6 4.5a12 12 0 0 1-2 2.5M9.8 12.2a7 7 0 0 1-1.8.3C4.5 12.5 2 8 2 8a12 12 0 0 1 2-2.5" />
              : <><path d="M2 8s2.5-4.5 6-4.5S14 8 14 8s-2.5 4.5-6 4.5S2 8 2 8Z" /><circle cx="8" cy="8" r="1.7" /></>}
          </svg>
        </button>
        <button type="button" className={styles.remove} aria-label={`Remove flight ${flight.test}`}
          onClick={() => onChange({ ...comparison, flights: comparison.flights.filter(item => item.test !== flight.test) })}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m5 5 6 6m0-6-6 6" /></svg>
        </button>
      </li>)}
      {!comparison.flights.length && <li className={styles.empty}>Choose flights to compare</li>}
    </ul>
    <div className={styles.tools}>
        <button className={styles.action} ref={opener} type="button" disabled={!comparison.flights.length}
          onClick={() => {
            setDraftTimeBasis(comparison.timeBasis);
            setOffsets(Object.fromEntries(comparison.flights.map(flight => [flight.test, String(flight.offset)])));
            setAlignOpen(true);
          }}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2v12M2 5h4m-2-2L2 5l2 2m10 4h-4m2-2 2 2-2 2" /></svg>Align…
        </button>
      <div className={styles.actions}>
        <button className={styles.action} type="button" onClick={onFit} aria-label="Fit all flights">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 2H2v4m8-4h4v4M2 10v4h4m8-4v4h-4" /></svg>Fit all
        </button>
        <div ref={exportTargetRef} className={styles.exportTarget} />
      </div>
    </div>
    {sourceErrors.length > 0 && <span className={styles.error} role="status">{sourceErrors.join(' · ')}</span>}
    {alignOpen && <dialog ref={dialog} className={styles.dialog} aria-labelledby="flight-alignment-title" onCancel={close}>
      <form onSubmit={event => { event.preventDefault(); if (!valid) return;
        onChange({ ...comparison, timeBasis: draftTimeBasis, flights: comparison.flights.map(flight => ({ ...flight, offset: Number(offsets[flight.test]) })) }); close(); }}>
        <h3 id="flight-alignment-title">Align flights</h3>
        <div className={styles.basisField}>
          <span>Time basis</span>
          <SearchableSelect ariaLabel="Flight time basis" searchable={false} showFooter={false} value={draftTimeBasis}
            options={[{ value: 'elapsed', label: 'Elapsed time' }, { value: 'stored', label: 'Stored time' }]}
            className={styles.timeBasis} title="" menuMinWidth={160}
            onChange={timeBasis => setDraftTimeBasis(timeBasis as FullFlightComparison['timeBasis'])} />
        </div>
        <p>Shift each flight relative to {draftTimeBasis === 'elapsed' ? 'its recording start' : 'its stored time'}. Positive values move it later.</p>
        <div className={styles.offsets}>{comparison.flights.map(flight => <label key={flight.test}>
          <span>{flight.test}</span><NumericField value={offsets[flight.test] ?? ''} allowEmpty={false} unit="s"
            aria-label={`Time shift for ${flight.test}`} onChange={event => setOffsets(previous => ({ ...previous, [flight.test]: event.target.value }))} />
        </label>)}</div>
        <div className={styles.dialogActions}><button type="button" className="btn" onClick={close}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={!valid}>Apply alignment</button></div>
      </form>
    </dialog>}
  </div>;
}
