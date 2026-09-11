import type uPlot from 'uplot';
import { fetchPlotImagePackage } from '../services/api';
import { imageMetadata } from './analysisMetadata';
import { checkExport, reportExport, yieldExport } from '../services/exportProgress';

export interface PlotPngContent {
  provenance?: Record<string, unknown>;
  title: string;
  /** Source test / TP identifiers and the meaning of the time axis. */
  scope: string[];
  /** Actual display/filter settings, reduction and relevant warnings. */
  details?: string[];
}

export interface PlotPngOptions extends PlotPngContent {
  includeMetadata?: boolean;
  filename: string;
  /** Encoding may finish after cancellation, but no download will be started. */
  signal?: AbortSignal;
}

/** Owned bitmap: dispose after encoding, composition, cancellation or failure. */
export interface PlotPngCapture {
  readonly metadata: Record<string, unknown>;
  readonly canvas: HTMLCanvasElement;
  readonly cssWidth: number;
  readonly cssHeight: number;
  readonly pixelRatio: number;
  dispose: () => void;
}

export interface PlotLayoutPngOptions {
  includeMetadata?: boolean;
  filename: string;
  title: string;
  columns: 2 | 3;
  plots: { /** Original one-based grid slot (1–9). */ slot: number; capture: () => PlotPngCapture }[];
  signal?: AbortSignal;
}

// Bound each RGBA bitmap to 64 MB and avoid browser-specific canvas dimension
// limits. Layouts also cap their input sum at 64 MB (at most 128 MB owned while
// composing). Reject oversize output rather than omit source identifiers or
// silently reduce the resolution of the displayed chart.
const MAX_EDGE = 8192;
const MAX_PIXELS = 16_000_000;
const MAX_TEXT_CHARACTERS = 64_000;
const MIN_CSS_WIDTH = 640;
const PADDING = 24;
const BACKGROUND = '#252526';
const FONT_FAMILY = '"Segoe UI", Arial, sans-serif';

interface TextRow {
  text: string;
  x: number;
  y: number;
  font: string;
  color: string;
}

interface LegendEntry {
  label: string;
  stroke: CanvasRenderingContext2D['strokeStyle'];
  width: number;
  dash: number[];
  alpha: number;
}

const sizeError = () => new Error(
  'This PNG exceeds the image size limit. Reduce the plot size or the number or length of its labels and details.'
);

const checkSize = (width: number, height: number) => {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width < 1 || height < 1 ||
      width > MAX_EDGE || height > MAX_EDGE || width * height > MAX_PIXELS) {
    throw sizeError();
  }
};

/** Wrap even a single long identifier; never ellipsize provenance in a file. */
function wrapText(context: CanvasRenderingContext2D, text: string, width: number): string[] {
  const lines: string[] = [];
  for (const paragraph of text.replace(/\r\n?/g, '\n').split('\n')) {
    if (!paragraph) {
      lines.push('');
      continue;
    }
    let line = '';
    let lastSpace = -1;
    // Code points keep surrogate-pair characters intact when breaking names.
    for (const character of paragraph) {
      const candidate = line + character;
      if (line && context.measureText(candidate).width > width) {
        if (lastSpace > 0) {
          lines.push(line.slice(0, lastSpace));
          line = line.slice(lastSpace + 1) + character;
        } else {
          lines.push(line);
          line = character;
        }
        lastSpace = line.lastIndexOf(' ');
      } else {
        line = candidate;
        if (character === ' ') lastSpace = line.length - 1;
      }
    }
    lines.push(line);
  }
  return lines;
}

function legendSnapshot(plot: uPlot): LegendEntry[] {
  return plot.series.slice(1).flatMap((series, offset) => {
    if (series.show === false) return [];
    const stroke = typeof series.stroke === 'function'
      ? series.stroke(plot, offset + 1)
      : series.stroke;
    return [{
      label: typeof series.label === 'string'
        ? series.label
        : series.label?.textContent || `Series ${offset + 1}`,
      stroke: stroke || '#d7dde3',
      width: Number.isFinite(series.width) ? Math.max(1, series.width!) : 1.5,
      dash: [...(series.dash ?? [])],
      alpha: Number.isFinite(series.alpha) ? Math.max(0, Math.min(1, series.alpha!)) : 1,
    }];
  });
}

function axisSnapshot(plot: uPlot): string {
  return ['x', 'y'].map((key) => {
    const scale = plot.scales[key];
    if (!scale || !Number.isFinite(scale.min) || !Number.isFinite(scale.max)) {
      throw new Error('The plot axes are not ready. Wait for the plot to finish drawing and try again.');
    }
    const axis = plot.axes.find((candidate) => candidate.scale === key);
    const label = axis?.label ? `${key.toUpperCase()} (${axis.label})` : key.toUpperCase();
    // Preserve the actual numeric bounds, including precision on narrow zooms.
    return `${label}: ${scale.min} to ${scale.max}`;
  }).join('  ·  ');
}

