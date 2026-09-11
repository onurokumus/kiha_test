import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { TooltipProps } from 'recharts';
import { formatValue } from '../../utils/formatters';
import './CustomScatterTooltip.css';

type ScatterTooltipProps = TooltipProps<number, string> & {
  chartRef: React.RefObject<HTMLDivElement>;
};

export const CustomScatterTooltip: React.FC<ScatterTooltipProps> = ({
  active,
  payload,
  coordinate,
  chartRef,
}) => {
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const [dismissedAt, setDismissedAt] = useState<string | null>(null);
  const data = payload?.[0]?.payload;
  const x = coordinate?.x;
  const y = coordinate?.y;
  const hoverKey = `${data?.id ?? data?.name ?? ''}:${x}:${y}`;

  useLayoutEffect(() => {
    const chart = chartRef.current;
    const tooltip = tooltipRef.current;
    if (!active || !chart || !tooltip || x === undefined || y === undefined) return;

    const updatePosition = () => {
      const chartBounds = chart.getBoundingClientRect();
      const tooltipBounds = tooltip.getBoundingClientRect();
      // Recharts coordinates are chart-local CSS pixels. Account for a scaled
      // desktop workspace before positioning the body-level floating card.
      const anchorX = chartBounds.left + x * (chartBounds.width / (chart.offsetWidth || 1));
      const anchorY = chartBounds.top + y * (chartBounds.height / (chart.offsetHeight || 1));
      const edge = 8;
      const gap = 12;
      const left = Math.max(
        edge,
        Math.min(
          anchorX + gap + tooltipBounds.width <= window.innerWidth - edge
            ? anchorX + gap
            : anchorX - gap - tooltipBounds.width,
          window.innerWidth - tooltipBounds.width - edge
        )
      );
      const top = Math.max(
        edge,
        Math.min(anchorY + gap, window.innerHeight - tooltipBounds.height - edge)
      );
      setPosition((previous) =>
        previous?.left === left && previous.top === top ? previous : { left, top }
      );
    };

    updatePosition();
    const observer = new ResizeObserver(updatePosition);
    observer.observe(chart);
    observer.observe(tooltip);
    window.addEventListener('resize', updatePosition);
    document.addEventListener('scroll', updatePosition, true);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', updatePosition);
      document.removeEventListener('scroll', updatePosition, true);
    };
  }, [active, chartRef, payload, x, y]);

  // A content portal escapes Recharts' wrapper, including its Escape-key
  // visibility rule, so preserve that dismissal behavior on the floating card.
  useEffect(() => {
    if (!active) {
      setDismissedAt(null);
      return;
    }
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setDismissedAt(hoverKey);
    };
    document.addEventListener('keydown', dismiss);
    return () => document.removeEventListener('keydown', dismiss);
  }, [active, hoverKey]);

  if (!active || !data || !payload?.length || dismissedAt === hoverKey) return null;

  let content: React.ReactNode;
  if (data.isDatasheet) {
    content = (
      <>
        <div className="chart-tooltip__header">
          Datasheet · {data.zone}
          <div className="chart-tooltip__meta">Point ID {formatValue(Number(data.pointId))}</div>
        </div>
        {payload.map((entry, index) => (
          <div key={index} className="chart-tooltip__row">
            <span className="chart-tooltip__label">{entry.name}</span>
            <span className="chart-tooltip__value">{formatValue(Number(entry.value))}</span>
          </div>
        ))}
      </>
    );
  } else if (data.isCluster) {
    // Cluster dots carry no TP identity — show the count instead of an empty header.
    content = (
      <>
        <div className="chart-tooltip__header">{data.clusterCount} overlapping points</div>
        <div className="chart-tooltip__hint">Click to inspect this group.</div>
      </>
    );
  } else {
    content = (
      <>
        <div className="chart-tooltip__header">
          {data.test ? `${data.test} · ` : ''}
          {data.name}
          {data.label ? ` — ${data.label}` : ''}
        </div>

        {payload.map((entry, index) => (
          <div key={index} className="chart-tooltip__row">
            <span className="chart-tooltip__label">{entry.name}</span>
            <span className="chart-tooltip__value">{formatValue(Number(entry.value))}</span>
          </div>
        ))}
      </>
    );
  }

  return createPortal(
    <div
      ref={tooltipRef}
      role="tooltip"
      className={`chart-tooltip scatter-tooltip${data.isDatasheet ? ' chart-tooltip--datasheet' : ''}`}
      style={{
        left: position?.left ?? 0,
        top: position?.top ?? 0,
        visibility: position ? 'visible' : 'hidden',
      }}
    >
      {content}
    </div>,
    document.body
  );
};
