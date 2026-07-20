import { useState, useCallback, useMemo } from 'react';
import {
  ScatterFilterState,
  FilterOptions,
  ScatterDataPoint,
  ParameterFilter,
  AggMode,
  StatsCache,
  TestTreeNode,
} from '../types';

const INITIAL_FILTER_STATE: ScatterFilterState = {
  tpKeys: [],
  labels: [],
  parameterFilters: [],
};

/** Multi-test TP filtering, FMS-style: a tests→test-points tree (selection
 *  stored per TP key; a test's checkbox is derived all/some/none), label
 *  multi-select, and per-column aggregate ranges (mean/min/max/any-sample)
 *  computed from the tp_stats cache. A parameter filter drops points whose
 *  test lacks the column; stats merely not loaded yet pass (no blanking). */
export const useScatterFilter = (
  rawData: ScatterDataPoint[],
  statsCache: StatsCache,
  columnsByTest: Record<string, string[]>
) => {
  const [filterState, setFilterState] = useState<ScatterFilterState>(INITIAL_FILTER_STATE);

  const filterOptions: FilterOptions = useMemo(() => {
    const byTest = new Map<string, TestTreeNode>();
    const labels = new Set<string>();
    rawData.forEach((point) => {
      if (point.label) labels.add(point.label);
      let node = byTest.get(point.test);
      if (!node) {
        node = { test: point.test, tps: [] };
        byTest.set(point.test, node);
      }
      node.tps.push({ key: point.id, name: point.name, label: point.label });
    });
    return {
      testTree: Array.from(byTest.values()).sort((a, b) =>
        a.test.localeCompare(b.test)
      ),
      labels: Array.from(labels).sort(),
    };
  }, [rawData]);

  /** Toggle several TP keys at once (a test parent click, shift ranges...). */
  const toggleTpKeys = useCallback((keys: string[], checked: boolean) => {
    setFilterState((prev) => {
      const set = new Set(prev.tpKeys);
      keys.forEach((k) => (checked ? set.add(k) : set.delete(k)));
      return { ...prev, tpKeys: Array.from(set) };
    });
  }, []);

  const toggleLabel = useCallback((label: string, checked: boolean) => {
    setFilterState((prev) => ({
      ...prev,
      labels: checked
        ? [...prev.labels, label]
        : prev.labels.filter((l) => l !== label),
    }));
  }, []);

  const addParameterFilter = useCallback((defaultColumn: string) => {
    setFilterState((prev) => ({
      ...prev,
      parameterFilters: [
        ...prev.parameterFilters,
        {
          id: `filter-${Date.now()}`,
          column: defaultColumn,
          mode: 'mean' as AggMode,
          min: null,
          max: null,
        },
      ],
    }));
  }, []);

  const updateParameterFilter = useCallback(
    (
      id: string,
      field: 'column' | 'mode' | 'min' | 'max',
      value: string | number | null
    ) => {
      setFilterState((prev) => ({
        ...prev,
        parameterFilters: prev.parameterFilters.map((filter) =>
          filter.id === id ? { ...filter, [field]: value } : filter
        ),
      }));
    },
    []
  );

  const removeParameterFilter = useCallback((id: string) => {
    setFilterState((prev) => ({
      ...prev,
      parameterFilters: prev.parameterFilters.filter((filter) => filter.id !== id),
    }));
  }, []);

  const clearFilters = useCallback(() => {
    setFilterState(INITIAL_FILTER_STATE);
  }, []);

  const hasActiveFilters = useMemo(() => {
    const hasParameterFilters = filterState.parameterFilters.some(
      (filter) => filter.column && (filter.min !== null || filter.max !== null)
    );
    return (
      filterState.tpKeys.length > 0 ||
      filterState.labels.length > 0 ||
      hasParameterFilters
    );
  }, [filterState]);

  /** Columns that active parameter filters need stats for. */
  const filterColumns = useMemo(
    () =>
      Array.from(
        new Set(
          filterState.parameterFilters
            .filter((f) => f.column && (f.min !== null || f.max !== null))
            .map((f) => f.column)
        )
      ),
    [filterState.parameterFilters]
  );

  const passesParameterFilter = useCallback(
    (point: ScatterDataPoint, filter: ParameterFilter): boolean => {
      const testCols = columnsByTest[point.test];
      if (testCols && !testCols.includes(filter.column)) {
        return false; // this test has no such variable at all
      }
      const stats = statsCache[point.test]?.[filter.column];
      if (!stats) return true; // stats not loaded yet — don't blank the plot
      const stat = stats[point.tp.id];
      if (!stat) return false;

      if (filter.mode === 'any') {
        // Any sample in [min, max] <=> the TP's [min, max] overlaps the range
        if (stat.min === null || stat.max === null) return false;
        const minPass = filter.min === null || stat.max >= filter.min;
        const maxPass = filter.max === null || stat.min <= filter.max;
        return minPass && maxPass;
      }

      const value = stat[filter.mode];
      if (value === null) return false; // all-NaN column in this TP
      const minPass = filter.min === null || value >= filter.min;
      const maxPass = filter.max === null || value <= filter.max;
      return minPass && maxPass;
    },
    [statsCache, columnsByTest]
  );

  const applyFilters = useCallback(
    (data: ScatterDataPoint[]): ScatterDataPoint[] => {
      if (!hasActiveFilters) return data;

      const keySet = new Set(filterState.tpKeys);
      return data.filter((point) => {
        if (keySet.size > 0 && !keySet.has(point.id)) return false;
        if (filterState.labels.length > 0) {
          if (!filterState.labels.includes(point.label)) return false;
        }
        for (const filter of filterState.parameterFilters) {
          if (!filter.column) continue;
          if (filter.min === null && filter.max === null) continue;
          if (!passesParameterFilter(point, filter)) return false;
        }
        return true;
      });
    },
    [filterState, hasActiveFilters, passesParameterFilter]
  );

  return {
    filterState,
    filterOptions,
    filterColumns,
    toggleTpKeys,
    toggleLabel,
    addParameterFilter,
    updateParameterFilter,
    removeParameterFilter,
    clearFilters,
    hasActiveFilters,
    applyFilters,
  };
};
