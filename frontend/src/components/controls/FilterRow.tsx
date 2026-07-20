import React from 'react';
import { FilterKind } from '../../types';
import { SelectStyle } from '../../constants/styles';
import { FILTER_LABELS, FilterUi } from '../../constants/filters';

interface FilterRowProps {
  ui: FilterUi;
  onChange: (patch: Partial<FilterUi>) => void;
  /** Sample rate for the Nyquist hint (null hides it). */
  fs: number | null;
  /** Tooltip on the kind dropdown, e.g. scope of what it applies to. */
  title?: string;
}

/** DSP filter picker + its parameter inputs. Renders as inline flex items —
 *  the caller provides the flex-row container (a plot's header row, or its
 *  floating collapsed-cell overlay). One instance per plot (App.plotFilters). */
export const FilterRow: React.FC<FilterRowProps> = ({ ui, onChange, fs, title }) => (
  <>
    <select
      value={ui.kind}
      onChange={(e) => onChange({ kind: e.target.value as '' | FilterKind })}
      style={{ ...SelectStyle, maxWidth: 110 }}
      title={title}
    >
      <option value="">— none —</option>
      {(Object.keys(FILTER_LABELS) as FilterKind[]).map((k) => (
        <option key={k} value={k}>
          {FILTER_LABELS[k]}
        </option>
      ))}
    </select>
    {(ui.kind === 'lowpass' ||
      ui.kind === 'highpass' ||
      ui.kind === 'bandpass' ||
      ui.kind === 'bandstop') && (
      <>
        <span style={{ color: '#a0a0a0', fontSize: 11, lineHeight: '24px' }}>order</span>
        <input
          className="input"
          style={{ width: 40 }}
          value={ui.order}
          onChange={(e) => onChange({ order: e.target.value })}
        />
        <input
          className="input"
          style={{ width: 65 }}
          placeholder="f1 Hz"
          value={ui.f1}
          onChange={(e) => onChange({ f1: e.target.value })}
        />
        {(ui.kind === 'bandpass' || ui.kind === 'bandstop') && (
          <input
            className="input"
            style={{ width: 65 }}
            placeholder="f2 Hz"
            value={ui.f2}
            onChange={(e) => onChange({ f2: e.target.value })}
          />
        )}
        {fs && (
          <span style={{ color: '#909090', fontSize: 10, lineHeight: '24px' }}>
            Nyquist {fs / 2} Hz
          </span>
        )}
      </>
    )}
    {ui.kind === 'moving_avg' && (
      <>
        <input
          className="input"
          style={{ width: 50 }}
          value={ui.winS}
          onChange={(e) => onChange({ winS: e.target.value })}
        />
        <span style={{ color: '#a0a0a0', fontSize: 11, lineHeight: '24px' }}>s window</span>
      </>
    )}
  </>
);
