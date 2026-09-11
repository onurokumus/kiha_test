/** Split preview choices are browser preferences, separate from saved TP data. */
export const MAX_SPLIT_PLOTS = 9;
const STORAGE_PREFIX = 'ptt.split-plots.v1:';

export function normalizeSplitColumns(value: unknown, available: readonly string[]): string[] {
  const allowed = new Set(available);
  const result = Array.isArray(value)
    ? [...new Set(value.filter((column): column is string =>
      typeof column === 'string' && allowed.has(column)))].slice(0, MAX_SPLIT_PLOTS)
    : [];
  return result.length ? result : available.slice(0, 1);
}

export function loadSplitColumns(test: string, available: readonly string[]): string[] {
  try {
    const stored = window.localStorage.getItem(STORAGE_PREFIX + test);
    return normalizeSplitColumns(stored ? JSON.parse(stored) : null, available);
  } catch {
    return normalizeSplitColumns(null, available);
  }
}

export function saveSplitColumns(test: string, columns: readonly string[]): void {
  try {
    window.localStorage.setItem(STORAGE_PREFIX + test, JSON.stringify(columns));
  } catch {
    // Storage restrictions must not prevent editing in the current view.
  }
}
