import { useEffect, useImperativeHandle, useRef, useState, type Ref } from 'react';
import { createPortal } from 'react-dom';
import type { SpectrumData, SpectrumXAxis } from '../../types';
import base from '../controls/PlotExportControls.module.css';
import styles from './SpectrumAnalysisDetails.module.css';

export interface SpectrumAnalysisTrace {
  label: string;
  data: SpectrumData;
}
export interface SpectrumFailure { label: string; message: string }

interface Props {
  actionsRef?: Ref<SpectrumAnalysisActions>;
  hideTrigger?: boolean;
  label: string;
  traces: SpectrumAnalysisTrace[];
  failures: SpectrumFailure[];
  contextKey: string;
  axisMode: SpectrumXAxis;
  logY: boolean;
  loading: boolean;
}
export interface SpectrumAnalysisActions { open: () => void }
const numeric = (value: number | null | undefined) => value != null && Number.isFinite(value) ? String(value) : 'unavailable';

function TraceDetails({ trace, axisMode }: { trace: SpectrumAnalysisTrace; axisMode: SpectrumXAxis }) {
  const { data } = trace;
  const method = data.method, reduction = data.reduction, quality = data.quality;
  const rows: [string, string][] = [
    ['Rows', data.i0 != null && data.i1 != null ? `[${data.i0}, ${data.i1}) — end row excluded` : 'Exact row bounds unavailable from this backend.'],
    ['Sample times', data.time_start_s != null && data.time_end_s != null
      ? `${numeric(data.time_start_s)} to ${numeric(data.time_end_s)} s (first and last stored sample centers)`
      : 'Actual stored sample times unavailable from this backend.'],
    ['Samples', `${data.n_samples.toLocaleString()} samples at ${numeric(data.fs_hz)} Hz; record duration N/fs = ${numeric(data.n_samples / data.fs_hz)} s`],
    ['Signal quality', `${data.finite_count == null ? 'Finite count unavailable' : `${data.finite_count.toLocaleString()} finite`}; ${data.nan_count.toLocaleString()} missing. Missing values are interpolated by sample index; edge values are held.`],
  ];
  if (method) {
    rows.push(['Estimator', `${data.mode === 'welch' ? 'Welch PSD' : 'FFT peak amplitude'} · ${method.version}. One-sided; ${method.window === 'hann_periodic' ? 'periodic Hann' : 'rectangular'} window; constant detrend. Units: ${method.units}.`]);
    rows.push(['Frequency bins', `NFFT ${method.nfft}; native spacing ${numeric(method.bin_spacing_hz)} Hz. No zero padding or sub-bin peak fitting.`]);
    if (data.mode === 'welch') rows.push(['Welch segments', `${method.segment_count} complete segments; length ${method.nperseg}, overlap ${method.noverlap}; arithmetic mean. ${method.used_samples} samples covered; ${method.trailing_samples} trailing samples unused. Each segment is constant-detrended after global mean removal.`]);
  } else rows.push(['Estimator', 'Detailed method metadata unavailable. Update the backend for reproducible analysis details.']);
  rows.push(['Display reduction', reduction
    ? `${reduction.n_bins_returned.toLocaleString()} of ${reduction.n_bins_original.toLocaleString()} bins; ${reduction.method === 'none' ? 'unreduced' : `max-bin groups of ${reduction.factor}, keeping each maximizing bin’s actual frequency`}.`
    : 'Reduction metadata unavailable; do not assume the display contains every bin.']);
  if (data.peak) {
    const order = axisMode === 'per_rev' && data.mean_rpm ? ` (${numeric(data.peak.frequency_hz * 60 / data.mean_rpm)} cycles/rev)` : '';
    rows.push(['Peak', `${numeric(data.peak.frequency_hz)} Hz${order}; ${numeric(data.peak.magnitude)} ${data.mode === 'welch' ? 'U²/Hz' : 'U'}; native bin ${data.peak.bin_index}. Largest unreduced bin, first bin wins ties; linear value.`]);
  }
  if (data.mean_rpm != null) rows.push(['RPM', `${data.rpm_col}: mean ${numeric(data.mean_rpm)}, min ${numeric(data.min_rpm)}, max ${numeric(data.max_rpm)} rpm (absolute finite values, including zeros). ${data.rpm_finite_count ?? 'Unknown'} finite; ${data.rpm_nan_count ?? 'unknown'} missing.`]);
  rows.push(['Timing', quality
    ? `Source: ${quality.time_source ?? 'unknown'}. ${quality.gap_metadata_available ? `${quality.known_gap_count} recorded gaps in these rows.` : 'Recorded gap metadata unavailable.'} Quantized: ${quality.time_quantized == null ? 'unknown' : quality.time_quantized ? 'yes' : 'no'}; jitter warning: ${quality.jitter_warning == null ? 'unknown' : quality.jitter_warning ? 'yes' : 'no'}. Uniform row spacing at the metadata sample rate is assumed; timestamps are not resampled.`
    : 'Timing-quality metadata unavailable. Uniform row spacing at the metadata sample rate is assumed.']);
  return <section className={styles.trace} aria-label={`${trace.label} analysis`}>
    <h3>{trace.label}</h3>
    <dl>{rows.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
  </section>;
}

export function SpectrumAnalysisDetails({ label, traces, failures, contextKey, axisMode, logY, loading,
  actionsRef, hideTrigger = false }: Props) {
  const trigger = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const context = useRef(contextKey);
  const [open, setOpen] = useState(false);
  const disabled = loading || (!traces.length && !failures.length);
  const openDialog = () => {
    if (disabled) return;
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : trigger.current;
    setOpen(true);
  };
  useImperativeHandle(actionsRef, () => ({ open: openDialog }));
  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);
  useEffect(() => {
    if (context.current === contextKey) return;
    context.current = contextKey;
    dialog.current?.close(); setOpen(false);
  }, [contextKey]);
  const close = () => {
    dialog.current?.close(); setOpen(false);
    (opener.current?.isConnected ? opener.current : trigger.current)?.focus({ preventScroll: true });
  };
  return <>
    {!hideTrigger && <button type="button" ref={trigger} className={base.trigger}
      aria-label={`Spectrum details for ${label}`} aria-haspopup="dialog" aria-expanded={open}
      disabled={disabled}
      title={loading ? 'Wait for the current spectrum to finish.' : 'Source samples, method, missing values and display reduction'}
      onClick={openDialog}>Analysis</button>}
    {open && createPortal(<dialog ref={dialog} className={`${base.dialog} ${styles.dialog}`}
      aria-label={`Spectrum details for ${label}`}
      onCancel={(event) => { event.preventDefault(); close(); }} onClose={() => setOpen(false)}>
      <div className={base.heading}><strong>Spectrum analysis · {label}</strong><button type="button" onClick={close}>Close</button></div>
      <p>Calculated from stored samples, including saved edits and equations. Time-plot filters are not applied.
        U denotes the stored unit of {label}. All details describe the complete analysis interval;
        frequency zoom only changes the view.</p>
      {axisMode === 'per_rev' && <p>Order uses each trace’s mean absolute RPM: order = Hz × 60 / mean RPM.
        This is a mean-speed reference, without angular resampling. Only frequency is remapped; PSD stays in U²/Hz.</p>}
      {logY && <p>Log Y displays log10 of the linear value in the stated units, not decibels. Zero and negative values are hidden.</p>}
      {traces.some(({ data }) => data.mode === 'welch' && data.reduction?.method === 'max-bin') &&
        <p className={base.hint}>Do not integrate the reduced PSD display: its points are bucket maxima, not every uniformly spaced bin.</p>}
      {traces.map((trace) => <TraceDetails key={trace.label} trace={trace} axisMode={axisMode} />)}
      {failures.length > 0 && <section className={styles.trace} aria-label="Unavailable spectra">
        <h3>Unavailable spectra</h3>
        <ul>{failures.map((failure) => <li key={failure.label}><strong>{failure.label}</strong>: {failure.message}</li>)}</ul>
        <p>Close this dialog and use Retry on the plot after addressing the source problem.</p>
      </section>}
    </dialog>, document.body)}
  </>;
}
