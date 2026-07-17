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

  // Cluster dots carry no TP identity — show the count instead of an empty header
  if (data.isCluster) {
    return (
      <div
        style={{
          background: '#252526',
          border: '1px solid #3c3c3c',
          borderRadius: 4,
          padding: '8px 12px',
          fontSize: 12,
          color: '#e0e0e0',
        }}
      >
        <div style={{ color: '#569cd6', fontWeight: 'bold', fontSize: 14 }}>
          {data.clusterCount} overlapping points
        </div>
        <div style={{ color: '#a0a0a0', marginTop: 4 }}>Click to list them</div>
      </div>
    );
  }

  return (
    <div
      style={{
        background: '#252526',
        border: '1px solid #3c3c3c',
        borderRadius: 4,
        padding: '8px 12px',
        fontSize: 12,
        color: '#e0e0e0',
      }}
    >
      {/* Label with tail number, flight, test point, and maneuver */}
      <div
        style={{
          color: '#569cd6',
          fontWeight: 'bold',
          fontSize: 14,
          marginBottom: 8,
          paddingBottom: 6,
          borderBottom: '1px solid #3c3c3c',
        }}
      >
        {data.test ? `${data.test} · ` : ''}
        {data.name}
        {data.label ? ` — ${data.label}` : ''}
      </div>

      {/* Data values */}
      {payload.map((entry, index) => (
        <div
          key={index}
          style={{
            color: '#e0e0e0',
            marginTop: index > 0 ? 4 : 0,
          }}
        >
          <span style={{ color: '#a0a0a0' }}>{entry.name}: </span>
          {formatValue(Number(entry.value))}
        </div>
      ))}
    </div>
  );
};
