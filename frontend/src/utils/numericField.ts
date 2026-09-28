export interface NumericConstraints {
  min?: number | string;
  max?: number | string;
  exclusiveMin?: boolean;
  exclusiveMax?: boolean;
  integer?: boolean;
  allowEmpty?: boolean;
}

/** Decimal/scientific notation only. Empty or incomplete drafts are never zero. */
export function parseFiniteNumber(raw: string | number | readonly string[] | undefined): number | null {
  const text = String(raw ?? '').trim();
  if (!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(text)) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

export function numericError(raw: string | number | readonly string[] | undefined, constraints: NumericConstraints = {}): string {
  const text = String(raw ?? '').trim();
  if (!text) return constraints.allowEmpty ? '' : 'Enter a value.';
  const value = parseFiniteNumber(raw);
  if (value === null) return 'Enter a finite number.';
  if (constraints.integer && !Number.isInteger(value)) return 'Use a whole number.';
  const min = parseFiniteNumber(constraints.min);
  const max = parseFiniteNumber(constraints.max);
  if (min !== null && (constraints.exclusiveMin ? value <= min : value < min))
    return constraints.exclusiveMin ? `Use a value above ${min}.` : `Minimum ${min}.`;
  if (max !== null && (constraints.exclusiveMax ? value >= max : value > max))
    return constraints.exclusiveMax ? `Use a value below ${max}.` : `Maximum ${max}.`;
  return '';
}
