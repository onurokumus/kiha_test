import type { ScatterTickSet } from '../../utils/scatterTicks';

interface Props {
  xTicks: ScatterTickSet;
  yTicks: ScatterTickSet;
  xAxisMap?: Record<string, { scale: (value: number) => number }>;
  yAxisMap?: Record<string, { scale: (value: number) => number }>;
  offset?: { left: number; top: number; width: number; height: number };
}

/** A quiet solid grid, with zero as a useful reference rather than another tick. */
export function ScatterGrid({ xTicks, yTicks, xAxisMap, yAxisMap, offset }: Props) {
  const x = xAxisMap?.['0']?.scale, y = yAxisMap?.['0']?.scale;
  if (!x || !y || !offset || offset.width <= 0 || offset.height <= 0) return null;
  const { left, top, width, height } = offset;
  const layers = [
    { name: 'minor', xs: xTicks.minorTicks, ys: yTicks.minorTicks, stroke: 'var(--plot-grid-minor, #f0f3f7)', light: '#f0f3f7' },
    { name: 'major', xs: xTicks.ticks.filter(t => t !== 0), ys: yTicks.ticks.filter(t => t !== 0), stroke: 'var(--plot-grid, #e1e7ef)', light: '#e1e7ef' },
    { name: 'zero', xs: xTicks.ticks.includes(0) ? [0] : [], ys: yTicks.ticks.includes(0) ? [0] : [], stroke: 'var(--plot-zero, #a5b4ca)', light: '#a5b4ca' },
  ];
  return <g className="scatter-grid" pointerEvents="none" aria-hidden="true" shapeRendering="crispEdges">
    {layers.map(layer => <g key={layer.name} data-grid-layer={layer.name} stroke={layer.stroke} strokeWidth={1}>
      {layer.xs.map(value => <line key={`x-${value}`} data-export-stroke={layer.light}
        x1={x(value)} x2={x(value)} y1={top} y2={top + height} />)}
      {layer.ys.map(value => <line key={`y-${value}`} data-export-stroke={layer.light}
        x1={left} x2={left + width} y1={y(value)} y2={y(value)} />)}
    </g>)}
  </g>;
}
