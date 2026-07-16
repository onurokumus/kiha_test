export const formatValue = (value: number | string, decimals: number = 3): string | number => {
  if (typeof value !== 'number') return value;
  return Number.isInteger(value) ? value : value.toFixed(decimals);
};
