import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import type uPlot from 'uplot';
import type { PlotExportActions } from '../controls/PlotExportControls';
import type { PlotAnnotationActions, AnnotationStart } from './PlotAnnotations';
import styles from './PlotActionMenu.module.css';

interface Props {
  label: string;
  contextKey: string;
  targetRef: RefObject<HTMLDivElement>;
  getPlot?: () => uPlot | null;
  exportActions?: RefObject<PlotExportActions>;
  annotationActions?: RefObject<PlotAnnotationActions>;
  canAnnotate?: boolean;
  overlay?: { checked: boolean; enabled: boolean; onChange: (value: boolean) => void };
  notes?: { visible: boolean; onChange: (value: boolean) => void };
  resetLabel?: string;
  onReset: () => void;
  onResetY?: () => void;
  yAxis?: { automatic: boolean; enabled: boolean; onEdit: () => void };
  onEditFilter?: () => void;
  filterStatus?: string;
  onAnalysisDetails?: () => void;
}
interface Opening { x: number; y: number; timing?: AnnotationStart; contextKey: string }
interface Action { label: string; run: () => void; checked?: boolean; reason?: string }

/** One plot-scoped menu, outside grid clipping. Actions call the same owners as
 * visible controls; a context change dismisses captured pointer coordinates. */
