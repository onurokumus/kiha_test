import {
  ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { ConfirmAction, ConfirmContext, ConfirmOptions } from './confirm';

interface ConfirmRequest {
  options: ConfirmOptions;
  resolve: (accepted: boolean) => void;
}

const TOOLTIP_ID = 'page-hover-tooltip';

function readTooltipText(target: HTMLElement): string {
  const title = target.getAttribute('title');
  return title === '' && target.dataset.pageTooltipTitle !== undefined
    ? target.dataset.pageTooltipTitle
    : title ?? target.dataset.tooltip ?? '';
}

interface TooltipLegendEntry {
  label: string;
  color: string;
}

function readTooltipLegend(target: HTMLElement): TooltipLegendEntry[] | undefined {
  const value = target.dataset.tooltipLegend;
  if (!value) return;
  try {
    const entries: unknown = JSON.parse(value);
    if (Array.isArray(entries) && entries.length && entries.every((entry): entry is TooltipLegendEntry =>
      typeof entry === 'object' && entry !== null &&
      typeof entry.label === 'string' && entry.label.trim().length > 0 &&
      typeof entry.color === 'string' && CSS.supports('color', entry.color))) return entries;
  } catch {
    // Keep ordinary tooltip text when optional legend metadata is malformed.
  }
}

function PageTooltip() {
  const [tooltip, setTooltip] = useState<{
    target: HTMLElement;
    text: string;
    legend?: TooltipLegendEntry[];
    rect: DOMRect;
  } | null>(null);
  const [position, setPosition] = useState<{
    left: number;
    top: number;
    placement: 'top' | 'bottom';
  } | null>(null);
  const targetRef = useRef<HTMLElement | null>(null);
  const sourceRef = useRef<'pointer' | 'keyboard'>('pointer');
  const showTimerRef = useRef<number | null>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);

  const clearTimer = useCallback(() => {
    if (showTimerRef.current !== null) {
      window.clearTimeout(showTimerRef.current);
      showTimerRef.current = null;
    }
  }, []);

  const restoreTarget = useCallback((target: HTMLElement | null) => {
    if (!target) return;

    const savedTitle = target.dataset.pageTooltipTitle;
    if (savedTitle !== undefined) {
      // React may have supplied a newer title while the hint was visible.
      if (target.getAttribute('title') === '') target.setAttribute('title', savedTitle);
      delete target.dataset.pageTooltipTitle;
    }

    const previousDescribedBy = target.dataset.pageTooltipDescribedby;
    if (previousDescribedBy !== undefined) {
      if (previousDescribedBy) target.setAttribute('aria-describedby', previousDescribedBy);
      else target.removeAttribute('aria-describedby');
      delete target.dataset.pageTooltipDescribedby;
    }
  }, []);

  const hide = useCallback(() => {
    clearTimer();
    restoreTarget(targetRef.current);
    targetRef.current = null;
    setTooltip(null);
    setPosition(null);
  }, [clearTimer, restoreTarget]);

  const prepareTarget = useCallback(
    (target: HTMLElement) => {
      if (targetRef.current && targetRef.current !== target) {
        restoreTarget(targetRef.current);
      }
      targetRef.current = target;

      const title = target.getAttribute('title');
      const text = readTooltipText(target);
      if (!text.trim()) return '';

      // Suppress the unstyled browser bubble while our tooltip is active.
      if (title) {
        target.dataset.pageTooltipTitle = title;
        // An empty sentinel suppresses the native bubble while allowing React's
        // later title removal to be observed instead of resurrecting old text.
        target.setAttribute('title', '');
      }

      if (target.dataset.pageTooltipDescribedby === undefined) {
        const describedBy = target.getAttribute('aria-describedby') ?? '';
        target.dataset.pageTooltipDescribedby = describedBy;
        target.setAttribute(
          'aria-describedby',
          [describedBy, TOOLTIP_ID].filter(Boolean).join(' ')
        );
      }
      return text;
    },
    [restoreTarget]
  );

  const show = useCallback(
    (target: HTMLElement, immediate: boolean) => {
      clearTimer();
      if (targetRef.current !== target) {
        setTooltip(null);
        setPosition(null);
      }
      sourceRef.current = immediate ? 'keyboard' : 'pointer';
      if (!prepareTarget(target)) { hide(); return; }

      const reveal = () => {
        showTimerRef.current = null;
        if (targetRef.current !== target) return;
        if (!target.isConnected || !target.getClientRects().length) { hide(); return; }
        const text = readTooltipText(target);
        if (!text.trim()) { hide(); return; }
        setPosition(null);
        setTooltip({ target, text, legend: readTooltipLegend(target), rect: target.getBoundingClientRect() });
      };

      if (immediate) reveal();
      else showTimerRef.current = window.setTimeout(reveal, 320);
    },
    [clearTimer, prepareTarget, hide]
  );

  useEffect(() => {
    const selector = '[title], [data-tooltip], [data-page-tooltip-title]';
    let keyboardInput = true;
    const findTarget = (eventTarget: EventTarget | null) => {
      const target = eventTarget instanceof Element ? eventTarget.closest<HTMLElement>(selector) : null;
      // An open control already exposes its content; don't cover it with a hint.
      return target?.getAttribute('aria-expanded') === 'true' ? null : target;
    };

    const handlePointerDown = () => {
      keyboardInput = false;
      hide();
    };

    const handlePointerOver = (event: PointerEvent) => {
      if (event.pointerType === 'touch' || event.buttons !== 0) return;
      const target = findTarget(event.target);
      if (!target) {
        if (sourceRef.current === 'pointer') hide();
        return;
      }
      if (target === targetRef.current) return;
      show(target, false);
    };

    const handlePointerOut = (event: PointerEvent) => {
      const activeTarget = targetRef.current;
      if (!activeTarget || !(event.target instanceof Node) || !activeTarget.contains(event.target)) {
        return;
      }
      if (event.relatedTarget instanceof Node && activeTarget.contains(event.relatedTarget)) return;
      // Mouse focus survives clicks, but must never pin a hover tooltip.
      if (sourceRef.current === 'keyboard' && activeTarget.matches(':focus-within')) return;
      hide();
    };

    const handleFocusIn = (event: FocusEvent) => {
      const target = findTarget(event.target);
      if (target && keyboardInput) show(target, true);
      else hide();
    };

    const handleFocusOut = (event: FocusEvent) => {
      const activeTarget = targetRef.current;
      if (!activeTarget || !(event.target instanceof Node) || !activeTarget.contains(event.target)) {
        return;
      }
      if (event.relatedTarget instanceof Node && activeTarget.contains(event.relatedTarget)) return;
      hide();
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      // Escape often restores focus to a menu/dialog opener. Keep that return
      // quiet until the user navigates again instead of immediately reopening help.
      keyboardInput = event.key !== 'Escape';
      if (!['Tab', 'Shift', 'Control', 'Alt', 'Meta'].includes(event.key)) hide();
    };

    const handleVisibilityChange = () => {
      if (document.hidden) hide();
    };

    // Capture activation before child controls move focus or stop propagation.
    document.addEventListener('pointerdown', handlePointerDown, true);
    document.addEventListener('pointerover', handlePointerOver);
    document.addEventListener('pointerout', handlePointerOut);
    document.addEventListener('focusin', handleFocusIn);
    document.addEventListener('focusout', handleFocusOut);
    document.addEventListener('keydown', handleKeyDown, true);
    window.addEventListener('blur', hide);
    window.addEventListener('resize', hide);
    document.addEventListener('scroll', hide, true);
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown, true);
      document.removeEventListener('pointerover', handlePointerOver);
      document.removeEventListener('pointerout', handlePointerOut);
      document.removeEventListener('focusin', handleFocusIn);
      document.removeEventListener('focusout', handleFocusOut);
      document.removeEventListener('keydown', handleKeyDown, true);
      window.removeEventListener('blur', hide);
      window.removeEventListener('resize', hide);
      document.removeEventListener('scroll', hide, true);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      hide();
    };
  }, [hide, show]);

  const visibleTarget = tooltip?.target;
  useEffect(() => {
    if (!visibleTarget) return;
    // React can replace a control without a pointerout/focusout event. Watch
    // only while a tooltip is visible, and ignore unrelated plot/UI mutations.
    const observer = new MutationObserver(records => {
      if (targetRef.current !== visibleTarget) return;
      if (!visibleTarget.isConnected) { hide(); return; }
      const relevant = records.filter(record => record.target instanceof Element && record.target.contains(visibleTarget));
      if (!relevant.length) return;
      const contentChanged = relevant.some(record => record.target === visibleTarget &&
        ['title', 'data-tooltip', 'data-tooltip-legend'].includes(record.attributeName ?? ''));
      if (relevant.some(record => record.target === visibleTarget && record.attributeName === 'title') &&
          !visibleTarget.getAttribute('title')) delete visibleTarget.dataset.pageTooltipTitle;
      if (contentChanged || !visibleTarget.getClientRects().length ||
          getComputedStyle(visibleTarget).visibility === 'hidden' ||
          visibleTarget.getAttribute('aria-expanded') === 'true') hide();
    });
    observer.observe(document.body, { childList: true, subtree: true, attributes: true,
      attributeFilter: ['title', 'data-tooltip', 'data-tooltip-legend', 'aria-expanded', 'hidden', 'style', 'class', 'open'] });
    return () => observer.disconnect();
  }, [visibleTarget, hide]);

  useLayoutEffect(() => {
    const element = tooltipRef.current;
    if (!tooltip || !element) return;

    const bounds = element.getBoundingClientRect();
    const gap = 9;
    const edge = 8;
    const preferredLeft = tooltip.rect.left + tooltip.rect.width / 2 - bounds.width / 2;
    const left = Math.min(
      window.innerWidth - bounds.width - edge,
      Math.max(edge, preferredLeft)
    );
    const roomBelow = window.innerHeight - tooltip.rect.bottom;
    const placement = roomBelow >= bounds.height + gap + edge ? 'bottom' : 'top';
    const top =
      placement === 'bottom'
        ? tooltip.rect.bottom + gap
        : Math.max(edge, tooltip.rect.top - bounds.height - gap);
    // Resize/scroll can fire while focus and layout are settling after zoom.
    // Do not schedule another layout update for identical tooltip geometry.
    if (position?.left === left && position.top === top && position.placement === placement) return;
    setPosition({ left, top, placement });
  }, [tooltip, position]);

  if (!tooltip) return null;

  return (
    <div
      ref={tooltipRef}
      id={TOOLTIP_ID}
      role="tooltip"
      aria-label={tooltip.legend ? tooltip.text : undefined}
      className={`page-tooltip page-tooltip--${position?.placement ?? 'bottom'}${tooltip.legend ? ' page-tooltip--legend' : ''}`}
      style={{
        left: position?.left ?? tooltip.rect.left,
        top: position?.top ?? tooltip.rect.bottom,
        visibility: position ? 'visible' : 'hidden',
      }}
    >
      {tooltip.legend ? tooltip.legend.map((entry, index) =>
        <span className="page-tooltip__legend-item" key={`${index}-${entry.label}`}>
          <i className="page-tooltip__legend-line" style={{ borderColor: entry.color }} aria-hidden="true" />
          <span>{entry.label}</span>
        </span>) : tooltip.text}
    </div>
  );
}

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<ConfirmRequest | null>(null);
  const activeRef = useRef<ConfirmRequest | null>(null);
  const queueRef = useRef<ConfirmRequest[]>([]);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  const showNext = useCallback(() => {
    if (activeRef.current) return;
    const request = queueRef.current.shift() ?? null;
    activeRef.current = request;
    setActive(request);
  }, []);

  const confirm = useCallback<ConfirmAction>(
    (options) =>
      new Promise<boolean>((resolve) => {
        queueRef.current.push({ options, resolve });
        showNext();
      }),
    [showNext]
  );

  const settle = useCallback(
    (accepted: boolean) => {
      const request = activeRef.current;
      if (!request) return;
      activeRef.current = null;
      setActive(null);
      request.resolve(accepted);
      window.setTimeout(showNext, 0);
    },
    [showNext]
  );

  useEffect(
    () => () => {
      activeRef.current?.resolve(false);
      queueRef.current.forEach((request) => request.resolve(false));
      queueRef.current = [];
    },
    []
  );

  useEffect(() => {
    if (!active) return;
    previouslyFocusedRef.current = document.activeElement as HTMLElement | null;
    document.body.classList.add('has-page-dialog');
    const showCancel = active.options.showCancel !== false;
    window.requestAnimationFrame(() => {
      (showCancel ? cancelButtonRef.current : confirmButtonRef.current)?.focus();
    });

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        settle(showCancel ? false : true);
        return;
      }
      if (event.key !== 'Tab' || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>('button:not(:disabled)')
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.classList.remove('has-page-dialog');
      previouslyFocusedRef.current?.focus();
    };
  }, [active, settle]);

  const options = active?.options;
  const tone = options?.tone ?? 'default';
  const showCancel = options?.showCancel !== false;
  const toneLabel =
    tone === 'danger' ? 'Destructive action' : tone === 'warning' ? 'Data change' : 'Confirm action';

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <PageTooltip />
      {options && (
        <div
          className="page-confirm-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && showCancel) settle(false);
          }}
        >
          <div
            ref={dialogRef}
            className={`page-confirm-dialog page-confirm-dialog--${tone}`}
            role={showCancel ? 'alertdialog' : 'dialog'}
            aria-modal="true"
            aria-labelledby="page-confirm-title"
            aria-describedby="page-confirm-description"
          >
            <div className="page-confirm-header">
              <span className="page-confirm-kicker">{toneLabel}</span>
              <h2 id="page-confirm-title">{options.title}</h2>
            </div>
            <div className="page-confirm-content">
              <p id="page-confirm-description">{options.description}</p>
              {options.detail && <p className="page-confirm-detail">{options.detail}</p>}
            </div>
            <div className="page-confirm-actions">
              {showCancel && (
                <button
                  ref={cancelButtonRef}
                  type="button"
                  className="page-confirm-button page-confirm-cancel"
                  onClick={() => settle(false)}
                >
                  {options.cancelLabel ?? 'Cancel'}
                </button>
              )}
              <button
                ref={confirmButtonRef}
                type="button"
                className="page-confirm-button page-confirm-primary"
                onClick={() => settle(true)}
              >
                {options.confirmLabel ?? 'Continue'}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}