function downloadBlob(blob: Blob, requestedName: string, extension: 'png' | 'zip' = 'png'): void {
  const cleanedName = Array.from(requestedName, (character) =>
    character.charCodeAt(0) < 32 || '<>:"/\\|?*'.includes(character) ? '_' : character
  ).join('').trim().replace(/\.(png|zip)$/i, '');
  // Bound the filename separately from the fully retained image labels. Count
  // UTF-16 units for Windows while avoiding a split Unicode surrogate pair.
  let stem = '';
  for (const character of cleanedName) {
    if (stem.length + character.length > 210) break;
    stem += character;
  }
  const filename = `${stem.replace(/[. ]+$/g, '') || 'plot'}.${extension}`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.style.display = 'none';
  let revokeTimer = 0;
  let revoked = false;
  const release = () => {
    if (revoked) return;
    revoked = true;
    window.clearTimeout(revokeTimer);
    window.removeEventListener('pagehide', release);
    URL.revokeObjectURL(url);
  };
  try {
    document.body.appendChild(link);
    link.click();
    // Revoking in the click's task can race the browser's download consumer.
    // The URL also gets released if this page leaves before the timer runs.
    window.addEventListener('pagehide', release, { once: true });
    revokeTimer = window.setTimeout(release, 30_000);
  } catch (error) {
    release();
    throw error;
  } finally {
    link.remove();
  }
}

/**
 * Capture the current uPlot canvas with durable, fully wrapped context and legend.
 * The caller gates incomplete/error views and supplies their actual scope and
 * settings. No fetch, live-plot mutation, DOM screenshot or hidden-series data
 * is involved. This function is entirely synchronous so callers can capture
 * several plots in one task before any view can change between snapshots.
 */
export function capturePlotPng(plot: uPlot, options: PlotPngContent): PlotPngCapture {
  const source = plot.ctx.canvas;
  if (plot.status !== 1 || !plot.root.isConnected || source.width < 1 || source.height < 1 ||
      !Number.isFinite(plot.width) || plot.width <= 0) {
    throw new Error('The plot is not ready to export. Wait for it to finish drawing and try again.');
  }
  checkSize(source.width, source.height);
  const legend = legendSnapshot(plot);
  if (legend.length === 0) throw new Error('Show at least one plot trace before exporting an image.');
  const axes = axisSnapshot(plot);
  const title = options.title || 'Time plot';
  const scope = [...options.scope];
  const details = [...(options.details ?? []), `Displayed axes · ${axes}`];
  const characterCount = [title, ...scope, ...details, ...legend.map((entry) => entry.label)]
    .reduce((total, text) => total + text.length, 0);
  if (characterCount > MAX_TEXT_CHARACTERS) throw sizeError();

  // Derive density from this canvas, not window.devicePixelRatio: uPlot can
  // still have the previous density while a browser-zoom resize is settling.
  const pixelRatio = source.width / plot.width;
  const cssWidth = Math.max(MIN_CSS_WIDTH, plot.width + 2 * PADDING);
  const pixelWidth = Math.ceil(cssWidth * pixelRatio);
  const chartHeight = source.height / pixelRatio;
  checkSize(pixelWidth, source.height);
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) throw new Error('The browser could not create the PNG drawing surface.');
  const textRows: TextRow[] = [];
  const legendRows: { entry: LegendEntry; y: number }[] = [];
  let y = PADDING;

  const addText = (text: string, font: string, color: string, lineHeight: number, x = PADDING) => {
    context.font = font;
    for (const line of wrapText(context, text, cssWidth - x - PADDING)) {
      textRows.push({ text: line, x, y, font, color });
      y += lineHeight;
      checkSize(pixelWidth, Math.ceil((y + PADDING) * pixelRatio));
    }
  };

  addText(title, `600 18px ${FONT_FAMILY}`, '#edf2f5', 25);
  y += 7;
  scope.forEach((line) => addText(line, `12px ${FONT_FAMILY}`, '#c7d3dc', 18));
  if (scope.length > 0) y += 6;
  details.forEach((line) => addText(line, `11px ${FONT_FAMILY}`, '#aab7c1', 17));
  const chartTop = Math.ceil((y + 12) * pixelRatio) / pixelRatio;
  y = chartTop + chartHeight + 16;
  addText('Visible traces', `600 12px ${FONT_FAMILY}`, '#c7d3dc', 21);
  legend.forEach((entry) => {
    legendRows.push({ entry, y });
    addText(entry.label, `12px ${FONT_FAMILY}`, '#d7dde3', 18, PADDING + 42);
    y += 5;
  });
  const pixelHeight = Math.ceil((y + PADDING) * pixelRatio);
  checkSize(pixelWidth, pixelHeight);
  try {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
    context.fillStyle = BACKGROUND;
    context.fillRect(0, 0, pixelWidth, pixelHeight);
    context.scale(pixelRatio, pixelRatio);
    context.textBaseline = 'top';
    textRows.forEach((row) => {
      context.font = row.font;
      context.fillStyle = row.color;
      context.fillText(row.text, row.x, row.y);
    });
    const chartLeft = Math.round((cssWidth - plot.width) * pixelRatio / 2) / pixelRatio;
    // Native source pixels are copied without stretching the compact chart.
    context.drawImage(source, chartLeft, chartTop, plot.width, chartHeight);
    legendRows.forEach(({ entry, y: rowY }) => {
      context.save();
      context.strokeStyle = entry.stroke;
      context.lineWidth = entry.width;
      context.setLineDash(entry.dash);
      context.globalAlpha = entry.alpha;
      context.beginPath();
      context.moveTo(PADDING, rowY + 8);
      context.lineTo(PADDING + 30, rowY + 8);
      context.stroke();
      context.restore();
    });
  } catch (error) {
    canvas.width = 1;
    canvas.height = 1;
    throw error;
  }
  return {
    canvas, cssWidth, cssHeight: pixelHeight / pixelRatio, pixelRatio,
    metadata: structuredClone({ ...options.provenance, title, scope, details,
      axes: Object.fromEntries(['x', 'y'].map((key) => [key, {
        min: plot.scales[key].min, max: plot.scales[key].max,
        label: plot.axes.find((axis) => axis.scale === key)?.label ?? key,
      }])), legend: legend.map(({ label, width, dash, alpha }) => ({ label, width, dash, alpha })),
      pixel_ratio: pixelRatio }),
    dispose: () => { canvas.width = 1; canvas.height = 1; },
  };
}

