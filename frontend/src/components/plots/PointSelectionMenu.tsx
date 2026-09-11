import React, { useState, useMemo, useRef, useEffect, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { ScatterDataPoint } from '../../types';
import styles from './PointSelectionMenu.module.css';

interface PointSelectionMenuProps {
  points: ScatterDataPoint[];
  position: { x: number; y: number };
  onSelect: (point: ScatterDataPoint) => void;
  onClose: (restoreFocus?: boolean) => void;
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

  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;

    const updatePosition = () => {
      const rect = menu.getBoundingClientRect();
      const edge = 10;
      const x = Math.max(edge, Math.min(position.x, window.innerWidth - rect.width - edge));
      const y = Math.max(edge, Math.min(position.y, window.innerHeight - rect.height - edge));
      setAdjustedPosition((previous) => previous.x === x && previous.y === y ? previous : { x, y });
    };
    updatePosition();
    const observer = new ResizeObserver(updatePosition);
    observer.observe(menu);
    window.addEventListener('resize', updatePosition);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', updatePosition);
    };
  }, [position, points, searchText]);

  // Multi-select keeps the menu open. Closing from outside must leave that
  // event and its focus intact, so the next plot/control works on the first click.
  useEffect(() => {
    const closeIfOutside = (event: Event) => {
      const target = event.target as Node | null;
      if (target && menuRef.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener('mousedown', closeIfOutside);
    document.addEventListener('wheel', closeIfOutside);
    document.addEventListener('focusin', closeIfOutside);
    return () => {
      document.removeEventListener('mousedown', closeIfOutside);
      document.removeEventListener('wheel', closeIfOutside);
      document.removeEventListener('focusin', closeIfOutside);
    };
  }, [onClose]);

  const filteredPoints = useMemo(() => {
    const search = searchText.trim().toLowerCase();
    if (!search) return points;
    return points.filter((point) =>
      [point.name, point.label, point.test].some((value) => value.toLowerCase().includes(search))
    );
  }, [points, searchText]);

  // The scatter panel establishes an isolated, clipped stacking context.
  // A body portal keeps this menu above its resize rail and neighboring plots.
  return createPortal(
    <div
      ref={menuRef}
      data-menu-container
      role="dialog"
      aria-label="Overlapping test points"
      className={styles.menu}
      style={{ left: adjustedPosition.x, top: adjustedPosition.y }}
      onMouseDown={(event) => event.stopPropagation()}
      onWheel={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          onClose(true);
        }
      }}
    >
      <div className={styles.header}>
        <span>Select Point ({filteredPoints.length} / {points.length})</span>
        <button type="button" className={styles.close} onClick={() => onClose(true)}
          aria-label="Close point selection" title="Close point selection">✕</button>
      </div>

      <div className={styles.search}>
        <input type="text" aria-label="Search overlapping test points"
          placeholder="Search by test, TP, label..." value={searchText}
          onChange={(event) => setSearchText(event.target.value)} autoFocus />
      </div>

      <div className={styles.points}>
        {filteredPoints.length === 0 ? (
          <div className={styles.empty}>No matching points</div>
        ) : filteredPoints.map((point) => (
          <button key={point.id} type="button" className={styles.point} aria-pressed={point.isSelected}
            onClick={(event) => { event.stopPropagation(); onSelect(point); }}
            onMouseEnter={() => onHover?.(point.id)} onMouseLeave={() => onHover?.(null)}
            onFocus={() => onHover?.(point.id)} onBlur={() => onHover?.(null)}>
            <span className={styles.swatch} aria-hidden="true"
              style={{ background: point.color, borderColor: point.isSelected ? '#fff' : 'transparent' }} />
            <span className={styles.description}>
              <span className={styles.name}>{point.name}</span>
              <span className={styles.details}>{point.test}{point.label ? ` — ${point.label}` : ''}</span>
            </span>
            {point.isSelected && <span className={styles.selected}>✓ Selected</span>}
          </button>
        ))}
      </div>
    </div>,
    document.body
  );
};
