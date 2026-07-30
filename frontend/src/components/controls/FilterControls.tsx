import React, {
  memo,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { AggMode, FilterOptions, ScatterFilterState } from '../../types';
import styles from './FilterControls.module.css';

const AGG_MODES: { value: AggMode; label: string }[] = [
  { value: 'mean', label: 'Mean' },
  { value: 'min', label: 'Minimum' },
  { value: 'max', label: 'Maximum' },
  { value: 'any', label: 'Any sample' },
];

interface FilterControlsProps {
  filterState: ScatterFilterState;
  filterOptions: FilterOptions;
  columns: string[];
  onToggleTpKeys: (keys: string[], checked: boolean) => void;
  onToggleLabel: (label: string, checked: boolean) => void;
  onAddParameterFilter: () => void;
  onUpdateParameterFilter: (
    id: string,
    field: 'column' | 'mode' | 'min' | 'max',
    value: string | number | null
  ) => void;
  onRemoveParameterFilter: (id: string) => void;
  onClearFilters: () => void;
  hasActiveFilters: boolean;
  filteredCount: number;
  totalCount: number;
}

type SectionName = 'tests' | 'labels' | 'parameters';

const CheckboxItem: React.FC<{
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  indent?: boolean;
  dim?: boolean;
  indeterminate?: boolean;
}> = ({ label, checked, onChange, indent = false, dim = false, indeterminate = false }) => {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (inputRef.current) inputRef.current.indeterminate = indeterminate;
  }, [indeterminate]);

  return (
    <label
      className={`${styles.checkboxItem} ${indent ? styles.checkboxItemIndented : ''} ${
        dim ? styles.checkboxItemDimmed : ''
      }`}
    >
      <input
        ref={inputRef}
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span title={label}>{label}</span>
    </label>
  );
};

const NumericInput: React.FC<{
  label: string;
  placeholder: string;
  value: number | null;
  onCommit: (value: number | null) => void;
}> = ({ label, placeholder, value, onCommit }) => {
  const [text, setText] = useState(value === null ? '' : String(value));
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const commit = useCallback(
    (raw: string) => {
      const parsed = raw.trim() === '' ? null : Number(raw);
      onCommit(parsed === null || Number.isFinite(parsed) ? parsed : null);
    },
    [onCommit]
  );

  useEffect(() => {
    setText(value === null ? '' : String(value));
  }, [value]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    []
  );

  return (
    <label className={styles.rangeField}>
      <span>{label}</span>
      <input
        type="number"
        inputMode="decimal"
        placeholder={placeholder}
        value={text}
        step="0.01"
        onChange={(event) => {
          const raw = event.target.value;
          setText(raw);
          if (timer.current) clearTimeout(timer.current);
          timer.current = setTimeout(() => commit(raw), 600);
        }}
        onBlur={() => {
          if (timer.current) clearTimeout(timer.current);
          commit(text);
        }}
      />
    </label>
  );
};

