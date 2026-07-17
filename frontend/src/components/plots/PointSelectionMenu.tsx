import React, { useState, useMemo, useRef, useEffect } from 'react';
import { ScatterDataPoint } from '../../types';

interface PointSelectionMenuProps {
  points: ScatterDataPoint[];
  position: { x: number; y: number };
  onSelect: (point: ScatterDataPoint) => void;
  onClose: () => void;
  onHover?: (pointId: string | null) => void;
}

export const PointSelectionMenu: React.FC<PointSelectionMenuProps> = ({
  points,
  position,
  onSelect,
  onClose,
  onHover,
}) => {
  const [searchText, setSearchText] = useState('');
  const menuRef = useRef<HTMLDivElement>(null);
  const [adjustedPosition, setAdjustedPosition] = useState(position);

  // Adjust menu position to keep it within viewport bounds
  useEffect(() => {
    if (!menuRef.current) return;

    const menu = menuRef.current;
    const rect = menu.getBoundingClientRect();
    const padding = 10; // Padding from viewport edges

    let newX = position.x;
    let newY = position.y;

    // Check right edge
    if (position.x + rect.width > window.innerWidth - padding) {
      newX = window.innerWidth - rect.width - padding;
    }

    // Check left edge
    if (newX < padding) {
      newX = padding;
    }

    // Check bottom edge
    if (position.y + rect.height > window.innerHeight - padding) {
      newY = window.innerHeight - rect.height - padding;
    }

    // Check top edge
    if (newY < padding) {
      newY = padding;
    }

    setAdjustedPosition({ x: newX, y: newY });
  }, [position]);

  // The menu stays open across selections (multi-select from a cluster), so
  // outside-close must NOT consume the outside event. A full-screen invisible
  // backdrop used to do this and ate the first click on anything else in the
  // app after a menu selection (grid expand/minimize buttons appeared dead).
  // Document-level listeners close the menu while the control under the
  // cursor still receives its click. Wheel over the menu never reaches
  // document (the panel stops propagation for its own scrolling).
  useEffect(() => {
    const closeIfOutside = (e: Event) => {
      const t = e.target as Node | null;
      if (t && menuRef.current && menuRef.current.contains(t)) return;
      onClose();
    };
    document.addEventListener('mousedown', closeIfOutside);
    document.addEventListener('wheel', closeIfOutside);
    return () => {
      document.removeEventListener('mousedown', closeIfOutside);
      document.removeEventListener('wheel', closeIfOutside);
    };
  }, [onClose]);

  // Filter points based on search text
  const filteredPoints = useMemo(() => {
    if (!searchText.trim()) {
      return points;
    }

    const search = searchText.toLowerCase();
    return points.filter((point) => {
      const nameMatch = point.name.toLowerCase().includes(search);
      const labelMatch = point.label.toLowerCase().includes(search);
      const testMatch = point.test.toLowerCase().includes(search);
      return nameMatch || labelMatch || testMatch;
    });
  }, [points, searchText]);

  return (
    <>
      {/* Menu */}
      <div
        ref={menuRef}
        data-menu-container
        style={{
          position: 'fixed',
          left: adjustedPosition.x,
          top: adjustedPosition.y,
          background: '#2d2d2d',
          border: '1px solid #569cd6',
          borderRadius: 4,
          boxShadow: '0 4px 12px rgba(0, 0, 0, 0.4)',
          zIndex: 1000,
          minWidth: 200,
          maxHeight: 350,
          display: 'flex',
          flexDirection: 'column',
          animation: 'fadeIn 0.15s ease-out',
        }}
        onWheel={(e) => {
          // Prevent wheel events from propagating to parent (prevents zoom on main plot)
          e.stopPropagation();
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '8px 12px',
            borderBottom: '1px solid #3c3c3c',
            fontSize: 11,
            color: '#a0a0a0',
            fontWeight: 600,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <span>Select Point ({filteredPoints.length} / {points.length})</span>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#909090',
              cursor: 'pointer',
              fontSize: 14,
              padding: '0 4px',
              lineHeight: 1,
            }}
            title="Close"
          >
            ✕
          </button>
        </div>

        {/* Search Input */}
        <div style={{ padding: '8px 12px', borderBottom: '1px solid #3c3c3c' }}>
          <input
            type="text"
            placeholder="Search by test, TP, label..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onWheel={(e) => e.stopPropagation()}
            style={{
              width: '100%',
              background: '#1e1e1e',
              color: '#e0e0e0',
              border: '1px solid #3c3c3c',
              borderRadius: 3,
              padding: '6px 8px',
              fontSize: 11,
              fontFamily: 'Segoe UI, sans-serif',
              outline: 'none',
            }}
            autoFocus
          />
        </div>

        {/* Points List */}
        <div
          style={{
            overflowY: 'auto',
            maxHeight: 250,
          }}
          onWheel={(e) => {
            // Prevent wheel events from propagating to parent
            e.stopPropagation();
          }}
        >
          {filteredPoints.length === 0 ? (
            <div
              style={{
                padding: '16px 12px',
                textAlign: 'center',
                color: '#909090',
                fontSize: 11,
              }}
            >
              No matching points
            </div>
          ) : (
            filteredPoints.map((point, idx) => (
              <div
                key={point.id}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(point);
                }}
                style={{
                  padding: '8px 12px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  borderBottom: idx < filteredPoints.length - 1 ? '1px solid #3c3c3c' : 'none',
                  transition: 'background 0.15s ease',
                  fontSize: 12,
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = '#3c3c3c';
                  onHover?.(point.id);
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent';
                  onHover?.(null);
                }}
              >
                <div
                  style={{
                    width: 12,
                    height: 12,
                    borderRadius: '50%',
                    background: point.color,
                    border: point.isSelected ? '2px solid #fff' : 'none',
                    flexShrink: 0,
                  }}
                />
                <div style={{ flex: 1 }}>
                  <div style={{ color: '#e0e0e0' }}>{point.name}</div>
                  <div style={{ color: '#a0a0a0', fontSize: 10, marginTop: 2 }}>
                    {point.test}
                    {point.label ? ` — ${point.label}` : ''}
                  </div>
                </div>
                {point.isSelected && (
                  <div style={{ color: '#569cd6', fontSize: 10 }}>✓ Selected</div>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      <style>
        {`
          @keyframes fadeIn {
            from {
              opacity: 0;
              transform: translateY(-4px);
            }
            to {
              opacity: 1;
              transform: translateY(0);
            }
          }

          /* Custom scrollbar styling */
          div::-webkit-scrollbar {
            width: 8px;
          }

          div::-webkit-scrollbar-track {
            background: #1e1e1e;
            border-radius: 4px;
          }

          div::-webkit-scrollbar-thumb {
            background: #4a6b8a;
            border-radius: 4px;
          }

          div::-webkit-scrollbar-thumb:hover {
            background: #569cd6;
          }
        `}
      </style>
    </>
  );
};
