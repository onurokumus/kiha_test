import React, {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import { SearchableSelect } from './SearchableSelect';
import styles from './AxisControls.module.css';

export interface AxisControlsProps {
  columns: string[];
  xAxis: string;
  yAxis: string;
  onXAxisChange: (axis: string) => void;
  onYAxisChange: (axis: string) => void;
  mainZoom: [number, number, number, number] | null;
  onResetZoom: () => void;
  onReloadData: () => void;
  isLoading: boolean;
  clusteringAvailable: boolean;
  clusteringEnabled: boolean;
  onClusteringChange: (enabled: boolean) => void;
  datasheetZone: string;
  datasheetVisible: boolean;
  datasheetStatus: string;
  onDatasheetVisibilityChange: (visible: boolean) => void;
  horizontalErrorBars: boolean;
  verticalErrorBars: boolean;
  onHorizontalErrorBarsChange: (visible: boolean) => void;
  onVerticalErrorBarsChange: (visible: boolean) => void;
}

interface PopoverPosition extends CSSProperties {
  left: number;
  top?: number;
  bottom?: number;
  width: number;
  maxHeight: number;
}

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

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
  clusteringAvailable,
  clusteringEnabled,
  onClusteringChange,
  datasheetZone,
  datasheetVisible,
  datasheetStatus,
  onDatasheetVisibilityChange,
  horizontalErrorBars,
  verticalErrorBars,
  onHorizontalErrorBarsChange,
  onVerticalErrorBarsChange,
}) => {
  const [moreOpen, setMoreOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const moreButtonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const popoverId = useId();
  const [popoverPosition, setPopoverPosition] = useState<PopoverPosition>({
    left: 8,
    top: 8,
    width: 294,
    maxHeight: 460,
  });

  const updatePopoverPosition = useCallback(() => {
    const trigger = moreButtonRef.current;
    if (!trigger) return;

    const rect = trigger.getBoundingClientRect();
    const viewportPadding = 8;
    const gap = 6;
    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = document.documentElement.clientHeight;
    const width = Math.min(294, Math.max(180, viewportWidth - viewportPadding * 2));
    const left = Math.min(
      Math.max(viewportPadding, rect.right - width),
      Math.max(viewportPadding, viewportWidth - width - viewportPadding)
    );
    const roomBelow = viewportHeight - rect.bottom - gap - viewportPadding;
    const roomAbove = rect.top - gap - viewportPadding;
    const openAbove = roomBelow < 320 && roomAbove > roomBelow;
    const availableHeight = Math.max(0, openAbove ? roomAbove : roomBelow);
    const maxHeight = Math.min(460, availableHeight);

    setPopoverPosition(
      openAbove
        ? {
            left,
            bottom: viewportHeight - rect.top + gap,
            width,
            maxHeight,
          }
        : {
            left,
            top: rect.bottom + gap,
            width,
            maxHeight,
          }
    );
  }, []);

  useLayoutEffect(() => {
    if (moreOpen) updatePopoverPosition();
  }, [moreOpen, updatePopoverPosition]);

  useEffect(() => {
    if (!moreOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || popoverRef.current?.contains(target)) return;
      setMoreOpen(false);
    };

    const handleFocusIn = (event: FocusEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || popoverRef.current?.contains(target)) return;
      setMoreOpen(false);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setMoreOpen(false);
      moreButtonRef.current?.focus();
    };

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('focusin', handleFocusIn);
    document.addEventListener('keydown', handleKeyDown);
    window.addEventListener('resize', updatePopoverPosition);
    window.addEventListener('scroll', updatePopoverPosition, true);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('focusin', handleFocusIn);
      document.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('resize', updatePopoverPosition);
      window.removeEventListener('scroll', updatePopoverPosition, true);
    };
  }, [moreOpen, updatePopoverPosition]);

  const focusFirstPopoverControl = () => {
    popoverRef.current?.querySelector<HTMLElement>(focusableSelector)?.focus();
  };

  const handleMoreKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (!moreOpen || event.key !== 'Tab' || event.shiftKey) return;
    event.preventDefault();
    focusFirstPopoverControl();
  };

  const handlePopoverKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Tab') return;
    const controls = Array.from(
      popoverRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? []
    );
    if (controls.length === 0) return;

    const active = document.activeElement;
    if (event.shiftKey && active === controls[0]) {
      event.preventDefault();
      moreButtonRef.current?.focus();
      return;
    }
    if (!event.shiftKey && active === controls[controls.length - 1]) {
      event.preventDefault();
      const documentControls = Array.from(
        document.querySelectorAll<HTMLElement>(focusableSelector)
      ).filter(
        (element) => !popoverRef.current?.contains(element) && element.getClientRects().length > 0
      );
      const triggerIndex = moreButtonRef.current
        ? documentControls.indexOf(moreButtonRef.current)
        : -1;
      setMoreOpen(false);
      documentControls[triggerIndex + 1]?.focus();
    }
  };

  const reloadData = () => {
    onReloadData();
    setMoreOpen(false);
  };

  return (
    <div ref={rootRef} className={styles.root}>
      <div className={styles.commandBar}>
        <span className={styles.contextLabel}>TP mean scatter</span>

        <div className={styles.axisFields}>
          <div className={styles.axisField}>
            <span className={styles.axisLabel}>X</span>
            <SearchableSelect
              value={xAxis}
              onChange={onXAxisChange}
              options={columns.map((column) => ({ value: column, label: column }))}
              className={styles.axisSelect}
              appearance="embedded"
              ariaLabel="Scatter plot X axis"
              searchPlaceholder="Search X-axis signals..."
              optionNoun="signal"
            />
          </div>

          <div className={styles.axisField}>
            <span className={styles.axisLabel}>Y</span>
            <SearchableSelect
              value={yAxis}
              onChange={onYAxisChange}
              options={columns.map((column) => ({ value: column, label: column }))}
              className={styles.axisSelect}
              appearance="embedded"
              ariaLabel="Scatter plot Y axis"
              searchPlaceholder="Search Y-axis signals..."
              optionNoun="signal"
            />
          </div>
        </div>

        {mainZoom && (
          <button type="button" onClick={onResetZoom} className={styles.resetButton}>
            Reset zoom
          </button>
        )}

        <button
          ref={moreButtonRef}
          type="button"
          className={`${styles.moreButton} ${moreOpen ? styles.moreButtonOpen : ''}`}
          aria-expanded={moreOpen}
          aria-controls={popoverId}
          onClick={() => setMoreOpen((open) => !open)}
          onKeyDown={handleMoreKeyDown}
        >
          <span>More</span>
          <span className={styles.moreGlyph} aria-hidden="true">
            •••
          </span>
        </button>
      </div>

      {moreOpen &&
        createPortal(
          <div
            ref={popoverRef}
            id={popoverId}
            className={styles.popover}
            role="group"
            aria-label="Scatter plot options"
            style={popoverPosition}
            onKeyDown={handlePopoverKeyDown}
          >
            <button
              type="button"
              className={styles.reloadButton}
              onClick={reloadData}
              disabled={isLoading}
              title={isLoading ? 'Loading data…' : 'Reload data'}
            >
              <span className={styles.reloadIcon} aria-hidden="true">
                ↻
              </span>
              <span className={styles.actionCopy}>
                <strong>{isLoading ? 'Reloading data…' : 'Reload data'}</strong>
                <small>Refresh test-point statistics and traces</small>
              </span>
            </button>

            {datasheetZone && (
              <div className={styles.optionRow}>
                <span className={styles.actionCopy}>
                  <strong>Datasheet line</strong>
                  <small title={datasheetStatus}>{datasheetStatus}</small>
                </span>
                <button
                  type="button"
                  className={styles.switch}
                  role="switch"
                  aria-label={`Show datasheet line from ${datasheetZone}`}
                  aria-checked={datasheetVisible}
                  onClick={() => onDatasheetVisibilityChange(!datasheetVisible)}
                >
                  <span aria-hidden="true" />
                </button>
              </div>
            )}

            {clusteringAvailable && (
              <div className={styles.optionRow}>
                <span className={styles.actionCopy}>
                  <strong>Cluster overlaps</strong>
                  <small>Group nearby points to keep dense runs legible</small>
                </span>
                <button
                  type="button"
                  className={styles.switch}
                  role="switch"
                  aria-label="Cluster overlapping scatter points"
                  aria-checked={clusteringEnabled}
                  onClick={() => onClusteringChange(!clusteringEnabled)}
                >
                  <span aria-hidden="true" />
                </button>
              </div>
            )}

            <div className={styles.optionRow}>
              <span className={styles.actionCopy}>
                <strong>Horizontal min-max range</strong>
                <small>Left/right span for the X-axis variable</small>
              </span>
              <button
                type="button"
                className={styles.switch}
                role="switch"
                aria-label="Show horizontal X-axis minimum-to-maximum range bars"
                aria-checked={horizontalErrorBars}
                onClick={() => onHorizontalErrorBarsChange(!horizontalErrorBars)}
              >
                <span aria-hidden="true" />
              </button>
            </div>

            <div className={styles.optionRow}>
              <span className={styles.actionCopy}>
                <strong>Vertical min-max range</strong>
                <small>Down/up span for the Y-axis variable</small>
              </span>
              <button
                type="button"
                className={styles.switch}
                role="switch"
                aria-label="Show vertical Y-axis minimum-to-maximum range bars"
                aria-checked={verticalErrorBars}
                onClick={() => onVerticalErrorBarsChange(!verticalErrorBars)}
              >
                <span aria-hidden="true" />
              </button>
            </div>

            <div className={styles.gestureHelp}>
              <span className={styles.gestureTitle}>Scatter gestures</span>
              <ul>
                <li>
                  <kbd>Click</kbd>
                  <span>Select or deselect a point</span>
                </li>
                <li>
                  <kbd>Scroll</kbd>
                  <span>Zoom around the pointer</span>
                </li>
                <li>
                  <kbd>Drag</kbd>
                  <span>Pan the current view</span>
                </li>
              </ul>
            </div>
          </div>,
          document.body
        )}
    </div>
  );
};
