import React, { useState, memo } from 'react';

interface ClusterDotProps {
  cx: number;
  cy: number;
  count: number;
  onClick: (event: React.MouseEvent) => void;
}

const ClusterDotComponent: React.FC<ClusterDotProps> = ({
  cx,
  cy,
  count,
  onClick,
}) => {
  const [isHovered, setIsHovered] = useState(false);

  // Flat, minimal sizing
  const baseRadius = Math.min(10 + Math.log10(count) * 5, 20);
  const activeRadius = isHovered ? baseRadius + 2 : baseRadius;
  const strokeWidth = isHovered ? 2 : 1;
  const strokeColor = isHovered ? '#263685' : '#f7f8fa';
  const fillColor = isHovered ? '#3a6fa0' : '#2e5c8a';

  return (
    <g data-scatter-hover-target="cluster">
      {/* Single flat circle - no effects, no gradients */}
      <circle
        cx={cx}
        cy={cy}
        r={activeRadius}
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
        fontSize={Math.min(baseRadius * 0.85, 14)}
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
    prev.count === next.count
  );
};

export const ClusterDot = memo(ClusterDotComponent, arePropsEqual);
