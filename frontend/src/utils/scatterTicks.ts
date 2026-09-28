export interface ScatterTickSet {
  ticks: number[];
  minorTicks: number[];
  step: number;
  format: (value: number) => string;
}

const MAX_INTERVALS = 10;
const MAX_TICKS = MAX_INTERVALS + 2;

/** Decimal place represented by the step, including quarter-decade steps. */
function precisionExponent(step: number): number {
  const [coefficient, power] = step.toExponential(14).split('e');
  const fraction = (coefficient.split('.')[1] ?? '').replace(/0+$/, '');
  return Number(power) - fraction.length;
}

function compactExponent(value: string): string {
  return value.replace(/(\.\d*?[1-9])0+(?=e)|\.0+(?=e)/, '$1').replace('e+', 'e');
}

/** Use the grid step's precision, retaining meaningful small/large values. */
export function formatScatterTick(value: number, step: number): string {
  if (!Number.isFinite(value)) return '';
  if (value === 0) return '0';
  if (!Number.isFinite(step) || step <= 0) return compactExponent(String(value));

  const absolute = Math.abs(value);
  const places = precisionExponent(step);
  if (absolute >= 1e7 || absolute < 1e-4) {
    const digits = Math.min(16, Math.max(0, Math.floor(Math.log10(absolute)) - places));
    return compactExponent(value.toExponential(digits));
  }
  const fixed = value.toFixed(Math.min(100, Math.max(0, -places)));
  const trimmed = fixed.includes('.') ? fixed.replace(/0+$/, '').replace(/\.$/, '') : fixed;
  if (trimmed === '-0') return '0';
  const [integer, fraction] = trimmed.split('.');
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction ? `.${fraction}` : '');
}

function niceStep(raw: number): number {
  if (!Number.isFinite(raw)) return Number.MAX_VALUE;
  const minimum = Math.max(Number.MIN_VALUE, raw);
  const decade = 10 ** Math.floor(Math.log10(minimum)) || Number.MIN_VALUE;
  for (const coefficient of [1, 2, 2.5, 5, 10]) {
    const candidate = coefficient * decade;
    if (!Number.isFinite(candidate)) return Number.MAX_VALUE;
    if (candidate >= minimum * (1 - 1e-12)) return candidate;
  }
  return Number.MAX_VALUE;
}

/**
 * A zero-anchored engineering grid whose spacing changes with the viewport.
 * Only visible ticks are returned; callers retain the original axis domain.
 */
export function createScatterTicks(
  minimum: number,
  maximum: number,
  pixelLength: number,
  axis: 'x' | 'y' = 'x',
): ScatterTickSet {
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) {
    return { ticks: [], minorTicks: [], step: 0, format: value => formatScatterTick(value, 0) };
  }
  const low = Math.min(minimum, maximum);
  const high = Math.max(minimum, maximum);
  if (low === high) {
    return { ticks: [low === 0 ? 0 : low], minorTicks: [], step: 0,
      format: value => formatScatterTick(value, 0) };
  }

  const spacing = axis === 'x' ? 90 : 60;
  const available = Number.isFinite(pixelLength) && pixelLength > 0 ? pixelLength : spacing;
  const intervals = Math.max(1, Math.min(MAX_INTERVALS, Math.floor(available / spacing)));
  const span = high - low;
  // Divide first when subtracting opposite finite extremes would overflow.
  const raw = Number.isFinite(span) ? span / intervals : high / intervals - low / intervals;
  // A step below the precision available at a large offset creates duplicate
  // values and labels, even when the mathematical range is nonzero.
  const quantum = Math.max(Math.abs(low), Math.abs(high)) * Number.EPSILON;
  let step = niceStep(Math.max(raw, quantum, Number.MIN_VALUE));
  let ticks: number[] = [];
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const decimalPlaces = Math.max(0, -precisionExponent(step));
    ticks = [];
    const first = Math.floor(low / step);
    const tolerance = step * 1e-9;
    for (let offset = 0; offset <= MAX_TICKS + 2; offset += 1) {
      let value = (first + offset) * step;
      if (!Number.isFinite(value)) break;
      if (decimalPlaces <= 100) value = Number(value.toFixed(decimalPlaces));
      if (value < low && low - value <= tolerance) value = low;
      if (value > high && value - high <= tolerance) value = high;
      if (value > high) break;
      if (value >= low && (ticks.length === 0 || value > ticks[ticks.length - 1])) {
        ticks.push(value === 0 ? 0 : value);
      }
      if (ticks.length === MAX_TICKS) break;
    }
    if (ticks.length >= 2 || attempt === 2) break;
    // A panned range can straddle only one coarse multiple. Refine once or
    // twice before resorting to irregular endpoint labels on narrow plots.
    const finer = niceStep(Math.max(step / 2, quantum, Number.MIN_VALUE));
    if (finer >= step) break;
    step = finer;
  }
  // A very short axis or adjacent representable values may contain no two
  // multiples of a nice step. Show its actual bounds rather than empty labels.
  const useBounds = ticks.length < 2;
  if (useBounds) ticks.splice(0, ticks.length, low === 0 ? 0 : low, high === 0 ? 0 : high);

  const labels = ticks.map(value => formatScatterTick(value, useBounds ? 0 : step));
  // Extreme offset/range ratios can exceed fixed/scientific display precision.
  // Number's shortest round-tripping representation keeps those ticks distinct.
  const duplicate = labels.some((label, index) => labels.indexOf(label) !== index);
  const exactLabels = new Map(ticks.map((value, index) => [value,
    duplicate ? compactExponent(String(value)) : labels[index]]));
  const minorTicks = ticks.slice(1).map((value, index) => ticks[index] / 2 + value / 2)
    .filter((value, index) => value > ticks[index] && value < ticks[index + 1]);

  return { ticks, minorTicks, step,
    format: value => exactLabels.get(value) ?? formatScatterTick(value, step) };
}
