export interface SavedWaterfallColorRange {
  column: string;
  /** Linear amplitude units; null uses the current grid's automatic range. */
  linear: [number, number] | null;
  /** log10(amplitude) units, kept independently from the linear range. */
  log: [number, number] | null;
}

export function validWaterfallColorRange(
  value: unknown, logColor: boolean,
): value is [number, number] {
  if (!Array.isArray(value) || value.length !== 2) return false;
  const [min, max] = value;
  return typeof min === 'number' && typeof max === 'number'
    && Number.isFinite(min) && Number.isFinite(max)
    && min < max && Number.isFinite(max - min) && (logColor || min >= 0);
}

/** Increase shared precision until narrow ranges have distinct, truthful labels. */
export function waterfallColorTicks(range: [number, number]): { fraction: number; label: string }[] {
  if (!validWaterfallColorRange(range, true)) return [];
  const [min, max] = range;
  const midpoint = min + 0.5 * (max - min);
  const points = [{ fraction: 0, value: min },
    ...(midpoint > min && midpoint < max ? [{ fraction: 0.5, value: midpoint }] : []),
    { fraction: 1, value: max }];
  for (let precision = 5; precision <= 17; precision++) {
    const ticks = points.map(({ fraction, value }) => {
      const rounded = Number(value.toPrecision(precision));
      // Rounding the largest finite value can overflow while its source is valid.
      return { fraction, label: Number.isFinite(rounded) ? rounded.toString() : value.toString() };
    });
    if (new Set(ticks.map(tick => tick.label)).size === ticks.length) return ticks;
  }
  // Seventeen significant digits round-trip distinct IEEE-754 values.
  return points.map(({ fraction, value }) => ({ fraction, label: value.toString() }));
}

/** Keep each slot tied to its exact variable; source and FFT choices are independent. */
export function normalizeWaterfallColorRanges(value: unknown): (SavedWaterfallColorRange | null)[] {
  return Array.from({ length: 9 }, (_, index) => {
    const item = Array.isArray(value) ? value[index] : null;
    if (!item || typeof item !== 'object' || Array.isArray(item)
      || typeof item.column !== 'string' || !item.column.length
      || (item.linear !== null && !validWaterfallColorRange(item.linear, false))
      || (item.log !== null && !validWaterfallColorRange(item.log, true))) return null;
    return { column: item.column,
      linear: item.linear === null ? null : [item.linear[0], item.linear[1]],
      log: item.log === null ? null : [item.log[0], item.log[1]] };
  });
}

/** Editing one mode retains the other only when it belongs to this variable. */
export function changeWaterfallColorRange(
  saved: SavedWaterfallColorRange | null,
  column: string,
  logColor: boolean,
  range: [number, number] | null,
): SavedWaterfallColorRange {
  const previous = saved?.column === column ? saved : { column, linear: null, log: null };
  return { ...previous, [logColor ? 'log' : 'linear']: range };
}
