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

function PageTooltip() {
  const [tooltip, setTooltip] = useState<{
    target: HTMLElement;
    text: string;
    rect: DOMRect;
  } | null>(null);
  const [position, setPosition] = useState<{
    left: number;
    top: number;
    placement: 'top' | 'bottom';
  } | null>(null);
  const targetRef = useRef<HTMLElement | null>(null);
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
      target.setAttribute('title', savedTitle);
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
      const text = title ?? target.dataset.tooltip ?? target.dataset.pageTooltipTitle ?? '';
      if (!text.trim()) return '';

      // Suppress the unstyled browser bubble while our tooltip is active.
      if (title !== null) {
        target.dataset.pageTooltipTitle = title;
        target.removeAttribute('title');
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
      const text = prepareTarget(target);
      if (!text) return;

      const reveal = () => {
        showTimerRef.current = null;
        if (targetRef.current !== target || !target.isConnected) return;
        setPosition(null);
        setTooltip({ target, text, rect: target.getBoundingClientRect() });
      };

      if (immediate) reveal();
      else showTimerRef.current = window.setTimeout(reveal, 320);
    },
    [clearTimer, prepareTarget]
  );

  useEffect(() => {
    const selector = '[title], [data-tooltip], [data-page-tooltip-title]';
    const findTarget = (eventTarget: EventTarget | null) =>
      eventTarget instanceof Element ? eventTarget.closest<HTMLElement>(selector) : null;

    const handlePointerOver = (event: PointerEvent) => {
      if (event.pointerType === 'touch') return;
      const target = findTarget(event.target);
      if (!target || target === targetRef.current) return;
      show(target, false);
    };

    const handlePointerOut = (event: PointerEvent) => {
      const activeTarget = targetRef.current;
      if (!activeTarget || !(event.target instanceof Node) || !activeTarget.contains(event.target)) {
        return;
      }
      if (event.relatedTarget instanceof Node && activeTarget.contains(event.relatedTarget)) return;
      if (activeTarget.matches(':focus-within')) return;
      hide();
    };

    const handleFocusIn = (event: FocusEvent) => {
      const target = findTarget(event.target);
      if (target) show(target, true);
    };

    const handleFocusOut = (event: FocusEvent) => {
      const activeTarget = targetRef.current;
      if (!activeTarget || !(event.target instanceof Node) || !activeTarget.contains(event.target)) {
        return;
      }
      if (event.relatedTarget instanceof Node && activeTarget.contains(event.relatedTarget)) return;
      if (activeTarget.matches(':hover')) return;
      hide();
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && targetRef.current) hide();
    };

    const refreshPosition = () => {
      const target = targetRef.current;
      if (!target) return;
      if (!target.isConnected) {
        hide();
        return;
      }
      const rect = target.getBoundingClientRect();
      setTooltip((current) => {
        if (!current) return current;
        const previous = current.rect;
        return previous.x === rect.x && previous.y === rect.y &&
          previous.width === rect.width && previous.height === rect.height
          ? current : { ...current, rect };
      });
    };

    document.addEventListener('pointerover', handlePointerOver);
    document.addEventListener('pointerout', handlePointerOut);
    document.addEventListener('focusin', handleFocusIn);
    document.addEventListener('focusout', handleFocusOut);
    document.addEventListener('keydown', handleKeyDown);
    window.addEventListener('resize', refreshPosition);
    document.addEventListener('scroll', refreshPosition, true);
    return () => {
      document.removeEventListener('pointerover', handlePointerOver);
      document.removeEventListener('pointerout', handlePointerOut);
      document.removeEventListener('focusin', handleFocusIn);
      document.removeEventListener('focusout', handleFocusOut);
      document.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('resize', refreshPosition);
      document.removeEventListener('scroll', refreshPosition, true);
      hide();
    };
  }, [hide, show]);

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
      className={`page-tooltip page-tooltip--${position?.placement ?? 'bottom'}`}
      style={{
        left: position?.left ?? tooltip.rect.left,
        top: position?.top ?? tooltip.rect.bottom,
        visibility: position ? 'visible' : 'hidden',
      }}
    >
      {tooltip.text}
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
