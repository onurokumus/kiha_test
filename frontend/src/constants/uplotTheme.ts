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
