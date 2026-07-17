// Shared uPlot styling to match the FMS dark theme.

/** Cursor-sync groups: all plots in a mode track the same x position. */
export const TP_SYNC_KEY = 'ptt-tp-x'; // TP-overlay plots (relative time)
export const FULL_SYNC_KEY = 'ptt-full-x'; // full-test plots (absolute time)

export const ACCENT = '#569cd6';

export const AXIS_STYLE = {
  stroke: '#a0a0a0',
  grid: { stroke: '#3c3c3c', width: 1 },
  ticks: { stroke: '#3c3c3c', width: 1 },
  font: '11px Segoe UI',
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
  '#569cd6',
  '#ce9178',
  '#6a9955',
  '#c586c0',
  '#dcdcaa',
  '#4ec9b0',
  '#f48771',
  '#9cdcfe',
];

export const colorFor = (i: number): string => PALETTE[i % PALETTE.length];
