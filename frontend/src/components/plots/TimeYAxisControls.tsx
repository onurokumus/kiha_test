import { useEffect, useId, useImperativeHandle, useLayoutEffect, useRef, useState, type Ref } from 'react';
import { createPortal } from 'react-dom';
import { AxisRange, validAxisRange } from '../../utils/timePlotRanges';
import { NumericField } from '../controls/NumericField';
import { numericError, parseFiniteNumber } from '../../utils/numericField';
import styles from './TimeYAxisControls.module.css';

interface Props {
  actionsRef?: Ref<TimeYAxisActions>;
  hideTrigger?: boolean;
  label: string;
  range: AxisRange | null;
  disabled: boolean;
  getRange: () => AxisRange | null;
  onChange: (range: AxisRange | null) => void;
}
export interface TimeYAxisActions { open: () => void }

/** A small non-modal editor outside grid clipping. All input paths use the
 * same controlled Y range; a reset never changes the shared time interval. */
export function TimeYAxisControls({ label, range, disabled, getRange, onChange, actionsRef, hideTrigger = false }: Props) {
  const id = useId();
  const button = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const panel = useRef<HTMLFormElement>(null);
  const [open, setOpen] = useState(false);
  const [min, setMin] = useState('');
  const [max, setMax] = useState('');
  const minimum = parseFiniteNumber(min);
  const maximum = parseFiniteNumber(max);
  const rangeError = minimum !== null && maximum !== null && !validAxisRange([minimum, maximum])
    ? 'Keep minimum below maximum with a wider range.' : '';
  const error = numericError(min) || numericError(max) || rangeError;
  const [position, setPosition] = useState({ left: 0, top: 0 });
  const close = () => {
    setOpen(false);
    (opener.current?.isConnected ? opener.current : button.current)?.focus({ preventScroll: true });
  };
  const openEditor = () => {
    if (disabled) return;
    if (open) {
      panel.current?.querySelector('input')?.focus({ preventScroll: true });
      return;
    }
    // The action menu focuses its persistent trigger before handing off here.
    // Keep that element for positioning and focus return when our own trigger is hidden.
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : button.current;
    const current = getRange();
    setMin(current ? String(current[0]) : '');
    setMax(current ? String(current[1]) : '');
    setOpen(true);
  };
  useImperativeHandle(actionsRef, () => ({ open: openEditor }));

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchorElement = opener.current?.isConnected ? opener.current : button.current;
      if (!anchorElement || !panel.current) return;
      const anchor = anchorElement.getBoundingClientRect();
      const box = panel.current.getBoundingClientRect();
      setPosition({
        left: Math.max(8, Math.min(anchor.right - box.width, window.innerWidth - box.width - 8)),
        top: Math.max(8, Math.min(anchor.bottom + 6, window.innerHeight - box.height - 8)),
      });
    };
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
    };
  }, [open, error]);

  useEffect(() => {
    if (!open) return;
    panel.current?.querySelector('input')?.focus({ preventScroll: true });
    const outside = (event: MouseEvent) => {
      if (
        !panel.current?.contains(event.target as Node) &&
        !opener.current?.contains(event.target as Node)
      )
        setOpen(false);
    };
    // The menu may reopen through the same anchor as this editor. Dismiss the
    // editor without taking focus away from the menu's selected item.
    const menuOpened = () => setOpen(false);
    document.addEventListener('mousedown', outside);
    window.addEventListener('kiha:plot-menu-open', menuOpened);
    return () => {
      document.removeEventListener('mousedown', outside);
      window.removeEventListener('kiha:plot-menu-open', menuOpened);
    };
  }, [open]);

  return (
    <>
      {!hideTrigger && <button
        ref={button}
        type="button"
        className={styles.trigger}
        aria-label={`Y axis for ${label}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        data-manual={range !== null}
        disabled={disabled}
        title={
          range
            ? `Y: ${range[0]} to ${range[1]} — edit or reset`
            : 'Y axis: automatically fits the visible time interval — edit for a fixed range'
        }
        onClick={() => {
          if (open) {
            close();
            return;
          }
          openEditor();
        }}
      >
        Y axis{range ? ' •' : ''}
      </button>}
      {open &&
        createPortal(
          <form
            ref={panel}
            id={id}
            role="dialog"
            aria-modal="false"
            aria-label={`Y axis for ${label}`}
            aria-describedby={`${id}-hint`}
            className={styles.panel}
            style={position}
            noValidate
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                close();
              }
              if (event.key === 'Tab') {
                const controls = [
                  ...event.currentTarget.querySelectorAll<HTMLElement>('input, button'),
                ];
                const first = controls[0],
                  last = controls[controls.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                  event.preventDefault();
                  last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                  event.preventDefault();
                  first.focus();
                }
              }
            }}
            onSubmit={(event) => {
              event.preventDefault();
              if (minimum === null || maximum === null || error) return;
              const next: AxisRange = [minimum, maximum];
              onChange(next);
              close();
            }}
          >
            <div className={styles.heading}>
              <strong>Y axis · {label}</strong>
              <button type="button" aria-label="Close Y axis controls" onClick={close}>
                ×
              </button>
            </div>
            <div className={styles.fields}>
              <label>
                Minimum
                <NumericField
                  aria-label="Minimum"
                  value={min}
                  onChange={(event) => setMin(event.target.value)}
                  error={rangeError}
                />
              </label>
              <label>
                Maximum
                <NumericField
                  aria-label="Maximum"
                  value={max}
                  onChange={(event) => setMax(event.target.value)}
                  error={rangeError}
                />
              </label>
            </div>
            <div className={styles.actions}>
              <button type="submit" disabled={disabled || !!error}>
                Apply range
              </button>
              <button
                type="button"
                onClick={() => {
                  onChange(null);
                  close();
                }}
              >
                Reset Y
              </button>
            </div>
            <details className={styles.details}>
            <summary>Details</summary>
            <p id={`${id}-hint`}>
              Automatic Y fits the visible traces as you zoom or pan time. Apply a range to keep
              fixed bounds. Left-drag horizontally for X, vertically for Y, or diagonally for both.
              Alt + wheel / Alt + drag adjusts only Y. Reset Y restores automatic fitting and keeps time zoom.
            </p>
            </details>
          </form>,
          document.body
        )}
    </>
  );
}
