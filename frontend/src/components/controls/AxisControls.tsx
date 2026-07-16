import React from 'react';
import { SelectStyle } from '../../constants/styles';

interface AxisControlsProps {
  columns: string[];
  xAxis: string;
  yAxis: string;
  onXAxisChange: (axis: string) => void;
  onYAxisChange: (axis: string) => void;
  mainZoom: [number, number, number, number] | null;
  onResetZoom: () => void;
  onReloadData: () => void;
  isLoading: boolean;
}

export const AxisControls: React.FC<AxisControlsProps> = ({
  columns,
  xAxis,
  yAxis,
  onXAxisChange,
  onYAxisChange,
  mainZoom,
  onResetZoom,
  onReloadData,
  isLoading,
}) => {
  return (
    <div
      style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}
    >
      <span style={{ color: '#a0a0a0', fontSize: 12 }}>
        TP mean scatter — X:
      </span>
      <select value={xAxis} onChange={(e) => onXAxisChange(e.target.value)} style={SelectStyle}>
        {columns.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
      <span style={{ color: '#a0a0a0', fontSize: 12 }}>Y:</span>
      <select value={yAxis} onChange={(e) => onYAxisChange(e.target.value)} style={SelectStyle}>
        {columns.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
      {mainZoom && (
        <button
          onClick={onResetZoom}
          style={{
            background: '#3c3c3c',
            color: '#e0e0e0',
            border: 'none',
            borderRadius: 3,
            padding: '4px 8px',
            cursor: 'pointer',
            fontSize: 11,
          }}
        >
          Reset Zoom
        </button>
      )}
      <div style={{ flex: 1 }} />
      <button
        onClick={onReloadData}
        disabled={isLoading}
        title={isLoading ? 'Loading...' : 'Reload Data'}
        style={{
          background: isLoading ? '#2d2d2d' : '#1e5a2e',
          color: isLoading ? '#666' : '#e0e0e0',
          border: isLoading ? '1px solid #3c3c3c' : '1px solid #2d8a4a',
          borderRadius: 3,
          padding: '4px 8px',
          cursor: isLoading ? 'not-allowed' : 'pointer',
          fontSize: 14,
          width: 28,
          height: 24,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        ↻
      </button>
    </div>
  );
};
