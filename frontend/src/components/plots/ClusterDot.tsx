import React, { useState, memo } from 'react';

interface ClusterDotProps {
  cx: number;
  cy: number;
  count: number;
  pointScale: number;
  onClick: (event: React.MouseEvent) => void;
}

const ClusterDotComponent: React.FC<ClusterDotProps> = ({
  cx,
  cy,
  count,
  pointScale,
  onClick,
}) => {
  const [isHovered, setIsHovered] = useState(false);

  // Keep the count legible even when individual markers are very small.
  const defaultRadius = Math.min(10 + Math.log10(count) * 5, 20);
  const fontSize = Math.min(14, Math.max(Math.min(10, defaultRadius * 0.85), defaultRadius * pointScale * 0.85));
  const labelRadius = String(count).length * fontSize * 0.31 + 3;
  const baseRadius = Math.max(Math.min(defaultRadius, labelRadius), defaultRadius * pointScale);
  const activeRadius = isHovered ? baseRadius + 2 : baseRadius;
  const strokeWidth = isHovered ? 2 : 1;
  const strokeColor = isHovered ? 'var(--accent, #263685)' : 'var(--text-secondary, #f7f8fa)';
  const fillColor = isHovered ? '#3a6fa0' : '#2e5c8a';

  return (
    <g data-scatter-hover-target="cluster">
      {/* Single flat circle - no effects, no gradients */}
      <circle
        cx={cx}
        cy={cy}
        r={activeRadius}
        data-export-radius={baseRadius}
        fill={fillColor}
        fillOpacity={0.9}
        stroke={strokeColor}
        strokeWidth={strokeWidth}
        style={{
          cursor: 'pointer',
          transition: 'all 0.15s cubic-bezier(0.4, 0, 0.2, 1)',
        }}
        onMouseDown={(e) => {
          e.stopPropagation();
        }}
        onClick={(e) => {
          e.stopPropagation();
          onClick(e);
        }}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      />

      {/* Simple count text - larger for readability */}
      <text
        x={cx}
        y={cy}
        dy="0.35em"
        textAnchor="middle"
        fill="#ffffff"
        fontSize={fontSize}
        fontWeight="600"
        fontFamily="Manrope, Segoe UI, sans-serif"
        style={{
          pointerEvents: 'none',
          userSelect: 'none',
          transition: 'all 0.15s cubic-bezier(0.4, 0, 0.2, 1)',
        }}
      >
        {count}
      </text>
    </g>
  );
};

// Custom comparison for performance
const arePropsEqual = (prev: ClusterDotProps, next: ClusterDotProps) => {
  return (
    prev.cx === next.cx &&
    prev.cy === next.cy &&
    prev.count === next.count &&
    prev.pointScale === next.pointScale &&
    // A cluster can retain its center/count while its member points change.
    prev.onClick === next.onClick
  );
};

export const ClusterDot = memo(ClusterDotComponent, arePropsEqual);