export function PlotActionMenu({ label, contextKey, targetRef, getPlot, exportActions,
  annotationActions, canAnnotate, overlay, notes, resetLabel = 'Reset axes', onReset, onResetY,
  yAxis, onEditFilter, filterStatus, onAnalysisDetails }: Props) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [opening, setOpening] = useState<Opening | null>(null);
  const active = opening?.contextKey === contextKey ? opening : null;
  const latest = useRef({ contextKey, getPlot });
  latest.current = { contextKey, getPlot };
  useEffect(() => {
    if (opening && opening.contextKey !== contextKey) setOpening(null);
  }, [contextKey, opening]);

  const close = (focus = false) => {
    setOpening(null);
    if (focus) trigger.current?.focus({ preventScroll: true });
  };
  const openAt = (x: number, y: number, pointer = false) => {
    const plot = latest.current.getPlot?.();
    let timing: AnnotationStart | undefined;
    if (plot?.scales.x.min != null && plot.scales.x.max != null) {
      const range: [number, number] = [plot.scales.x.min, plot.scales.x.max];
      const rect = plot.over.getBoundingClientRect();
      const value = pointer && rect.width > 0
        ? plot.posToVal(Math.max(0, Math.min(rect.width, x - rect.left)) * plot.over.clientWidth / rect.width, 'x')
        : (range[0] + range[1]) / 2;
      if (Number.isFinite(value)) timing = { kind: 'marker', value, range };
    }
    window.dispatchEvent(new Event('kiha:plot-menu-open'));
    setOpening({ x, y, timing, contextKey: latest.current.contextKey });
  };
  const openRef = useRef(openAt);
  openRef.current = openAt;

  useEffect(() => {
    const target = targetRef.current;
    if (!target) return;
    const interactive = (event: Event) => event.target instanceof Element &&
      !!event.target.closest('button, input, textarea, select, a, [contenteditable="true"], [role="menu"]');
    const context = (event: MouseEvent) => {
      if (interactive(event)) return;
      event.preventDefault(); event.stopPropagation();
      const rect = target.getBoundingClientRect();
      // Browser-generated keyboard contextmenu events may have no coordinates.
      openRef.current(event.clientX || rect.left + 16, event.clientY || rect.top + 16,
        event.clientX !== 0 || event.clientY !== 0);
    };
    const keyboard = (event: KeyboardEvent) => {
      if (interactive(event) || !(event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10'))) return;
      event.preventDefault(); event.stopPropagation();
      const rect = target.getBoundingClientRect();
      openRef.current(rect.left + 16, rect.top + 16);
    };
    target.addEventListener('contextmenu', context);
    target.addEventListener('keydown', keyboard);
    return () => { target.removeEventListener('contextmenu', context); target.removeEventListener('keydown', keyboard); };
  }, [targetRef]);

  useLayoutEffect(() => {
    if (!active || !menu.current) return;
    const element = menu.current;
    const rect = element.getBoundingClientRect();
    element.style.left = `${Math.max(8, Math.min(active.x, window.innerWidth - rect.width - 8))}px`;
    element.style.top = `${Math.max(8, Math.min(active.y, window.innerHeight - rect.height - 8))}px`;
    element.querySelector<HTMLButtonElement>('[role^="menuitem"]')?.focus({ preventScroll: true });
  }, [active]);

  useEffect(() => {
    if (!active) return;
    const contains = (target: EventTarget | null) => target instanceof Node &&
      (menu.current?.contains(target) || trigger.current?.contains(target));
    const outside = (event: Event) => { if (!contains(event.target)) setOpening(null); };
    const dismiss = () => { setOpening(null); trigger.current?.focus({ preventScroll: true }); };
    const another = () => setOpening(null);
    const scroll = (event: Event) => { if (!(event.target instanceof Node && menu.current?.contains(event.target))) dismiss(); };
    const blur = () => setOpening(null);
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('focusin', outside);
    document.addEventListener('scroll', scroll, true);
    window.addEventListener('resize', dismiss);
    window.addEventListener('blur', blur);
    window.addEventListener('kiha:plot-menu-open', another);
    const target = targetRef.current;
    const initial = target?.getBoundingClientRect();
    const resize = new ResizeObserver(() => {
      const rect = target?.getBoundingClientRect();
      if (initial && rect && (rect.width !== initial.width || rect.height !== initial.height)) dismiss();
    });
    if (target) resize.observe(target);
    return () => {
      document.removeEventListener('pointerdown', outside, true); document.removeEventListener('focusin', outside);
      document.removeEventListener('scroll', scroll, true); window.removeEventListener('resize', dismiss);
      window.removeEventListener('blur', blur); window.removeEventListener('kiha:plot-menu-open', another); resize.disconnect();
    };
  }, [active, targetRef]);

  const actions: Action[] = [
    ...(exportActions ? [{ label: 'Export CSV / PNG…', run: () => exportActions.current?.open() }] : []),
    ...(onEditFilter ? [{ label: 'Filter settings…', run: onEditFilter }] : []),
    ...(onAnalysisDetails ? [{ label: 'Analysis details…', run: onAnalysisDetails }] : []),
    ...(overlay ? [{ label: 'Show original with filtered', checked: overlay.checked,
      reason: overlay.enabled ? undefined : 'Apply a time-domain filter first.', run: () => overlay.onChange(!overlay.checked) }] : []),
    { label: resetLabel, run: onReset },
    ...(onResetY ? [{ label: 'Auto-fit Y to visible data', checked: yAxis?.automatic, run: onResetY }] : []),
    ...(yAxis ? [{ label: 'Set Y-axis bounds…', reason: yAxis.enabled ? undefined : 'Wait for a time trace.', run: yAxis.onEdit }] : []),
    ...(annotationActions ? [
      { label: 'Add time marker…', reason: canAnnotate && active?.timing ? undefined : 'Wait for a time trace with source timing.',
        run: () => annotationActions.current?.open(active?.timing) },
      { label: 'Add interval note…', reason: canAnnotate && active?.timing ? undefined : 'Wait for a time trace with source timing.',
        run: () => { if (active?.timing) annotationActions.current?.open({ ...active.timing, kind: 'interval' }); } },
      { label: 'Manage time notes…', reason: canAnnotate ? undefined : 'Wait for a time trace with source timing.',
        run: () => annotationActions.current?.open() },
    ] : []),
    ...(notes ? [{ label: 'Show time notes (all plots)', checked: notes.visible, run: () => notes.onChange(!notes.visible) }] : []),
  ];

  return <>
    <button ref={trigger} type="button" className={styles.trigger} aria-label={`Plot actions for ${label}`}
      title="Plot actions (also right-click the plot)" aria-haspopup="menu" aria-expanded={!!active}
      aria-controls={active ? id : undefined} onClick={() => {
        if (active) close(); else { const rect = trigger.current!.getBoundingClientRect(); openAt(rect.left, rect.bottom + 4); }
      }} onKeyDown={(event) => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault(); const rect = trigger.current!.getBoundingClientRect(); openAt(rect.left, rect.bottom + 4);
        }
      }}>…</button>
    {active && createPortal(<div ref={menu} id={id} role="menu" aria-label={`Plot actions for ${label}`}
      className={styles.menu} style={{ left: active.x, top: active.y }} onContextMenu={(event) => event.preventDefault()}
      onKeyDown={(event) => {
        if (event.key === 'Escape' || event.key === 'Tab') {
          if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); }
          close(true); return;
        }
        const items = [...menu.current!.querySelectorAll<HTMLButtonElement>('[role^="menuitem"]')];
        const index = items.indexOf(document.activeElement as HTMLButtonElement);
        let next: number | undefined;
        if (event.key === 'ArrowDown') next = (index + 1) % items.length;
        else if (event.key === 'ArrowUp') next = (index - 1 + items.length) % items.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = items.length - 1;
        else if (event.key.length === 1 && /\S/.test(event.key) && !event.ctrlKey && !event.altKey && !event.metaKey) {
          next = items.findIndex((_, offset) => items[(index + 1 + offset) % items.length].textContent?.toLowerCase().startsWith(event.key.toLowerCase()));
          next = next < 0 ? undefined : (index + 1 + next) % items.length;
        }
        if (next !== undefined) { event.preventDefault(); items[next]?.focus(); }
      }}>
      <div className={styles.caption} title={label}>{label}</div>
      {filterStatus && <div className={styles.caption}>{filterStatus}</div>}
      {actions.map((action) => <button key={action.label} type="button" tabIndex={-1}
        role={action.checked === undefined ? 'menuitem' : 'menuitemcheckbox'} aria-checked={action.checked}
        aria-disabled={!!action.reason} title={action.reason} onClick={() => {
          if (action.reason) return;
          close(true); action.run();
        }}>
        <span className={styles.check} aria-hidden="true">{action.checked ? '✓' : ''}</span>
        <span>{action.label}{action.reason && <small>{action.reason}</small>}</span>
      </button>)}
    </div>, document.body)}
  </>;
}