async function encodeAndDownload(canvas: HTMLCanvasElement, filename: string, signal?: AbortSignal, metadata?: Record<string, unknown>) {
  reportExport(signal, { stage: 'Encoding PNG' });
  await yieldExport(signal);
  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((result) => result
      ? resolve(result)
      : reject(new Error('The browser could not encode this PNG. Reduce the plot size and try again.')), 'image/png');
  });
  if (signal?.aborted) return;
  if (metadata) {
    const archive = await fetchPlotImagePackage(blob, metadata, filename, signal);
    if (!signal?.aborted) downloadBlob(archive, filename, 'zip');
  } else downloadBlob(blob, filename);
}

/** Single-plot compatibility wrapper over the same capture used by layouts. */
export async function downloadPlotPng(plot: uPlot, options: PlotPngOptions): Promise<void> {
  const { signal, filename } = options;
  if (signal?.aborted) return;
  reportExport(signal, { stage: 'Capturing plot' });
  const capture = capturePlotPng(plot, options);
  try {
    await encodeAndDownload(capture.canvas, filename, signal, options.includeMetadata ? imageMetadata([capture.metadata]) : undefined);
  } finally {
    capture.dispose();
  }
}

/**
 * Capture selected plots together and download one fixed 2x2 or 3x3 image.
 * Successful captures are owned here even if a later callback fails. Every
 * callback completes synchronously before composition yields control. Every
 * live canvas and its provenance is frozen together; composition only reads
 * those owned captures and can yield between cells without changing the image.
 */
