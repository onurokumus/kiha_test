/** A time alignment changes only an axis that is the source's actual time
 * column. Retain the backend pairs for native provenance and CSV verification. */
export function alignedXYPairs(
  pairs: { x: number[]; y: number[] },
  xColumn: string,
  yColumn: string,
  timeColumn: string | null | undefined,
  timeOffset: number,
): { x: number[]; y: number[] } {
  return {
    x: timeOffset && xColumn === timeColumn ? pairs.x.map(value => value + timeOffset) : pairs.x,
    y: timeOffset && yColumn === timeColumn ? pairs.y.map(value => value + timeOffset) : pairs.y,
  };
}

export function xyFlightAxisLabel(
  column: string,
  label: string,
  timeColumns: readonly (string | null | undefined)[],
  timeBasis: 'stored' | 'elapsed',
): string {
  if (!timeColumns.some(timeColumn => timeColumn === column)) return label;
  if (timeColumns.some(timeColumn => timeColumn !== column)) return `${label} (time aligned per flight)`;
  return `${label} · aligned ${timeBasis === 'elapsed' ? 'elapsed' : 'stored'} time (s)`;
}
