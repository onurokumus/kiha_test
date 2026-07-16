import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import { AppTab, Header } from './components/layout/Header';
import { AxisControls } from './components/controls/AxisControls';
import { FilterControls } from './components/controls/FilterControls';
import { SelectedPointsPanel } from './components/controls/SelectedPointsPanel';
import { MainScatterPlot } from './components/plots/MainScatterPlot';
import { TimeSeriesGrid } from './components/plots/TimeSeriesGrid';
import SplitView from './components/split/SplitView';
import EditView from './components/edit/EditView';
import { useTestPointSelection } from './hooks/useTestPointSelection';
import { useScatterFilter } from './hooks/useScatterFilter';
import { useMainPlotZoom } from './hooks/useMainPlotZoom';
import { useTimeZoom } from './hooks/useTimeZoom';
import {
  fetchTests,
  fetchMeta,
  fetchTestPoints,
  fetchTpStats,
  fetchTestPointTrace,
  uploadTest,
} from './services/api';
import { noSelect } from './constants/styles';
import {
  ScatterDataPoint,
  StatsCache,
  TestInfo,
  TestMeta,
  TestPoint,
  TpStat,
} from './types';
import './App.css';

function App() {
  const [tests, setTests] = useState<TestInfo[]>([]);
  const [currentTest, setCurrentTest] = useState<string>('');
  // Multi-test caches: the scatter shows every ready test at once (FMS-style,
  // with tests playing the role tail numbers played there).
  const [metaByTest, setMetaByTest] = useState<Record<string, TestMeta>>({});
  const [tpsByTest, setTpsByTest] = useState<Record<string, TestPoint[]>>({});
  const [statsCache, setStatsCache] = useState<StatsCache>({});
  const [loading, setLoading] = useState(true);
  const [loadingTestPointIds, setLoadingTestPointIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [xAxis, setXAxis] = useState('');
  const [yAxis, setYAxis] = useState('');
  const [expandedPlot, setExpandedPlot] = useState<number | null>(null);
  const [clusteringEnabled, setClusteringEnabled] = useState(true);
  const [isEditMode, setIsEditMode] = useState(false);
  const [plotConfigs, setPlotConfigs] = useState<string[]>([]);
  // Right-panel mode: 'tp' overlays selected TPs from t=0; 'full' browses the
  // active test with windowed pyramid reads; 'spectrum' FFT/Welch per column;
  // 'xy' scatters each column against a shared X column.
  const [viewMode, setViewMode] = useState<'tp' | 'full' | 'spectrum' | 'xy'>('tp');
  const [fullRange, setFullRange] = useState<[number, number] | null>(null);
  const [specMode, setSpecMode] = useState<'fft' | 'welch'>('fft');
  const [specLogY, setSpecLogY] = useState(false);
  // Spectrum/XY data source: selected test points, or the active test
  const [specSource, setSpecSource] = useState<'tp' | 'full'>('tp');
  const [xySource, setXYSource] = useState<'tp' | 'full'>('tp');
  const [xCol, setXCol] = useState('');
  const [tab, setTab] = useState<AppTab>('analyze');
  const [notice, setNotice] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const dragDepth = useRef(0);
  // Set while an /edit rebuild runs; the poller reloads the test when ready.
  const rebuildPending = useRef(false);

  // Dedupe guards for async fetches.
  const metaInFlight = useRef<Set<string>>(new Set());
  const statsInFlight = useRef<Set<string>>(new Set()); // `${test}|${col}`
  const tracesInFlight = useRef<Set<string>>(new Set()); // `${id}|${col}`

  const MAX_SELECTED_POINTS = 6;

  const { selectedTPs, hiddenTPs, toggleTestPoint, toggleVisibility, removeTP, clearAll, setSelectedTPs } =
    useTestPointSelection(MAX_SELECTED_POINTS);

  const { timeZoom, setTimeZoom, resetTimeZoom } = useTimeZoom();

  /** Active test's meta — split/edit tabs and full/spectrum/xy modes use it. */
  const meta = metaByTest[currentTest] ?? null;

  /** Plottable variables of the ACTIVE test (grid, split, edit). */
  const dataColumns = useMemo(
    () => (meta ? meta.columns.filter((c) => c !== meta.time_column) : []),
    [meta]
  );

  /** Union of every ready test's variables (scatter axes, filters). */
  const unionColumns = useMemo(() => {
    const set = new Set<string>();
    Object.values(metaByTest).forEach((m) => {
      m.columns.forEach((c) => {
        if (c !== m.time_column) set.add(c);
      });
    });
    return Array.from(set).sort();
  }, [metaByTest]);

  const columnsByTest = useMemo(() => {
    const out: Record<string, string[]> = {};
    Object.entries(metaByTest).forEach(([name, m]) => {
      out[name] = m.columns.filter((c) => c !== m.time_column);
    });
    return out;
  }, [metaByTest]);

  /** Drop every cache for one test (after rebuild/rename/delete/split-save). */
  const invalidateTest = useCallback(
    (name: string) => {
      setMetaByTest((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
      setTpsByTest((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
      setStatsCache((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
      setSelectedTPs((prev) => prev.filter((s) => s.test !== name));
      Array.from(statsInFlight.current)
        .filter((k) => k.startsWith(`${name}|`))
        .forEach((k) => statsInFlight.current.delete(k));
    },
    [setSelectedTPs]
  );

  // Fetch test list on mount, select the first ready test
  useEffect(() => {
    const loadTests = async () => {
      try {
        setLoading(true);
        setError(null);
        const list = await fetchTests();
        setTests(list);
        const firstReady = list.find((t) => t.status === 'ready');
        if (firstReady) setCurrentTest(firstReady.name);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch tests');
        console.error('Error fetching tests:', err);
      } finally {
        setLoading(false);
      }
    };

    loadTests();
  }, []);

  // Load meta + test points for every ready test; prune tests that are gone.
  useEffect(() => {
    const readyNames = new Set(tests.filter((t) => t.status === 'ready').map((t) => t.name));

    setMetaByTest((prev) => {
      const stale = Object.keys(prev).filter((n) => !readyNames.has(n));
      if (!stale.length) return prev;
      const next = { ...prev };
      stale.forEach((n) => delete next[n]);
      return next;
    });
    setTpsByTest((prev) => {
      const stale = Object.keys(prev).filter((n) => !readyNames.has(n));
      if (!stale.length) return prev;
      const next = { ...prev };
      stale.forEach((n) => delete next[n]);
      return next;
    });

    readyNames.forEach((name) => {
      if (metaByTest[name] || metaInFlight.current.has(name)) return;
      metaInFlight.current.add(name);
      Promise.all([fetchMeta(name), fetchTestPoints(name)])
        .then(([m, tps]) => {
          setMetaByTest((prev) => ({ ...prev, [name]: m }));
          setTpsByTest((prev) => ({ ...prev, [name]: tps.test_points }));
        })
        .catch((e) => console.error(`load failed for ${name}:`, e))
        .finally(() => metaInFlight.current.delete(name));
    });
  }, [tests, metaByTest]);

  // Defaults driven by the active test's schema (once its meta arrives)
  useEffect(() => {
    if (!meta) return;
    const cols = meta.columns.filter((c) => c !== meta.time_column);
    setXAxis((prev) => (unionColumns.includes(prev) ? prev : cols[0] || ''));
    setYAxis((prev) => (unionColumns.includes(prev) ? prev : cols[1] || cols[0] || ''));
    setXCol((prev) => (cols.includes(prev) ? prev : cols[0] || ''));
    setPlotConfigs((prev) => {
      const valid = prev.filter((c) => cols.includes(c));
      return valid.length > 0 ? valid : cols.slice(0, 9);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta]);

  // Poll the test list while any ingest or rebuild runs; auto-select the
  // first ready test if none is selected, and reload the current test when
  // its rebuild completes.
  useEffect(() => {
    const busy = tests.some((t) => t.status === 'ingesting' || t.status === 'rebuilding');
    if (!busy && !rebuildPending.current) return;
    const id = window.setInterval(async () => {
      try {
        const list = await fetchTests();
        setTests(list);
        if (!currentTest) {
          const firstReady = list.find((t) => t.status === 'ready');
          if (firstReady) setCurrentTest(firstReady.name);
          return;
        }
        const mine = list.find((t) => t.name === currentTest);
        if (rebuildPending.current && mine?.status === 'ready') {
          rebuildPending.current = false;
          invalidateTest(currentTest); // loader refetches with the new schema
          setNotice(`${currentTest}: rebuild complete`);
        }
        if (rebuildPending.current && mine?.status === 'error') {
          rebuildPending.current = false;
          setNotice(`${currentTest}: rebuild FAILED — ${mine.error ?? 'see status'}`);
        }
      } catch {
        // transient — keep polling
      }
    }, 2000);
    return () => window.clearInterval(id);
  }, [tests, currentTest, invalidateTest]);

  // Auto-clear transient notices
  useEffect(() => {
    if (!notice) return;
    const id = window.setTimeout(() => setNotice(''), 6000);
    return () => window.clearTimeout(id);
  }, [notice]);

  const handleUploadFiles = useCallback(async (files: File[]) => {
    for (const f of files) {
      if (!f.name.toLowerCase().endsWith('.csv')) {
        setNotice(`${f.name}: only .csv files can be uploaded`);
        continue;
      }
      setNotice(`uploading ${f.name}…`);
      try {
        const res = await uploadTest(f);
        setNotice(`${res.name}: ingesting…`);
      } catch (e) {
        setNotice(`${f.name}: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    try {
      setTests(await fetchTests());
    } catch {
      // list refresh failure is non-fatal; polling will catch up
    }
  }, []);

  // Switching back from the split editor: the active test's TP definitions
  // may have changed — refetch it (and its stats).
  const handleTabChange = (next: AppTab) => {
    if (next === tab) return;
    setTab(next);
    if (next === 'analyze' && currentTest) {
      invalidateTest(currentTest);
    }
  };

  // Whole-window drag-and-drop CSV upload
  const dragHandlers = {
    onDragEnter: (e: React.DragEvent) => {
      if (!e.dataTransfer.types.includes('Files')) return;
      e.preventDefault();
      dragDepth.current += 1;
      setIsDragging(true);
    },
    onDragOver: (e: React.DragEvent) => {
      if (e.dataTransfer.types.includes('Files')) e.preventDefault();
    },
    onDragLeave: () => {
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setIsDragging(false);
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      dragDepth.current = 0;
      setIsDragging(false);
      const files = Array.from(e.dataTransfer.files ?? []);
      if (files.length) handleUploadFiles(files);
    },
  };

  // Manual reload of everything shown
  const reloadData = async () => {
    try {
      setLoading(true);
      setError(null);
      Object.keys(metaByTest).forEach((name) => invalidateTest(name));
      setTests(await fetchTests());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reload');
      console.error('Error reloading:', err);
    } finally {
      setLoading(false);
    }
  };

  // Scatter x/y from per-TP aggregates (mean), across ALL ready tests
  const rawScatterData: ScatterDataPoint[] = useMemo(() => {
    if (!xAxis || !yAxis) return [];
    const selectedMap = new Map(selectedTPs.map((s) => [s.id, s]));
    const data: ScatterDataPoint[] = [];

    Object.entries(tpsByTest).forEach(([test, tps]) => {
      const xStats = statsCache[test]?.[xAxis];
      const yStats = statsCache[test]?.[yAxis];
      if (!xStats || !yStats) return;
      tps.forEach((tp) => {
        const x = xStats[tp.id]?.mean;
        const y = yStats[tp.id]?.mean;
        if (x === null || x === undefined || y === null || y === undefined) return;
        const id = `${test}:${tp.id}`;
        const sel = selectedMap.get(id);
        data.push({
          x,
          y,
          id,
          test,
          name: tp.name,
          label: tp.label,
          tp,
          color: sel?.color || '#569cd6',
          isSelected: !!sel,
        });
      });
    });

    return data;
  }, [tpsByTest, xAxis, yAxis, statsCache, selectedTPs]);

  const {
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
  } = useScatterFilter(rawScatterData, statsCache, columnsByTest);

  // Fetch tp_stats per (test, column) for scatter axes and active filters
  useEffect(() => {
    const needed = Array.from(new Set([xAxis, yAxis, ...filterColumns])).filter(Boolean);
    if (needed.length === 0) return;

    Object.entries(tpsByTest).forEach(([test, tps]) => {
      if (tps.length === 0) return;
      const testCols = columnsByTest[test] ?? [];
      needed.forEach((col) => {
        const key = `${test}|${col}`;
        if (!testCols.includes(col)) return;
        if (statsCache[test]?.[col] || statsInFlight.current.has(key)) return;
        statsInFlight.current.add(key);
        fetchTpStats(test, col)
          .then((stats) => {
            const byId: Record<number, TpStat> = {};
            stats.forEach((s) => {
              byId[s.id] = s;
            });
            setStatsCache((prev) => ({
              ...prev,
              [test]: { ...(prev[test] ?? {}), [col]: byId },
            }));
          })
          .catch((err) => {
            console.error(`tp_stats failed for ${test}/${col}:`, err);
          })
          .finally(() => {
            statsInFlight.current.delete(key);
          });
      });
    });
  }, [tpsByTest, columnsByTest, xAxis, yAxis, filterColumns, statsCache]);

  // Fetch missing traces for selected test points (columns shown in the grid,
  // restricted to what each TP's own test actually has)
  useEffect(() => {
    if (selectedTPs.length === 0) return;

    selectedTPs.forEach((s) => {
      const testCols = columnsByTest[s.test] ?? [];
      const missing = plotConfigs.filter(
        (col) =>
          testCols.includes(col) &&
          !(col in s.traces) &&
          !tracesInFlight.current.has(`${s.id}|${col}`)
      );
      if (missing.length === 0) return;

      missing.forEach((col) => tracesInFlight.current.add(`${s.id}|${col}`));
      setLoadingTestPointIds((prev) => new Set(prev).add(s.id));

      fetchTestPointTrace(s.test, s.tpId, missing)
        .then((resp) => {
          setSelectedTPs((prev) =>
            prev.map((p) =>
              p.id === s.id ? { ...p, traces: { ...p.traces, ...resp.series } } : p
            )
          );
        })
        .catch((err) => {
          console.error(`traces failed for ${s.id}:`, err);
        })
        .finally(() => {
          missing.forEach((col) => tracesInFlight.current.delete(`${s.id}|${col}`));
          setLoadingTestPointIds((prev) => {
            const next = new Set(prev);
            next.delete(s.id);
            return next;
          });
        });
    });
  }, [selectedTPs, plotConfigs, columnsByTest, setSelectedTPs]);

  // Apply filters to get final scatter data
  const scatterData = useMemo(() => {
    return applyFilters(rawScatterData);
  }, [applyFilters, rawScatterData]);

  const { mainZoom, handleMainWheel, handlePan, resetZoom } = useMainPlotZoom(scatterData);

  // The scatter is global now — switching the active test only affects the
  // per-test views (grid modes, split/edit tabs).
  const handleTestChange = (test: string) => {
    if (test === currentTest) return;
    setCurrentTest(test);
    setFullRange(null);
    setExpandedPlot(null);
  };

  // The active time zoom depends on the right-panel mode
  const activeTimeZoom = viewMode === 'tp' ? timeZoom : fullRange;
  const handleActiveTimeZoom = (domain: [number, number]) => {
    if (viewMode === 'tp') setTimeZoom(domain);
    else setFullRange(domain);
  };
  const resetActiveTimeZoom = () => {
    if (viewMode === 'tp') resetTimeZoom();
    else setFullRange(null);
  };

  const handleXAxisChange = (axis: string) => {
    setXAxis(axis);
    resetZoom();
  };

  const handleYAxisChange = (axis: string) => {
    setYAxis(axis);
    resetZoom();
  };

  const handleToggleExpand = (index: number) => {
    setExpandedPlot(expandedPlot === index ? null : index);
  };

  const handleScatterToggle = useCallback(
    (point: ScatterDataPoint) => {
      // Resolve an open-ended TP to a concrete end time (next TP start, or
      // end of data) — TP-sourced spectra/XY need a real range.
      const m = metaByTest[point.test];
      const siblings = tpsByTest[point.test] ?? [];
      const dataEnd = (m?.t_start ?? 0) + (m?.duration_s ?? 0);
      let endS = point.tp.end_s;
      if (endS === null || endS === undefined) {
        const nexts = siblings
          .filter((o) => o.id !== point.tp.id && o.start_s > point.tp.start_s)
          .map((o) => o.start_s);
        endS = nexts.length ? Math.min(...nexts) : dataEnd;
      }
      toggleTestPoint(point.test, point.tp, endS);
    },
    [toggleTestPoint, metaByTest, tpsByTest]
  );

  // -- Edit tab callbacks --
  const handleRebuildStarted = async () => {
    rebuildPending.current = true;
    try {
      setTests(await fetchTests());
    } catch {
      // poller will catch up
    }
  };

  const handleMetaSaved = () => {
    if (!currentTest) return;
    fetchMeta(currentTest)
      .then((m) => setMetaByTest((prev) => ({ ...prev, [currentTest]: m })))
      .catch(console.error);
  };

  const handleTestGone = async (newName: string) => {
    const old = currentTest;
    invalidateTest(old);
    if (newName) {
      setCurrentTest(newName);
      try {
        setTests(await fetchTests());
      } catch {
        // list refresh is cosmetic here
      }
      return;
    }
    // deleted — fall back to the first ready test
    setTab('analyze');
    try {
      const list = await fetchTests();
      setTests(list);
      const firstReady = list.find((t) => t.status === 'ready' && t.name !== old);
      setCurrentTest(firstReady?.name ?? '');
    } catch (e) {
      console.error('test list refresh failed:', e);
    }
  };

  const xLabel = xAxis ? `${xAxis} (TP mean)` : '';
  const yLabel = yAxis ? `${yAxis} (TP mean)` : '';

  const dropOverlay = isDragging && (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 2000,
        background: '#1e1e1ecc',
        border: '3px dashed #569cd6',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: '#569cd6',
        fontSize: 20,
        pointerEvents: 'none',
      }}
    >
      Drop CSV file(s) to upload
    </div>
  );

  // Loading state (before the active test's meta is available)
  if ((loading || (currentTest && !meta)) && !error) {
    if (!currentTest && !loading) {
      // fall through to the no-tests screen below
    } else {
      return (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100vh',
            background: '#1e1e1e',
            color: '#e0e0e0',
            fontFamily: 'Segoe UI, sans-serif',
            fontSize: 14,
          }}
        >
          <div style={{ textAlign: 'center' }}>
            <div style={{ marginBottom: 12, fontSize: 16 }}>Loading PTT Backend...</div>
            <div style={{ color: '#909090', fontSize: 12 }}>Fetching propeller test data</div>
          </div>
        </div>
      );
    }
  }

  // Error state
  if (error) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100vh',
          background: '#1e1e1e',
          color: '#e0e0e0',
          fontFamily: 'Segoe UI, sans-serif',
          fontSize: 14,
        }}
      >
        <div style={{ textAlign: 'center', maxWidth: 500 }}>
          <div style={{ marginBottom: 12, fontSize: 16, color: '#f48771' }}>Error Loading Data</div>
          <div style={{ color: '#909090', fontSize: 12, marginBottom: 20 }}>{error}</div>
          <div style={{ color: '#909090', fontSize: 11, lineHeight: 1.6 }}>
            <div>Make sure the backend is running:</div>
            <div style={{ marginTop: 8, background: '#252526', padding: 8, borderRadius: 4, fontFamily: 'Consolas, monospace' }}>
              backend\run_backend.bat
            </div>
          </div>
        </div>
      </div>
    );
  }

  // No tests yet — still show the header so uploads are possible
  if (!currentTest) {
    return (
      <div
        {...dragHandlers}
        style={{
          display: 'flex',
          flexDirection: 'column',
          height: '100vh',
          background: '#1e1e1e',
          color: '#e0e0e0',
          fontFamily: 'Segoe UI, sans-serif',
          fontSize: 13,
          ...noSelect,
        }}
      >
        {dropOverlay}
        <Header
          tests={tests}
          tab={tab}
          onTabChange={handleTabChange}
          onUploadFiles={handleUploadFiles}
          notice={notice}
        />
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ textAlign: 'center', maxWidth: 500 }}>
            <div style={{ marginBottom: 12, fontSize: 16 }}>No test data available</div>
            <div style={{ color: '#909090', fontSize: 12 }}>
              Upload a test CSV with the button above, or drop a .csv file anywhere in this window.
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      {...dragHandlers}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        background: '#1e1e1e',
        color: '#e0e0e0',
        fontFamily: 'Segoe UI, sans-serif',
        fontSize: 13,
        ...noSelect,
      }}
    >
      {dropOverlay}
      <Header
        tests={tests}
        tab={tab}
        onTabChange={handleTabChange}
        onUploadFiles={handleUploadFiles}
        notice={notice}
      />

      {tab === 'split' && meta ? (
        <SplitView
          test={currentTest}
          meta={meta}
          columns={dataColumns}
          tests={tests}
          onTestChange={handleTestChange}
        />
      ) : tab === 'edit' && meta ? (
        <EditView
          test={currentTest}
          meta={meta}
          tests={tests}
          onTestChange={handleTestChange}
          onRebuildStarted={handleRebuildStarted}
          onTestGone={handleTestGone}
          onMetaSaved={handleMetaSaved}
        />
      ) : (
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden', minHeight: 0 }}>
        {/* Left Panel - Main Scatter Plot (all ready tests) */}
        <div style={{ width: '40%', padding: 12, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div
            style={{
              background: '#252526',
              borderRadius: 4,
              border: '1px solid #3c3c3c',
              display: 'flex',
              flexDirection: 'column',
              flex: 1,
              minHeight: 0,
              overflow: 'hidden',
              padding: 12,
            }}
          >
            <AxisControls
              columns={unionColumns}
              xAxis={xAxis}
              yAxis={yAxis}
              onXAxisChange={handleXAxisChange}
              onYAxisChange={handleYAxisChange}
              mainZoom={mainZoom}
              onResetZoom={resetZoom}
              onReloadData={reloadData}
              isLoading={loading}
            />
            <FilterControls
              filterState={filterState}
              filterOptions={filterOptions}
              columns={unionColumns}
              onToggleTpKeys={toggleTpKeys}
              onToggleLabel={toggleLabel}
              onAddParameterFilter={() => addParameterFilter(unionColumns[0] || '')}
              onUpdateParameterFilter={updateParameterFilter}
              onRemoveParameterFilter={removeParameterFilter}
              onClearFilters={clearFilters}
              hasActiveFilters={hasActiveFilters}
              filteredCount={scatterData.length}
              totalCount={rawScatterData.length}
            />
            <div style={{ fontSize: 11, color: '#909090', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span>Click to select/deselect • Scroll to zoom • Drag to pan</span>
              {rawScatterData.length > 500 && (
                <button
                  onClick={() => setClusteringEnabled(!clusteringEnabled)}
                  style={{
                    fontSize: 10,
                    padding: '3px 8px',
                    background: clusteringEnabled ? '#1e3a52' : '#3c3c3c',
                    color: clusteringEnabled ? '#569cd6' : '#909090',
                    border: clusteringEnabled ? '1px solid #569cd6' : '1px solid #555',
                    borderRadius: 3,
                    cursor: 'pointer',
                    fontFamily: 'Segoe UI, sans-serif',
                  }}
                >
                  {clusteringEnabled ? '✓ Clustering ON' : 'Clustering OFF'}
                </button>
              )}
            </div>
            <MainScatterPlot
              scatterData={scatterData}
              rawDataCount={rawScatterData.length}
              xLabel={xLabel}
              yLabel={yLabel}
              mainZoom={mainZoom}
              onToggleTestPoint={handleScatterToggle}
              onWheel={handleMainWheel}
              onPan={handlePan}
              clusteringEnabled={clusteringEnabled}
            />
          </div>
        </div>

        {/* Right Panel - Time Series Plots */}
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            padding: '12px 12px 12px 0',
            minHeight: 0,
            overflow: 'hidden',
          }}
        >
          <SelectedPointsPanel
            selectedTPs={selectedTPs}
            hiddenTPs={hiddenTPs}
            onToggleVisibility={toggleVisibility}
            onRemoveTP={removeTP}
            onClearAll={() => {
              clearAll();
              resetTimeZoom();
            }}
            timeZoom={activeTimeZoom}
            onResetTimeZoom={resetActiveTimeZoom}
            maxPoints={MAX_SELECTED_POINTS}
            loadingTestPointIds={loadingTestPointIds}
            isEditMode={isEditMode}
            onToggleEditMode={() => setIsEditMode(!isEditMode)}
            viewMode={viewMode}
            onViewModeChange={setViewMode}
            specMode={specMode}
            onSpecModeChange={setSpecMode}
            specLogY={specLogY}
            onSpecLogYChange={setSpecLogY}
            columns={unionColumns}
            xCol={xCol}
            onXColChange={setXCol}
            tests={tests}
            currentTest={currentTest}
            onTestChange={handleTestChange}
            specSource={specSource}
            onSpecSourceChange={setSpecSource}
            xySource={xySource}
            onXYSourceChange={setXYSource}
          />
          <TimeSeriesGrid
            viewMode={viewMode}
            test={currentTest}
            fs={meta?.fs_hz ?? null}
            columns={dataColumns}
            selectedTPs={selectedTPs}
            hiddenTPs={hiddenTPs}
            expandedPlot={expandedPlot}
            onToggleExpand={handleToggleExpand}
            timeZoom={activeTimeZoom}
            onTimeZoomChange={handleActiveTimeZoom}
            onTimeZoomReset={resetActiveTimeZoom}
            specMode={specMode}
            specLogY={specLogY}
            specSource={specSource}
            xySource={xySource}
            xCol={xCol}
            columnsByTest={columnsByTest}
            isEditMode={isEditMode}
            plotConfigs={plotConfigs}
            onPlotConfigChange={setPlotConfigs}
          />
        </div>
      </div>
      )}
    </div>
  );
}

export default App;
