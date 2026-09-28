import type uPlot from 'uplot';

// Shared live chart colors. Exports opt one plot into its original light palette.
const lightExports = new WeakSet<uPlot>();

export function plotColor(plot: uPlot, token: string, fallback: string): string {
  return lightExports.has(plot) ? fallback
    : getComputedStyle(plot.root).getPropertyValue(token).trim() || fallback;
}

/** Keep source hues/identities while lifting dark traces onto the dark canvas. */
export function themeSeriesColor(color: string, dark: boolean): string {
  const match = /^#([\da-f]{6})([\da-f]{2})?$/i.exec(color);
  if (!dark || !match) return color;
  const channels = [0, 2, 4].map(offset => parseInt(match[1].slice(offset, offset + 2), 16));
  const luminance = (mix: number) => channels.reduce((sum, channel, index) => {
    const value = (channel + (255 - channel) * mix) / 255;
    return sum + (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
      * [0.2126, 0.7152, 0.0722][index];
  }, 0);
  if (luminance(0) >= 0.32) return color;
  let low = 0, high = 1;
  for (let step = 0; step < 12; step++) {
    const middle = (low + high) / 2;
    if (luminance(middle) < 0.32) low = middle; else high = middle;
  }
  return '#' + channels.map(channel => Math.round(channel + (255 - channel) * high)
    .toString(16).padStart(2, '0')).join('') + (match[2] ?? '');
}

export function plotSeriesColor(plot: uPlot, color: string): string {
  return themeSeriesColor(color, !lightExports.has(plot) && document.documentElement.dataset.theme === 'dark');
}

/** Redraw in place: do not set data, auto-fit scales, or publish range changes. */
export function redrawPlotTheme(plot: uPlot): void {
  plot.batch(() => plot.redraw(false, true));
  // uPlot initializes HTML legend colors once; canvas colors are evaluated per draw.
  const rows = plot.root.querySelectorAll<HTMLElement>('.u-series');
  const offset = plot.series.length - rows.length;
  rows.forEach((row, rowIndex) => {
    const index = rowIndex + offset;
    const series = plot.series[index];
    if (!series || index === 0) return;
    const stroke = typeof series.stroke === 'function' ? series.stroke(plot, index) : series.stroke;
    const fill = typeof series.fill === 'function' ? series.fill(plot, index) : series.fill;
    const marker = row.querySelector<HTMLElement>('.u-marker');
    if (marker) {
      if (typeof stroke === 'string') marker.style.borderColor = stroke;
      if (typeof fill === 'string') marker.style.background = fill;
    } else {
      const label = row.querySelector<HTMLElement>('.u-label');
      const color = series.width ? stroke : fill;
      if (label && typeof color === 'string') label.style.color = color;
    }
  });
}

/** Synchronous capture keeps theme changes invisible and always restores the live chart. */
export function withLightPlot<T>(plot: uPlot, capture: () => T): T {
  if (plot.status !== 1 || !plot.root.isConnected ||
      document.documentElement.dataset.theme !== 'dark' || lightExports.has(plot)) return capture();
  lightExports.add(plot);
  try {
    redrawPlotTheme(plot);
    return capture();
  } finally {
    lightExports.delete(plot);
    redrawPlotTheme(plot);
  }
}

/** Cursor-sync groups: all plots in a mode track the same x position. */
export const TP_SYNC_KEY = 'ptt-tp-x'; // TP-overlay plots (relative time)
export const FULL_SYNC_KEY = 'ptt-full-x'; // full-test plots (absolute time)

export const ACCENT = '#263685';

export const PLOT_PADDING: [number, number, number, number] = [6, 20, 0, 0];

const AXIS_FONT = '11px Manrope';

const axisSize = (plot: uPlot, values: string[] | null, axisIndex: number) => {
  const side = plot.axes[axisIndex].side ?? (axisIndex === 0 ? 2 : 3);
  if (side === 0 || side === 2) return 22;

  // Numeric gutters follow the actual tick text, including scientific notation.
  const context = plot.ctx;
  context.save();
  context.font = AXIS_FONT;
  const width = Math.max(0, ...(values ?? []).map(value => context.measureText(value ?? '').width));
  context.restore();
  return Math.max(28, Math.ceil(width) + 9);
};

export const AXIS_STYLE = {
  stroke: (plot: uPlot) => plotColor(plot, '--muted', '#626f83'),
  grid: { stroke: (plot: uPlot) => plotColor(plot, '--border-subtle', '#dfe4ec'), width: 1 },
  ticks: { stroke: (plot: uPlot) => plotColor(plot, '--border', '#dfe4ec'), width: 1, size: 4 },
  font: AXIS_FONT,
  labelFont: AXIS_FONT,
  gap: 3,
  size: axisSize,
  labelSize: 16,
} as const;

export const TIME_AXIS_STYLE = {
  ...AXIS_STYLE,
  label: 'Time (s)',
} as const;

/** Auto-range guard for mode-2 (facet/scatter) scales. uPlot's default x
 *  range there is exactly [dataMin, dataMax] with ZERO padding (snapNumX), so
 *  a constant column — e.g. tp_id or a setpoint inside one TP — collapses the
 *  scale to zero width. numAxisSplits' tick loop (`val += incr` until
 *  `val > scaleMax`) then never terminates and pushes ticks until the tab
 *  dies of OOM (or RangeError, bug 1.15b). Near-flat spans hit the same loop
 *  when the tick increment underflows double precision at the values'
 *  magnitude (val + incr === val). Only auto-ranging goes through this fn —
 *  explicit setScale min/max (drag-zoom, pan) bypass scale.range entirely. */
export const safeRange = (
  _u: unknown,
  min: number | null,
  max: number | null
): [number | null, number | null] => {
  if (min == null || max == null) return [null, null];
  const mag = Math.max(Math.abs(min), Math.abs(max));
  const span = max - min;
  if (span <= mag * 1e-9) {
    // flat or below float-precision resolution: pad proportional to magnitude
    const pad = mag === 0 ? 1 : mag * 1e-3;
    return [min - pad, max + pad];
  }
  return [min - span * 0.05, max + span * 0.05];
};

const PALETTE = [
  '#263685',
  '#ae673b',
  '#5c7d34',
  '#945da8',
  '#806b20',
  '#237c66',
  '#b84343',
  '#187c9f',
];

export const colorFor = (i: number): string => PALETTE[i % PALETTE.length];
