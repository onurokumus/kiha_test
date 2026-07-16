import React, { useState, memo, useMemo, useRef, useCallback, useEffect } from 'react';
import { ScatterFilterState, FilterOptions, AggMode } from '../../types';
import { buttonStyle } from '../../constants/styles';

const AGG_MODES: { value: AggMode; label: string }[] = [
  { value: 'mean', label: 'mean' },
  { value: 'min', label: 'min' },
  { value: 'max', label: 'max' },
  { value: 'any', label: 'any sample' },
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

const searchInputStyle: React.CSSProperties = {
  background: '#3c3c3c',
  color: '#e0e0e0',
  border: '1px solid #555',
  borderRadius: 3,
  padding: '4px 6px',
  fontSize: 10,
  outline: 'none',
  width: '100%',
  marginBottom: 4,
};

const selectStyle: React.CSSProperties = {
  background: '#3c3c3c',
  color: '#e0e0e0',
  border: '1px solid #555',
  borderRadius: 3,
  padding: '3px 6px',
  fontSize: 11,
  cursor: 'pointer',
};

const linkButtonStyle: React.CSSProperties = {
  background: 'transparent',
  border: 'none',
  color: '#569cd6',
  cursor: 'pointer',
  fontSize: 9,
  padding: 0,
};

/** Compact checkbox row (indeterminate via ref, FMS-style). */
const CheckboxItem: React.FC<{
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  indent?: boolean;
  dim?: boolean;
  checkboxRef?: (el: HTMLInputElement | null) => void;
}> = ({ label, checked, onChange, indent = false, dim = false, checkboxRef }) => (
  <label
    style={{
      display: 'flex',
      alignItems: 'center',
      gap: 4,
      fontSize: 11,
      color: dim ? '#909090' : '#e0e0e0',
      cursor: 'pointer',
      padding: '2px 0',
      paddingLeft: indent ? 20 : 0,
    }}
    onClick={(e) => {
      e.preventDefault();
      onChange(!checked);
    }}
  >
    <input
      ref={checkboxRef}
      type="checkbox"
      checked={checked}
      onChange={() => {}}
      style={{ cursor: 'pointer', accentColor: '#569cd6', pointerEvents: 'none' }}
    />
    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
  </label>
);

/** Debounced numeric input for parameter ranges. */
const NumericInput: React.FC<{
  placeholder: string;
  value: number | null;
  onCommit: (value: number | null) => void;
}> = ({ placeholder, value, onCommit }) => {
  const [text, setText] = useState(value === null ? '' : String(value));
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
    <input
      type="number"
      placeholder={placeholder}
      value={text}
      step="0.01"
      onChange={(e) => {
        const raw = e.target.value;
        setText(raw);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => {
          const parsed = raw.trim() === '' ? null : Number(raw);
          onCommit(parsed === null || Number.isFinite(parsed) ? parsed : null);
        }, 600);
      }}
      onBlur={() => {
        if (timer.current) clearTimeout(timer.current);
        const parsed = text.trim() === '' ? null : Number(text);
        onCommit(parsed === null || Number.isFinite(parsed) ? parsed : null);
      }}
      style={{
        background: '#3c3c3c',
        color: '#e0e0e0',
        border: '1px solid #555',
        borderRadius: 3,
        padding: '3px 6px',
        fontSize: 11,
        width: 70,
      }}
    />
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
  const [isExpanded, setIsExpanded] = useState(false);
  const [testSearch, setTestSearch] = useState('');
  const [labelSearch, setLabelSearch] = useState('');
  const [expandedTests, setExpandedTests] = useState<Set<string>>(new Set());
  const testCheckboxRefs = useRef<Map<string, HTMLInputElement>>(new Map());

  const selectedKeys = useMemo(() => new Set(filterState.tpKeys), [filterState.tpKeys]);

  // search: a hit on the test shows all its TPs; otherwise only matching TPs
  const visibleTree = useMemo(() => {
    const search = testSearch.trim().toLowerCase();
    if (!search) return filterOptions.testTree;
    return filterOptions.testTree
      .map((node) => {
        if (node.test.toLowerCase().includes(search)) return node;
        const tps = node.tps.filter(
          (tp) =>
            tp.name.toLowerCase().includes(search) ||
            tp.label.toLowerCase().includes(search)
        );
        return { ...node, tps };
      })
      .filter((node) => node.test.toLowerCase().includes(search) || node.tps.length > 0);
  }, [filterOptions.testTree, testSearch]);

  const visibleLabels = useMemo(() => {
    const search = labelSearch.trim().toLowerCase();
    if (!search) return filterOptions.labels;
    return filterOptions.labels.filter((l) => l.toLowerCase().includes(search));
  }, [filterOptions.labels, labelSearch]);

  const getTestCheckState = useCallback(
    (test: string) => {
      const node = filterOptions.testTree.find((n) => n.test === test);
      const total = node?.tps.length ?? 0;
      const selected = node?.tps.filter((tp) => selectedKeys.has(tp.key)).length ?? 0;
      return {
        checked: total > 0 && selected === total,
        indeterminate: selected > 0 && selected < total,
      };
    },
    [filterOptions.testTree, selectedKeys]
  );

  const toggleTestExpansion = (test: string) => {
    setExpandedTests((prev) => {
      const next = new Set(prev);
      if (next.has(test)) next.delete(test);
      else next.add(test);
      return next;
    });
  };

  const activeParamCount = filterState.parameterFilters.filter(
    (f) => f.column && (f.min !== null || f.max !== null)
  ).length;

  return (
    <div
      style={{
        background: '#252526',
        border: '1px solid #3c3c3c',
        borderRadius: 4,
        padding: 8,
        marginBottom: 8,
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: isExpanded ? 8 : 0 }}>
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          style={{ ...buttonStyle, fontSize: 11, padding: '4px 8px', display: 'flex', alignItems: 'center', gap: 4 }}
        >
          <span>{isExpanded ? '▼' : '▶'}</span>
          <span>Filters</span>
        </button>
        <span style={{ fontSize: 11, color: '#909090' }}>
          {filteredCount} / {totalCount} points
        </span>
        {hasActiveFilters && (
          <span style={{ fontSize: 10, color: '#569cd6', background: '#1e3a52', padding: '2px 6px', borderRadius: 3 }}>
            Active
          </span>
        )}
        <div style={{ flex: 1 }} />
        {hasActiveFilters && (
          <button onClick={onClearFilters} style={{ ...buttonStyle, fontSize: 11, padding: '4px 8px' }}>
            Clear All
          </button>
        )}
      </div>

      {isExpanded && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {/* Categorical filters */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(2, 1fr)',
              gap: 12,
              padding: 8,
              background: '#1e1e1e',
              borderRadius: 3,
            }}
          >
            {/* Tests with nested Test Points */}
            <div style={{ display: 'flex', flexDirection: 'column', height: 200 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 11, color: '#a0a0a0', fontWeight: 600 }}>
                  Tests &amp; Test Points
                  {filterState.tpKeys.length > 0 && (
                    <span style={{ color: '#569cd6', marginLeft: 4 }}>({filterState.tpKeys.length})</span>
                  )}
                </span>
                <div style={{ display: 'flex', gap: 4 }}>
                  <button
                    onClick={() => setExpandedTests(new Set(filterOptions.testTree.map((n) => n.test)))}
                    style={linkButtonStyle}
                    title="Expand all"
                  >
                    Expand
                  </button>
                  <span style={{ color: '#555', fontSize: 9 }}>|</span>
                  <button onClick={() => setExpandedTests(new Set())} style={linkButtonStyle} title="Collapse all">
                    Collapse
                  </button>
                </div>
              </div>
              <input
                type="text"
                placeholder="Search tests/points..."
                value={testSearch}
                onChange={(e) => setTestSearch(e.target.value)}
                style={searchInputStyle}
              />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2, flex: 1, overflowY: 'auto', minHeight: 0 }}>
                {visibleTree.map((node) => {
                  const checkState = getTestCheckState(node.test);
                  return (
                    <div key={node.test}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        {node.tps.length > 0 && (
                          <button
                            onClick={() => toggleTestExpansion(node.test)}
                            style={{
                              background: 'transparent',
                              border: 'none',
                              color: '#a0a0a0',
                              cursor: 'pointer',
                              padding: 0,
                              fontSize: 10,
                              width: 12,
                            }}
                          >
                            {expandedTests.has(node.test) ? '▼' : '▶'}
                          </button>
                        )}
                        <CheckboxItem
                          label={`${node.test} (${node.tps.length})`}
                          checked={checkState.checked}
                          dim={node.tps.length === 0}
                          onChange={(checked) =>
                            onToggleTpKeys(node.tps.map((tp) => tp.key), checked)
                          }
                          checkboxRef={(el) => {
                            if (el) {
                              testCheckboxRefs.current.set(node.test, el);
                              el.indeterminate = checkState.indeterminate;
                            } else {
                              testCheckboxRefs.current.delete(node.test);
                            }
                          }}
                        />
                      </div>
                      {expandedTests.has(node.test) &&
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
                  <span style={{ fontSize: 10, color: '#666' }}>no matches</span>
                )}
              </div>
            </div>

            {/* Labels */}
            <div style={{ display: 'flex', flexDirection: 'column', height: 200 }}>
              <div style={{ display: 'flex', alignItems: 'center', marginBottom: 4, minHeight: 14 }}>
                <span style={{ fontSize: 11, color: '#a0a0a0', fontWeight: 600 }}>
                  Labels
                  {filterState.labels.length > 0 && (
                    <span style={{ color: '#569cd6', marginLeft: 4 }}>({filterState.labels.length})</span>
                  )}
                </span>
              </div>
              <input
                type="text"
                placeholder="Search labels..."
                value={labelSearch}
                onChange={(e) => setLabelSearch(e.target.value)}
                style={searchInputStyle}
              />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2, flex: 1, overflowY: 'auto', minHeight: 0 }}>
                {visibleLabels.map((label) => (
                  <CheckboxItem
                    key={label}
                    label={label}
                    checked={filterState.labels.includes(label)}
                    onChange={(checked) => onToggleLabel(label, checked)}
                  />
                ))}
                {visibleLabels.length === 0 && (
                  <span style={{ fontSize: 10, color: '#666' }}>
                    {filterOptions.labels.length === 0 ? 'no labels in data' : 'no matches'}
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Parameter filters */}
          <div style={{ padding: 8, background: '#1e1e1e', borderRadius: 3 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <span style={{ fontSize: 11, color: '#a0a0a0', fontWeight: 600 }}>
                Parameters
                {activeParamCount > 0 && (
                  <span style={{ color: '#569cd6', marginLeft: 4 }}>({activeParamCount})</span>
                )}
              </span>
              <button
                onClick={onAddParameterFilter}
                style={{ ...buttonStyle, fontSize: 11, padding: '2px 8px', background: '#569cd6', color: '#000' }}
              >
                + Add
              </button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 160, overflowY: 'auto' }}>
              {filterState.parameterFilters.length === 0 && (
                <div style={{ fontSize: 11, color: '#666', textAlign: 'center', padding: '8px 0' }}>
                  No parameter filters. Click &quot;+ Add&quot; to add one.
                </div>
              )}
              {filterState.parameterFilters.map((filter, index) => (
                <div
                  key={filter.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    padding: 6,
                    background: '#252526',
                    borderRadius: 3,
                  }}
                >
                  <span style={{ fontSize: 11, color: '#909090', minWidth: 14 }}>{index + 1}.</span>
                  <select
                    value={filter.column}
                    onChange={(e) => onUpdateParameterFilter(filter.id, 'column', e.target.value)}
                    style={{ ...selectStyle, minWidth: 120, maxWidth: 170 }}
                  >
                    {columns.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                  <select
                    value={filter.mode}
                    onChange={(e) => onUpdateParameterFilter(filter.id, 'mode', e.target.value)}
                    style={selectStyle}
                    title="aggregation over the test point"
                  >
                    {AGG_MODES.map((m) => (
                      <option key={m.value} value={m.value}>
                        {m.label}
                      </option>
                    ))}
                  </select>
                  <NumericInput
                    placeholder="Min"
                    value={filter.min}
                    onCommit={(v) => onUpdateParameterFilter(filter.id, 'min', v)}
                  />
                  <span style={{ color: '#666', fontSize: 11 }}>–</span>
                  <NumericInput
                    placeholder="Max"
                    value={filter.max}
                    onCommit={(v) => onUpdateParameterFilter(filter.id, 'max', v)}
                  />
                  <div style={{ flex: 1 }} />
                  <button
                    onClick={() => onRemoveParameterFilter(filter.id)}
                    style={{ background: 'transparent', color: '#a0a0a0', border: 'none', cursor: 'pointer', fontSize: 12 }}
                    title="Remove filter"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export const FilterControls = memo(FilterControlsComponent);
