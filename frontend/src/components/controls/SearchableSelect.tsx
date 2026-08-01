import {
  CSSProperties,
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
}

interface SearchableSelectProps {
  value: string;
  options: readonly SearchableSelectOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyMessage?: string;
  optionNoun?: string;
  className?: string;
  style?: CSSProperties;
  size?: 'compact' | 'default';
  appearance?: 'default' | 'embedded' | 'plot';
  disabled?: boolean;
  title?: string;
  searchable?: boolean;
  menuMinWidth?: number;
  menuMaxWidth?: number;
}

interface MenuPosition {
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
    const availableWidth = Math.max(180, viewportWidth - viewportPadding * 2);
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
    const availableHeight = Math.max(120, openAbove ? roomAbove : roomBelow);
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

  const closeMenu = (restoreFocus = false) => {
    setIsOpen(false);
    setQuery('');
    if (restoreFocus) {
      requestAnimationFrame(() => triggerRef.current?.focus());
    }
  };

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
      if (event.key !== 'Escape') return;
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
  }, [isOpen, updateMenuPosition]);

  useEffect(() => {
    if (!isOpen) return;
    requestAnimationFrame(() => {
      if (searchable) searchRef.current?.focus();
      else menuRef.current?.focus();
    });
  }, [isOpen, searchable]);

  useEffect(() => {
    if (!isOpen) return;
    setActiveIndex(selectedVisibleIndex >= 0 ? selectedVisibleIndex : firstEnabledIndex);
  }, [
    firstEnabledIndex,
    isOpen,
    query,
    selectedVisibleIndex,
    value,
    visibleOptionsKey,
  ]);

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
    let next = activeIndex;
    for (let checked = 0; checked < visibleOptions.length; checked += 1) {
      next = (next + direction + visibleOptions.length) % visibleOptions.length;
      if (!visibleOptions[next].disabled) {
        focusOption(next);
        return;
      }
    }
  };

  const chooseOption = (option: SearchableSelectOption) => {
    if (option.disabled) return;
    onChange(option.value);
    closeMenu(true);
  };

  const focusAdjacentControl = (backwards: boolean) => {
    const controls = Array.from(document.querySelectorAll<HTMLElement>(focusableSelector)).filter(
      (element) =>
        !menuRef.current?.contains(element) &&
        element.getAttribute('aria-hidden') !== 'true' &&
        element.getClientRects().length > 0
    );
    const triggerIndex = triggerRef.current ? controls.indexOf(triggerRef.current) : -1;
    const nextIndex = triggerIndex + (backwards ? -1 : 1);
    closeMenu();
    controls[nextIndex]?.focus();
  };

  const handleMenuKeyDown = (event: ReactKeyboardEvent) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      moveActive(1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      moveActive(-1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      const firstEnabled = visibleOptions.findIndex((option) => !option.disabled);
      if (firstEnabled >= 0) focusOption(firstEnabled);
    } else if (event.key === 'End') {
      event.preventDefault();
      let lastEnabled = -1;
      for (let index = visibleOptions.length - 1; index >= 0; index -= 1) {
        if (!visibleOptions[index].disabled) {
          lastEnabled = index;
          break;
        }
      }
      if (lastEnabled >= 0) focusOption(lastEnabled);
    } else if (event.key === 'Enter' && activeIndex >= 0) {
      event.preventDefault();
      chooseOption(visibleOptions[activeIndex]);
    } else if (event.key === 'Tab') {
      event.preventDefault();
      focusAdjacentControl(event.shiftKey);
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
          style={menuPosition}
          onKeyDown={handleMenuKeyDown}
          tabIndex={searchable ? undefined : -1}
        >
          {searchable && (
            <div className={styles.searchBar}>
              <SearchIcon />
              <input
                ref={searchRef}
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={searchPlaceholder ?? `Search ${pluralNoun}...`}
                aria-label={searchPlaceholder ?? `Search ${pluralNoun}`}
                aria-controls={listboxId}
                aria-activedescendant={
                  activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined
                }
                aria-describedby={statusId}
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

          <div id={listboxId} className={styles.optionList} role="listbox" aria-label={ariaLabel}>
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
                const selected = option.value === value;
                const active = index === activeIndex;

                return (
                  <div key={`${option.group ?? ''}:${option.value}`} role="presentation">
                    {showGroup && <div className={styles.groupLabel}>{option.group}</div>}
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
                      onPointerMove={() => {
                        if (!option.disabled && activeIndex !== index) setActiveIndex(index);
                      }}
                      onClick={() => chooseOption(option)}
                    >
                      <span className={styles.optionRail} aria-hidden="true" />
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

          <div className={styles.menuFooter}>
            <span id={statusId} role="status" aria-live="polite">
              {resultSummary}
            </span>
            <span className={styles.keyHints} aria-hidden="true">
              <kbd>↑↓</kbd> move <kbd>Enter</kbd> choose
            </span>
          </div>
        </div>,
        document.body
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
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={isOpen}
        aria-controls={isOpen ? listboxId : undefined}
        aria-haspopup="listbox"
        disabled={disabled || options.length === 0}
        title={title}
        onClick={() => (isOpen ? closeMenu() : openMenu())}
        onKeyDown={handleTriggerKeyDown}
      >
        <span className={styles.triggerRail} aria-hidden="true" />
        <span className={selectedOption ? styles.triggerValue : styles.triggerPlaceholder}>
          {selectedOption?.label ?? placeholder}
        </span>
        <span className={styles.chevron} aria-hidden="true">
          <ChevronIcon />
        </span>
      </button>
      {menu}
    </div>
  );
};
