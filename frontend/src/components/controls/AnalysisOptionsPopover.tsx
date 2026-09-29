import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import styles from './SelectedPointsPanel.module.css';

interface Props {
  contextKey: string;
  available: boolean;
  summary: string | undefined;
  children: ReactNode;
}

/** Floating settings keep the analysis deck and its trigger stationary. */
export function AnalysisOptionsPopover({ contextKey, available, summary, children }: Props) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const pointerInside = useRef(false);
  const focusInside = useRef(false);
  const [opening, setOpening] = useState<string | null>(null);
  const open = available && opening === contextKey;

  useEffect(() => { setOpening(null); }, [contextKey]);

  useLayoutEffect(() => {
    if (!open || !trigger.current || !panel.current) return;
    const anchor = trigger.current;
    const popup = panel.current;
    const position = () => {
      const rect = anchor.getBoundingClientRect();
      const width = Math.min(520, document.documentElement.clientWidth - 24);
      popup.style.width = `${width}px`;
      popup.style.left = `${Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12))}px`;
      const roomBelow = window.innerHeight - rect.bottom - 20;
      const roomAbove = rect.top - 20;
      const above = roomBelow < Math.min(popup.scrollHeight + 2, 180) && roomAbove > roomBelow;
      popup.style.maxHeight = `${Math.max(0, above ? roomAbove : roomBelow)}px`;
      popup.style.top = `${Math.max(12, above ? rect.top - popup.offsetHeight - 8 : rect.bottom + 8)}px`;
    };
    position();
    const observer = new ResizeObserver(position);
    observer.observe(anchor);
    observer.observe(popup);
    const toolsGroup = anchor.closest('[data-analysis-tools]');
    if (toolsGroup) observer.observe(toolsGroup);
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    // React capture follows nested portals too (the searchable RPM picker).
    // Reset at document capture, then inspect after React dispatch has run.
    const resetPointer = () => { pointerInside.current = false; };
    const resetFocus = () => { focusInside.current = false; };
    const outsidePointer = () => { if (!pointerInside.current) setOpening(null); };
    const outsideFocus = () => { if (!focusInside.current) setOpening(null); };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      // Let the nested picker consume the first Escape, regardless of listener order.
      if (panel.current?.querySelector('[aria-haspopup="listbox"][aria-expanded="true"]')) return;
      event.preventDefault();
      setOpening(null);
      trigger.current?.focus({ preventScroll: true });
    };
    document.addEventListener('pointerdown', resetPointer, true);
    document.addEventListener('focusin', resetFocus, true);
    document.addEventListener('pointerdown', outsidePointer);
    document.addEventListener('focusin', outsideFocus);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', resetPointer, true);
      document.removeEventListener('focusin', resetFocus, true);
      document.removeEventListener('pointerdown', outsidePointer);
      document.removeEventListener('focusin', outsideFocus);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  return <div className={styles.options}
    onPointerDownCapture={() => { pointerInside.current = true; }}
    onFocusCapture={() => { focusInside.current = true; }}>
    <button ref={trigger} type="button" className={styles.optionsButton}
      disabled={!available} aria-label="Options" aria-expanded={open} aria-controls={open ? id : undefined}
      data-tooltip={available ? (open ? undefined : 'Options') : 'No additional options for this view'}
      onClick={() => setOpening(open ? null : contextKey)}
      onKeyDown={event => {
        if (event.key === 'Tab' && !event.shiftKey && open) {
          const first = panel.current?.querySelector<HTMLElement>('button:not(:disabled), select, input');
          if (first) { event.preventDefault(); first.focus(); }
        }
      }}>
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 4h12M2 12h12M6 2v4M10 10v4" /></svg>
    </button>
    {open && createPortal(<div ref={panel} id={id} role="region" aria-label="Analysis options"
      className={styles.optionsPanel} onKeyDown={event => {
        if (event.key !== 'Tab') return;
        const fields = panel.current?.querySelectorAll('button:not(:disabled), select, input');
        if (!fields?.length) return;
        if (event.shiftKey && event.target === fields[0]) {
          event.preventDefault();
          trigger.current?.focus();
        } else if (!event.shiftKey && event.target === fields[fields.length - 1]) {
          // Continue in the deck's natural order after the portaled last field.
          trigger.current?.focus();
          setOpening(null);
        }
      }}>
      <div className={styles.optionsHeading}>
        <span>{contextKey.startsWith('spectrum') ? 'Spectrum options' : 'Time options'}</span>
        <span className={styles.optionsSummary}>{summary}</span>
      </div>
      <div className={styles.optionsBody}>{children}</div>
    </div>, document.body)}
  </div>;
}
