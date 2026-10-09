import {
  CSSProperties,
  ReactNode,
  KeyboardEvent as ReactKeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import styles from './SearchableSelect.module.css';

export interface SearchableSelectOption {
  value: string;
  label: string;
  description?: string;
  keywords?: string[];
  group?: string;
  disabled?: boolean;
  color?: string;
}

interface SearchableSelectProps {
  value: string;
  options: readonly SearchableSelectOption[];
  onChange: (value: string) => void;
  /** Multi-selection stays open after each toggle. Other selectors stay single. */
  multipleValues?: readonly string[];
  ariaLabel: string;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyMessage?: string;
  optionNoun?: string;
  className?: string;
  style?: CSSProperties;
  size?: 'compact' | 'default';
  appearance?: 'default' | 'embedded' | 'plot' | 'title';
  disabled?: boolean;
  title?: string;
  searchable?: boolean;
  menuMinWidth?: number;
  menuMaxWidth?: number;
  triggerContent?: ReactNode;
  showFooter?: boolean;
}

interface MenuPosition {
  left: number;
  top?: number;
  bottom?: number;
  width: number;
  maxHeight: number;
}

function normalize(value: string): string {
  return value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase();
}

function optionMatches(option: SearchableSelectOption, query: string): boolean {
  const terms = normalize(query).split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;

  const haystack = normalize(
    [option.label, option.value, option.description, option.group, ...(option.keywords ?? [])]
      .filter(Boolean)
      .join(' ')
  );
  return terms.every((term) => haystack.includes(term));
}

const SearchIcon = () => (
  <svg viewBox="0 0 16 16" aria-hidden="true">
    <circle cx="7" cy="7" r="4.25" />
    <path d="m10.2 10.2 3.1 3.1" />
  </svg>
);

const ChevronIcon = () => (
  <svg viewBox="0 0 16 16" aria-hidden="true">
    <path d="m4.25 6.25 3.75 3.5 3.75-3.5" />
  </svg>
);

const ClearIcon = () => (
  <svg viewBox="0 0 16 16" aria-hidden="true">
    <path d="m4.5 4.5 7 7m0-7-7 7" />
  </svg>
);

const CheckIcon = () => (
  <svg viewBox="0 0 16 16" aria-hidden="true">
    <path d="m3.25 8.3 2.85 2.8 6.65-6.4" />
  </svg>
);

export const SearchableSelect = ({
  value,
  options,
  onChange,
  multipleValues,
  ariaLabel,
  placeholder = 'Choose an option',
  searchPlaceholder,
  emptyMessage = 'No matching options',
  optionNoun = 'option',
  className,
  style,
  size = 'default',
  appearance = 'default',
  disabled = false,
  title,
  searchable = true,
  menuMinWidth = 286,
  menuMaxWidth = 440,
  triggerContent,
  showFooter = true,
}: SearchableSelectProps) => {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(-1);
  const [placement, setPlacement] = useState<'above' | 'below'>('below');
  const [menuPosition, setMenuPosition] = useState<MenuPosition>({
    left: 0,
    top: 0,
    width: menuMinWidth,
    maxHeight: 360,
  });
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const listboxRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listboxId = useId();
  const statusId = useId();

  const selectedOption = useMemo(
    () => options.find((option) => option.value === value),
    [options, value]
  );

  const visibleOptions = useMemo(
    () => options.filter((option) => optionMatches(option, query)),
    [options, query]
  );
  const selectedVisibleIndex = visibleOptions.findIndex(
    (option) => option.value === value && !option.disabled
  );
  const firstEnabledIndex = visibleOptions.findIndex((option) => !option.disabled);
  const visibleOptionsKey = visibleOptions
    .map((option) => `${option.value}:${option.disabled ? '0' : '1'}`)
    .join('\u0001');

  const updateMenuPosition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;

    const rect = trigger.getBoundingClientRect();
    const viewportPadding = 8;
    const gap = 6;
    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = document.documentElement.clientHeight;
    const availableWidth = Math.max(0, viewportWidth - viewportPadding * 2);
    const width = Math.min(
      Math.max(rect.width, Math.min(menuMinWidth, availableWidth)),
      Math.min(menuMaxWidth, availableWidth)
    );
    const left = Math.min(
      Math.max(viewportPadding, rect.left),
      Math.max(viewportPadding, viewportWidth - width - viewportPadding)
    );
    const roomBelow = viewportHeight - rect.bottom - gap - viewportPadding;
    const roomAbove = rect.top - gap - viewportPadding;
    const openAbove = roomBelow < 230 && roomAbove > roomBelow;
    const availableHeight = Math.max(0, openAbove ? roomAbove : roomBelow);
    const maxHeight = Math.min(368, availableHeight);

    setPlacement(openAbove ? 'above' : 'below');
    setMenuPosition(
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
  }, [menuMaxWidth, menuMinWidth]);

  const closeMenu = useCallback((restoreFocus = false) => {
    const focusScope = triggerRef.current?.closest('[data-select-focus-scope]')
      ?.getAttribute('data-select-focus-scope');
    setIsOpen(false);
    setQuery('');
    if (restoreFocus) {
      requestAnimationFrame(() => {
        if (triggerRef.current?.isConnected) {
          triggerRef.current.focus({ preventScroll: true });
          return;
        }
        // Changing a plot signal intentionally remounts its chart. Restore
        // keyboard focus to that slot's replacement title, not the detached
        // trigger; other grids and duplicate signals keep independent scopes.
        if (!focusScope) return;
        const scope = Array.from(document.querySelectorAll('[data-select-focus-scope]'))
          .find(element => element.getAttribute('data-select-focus-scope') === focusScope);
        const replacement = Array.from(scope?.querySelectorAll<HTMLButtonElement>('[data-searchable-select] > button') ?? [])
          .find(button => button.getAttribute('aria-label') === ariaLabel && !button.disabled);
        replacement?.focus({ preventScroll: true });
      });
    }
  }, [ariaLabel]);

  const openMenu = (initialQuery = '') => {
    if (disabled || options.length === 0) return;
    updateMenuPosition();
    setQuery(initialQuery);
    setIsOpen(true);
  };

  useLayoutEffect(() => {
    if (!isOpen) return;
    updateMenuPosition();
  }, [isOpen, updateMenuPosition]);

  useEffect(() => {
    if (disabled || options.length === 0) {
      setIsOpen(false);
      setQuery('');
    }
  }, [disabled, options.length]);

  useEffect(() => {
    if (!isOpen) return;

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      closeMenu();
    };
    const onFocusIn = (event: FocusEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      closeMenu();
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault();
      closeMenu(true);
    };

    window.addEventListener('resize', updateMenuPosition);
    window.addEventListener('scroll', updateMenuPosition, true);
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('focusin', onFocusIn);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('resize', updateMenuPosition);
      window.removeEventListener('scroll', updateMenuPosition, true);
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('focusin', onFocusIn);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [isOpen, updateMenuPosition, closeMenu]);

  useEffect(() => {
    if (!isOpen) return;
    const frame = requestAnimationFrame(() => {
      if (searchable) searchRef.current?.focus();
      else listboxRef.current?.focus();
      const selected = menuRef.current?.querySelector('[aria-selected="true"]');
      selected?.scrollIntoView({ block: 'nearest' });
    });
    return () => cancelAnimationFrame(frame);
  }, [isOpen, searchable]);

  useEffect(() => {
    if (!isOpen) return;
    setActiveIndex(selectedVisibleIndex >= 0 ? selectedVisibleIndex : firstEnabledIndex);
  }, [firstEnabledIndex, isOpen, query, selectedVisibleIndex, value, visibleOptionsKey]);

  const focusOption = (index: number) => {
    setActiveIndex(index);
    requestAnimationFrame(() => {
      document.getElementById(`${listboxId}-option-${index}`)?.scrollIntoView({
        block: 'nearest',
      });
    });
  };

  const moveActive = (direction: 1 | -1) => {
    if (visibleOptions.length === 0) return;
    let next = activeIndex < 0 && direction === -1 ? 0 : activeIndex;
    for (let checked = 0; checked < visibleOptions.length; checked += 1) {
      next = (next + direction + visibleOptions.length) % visibleOptions.length;
      if (!visibleOptions[next].disabled) {
        focusOption(next);
        return;
      }
    }
  };

  const chooseOption = (option: SearchableSelectOption | undefined) => {
    if (!option || option.disabled) return;
    onChange(option.value);
    if (!multipleValues) closeMenu(true);
  };

  const handleMenuKeyDown = (event: ReactKeyboardEvent) => {
    if (event.nativeEvent.isComposing) return;
    const editingQuery = event.target === searchRef.current;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      moveActive(1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      moveActive(-1);
    } else if (event.key === 'Home' && !editingQuery) {
      event.preventDefault();
      const firstEnabled = visibleOptions.findIndex((option) => !option.disabled);
      if (firstEnabled >= 0) focusOption(firstEnabled);
    } else if (event.key === 'End' && !editingQuery) {
      event.preventDefault();
      let lastEnabled = -1;
      for (let index = visibleOptions.length - 1; index >= 0; index -= 1) {
        if (!visibleOptions[index].disabled) {
          lastEnabled = index;
          break;
        }
      }
      if (lastEnabled >= 0) focusOption(lastEnabled);
    } else if (
      (event.key === 'Enter' || (event.key === ' ' && !editingQuery)) &&
      activeIndex >= 0
    ) {
      event.preventDefault();
      chooseOption(visibleOptions[activeIndex]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      closeMenu(true);
    } else if (event.key === 'Tab') {
      // The menu is portaled at the end of the document. Return to the trigger
      // before native Tab navigation so hidden/inert controls and tab order
      // are handled by the browser, including at the document boundaries.
      triggerRef.current?.focus();
      closeMenu();
    }
  };

  const handleTriggerKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      openMenu();
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openMenu();
    } else if (
      searchable &&
      event.key.length === 1 &&
      !event.altKey &&
      !event.ctrlKey &&
      !event.metaKey
    ) {
      event.preventDefault();
      openMenu(event.key);
    }
  };

  const pluralNoun = options.length === 1 ? optionNoun : `${optionNoun}s`;
  const resultSummary = query
    ? `${visibleOptions.length} of ${options.length} ${pluralNoun}`
    : `${options.length} ${pluralNoun}`;
  const rootClassName = [styles.root, styles[size], styles[appearance], className ?? '']
    .filter(Boolean)
    .join(' ');

  const menu = isOpen
    ? createPortal(
        <div
          ref={menuRef}
          className={styles.menu}
          data-placement={placement}
          data-searchable={searchable || undefined}
          style={menuPosition}
          onKeyDown={handleMenuKeyDown}
        >
          {searchable && (
            <div className={styles.searchBar}>
              <SearchIcon />
              <input
                ref={searchRef}
                type="search"
                role="combobox"
                aria-expanded={true}
                aria-autocomplete="list"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={searchPlaceholder ?? `Search ${pluralNoun}...`}
                aria-label={searchPlaceholder ?? `Search ${pluralNoun}`}
                aria-controls={listboxId}
                aria-activedescendant={
                  visibleOptions[activeIndex] ? `${listboxId}-option-${activeIndex}` : undefined
                }
                aria-describedby={showFooter ? statusId : undefined}
                autoComplete="off"
                spellCheck={false}
              />
              {query && (
                <button
                  type="button"
                  className={styles.clearSearch}
                  aria-label="Clear search"
                  tabIndex={-1}
                  onClick={() => {
                    setQuery('');
                    searchRef.current?.focus();
                  }}
                >
                  <ClearIcon />
                </button>
              )}
            </div>
          )}

          <div
            ref={listboxRef}
            id={listboxId}
            className={styles.optionList}
            role="listbox"
            aria-multiselectable={multipleValues ? true : undefined}
            aria-label={ariaLabel}
            tabIndex={searchable ? undefined : -1}
            aria-activedescendant={
              !searchable && visibleOptions[activeIndex]
                ? `${listboxId}-option-${activeIndex}`
                : undefined
            }
          >
            {visibleOptions.length === 0 ? (
              <div className={styles.emptyState}>
                <SearchIcon />
                <strong>{emptyMessage}</strong>
                <span>Try a shorter name or a different spelling.</span>
              </div>
            ) : (
              visibleOptions.map((option, index) => {
                const previousGroup = index > 0 ? visibleOptions[index - 1].group : undefined;
                const showGroup = Boolean(option.group && option.group !== previousGroup);
                const selected = multipleValues ? multipleValues.includes(option.value) : option.value === value;
                const active = index === activeIndex;

                return (
                  <div key={`${option.group ?? ''}:${option.value}`} role="presentation">
                    {showGroup && (
                      <div className={styles.groupLabel} role="presentation">
                        {option.group}
                      </div>
                    )}
                    <button
                      id={`${listboxId}-option-${index}`}
                      type="button"
                      className={styles.option}
                      role="option"
                      aria-selected={selected}
                      aria-disabled={option.disabled || undefined}
                      data-active={active || undefined}
                      disabled={option.disabled}
                      tabIndex={-1}
                      onMouseDown={(event) => event.preventDefault()}
                      onPointerMove={() => {
                        if (!option.disabled && activeIndex !== index) setActiveIndex(index);
                      }}
                      onClick={() => chooseOption(option)}
                    >
                      <span className={styles.optionRail} style={option.color ? { background: option.color, opacity: 1 } : undefined} aria-hidden="true" />
                      <span className={styles.optionCopy}>
                        <strong>{option.label}</strong>
                        {option.description && <small>{option.description}</small>}
                      </span>
                      <span className={styles.check} aria-hidden="true">
                        {selected && <CheckIcon />}
                      </span>
                    </button>
                  </div>
                );
              })
            )}
          </div>

          {showFooter && <div className={styles.menuFooter}>
            <span id={statusId} role="status" aria-live="polite">
              {multipleValues ? `${multipleValues.length} selected · ${resultSummary}` : resultSummary}
            </span>
            <span className={styles.keyHints} aria-hidden="true">
              <kbd>↑↓</kbd> move <kbd>Enter</kbd> {multipleValues ? 'toggle' : 'choose'}
            </span>
          </div>}
        </div>,
        // Keep a native modal's popup in its top layer and focus scope.
        rootRef.current?.closest('dialog') ?? document.body
      )
    : null;

  return (
    <div
      ref={rootRef}
      className={rootClassName}
      style={style}
      data-open={isOpen || undefined}
      data-searchable-select
    >
      <button
        ref={triggerRef}
        type="button"
        className={styles.trigger}
        role={searchable ? undefined : 'combobox'}
        aria-label={ariaLabel}
        aria-expanded={isOpen}
        aria-controls={isOpen ? listboxId : undefined}
        aria-haspopup="listbox"
        disabled={disabled || options.length === 0}
        data-tooltip={isOpen ? undefined : title ?? selectedOption?.label}
        onClick={() => (isOpen ? closeMenu() : openMenu())}
        onKeyDown={handleTriggerKeyDown}
      >
        {triggerContent ?? <>
        <span className={styles.triggerRail} style={selectedOption?.color ? { background: selectedOption.color, opacity: 1 } : undefined} aria-hidden="true" />
        <span className={selectedOption ? styles.triggerValue : styles.triggerPlaceholder}>
          {selectedOption?.label ?? placeholder}
        </span>
        <span className={styles.chevron} aria-hidden="true">
          <ChevronIcon />
        </span>
        </>}
      </button>
      {menu}
    </div>
  );
};
