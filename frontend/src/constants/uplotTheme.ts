import type uPlot from 'uplot';

// Shared uPlot styling for the light engineering workspace.

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
  stroke: '#626f83',
  grid: { stroke: '#dfe4ec', width: 1 },
  ticks: { stroke: '#dfe4ec', width: 1, size: 4 },
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
