export const formatValue = (value: number | string): string | number => {
  if (typeof value !== 'number') return value;
  return Number.isInteger(value) ? value : value.toFixed(3);
};

// Round to 3 decimals (millisecond precision for the split editor's times).
// Shared by SplitView and SplitPlot so the two cannot drift.
export const round3 = (v: number) => Math.round(v * 1000) / 1000;