export async function downloadPlotLayoutPng(options: PlotLayoutPngOptions): Promise<void> {
  const { filename, title, columns, signal } = options;
  if (signal?.aborted) return;
  if (columns !== 2 && columns !== 3) throw new Error('Choose a 2x2 or 3x3 PNG layout.');
  const plots = [...options.plots].sort((a, b) => a.slot - b.slot);
  if (plots.length === 0 || plots.length > columns * columns) {
    throw new Error(`Choose between 1 and ${columns * columns} plots for this PNG layout.`);
  }
  if (plots.some((plot) => !Number.isInteger(plot.slot) || plot.slot < 1 || plot.slot > 9) ||
      new Set(plots.map((plot) => plot.slot)).size !== plots.length) {
    throw new Error('Choose each original plot slot once (slots 1–9).');
  }
  if (title.length > MAX_TEXT_CHARACTERS) throw sizeError();
  reportExport(signal, { stage: 'Capturing plots' });
  const captures: { slot: number; image: PlotPngCapture }[] = [];
  let output: HTMLCanvasElement | null = null;
  try {
    let capturedPixels = 0;
    for (const plot of plots) {
      checkExport(signal);
      let image: PlotPngCapture;
      try {
        image = plot.capture();
      } catch (error) {
        throw new Error(`Plot ${plot.slot}: ${error instanceof Error ? error.message : 'The plot could not be captured.'}`);
      }
      captures.push({ slot: plot.slot, image });
      checkSize(image.canvas.width, image.canvas.height);
      if (image.canvas.width <= 1 || image.canvas.height <= 1 ||
          !Number.isFinite(image.pixelRatio) || image.pixelRatio <= 0) {
        throw new Error(`Plot ${plot.slot} is no longer available. Refresh the export selection and try again.`);
      }
      capturedPixels += image.canvas.width * image.canvas.height;
      if (capturedPixels > MAX_PIXELS) throw sizeError();
    }
    reportExport(signal, { stage: 'Composing image', completed: 0, total: plots.length, unit: 'plots' });
    await yieldExport(signal);
    const pixelRatio = Math.max(...captures.map(({ image }) => image.pixelRatio));
    // Different live plots can round native dimensions by one pixel. Allow
    // that rounding, but reject a genuinely mixed density during browser zoom
    // rather than silently rescale one card's text or native chart pixels.
    if (captures.some(({ image }) => Math.abs(image.pixelRatio / pixelRatio - 1) > 0.01)) {
      throw new Error('The plot resolutions are still changing. Wait for resizing or browser zoom to finish and try again.');
    }
    const padding = Math.ceil(20 * pixelRatio);
    const gap = Math.ceil(16 * pixelRatio);
    const slotHeight = Math.ceil(32 * pixelRatio);
    const cellWidth = Math.max(...captures.map(({ image }) => image.canvas.width));
    const cellHeight = slotHeight + Math.max(...captures.map(({ image }) => image.canvas.height));
    const width = 2 * padding + columns * cellWidth + (columns - 1) * gap;
    checkSize(width, columns * cellHeight);
    output = document.createElement('canvas');
    const context = output.getContext('2d');
    if (!context) throw new Error('The browser could not create the PNG layout drawing surface.');
    const headingRows: TextRow[] = [];
    let y = padding;
    const heading = (text: string, size: number, weight: number, color: string, lineHeight: number) => {
      const font = `${weight} ${size * pixelRatio}px ${FONT_FAMILY}`;
      context.font = font;
      for (const line of wrapText(context, text, width - 2 * padding)) {
        headingRows.push({ text: line, x: padding, y, font, color });
        y += Math.ceil(lineHeight * pixelRatio);
        checkSize(width, y + columns * cellHeight);
      }
    };
    heading(title || 'Time plot layout', 20, 600, '#edf2f5', 28);
    heading(`${columns}×${columns} layout · ${captures.length} selected plot${captures.length === 1 ? '' : 's'} · Original grid slot numbers`,
      12, 400, '#b7c7d2', 20);
    const gridTop = y + padding;
    const height = gridTop + columns * cellHeight + (columns - 1) * gap + padding;
    checkSize(width, height);
    output.width = width;
    output.height = height;
    context.fillStyle = '#1c1f22';
    context.fillRect(0, 0, width, height);
    context.textBaseline = 'top';
    headingRows.forEach((row) => {
      context.font = row.font;
      context.fillStyle = row.color;
      context.fillText(row.text, row.x, row.y);
    });
    for (let index = 0; index < columns * columns; index += 1) {
      const left = padding + (index % columns) * (cellWidth + gap);
      const top = gridTop + Math.floor(index / columns) * (cellHeight + gap);
      context.fillStyle = BACKGROUND;
      context.fillRect(left, top, cellWidth, cellHeight);
      const capture = captures[index];
      if (!capture) continue; // Intentionally blank unused cells.
      context.font = `600 ${12 * pixelRatio}px ${FONT_FAMILY}`;
      context.fillStyle = '#bdd6e6';
      context.fillText(`Plot ${capture.slot}`, left + padding, top + Math.ceil(10 * pixelRatio));
      context.drawImage(capture.image.canvas,
        left + Math.floor((cellWidth - capture.image.canvas.width) / 2), top + slotHeight);
      reportExport(signal, { stage: 'Composing image', completed: index + 1, total: captures.length, unit: 'plots' });
      await yieldExport(signal);
    }
    // The composed bitmap now owns every card's pixels. Release source cards
    // before encoding; source sum and output each stay within 16M pixels.
    const metadata = options.includeMetadata ? imageMetadata(captures.map(({ slot, image }) =>
      ({ ...image.metadata, slot })), columns === 2 ? '2x2' : '3x3') : undefined;
    captures.forEach(({ image }) => image.dispose());
    captures.length = 0;
    await encodeAndDownload(output, filename, signal, metadata);
  } finally {
    captures.forEach(({ image }) => image.dispose());
    if (output) { output.width = 1; output.height = 1; }
  }
}
