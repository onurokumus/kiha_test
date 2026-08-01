import React from 'react';
import { TooltipProps } from 'recharts';
import { formatValue } from '../../utils/formatters';

export const CustomScatterTooltip: React.FC<TooltipProps<number, string>> = ({
  active,
  payload,
}) => {
  if (!active || !payload || payload.length === 0) {
    return null;
  }

  const data = payload[0].payload;

  if (data.isDatasheet) {
    return (
      <div className="chart-tooltip chart-tooltip--datasheet">
        <div className="chart-tooltip__header">
          Datasheet · {data.zone}
          <div className="chart-tooltip__meta">
            Point ID {formatValue(Number(data.pointId))}
          </div>
        </div>
        {payload.map((entry, index) => (
          <div key={index} className="chart-tooltip__row">
            <span className="chart-tooltip__label">{entry.name}</span>
            <span className="chart-tooltip__value">{formatValue(Number(entry.value))}</span>
          </div>
        ))}
      </div>
    );
  }

  // Cluster dots carry no TP identity — show the count instead of an empty header.
  if (data.isCluster) {
    return (
      <div className="chart-tooltip">
        <div className="chart-tooltip__header">
          {data.clusterCount} overlapping points
        </div>
        <div className="chart-tooltip__hint">Click to inspect this group.</div>
      </div>
    );
  }

  return (
    <div className="chart-tooltip">
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
    </div>
  );
};
