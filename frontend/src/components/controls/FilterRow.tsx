import React, { useId } from 'react';
import { FilterKind } from '../../types';
import { DEFAULT_FILTER_UI, FILTER_LABELS, FilterUi } from '../../constants/filters';
import styles from './FilterRow.module.css';

interface FilterRowProps {
  ui: FilterUi;
  onChange: (patch: Partial<FilterUi>) => void;
  /** Sample rate for the Nyquist hint (null hides it). */
  fs: number | null;
  /** Tooltip on the kind dropdown, e.g. scope of what it applies to. */
  title?: string;
}

/** Labelled DSP controls laid out for the on-demand plot filter dialog. */
export const FilterRow: React.FC<FilterRowProps> = ({ ui, onChange, fs, title }) => {
  const hintId = useId();
  const windowMs = Number(ui.despikeWindowMs);
  const maxSpikeMs = Number(ui.maxSpikeMs);
  const threshold = Number(ui.threshold);
  const absFloor = Number(ui.absFloor);
  const windowValueValid = Number.isFinite(windowMs) && windowMs > 0;
  const maxSpikeValueValid = Number.isFinite(maxSpikeMs) && maxSpikeMs > 0;
  const thresholdValid = Number.isFinite(threshold) && threshold > 0;
  const absFloorValid = Number.isFinite(absFloor) && absFloor >= 0;
  const despikeNumbersValid =
    windowValueValid && maxSpikeValueValid && thresholdValid && absFloorValid;
  const despikeWindowValid = despikeNumbersValid && windowMs > 2 * maxSpikeMs;
  const despikeMessage = !despikeNumbersValid
    ? 'Use positive values; minimum jump may be zero.'
    : !despikeWindowValid
      ? 'Window must be longer than twice the maximum spike.'
      : 'Removes short runs that depart from the local median.';

  return (
    <div className={styles.row}>
      <label className={`${styles.field} ${styles.kindField}`}>
        <span>Filter</span>
        <select
          value={ui.kind}
          onChange={(event) => onChange({ kind: event.target.value as '' | FilterKind })}
          title={title}
        >
          <option value="">None</option>
          {(Object.keys(FILTER_LABELS) as FilterKind[]).map((kind) => (
            <option key={kind} value={kind}>
              {FILTER_LABELS[kind]}
            </option>
          ))}
        </select>
      </label>

      {(ui.kind === 'lowpass' ||
        ui.kind === 'highpass' ||
        ui.kind === 'bandpass' ||
        ui.kind === 'bandstop') && (
        <>
          <label className={styles.field}>
            <span>Order</span>
            <input
              type="number"
              min="1"
              max="10"
              step="1"
              inputMode="numeric"
              value={ui.order}
              onChange={(event) => onChange({ order: event.target.value })}
              title="Butterworth filter order, from 1 to 10"
            />
          </label>
          <label className={styles.field}>
            <span>
              {ui.kind === 'bandpass' || ui.kind === 'bandstop' ? 'Low cutoff (Hz)' : 'Cutoff (Hz)'}
            </span>
            <input
              type="number"
              min="0"
              step="any"
              inputMode="decimal"
              value={ui.f1}
              onChange={(event) => onChange({ f1: event.target.value })}
              title="Cutoff frequency in hertz"
            />
          </label>
          {(ui.kind === 'bandpass' || ui.kind === 'bandstop') && (
            <label className={styles.field}>
              <span>High cutoff (Hz)</span>
              <input
                type="number"
                min="0"
                step="any"
                inputMode="decimal"
                value={ui.f2}
                onChange={(event) => onChange({ f2: event.target.value })}
                title="Upper cutoff frequency in hertz"
              />
            </label>
          )}
          {fs && (
            <span className={styles.nyquist} title="Cutoff frequencies must stay below this value">
              Nyquist {fs / 2} Hz
            </span>
          )}
        </>
      )}

      {ui.kind === 'moving_avg' && (
        <label className={styles.field}>
          <span>Window (s)</span>
          <input
            type="number"
            min="0"
            step="any"
            inputMode="decimal"
            value={ui.winS}
            onChange={(event) => onChange({ winS: event.target.value })}
            title="Averaging window duration in seconds"
          />
        </label>
      )}

      {ui.kind === 'despike' && (
        <>
          <label className={styles.field}>
            <span>Window (ms)</span>
            <input
              type="number"
              min="0"
              step="any"
              inputMode="decimal"
              value={ui.despikeWindowMs}
              onChange={(event) => onChange({ despikeWindowMs: event.target.value })}
              aria-describedby={hintId}
              aria-invalid={!despikeWindowValid}
              title="Local-median window in milliseconds; must exceed twice the maximum spike duration"
            />
          </label>
          <label className={styles.field}>
            <span>Max spike (ms)</span>
            <input
              type="number"
              min="0"
              step="any"
              inputMode="decimal"
              value={ui.maxSpikeMs}
              onChange={(event) => onChange({ maxSpikeMs: event.target.value })}
              aria-describedby={hintId}
              aria-invalid={!despikeWindowValid}
              title="Longest consecutive spike to replace, in milliseconds"
            />
          </label>
          <label className={styles.field}>
            <span>Threshold (MAD)</span>
            <input
              type="number"
              min="0"
              step="any"
              inputMode="decimal"
              value={ui.threshold}
              onChange={(event) => onChange({ threshold: event.target.value })}
              aria-describedby={hintId}
              aria-invalid={!thresholdValid}
              title="Deviation threshold in robust MAD sigmas; lower values detect more spikes"
            />
          </label>
          <label className={styles.field}>
            <span>Min jump (units)</span>
            <input
              type="number"
              min="0"
              step="any"
              inputMode="decimal"
              value={ui.absFloor}
              onChange={(event) => onChange({ absFloor: event.target.value })}
              aria-describedby={hintId}
              aria-invalid={!absFloorValid}
              title="Ignore deviations smaller than this value in the plotted signal's units"
            />
          </label>
          <label className={styles.field}>
            <span>Replacement</span>
            <select
              value={ui.replacement}
              onChange={(event) =>
                onChange({
                  replacement: event.target.value as 'linear' | 'median',
                })
              }
              title="How detected spike samples are replaced"
            >
              <option value="linear">Linear</option>
              <option value="median">Local median</option>
            </select>
          </label>
          <span
            id={hintId}
            className={despikeWindowValid ? styles.explanation : styles.validation}
            role={despikeWindowValid ? undefined : 'alert'}
          >
            {despikeMessage}
          </span>
        </>
      )}

      {ui.kind && (
        <button
          type="button"
          className={styles.clearButton}
          onClick={() => onChange({ ...DEFAULT_FILTER_UI })}
          title="Turn off this plot's filter and reset its settings"
        >
          Clear
        </button>
      )}
    </div>
  );
};
