import React, { useState, useEffect, memo } from 'react';

interface AnimatedDotProps {
  cx: number;
  cy: number;
  payload: {
    color: string;
    isSelected: boolean;
  };
  onToggle: (event: React.MouseEvent) => void;
  isHighlighted?: boolean;
  onPointHover?: () => void;
}

const AnimatedDotComponent: React.FC<AnimatedDotProps> = ({
  cx,
  cy,
  payload,
  onToggle,
  isHighlighted = false,
  onPointHover
}) => {
  const [displayColor, setDisplayColor] = useState(payload.color);
  const [displayR, setDisplayR] = useState(payload.isSelected ? 8 : 6);
  const [displayStroke, setDisplayStroke] = useState(payload.isSelected ? '#fff' : 'transparent');
  const [isHovered, setIsHovered] = useState(false);

  useEffect(() => {
    const timeout = setTimeout(() => {
      setDisplayColor(payload.color);
      setDisplayR(payload.isSelected ? 8 : 6);
      setDisplayStroke(payload.isSelected ? '#fff' : 'transparent');
    }, 10);
    return () => clearTimeout(timeout);
  }, [payload.color, payload.isSelected]);

  // Calculate hover effects
  const baseRadius = payload.isSelected ? 8 : 6;
  const shouldShowEffects = isHovered || isHighlighted;
  const hoverRadius = shouldShowEffects ? baseRadius * 1.4 : baseRadius;
  const glowRadius = hoverRadius + 6;
  const pulseRadius = hoverRadius + 3;

  return (
    <g>
      {/* Outer glow ring - only visible on hover or highlight */}
      {shouldShowEffects && (
        <>
          <circle
            cx={cx}
            cy={cy}
            r={glowRadius}
            fill="none"
            stroke={displayColor}
            strokeWidth={1}
            opacity={0.3}
            style={{
              transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
            }}
          >
            <animate
              attributeName="r"
              values={`${glowRadius};${glowRadius + 2};${glowRadius}`}
              dur="1.5s"
              repeatCount="indefinite"
            />
            <animate
              attributeName="opacity"
              values="0.3;0.15;0.3"
              dur="1.5s"
              repeatCount="indefinite"
            />
          </circle>
          <circle
            cx={cx}
            cy={cy}
            r={pulseRadius}
            fill="none"
            stroke={displayColor}
            strokeWidth={1.5}
            opacity={0.5}
            style={{
              transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
            }}
          />
        </>
      )}

      {/* Main dot */}
      <circle
        cx={cx}
        cy={cy}
        r={displayR}
        fill={displayColor}
        stroke={displayStroke}
        strokeWidth={2}
        style={{
          cursor: 'pointer',
          transition: 'fill 0.3s ease, stroke 0.3s ease',
          filter: shouldShowEffects ? 'brightness(1.3) drop-shadow(0 0 4px currentColor)' : 'none',
        }}
        onMouseDown={(e) => {
          e.stopPropagation();
        }}
        onClick={(e) => {
          e.stopPropagation();
          onToggle(e);
        }}
        onMouseEnter={() => {
          setIsHovered(true);
          onPointHover?.();
        }}
        onMouseLeave={() => setIsHovered(false)}
      >
        <animate attributeName="r" to={hoverRadius.toString()} dur="0.2s" fill="freeze" />
      </circle>

      {/* Inner highlight - only visible on hover or highlight */}
      {shouldShowEffects && (
        <circle
          cx={cx - hoverRadius * 0.25}
          cy={cy - hoverRadius * 0.25}
          r={hoverRadius * 0.35}
          fill="#ffffff"
          opacity={0.4}
          style={{
            pointerEvents: 'none',
            transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
          }}
        />
      )}
    </g>
  );
};

// Custom comparison to prevent unnecessary re-renders with large datasets
const arePropsEqual = (prev: AnimatedDotProps, next: AnimatedDotProps) => {
  return (
    prev.cx === next.cx &&
    prev.cy === next.cy &&
    prev.payload.color === next.payload.color &&
    prev.payload.isSelected === next.payload.isSelected &&
    prev.isHighlighted === next.isHighlighted
  );
};

export const AnimatedDot = memo(AnimatedDotComponent, arePropsEqual);
