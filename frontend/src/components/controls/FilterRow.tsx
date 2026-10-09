import React from 'react';
import { NumericField } from './NumericField';
import { parseFiniteNumber } from '../../utils/numericField';
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
  /** The same controls also configure durable upload preprocessing. */
  clearTitle?: string;
}

/** Labelled DSP controls laid out for the on-demand plot filter dialog. */
export const FilterRow: React.FC<FilterRowProps> = ({ ui, onChange, fs, title,
  clearTitle = "Turn off this plot's filter and reset its settings" }) => {
  const windowMs = parseFiniteNumber(ui.despikeWindowMs);
  const maxSpikeMs = parseFiniteNumber(ui.maxSpikeMs);
  const f1 = parseFiniteNumber(ui.f1);
  const f2 = parseFiniteNumber(ui.f2);
  const nyquist = fs && fs > 0 ? fs / 2 : undefined;
  const isBand = ui.kind === 'bandpass' || ui.kind === 'bandstop';
  const bandError = isBand && f1 !== null && f2 !== null && f2 <= f1
    ? 'Keep the high cutoff above the low cutoff.' : '';
  const despikeError = windowMs !== null && maxSpikeMs !== null && windowMs > 0 && maxSpikeMs > 0 && windowMs <= 2 * maxSpikeMs
    ? 'Window must exceed twice the max spike.' : '';

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
            <NumericField
              min="1"
              max="10"
              step="1"
              integer
              inputMode="numeric"
              value={ui.order}
              aria-label="Order"
              onChange={(event) => onChange({ order: event.target.value })}
              title="Butterworth filter order, from 1 to 10"
            />
          </label>
          <label className={styles.field}>
            <span>
              {isBand ? 'Low cutoff' : 'Cutoff'}
            </span>
            <NumericField
              min="0"
              step="any"
              inputMode="decimal"
              value={ui.f1}
              aria-label={isBand ? 'Low cutoff (Hz)' : 'Cutoff (Hz)'}
              unit="Hz"
              exclusiveMin
              max={nyquist}
              exclusiveMax
              error={bandError}
              onChange={(event) => onChange({ f1: event.target.value })}
              title="Cutoff frequency in hertz"
            />
          </label>
          {(ui.kind === 'bandpass' || ui.kind === 'bandstop') && (
            <label className={styles.field}>
              <span>High cutoff</span>
              <NumericField
                min="0"
                step="any"
                inputMode="decimal"
                value={ui.f2}
                aria-label="High cutoff (Hz)"
                unit="Hz"
                exclusiveMin
                max={nyquist}
                exclusiveMax
                error={bandError}
                onChange={(event) => onChange({ f2: event.target.value })}
                title="Upper cutoff frequency in hertz"
              />
            </label>
          )}
          {fs && (
            <span className={styles.nyquist} title={`Cutoff frequencies must stay below ${fs / 2} Hz`}>
              Nyquist {Number((fs / 2).toPrecision(7))} Hz
            </span>
          )}
        </>
      )}

      {ui.kind === 'moving_avg' && (
        <label className={styles.field}>
          <span>Window</span>
          <NumericField
            min="0"
            step="any"
            inputMode="decimal"
            value={ui.winS}
            aria-label="Window (s)"
            unit="s"
            exclusiveMin
            onChange={(event) => onChange({ winS: event.target.value })}
            title="Averaging window duration in seconds"
          />
        </label>
      )}

      {ui.kind === 'despike' && (
        <>
          <label className={styles.field}>
            <span>Window</span>
            <NumericField
              min="0"
              step="any"
              inputMode="decimal"
              value={ui.despikeWindowMs}
              aria-label="Window (ms)"
              unit="ms"
              exclusiveMin
              error={despikeError}
              onChange={(event) => onChange({ despikeWindowMs: event.target.value })}
              title="Local-median window in milliseconds; must exceed twice the maximum spike duration"
            />
          </label>
          <label className={styles.field}>
            <span>Max spike</span>
            <NumericField
              min="0"
              step="any"
              inputMode="decimal"
              value={ui.maxSpikeMs}
              aria-label="Max spike (ms)"
              unit="ms"
              exclusiveMin
              error={despikeError}
              onChange={(event) => onChange({ maxSpikeMs: event.target.value })}
              title="Longest consecutive spike to replace, in milliseconds"
            />
          </label>
          <label className={styles.field}>
            <span>Threshold</span>
            <NumericField
              min="0"
              step="any"
              inputMode="decimal"
              value={ui.threshold}
              aria-label="Threshold (MAD)"
              unit="MAD"
              exclusiveMin
              onChange={(event) => onChange({ threshold: event.target.value })}
              title="Deviation threshold in robust MAD sigmas; lower values detect more spikes"
            />
          </label>
          <label className={styles.field}>
            <span>Min jump</span>
            <NumericField
              min="0"
              step="any"
              inputMode="decimal"
              value={ui.absFloor}
              aria-label="Min jump (units)"
              unit="units"
              onChange={(event) => onChange({ absFloor: event.target.value })}
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
          <details className={styles.explanation}>
            <summary>Details</summary>
            <p>Removes short runs that depart from the local median. Threshold uses robust MAD sigmas; minimum jump uses the signal's units.</p>
          </details>
        </>
      )}

      {ui.kind && (
        <button
          type="button"
          className={styles.clearButton}
          onClick={() => onChange({ ...DEFAULT_FILTER_UI })}
          title={clearTitle}
        >
          Clear
        </button>
      )}
    </div>
  );
};
