import type uPlot from 'uplot';
import type { FilterSpec, AnyPlotExportRequest, PlotExportBundleRequest } from '../types';
import { fetchPlotCsv, fetchPlotCsvBundle } from '../services/api';
import { FILTER_LABELS } from '../constants/filters';

export function plotExportRange(plot: uPlot | null): [number, number] {
  const min = plot?.scales.x.min, max = plot?.scales.x.max;
  if (min == null || max == null || !Number.isFinite(min) || !Number.isFinite(max) || max <= min) {
    throw new Error('Wait for a plot with a valid time range before exporting.');
  }
  return [min, max];
}

/** Default view means the complete source, not its rounded last display point.
 * Detect a local wheel/pan preview too: its scale can lead React by 120 ms. */
export function plotCsvRange(plot: uPlot | null, zoomed: boolean, faceted = false): [number, number] | null {
  const range = plotExportRange(plot);
  if (zoomed || !plot) return range;
  const data = plot.data as unknown[];
  const times = faceted
    ? data.slice(1).filter((_facet, index) => plot.series[index + 1]?.show !== false)
      .map((facet) => (facet as unknown[])[0] as ArrayLike<number | null>)
    : [data[0] as ArrayLike<number | null>];
  let min = Infinity, max = -Infinity;
  for (const axis of times) {
    if (!axis) continue;
    for (let index = 0; index < axis.length; index++) {
      const value = axis[index];
      if (value != null && Number.isFinite(value)) {
        min = Math.min(min, value); max = Math.max(max, value);
      }
    }
  }
  return range[0] === min && range[1] === max ? null : range;
}

export async function downloadPlotCsv(request: AnyPlotExportRequest, signal: AbortSignal) {
  const { blob, filename } = await fetchPlotCsv(request, signal);
  if (signal.aborted) return;
  downloadFile(blob, filename, request.include_metadata ? 'zip' : 'csv');
}

export async function downloadPlotCsvBundle(request: PlotExportBundleRequest, signal: AbortSignal) {
  const { blob, filename } = await fetchPlotCsvBundle(request, signal);
  if (signal.aborted) return;
  downloadFile(blob, filename, 'zip');
}

function downloadFile(blob: Blob, filename: string, extension: 'csv' | 'zip') {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename.replace(/\.(csv|zip)$/i, '').replace(/[<>:"/\\|?*\p{Cc}]/gu, '_').slice(0, 210) + `.${extension}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  const release = () => URL.revokeObjectURL(url);
  const timer = window.setTimeout(() => {
    release(); window.removeEventListener('pagehide', onHide);
  }, 60_000);
  const onHide = () => { window.clearTimeout(timer); release(); };
  window.addEventListener('pagehide', onHide, { once: true });
}

/** Record actual numeric settings, with units, rather than transient input text. */
export function plotFilterDetails(spec: FilterSpec | null): string[] {
  if (!spec) return ['Original stored data; includes prior saved edits and equations.'];
  const details = [`Filter: ${FILTER_LABELS[spec.kind]}. Values retain the variable's units.`];
  if (spec.order != null) details.push(`Order: ${spec.order}`);
  if (spec.f1 != null) details.push(`Cutoff: ${spec.f1} Hz${spec.f2 != null ? ` to ${spec.f2} Hz` : ''}`);
  if (spec.windowS != null) details.push(`Window: ${spec.windowS} s`);
  if (spec.kind === 'despike') details.push(
    `Max spike: ${spec.maxSpikeS} s; threshold multiplier: ${spec.threshold} (1.4826 × residual MAD); minimum jump: ${spec.absFloor} units; replacement: ${spec.replacement}.`
  );
  return details;
}