const FilterControlsComponent: React.FC<FilterControlsProps> = ({
  filterState,
  filterOptions,
  columns,
  onToggleTpKeys,
  onToggleLabel,
  onAddParameterFilter,
  onUpdateParameterFilter,
  onRemoveParameterFilter,
  onClearFilters,
  hasActiveFilters,
  filteredCount,
  totalCount,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [testSearch, setTestSearch] = useState('');
  const [labelSearch, setLabelSearch] = useState('');
  const [expandedTests, setExpandedTests] = useState<Set<string>>(new Set());
  const [openSections, setOpenSections] = useState<Record<SectionName, boolean>>(() => ({
    tests: true,
    labels: false,
    parameters: filterState.parameterFilters.length > 0,
  }));
  const [drawerMaxHeight, setDrawerMaxHeight] = useState(420);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const drawerId = useId();

  const selectedKeys = useMemo(() => new Set(filterState.tpKeys), [filterState.tpKeys]);
  const activeParameterFilters = useMemo(
    () =>
      filterState.parameterFilters.filter(
        (filter) => filter.column && (filter.min !== null || filter.max !== null)
      ),
    [filterState.parameterFilters]
  );
  const activeFilterCount =
    filterState.tpKeys.length + filterState.labels.length + activeParameterFilters.length;

  const activeChips = useMemo(() => {
    const chips: string[] = [];
    if (filterState.tpKeys.length > 0) {
      chips.push(
        `${filterState.tpKeys.length} test point${filterState.tpKeys.length === 1 ? '' : 's'}`
      );
    }
    if (filterState.labels.length > 0) {
      chips.push(`${filterState.labels.length} label${filterState.labels.length === 1 ? '' : 's'}`);
    }
    activeParameterFilters.forEach((filter) => {
      const lower = filter.min === null ? '−∞' : String(filter.min);
      const upper = filter.max === null ? '+∞' : String(filter.max);
      chips.push(`${filter.column} ${lower}–${upper}`);
    });
    return chips;
  }, [activeParameterFilters, filterState.labels.length, filterState.tpKeys.length]);

  const visibleTree = useMemo(() => {
    const search = testSearch.trim().toLowerCase();
    if (!search) return filterOptions.testTree;
    return filterOptions.testTree
      .map((node) => {
        if (node.test.toLowerCase().includes(search)) return node;
        const tps = node.tps.filter(
          (tp) => tp.name.toLowerCase().includes(search) || tp.label.toLowerCase().includes(search)
        );
        return { ...node, tps };
      })
      .filter((node) => node.test.toLowerCase().includes(search) || node.tps.length > 0);
  }, [filterOptions.testTree, testSearch]);

  const visibleLabels = useMemo(() => {
    const search = labelSearch.trim().toLowerCase();
    if (!search) return filterOptions.labels;
    return filterOptions.labels.filter((label) => label.toLowerCase().includes(search));
  }, [filterOptions.labels, labelSearch]);

  const getTestCheckState = useCallback(
    (test: string) => {
      const node = filterOptions.testTree.find((entry) => entry.test === test);
      const total = node?.tps.length ?? 0;
      const selected = node?.tps.filter((tp) => selectedKeys.has(tp.key)).length ?? 0;
      return {
        checked: total > 0 && selected === total,
        indeterminate: selected > 0 && selected < total,
      };
    },
    [filterOptions.testTree, selectedKeys]
  );

  const closeDrawer = useCallback((returnFocus = false) => {
    setIsOpen(false);
    if (returnFocus) requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);

  useEffect(() => {
    if (!isOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) closeDrawer();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      closeDrawer(true);
    };

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [closeDrawer, isOpen]);

  useLayoutEffect(() => {
    if (!isOpen || !rootRef.current) return;

    const root = rootRef.current;
    const panel = root.parentElement;
    if (!panel) return;

    const updateBounds = () => {
      const rootBounds = root.getBoundingClientRect();
      const panelBounds = panel.getBoundingClientRect();
      setDrawerMaxHeight(Math.max(112, Math.floor(panelBounds.bottom - rootBounds.bottom - 12)));
    };

    updateBounds();
    window.addEventListener('resize', updateBounds);
    const observer = new ResizeObserver(updateBounds);
    observer.observe(panel);
    return () => {
      window.removeEventListener('resize', updateBounds);
      observer.disconnect();
    };
  }, [isOpen]);

  const toggleTestExpansion = (test: string) => {
    setExpandedTests((current) => {
      const next = new Set(current);
      if (next.has(test)) next.delete(test);
      else next.add(test);
      return next;
    });
  };

  const toggleSection = (section: SectionName) => {
    setOpenSections((current) => ({ ...current, [section]: !current[section] }));
  };

  const sectionIds: Record<SectionName, string> = {
    tests: `${drawerId}-tests`,
    labels: `${drawerId}-labels`,
    parameters: `${drawerId}-parameters`,
  };

  const sectionToggle = (section: SectionName, label: string, count: number) => (
    <button
      type="button"
      className={styles.sectionToggle}
      aria-expanded={openSections[section]}
      aria-controls={sectionIds[section]}
      onClick={() => toggleSection(section)}
    >
      <span className={styles.sectionChevron} aria-hidden="true" />
      <span>{label}</span>
      {count > 0 && <span className={styles.sectionCount}>{count}</span>}
    </button>
  );

  return (
    <div ref={rootRef} className={`${styles.root} ${isOpen ? styles.rootOpen : ''}`}>
      <div className={styles.summaryBar}>
        <button
          ref={triggerRef}
          type="button"
          className={styles.trigger}
          aria-expanded={isOpen}
          aria-controls={drawerId}
          onClick={() => setIsOpen((open) => !open)}
        >
          <span className={styles.filterGlyph} aria-hidden="true">
            ≡
          </span>
          <span>Filters</span>
          {activeFilterCount > 0 && (
            <span className={styles.activeBadge} aria-label={`${activeFilterCount} active filters`}>
              {activeFilterCount}
            </span>
          )}
        </button>

        <span className={styles.resultCount} aria-live="polite" aria-atomic="true">
          <strong>{filteredCount}</strong> / {totalCount} points
        </span>

        {activeChips.length > 0 && (
          <div className={styles.activeChips} aria-label="Active filter summary">
            {activeChips.slice(0, 2).map((chip) => (
              <span key={chip} className={styles.activeChip} title={chip}>
                {chip}
              </span>
            ))}
            {activeChips.length > 2 && (
              <span className={styles.moreChip}>+{activeChips.length - 2}</span>
            )}
          </div>
        )}

        {hasActiveFilters && (
          <button
            type="button"
            className={styles.clearButton}
            onClick={onClearFilters}
            aria-label="Clear all scatter filters"
          >
            Clear
          </button>
        )}
      </div>

      {isOpen && (
        <div
          id={drawerId}
          className={styles.drawer}
          role="region"
          aria-label="Scatter filters"
          style={{ maxHeight: drawerMaxHeight }}
        >
          <div className={styles.drawerHeader}>
            <div>
              <strong>Filter scatter points</strong>
              <span>Selections combine to narrow the visible result.</span>
            </div>
            <button
              type="button"
              className={styles.closeButton}
              onClick={() => closeDrawer(true)}
              aria-label="Close filters"
            >
              ×
            </button>
          </div>

          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              {sectionToggle('tests', 'Tests & test points', filterState.tpKeys.length)}
              <div className={styles.sectionActions}>
                <button
                  type="button"
                  onClick={() =>
                    setExpandedTests(new Set(filterOptions.testTree.map((node) => node.test)))
                  }
                >
                  Expand
                </button>
                <button type="button" onClick={() => setExpandedTests(new Set())}>
                  Collapse
                </button>
              </div>
            </div>
            {openSections.tests && (
              <div id={sectionIds.tests} className={styles.sectionBody}>
                <label className={styles.searchField}>
                  <span className={styles.screenReaderOnly}>Search tests and test points</span>
                  <input
                    type="search"
                    placeholder="Search tests or points"
                    value={testSearch}
                    onChange={(event) => setTestSearch(event.target.value)}
                  />
                </label>
                <div className={styles.optionList}>
                  {visibleTree.map((node) => {
                    const checkState = getTestCheckState(node.test);
                    const testExpanded = expandedTests.has(node.test);
                    return (
                      <div key={node.test} className={styles.testGroup}>
                        <div className={styles.testRow}>
                          <button
                            type="button"
                            className={styles.treeToggle}
                            aria-expanded={testExpanded}
                            aria-label={`${testExpanded ? 'Collapse' : 'Expand'} ${node.test}`}
                            onClick={() => toggleTestExpansion(node.test)}
                            disabled={node.tps.length === 0}
                          >
                            <span aria-hidden="true" />
                          </button>
                          <CheckboxItem
                            label={`${node.test} (${node.tps.length})`}
                            checked={checkState.checked}
                            indeterminate={checkState.indeterminate}
                            dim={node.tps.length === 0}
                            onChange={(checked) =>
                              onToggleTpKeys(
                                node.tps.map((tp) => tp.key),
                                checked
                              )
                            }
                          />
                        </div>
                        {testExpanded &&
                          node.tps.map((tp) => (
                            <CheckboxItem
                              key={tp.key}
                              label={tp.label ? `${tp.name} — ${tp.label}` : tp.name}
                              checked={selectedKeys.has(tp.key)}
                              onChange={(checked) => onToggleTpKeys([tp.key], checked)}
                              indent
                            />
                          ))}
                      </div>
                    );
                  })}
                  {visibleTree.length === 0 && (
                    <p className={styles.emptyMessage}>No matching tests or points.</p>
                  )}
                </div>
              </div>
            )}
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              {sectionToggle('labels', 'Labels', filterState.labels.length)}
            </div>
            {openSections.labels && (
              <div id={sectionIds.labels} className={styles.sectionBody}>
                <label className={styles.searchField}>
                  <span className={styles.screenReaderOnly}>Search labels</span>
                  <input
                    type="search"
                    placeholder="Search labels"
                    value={labelSearch}
                    onChange={(event) => setLabelSearch(event.target.value)}
                  />
                </label>
                <div className={styles.optionList}>
                  {visibleLabels.map((label) => (
                    <CheckboxItem
                      key={label}
                      label={label}
                      checked={filterState.labels.includes(label)}
                      onChange={(checked) => onToggleLabel(label, checked)}
                    />
                  ))}
                  {visibleLabels.length === 0 && (
                    <p className={styles.emptyMessage}>
                      {filterOptions.labels.length === 0
                        ? 'No labels are available in this data.'
                        : 'No matching labels.'}
                    </p>
                  )}
                </div>
              </div>
            )}
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              {sectionToggle('parameters', 'Parameters', activeParameterFilters.length)}
              <button
                type="button"
                className={styles.addButton}
                onClick={onAddParameterFilter}
                disabled={columns.length === 0}
              >
                + Add
              </button>
            </div>
            {openSections.parameters && (
              <div id={sectionIds.parameters} className={styles.parameterBody}>
                {filterState.parameterFilters.length === 0 ? (
                  <p className={styles.emptyMessage}>
                    Add a range to filter by a calculated test-point value.
                  </p>
                ) : (
                  <div className={styles.parameterList}>
                    {filterState.parameterFilters.map((filter, index) => (
                      <div key={filter.id} className={styles.parameterRow}>
                        <span className={styles.parameterIndex} aria-hidden="true">
                          {index + 1}
                        </span>
                        <label className={styles.parameterField}>
                          <span>Column</span>
                          <select
                            value={filter.column}
                            onChange={(event) =>
                              onUpdateParameterFilter(filter.id, 'column', event.target.value)
                            }
                          >
                            {columns.map((column) => (
                              <option key={column} value={column}>
                                {column}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className={styles.parameterField}>
                          <span>Aggregation</span>
                          <select
                            value={filter.mode}
                            onChange={(event) =>
                              onUpdateParameterFilter(filter.id, 'mode', event.target.value)
                            }
                          >
                            {AGG_MODES.map((mode) => (
                              <option key={mode.value} value={mode.value}>
                                {mode.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <NumericInput
                          label="Minimum"
                          placeholder="Min"
                          value={filter.min}
                          onCommit={(value) => onUpdateParameterFilter(filter.id, 'min', value)}
                        />
                        <NumericInput
                          label="Maximum"
                          placeholder="Max"
                          value={filter.max}
                          onCommit={(value) => onUpdateParameterFilter(filter.id, 'max', value)}
                        />
                        <button
                          type="button"
                          className={styles.removeButton}
                          onClick={() => onRemoveParameterFilter(filter.id)}
                          aria-label={`Remove parameter filter ${index + 1}`}
                          title="Remove filter"
                        >
                          ×
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
};

export const FilterControls = memo(FilterControlsComponent);
