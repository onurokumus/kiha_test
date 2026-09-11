import { useId } from 'react';
import { ScatterDataPoint } from '../../types';

interface ChartAxis {
  scale: (value: number) => number;
}

interface ScatterRangeBarsProps {
  points: readonly ScatterDataPoint[];
  horizontal: boolean;
  vertical: boolean;
  // Recharts Customized supplies the current chart state on every redraw.
  xAxisMap?: Record<string, ChartAxis>;
  yAxisMap?: Record<string, ChartAxis>;
  offset?: { left: number; top: number; width: number; height: number };
}

const CAP = 6;

/** Draw the existing mean-to-min/max distances with the chart's actual scales.
 * Recharts 2.15 ErrorBar keys bars by their coordinates: identical ranges get
 * duplicate React keys and can leave old lines behind after zoom/pan. Stable
 * test-point IDs keep overlapping ranges independent through every redraw. */
export function ScatterRangeBars({
  points,
  horizontal,
  vertical,
  xAxisMap,
  yAxisMap,
  offset,
}: ScatterRangeBarsProps) {
  const clipId = `scatter-ranges-${useId().replace(/:/g, '')}`;
  const xScale = xAxisMap?.['0']?.scale;
  const yScale = yAxisMap?.['0']?.scale;
  if (!xScale || !yScale || !offset) return null;

  return (
    <g className="scatter-range-series" aria-hidden="true" pointerEvents="none">
      <defs>
        <clipPath id={clipId}>
          <rect x={offset.left} y={offset.top} width={offset.width} height={offset.height} />
        </clipPath>
      </defs>
      <g clipPath={`url(#${clipId})`} strokeWidth={1.25} strokeOpacity={0.78}>
        {points.map((point) => {
          const cx = xScale(point.x);
          const cy = yScale(point.y);
          if (!Number.isFinite(cx) || !Number.isFinite(cy)) return null;
          const xLow = point.xError ? xScale(point.x - point.xError[0]) : NaN;
          const xHigh = point.xError ? xScale(point.x + point.xError[1]) : NaN;
          const yLow = point.yError ? yScale(point.y - point.yError[0]) : NaN;
          const yHigh = point.yError ? yScale(point.y + point.yError[1]) : NaN;

          return (
            <g key={point.id} data-range-point-id={point.id}>
              {horizontal && Number.isFinite(xLow) && Number.isFinite(xHigh) && (
                <g data-range-axis="x" stroke="#8ec8ec">
                  <line x1={xLow} y1={cy - CAP} x2={xLow} y2={cy + CAP} />
                  <line x1={xLow} y1={cy} x2={xHigh} y2={cy} />
                  <line x1={xHigh} y1={cy - CAP} x2={xHigh} y2={cy + CAP} />
                </g>
              )}
              {vertical && Number.isFinite(yLow) && Number.isFinite(yHigh) && (
                <g data-range-axis="y" stroke="#d7ba7d">
                  <line x1={cx - CAP} y1={yLow} x2={cx + CAP} y2={yLow} />
                  <line x1={cx} y1={yLow} x2={cx} y2={yHigh} />
                  <line x1={cx - CAP} y1={yHigh} x2={cx + CAP} y2={yHigh} />
                </g>
              )}
            </g>
          );
        })}
      </g>
    </g>
  );
}
