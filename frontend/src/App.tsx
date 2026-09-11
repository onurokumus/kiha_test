import { PlotViewports } from './utils/plotViewport';
import { SessionControls } from './components/controls/SessionControls';
import { AnalysisSession } from './services/analysisSession';
import { SessionRecovery } from './services/sessionSources';
import { CSSProperties, useState, useMemo, useEffect, useRef, useCallback } from 'react';
import { AppTab, Header } from './components/layout/Header';
import { AxisControls } from './components/controls/AxisControls';
import { FilterControls } from './components/controls/FilterControls';
import { SelectedPointsPanel } from './components/controls/SelectedPointsPanel';
import { MainScatterPlot } from './components/plots/MainScatterPlot';
import { PlotStateOverlay } from './components/plots/PlotState';
import { TimeSeriesGrid } from './components/plots/TimeSeriesGrid';
import SplitView from './components/split/SplitView';
import EditView from './components/edit/EditView';
import UploadView from './components/upload/UploadView';
import SettingsView from './components/settings/SettingsView';
import ComponentStatisticsView from './components/components/ComponentStatisticsView';
import { useConfirm } from './components/feedback/confirm';
import { useTestPointSelection } from './hooks/useTestPointSelection';
import { useScatterFilter } from './hooks/useScatterFilter';
import { useMainPlotZoom } from './hooks/useMainPlotZoom';
import { useTimeZoom } from './hooks/useTimeZoom';
import { useUploadManager } from './hooks/useUploadManager';
import { UploadDataOptions } from './services/resumableUpload';
import { useUnsavedChanges } from './hooks/useUnsavedChanges';
import { assignColor } from './utils/colorManager';
import { tpStatErrorRange } from './utils/scatterRanges';
import { AxisRange, SavedTimeYRange, timePlotContexts } from './utils/timePlotRanges';
import { MAX_SELECTED_TEST_POINTS } from './constants/selection';
import {
  fetchTests,
  fetchAnalysisSources,
  fetchMeta,
  fetchTestPoints,
  fetchTpStats,
  fetchTestPointTrace,
  fetchWindow,
  isAbortError,
  putDefaultSettings,
} from './services/api';
import { DEFAULT_FILTER_UI, FilterUi, buildFilterSpec } from './constants/filters';
import { AppSettings, loadSettings, saveSettings, parseUploadFs } from './constants/settings';
import { isBusyStatus } from './constants/status';
import {
  hasSavedAnalysisSession,
  loadAnalysisSession,
  PlotDensity,
  saveAnalysisSession,
} from './services/analysisSession';
import { AnalysisSource, captureSessionSources, referencedNames, resolveSessionSources, retainPendingSourceReferences } from './services/sessionSources';
import {
  DatasheetDataPoint,
  ScatterDataPoint,
  SelectedTestPoint,
  SpectrumXAxis,
  StatsCache,
  TestInfo,
  TestMeta,
  TestPoint,
  TpStat,
  WindowDisplayMode,
} from './types';
import './App.css';

/**
 * Best default scatter axes across a multi-test library: pick the X/Y column
 * pair that lets the MOST tests plot a point (a point needs BOTH columns), so
 * loading a narrow, oddly-named test can't hide every other dataset by default.
 * X = the column present in the most tests; Y = the column (≠X) co-occurring
 * with X in the most tests. Ties break alphabetically for a stable pick. This
 * favours cross-test visibility over guaranteeing the active test shows.
 */
function bestAxisPair(columnsByTest: Record<string, string[]>): [string, string] {
  const testCols = Object.values(columnsByTest)
    .map((c) => new Set(c))
    .filter((s) => s.size > 0);
  if (testCols.length === 0) return ['', ''];

  const testCount = new Map<string, number>();
  testCols.forEach((s) => s.forEach((c) => testCount.set(c, (testCount.get(c) ?? 0) + 1)));
  const ranked = [...testCount.keys()].sort(
    (a, b) => testCount.get(b)! - testCount.get(a)! || (a < b ? -1 : 1)
  );

  const x = ranked[0] ?? '';
  let y = '';
  let bestBoth = -1;
  for (const c of ranked) {
    // ranked is test-count-descending, so the first column reaching a given
    // co-occurrence count is also the most common one — no explicit tie-break.
    if (c === x) continue;
    const both = testCols.reduce((n, s) => n + (s.has(x) && s.has(c) ? 1 : 0), 0);
    if (both > bestBoth) {
      bestBoth = both;
      y = c;
    }
  }
  return [x, y || x];
}

/** Prefer an explicit RPM-like signal without assuming a fixed test schema.
 *  The user can override this choice from the Spectrum controls. */
function bestRpmColumn(columns: string[]): string {
  const score = (column: string) => {
    const normalized = column.toLocaleLowerCase();
    if (normalized === 'rpm') return 0;
    if (/(^|[^a-z0-9])rpm([^a-z0-9]|$)/i.test(column)) return 1;
    if (normalized.includes('rpm')) return 2;
    return 3;
  };
  return (
    columns
      .map((column, index) => ({ column, index, score: score(column) }))
      .filter((candidate) => candidate.score < 3)
      .sort((a, b) => a.score - b.score || a.index - b.index)[0]?.column ?? ''
  );
}

function App() {
  const confirmAction = useConfirm();
  const [restoredSession] = useState(loadAnalysisSession);
  const recoveryInputRef = useRef<AnalysisSession | null>(hasSavedAnalysisSession() ? restoredSession : null);
  const [sessionEpoch, setSessionEpoch] = useState(0);
  const [plotViewports, setPlotViewports] = useState<PlotViewports>(restoredSession.plotViewports);
  const [hasRestoredSession] = useState(hasSavedAnalysisSession);
  const [sessionRecoveryReady, setSessionRecoveryReady] = useState(false);
  const [recoveryMessages, setRecoveryMessages] = useState<string[]>([]);
  const [recoveryLegacy, setRecoveryLegacy] = useState(false);
  const [recoveryNeedsReview, setRecoveryNeedsReview] = useState(false);
  const [sourceCatalog, setSourceCatalog] = useState<AnalysisSource[]>([]);
  const [sourceRefreshVersion, setSourceRefreshVersion] = useState(0);
  const recoverSessionRef = useRef<(legacy?: boolean) => Promise<void>>(async () => {});
  const recoveryAttemptRef = useRef(0);
  const recoveredOrderRef = useRef<string[]>([]);
  const [tests, setTestList] = useState<TestInfo[]>([]);
  const [testListStatus, setTestListStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const setTests = useCallback((next: TestInfo[] | ((previous: TestInfo[]) => TestInfo[])) => {
    setTestList(next);
    setTestListStatus('ready');
  }, []);
  const [currentTest, setCurrentTest] = useState<string>(
    hasRestoredSession ? restoredSession.currentTest : ''
  );
  // Multi-test caches: the scatter shows every ready test at once (FMS-style,
  // with tests playing the role tail numbers played there).
  const [metaByTest, setMetaByTest] = useState<Record<string, TestMeta>>({});
  const [tpsByTest, setTpsByTest] = useState<Record<string, TestPoint[]>>({});
  const [statsCache, setStatsCache] = useState<StatsCache>({});
  const [loading, setLoading] = useState(true);
  const [loadingStats, setLoadingStats] = useState<Set<string>>(new Set());
  const [statsErrors, setStatsErrors] = useState<Record<string, string>>({});
  const [statsRetry, setStatsRetry] = useState(0);
  const [loadingTestPointIds, setLoadingTestPointIds] = useState<Set<string>>(new Set());
  const [traceErrors, setTraceErrors] = useState<Record<string, string>>({});
  const [traceRetry, setTraceRetry] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [sourceCatalogError, setSourceCatalogError] = useState<string | null>(null);
  const [metaErrors, setMetaErrors] = useState<Record<string, string>>({});
  // Saved preferences (localStorage) — seed the defaults below and feed the
  // Settings tab. Declared first: several states initialize from it.
  const [settings, setSettings] = useState<AppSettings>(loadSettings);
  // Unsaved Settings-page edits (null = none). Hoisted here so switching tabs
  // doesn't silently discard them; nothing applies until Save.
  const [settingsDraft, setSettingsDraft] = useState<AppSettings | null>(null);
  const [xAxis, setXAxis] = useState(hasRestoredSession ? restoredSession.xAxis : '');
  const [yAxis, setYAxis] = useState(hasRestoredSession ? restoredSession.yAxis : '');
  // True once the user picks a scatter axis. While false, the axes auto-default
  // to the preferred/most-shared pair (recomputed as tests load); once the
  // user chooses, their pick is kept as long as it exists in any test.
  const [axesUserSet, setAxesUserSet] = useState(hasRestoredSession && restoredSession.axesUserSet);
  const [expandedPlot, setExpandedPlot] = useState<number | null>(null);
  const [clusteringEnabled, setClusteringEnabled] = useState(settings.clustering);
  const [datasheetVisible, setDatasheetVisible] = useState(settings.datasheetVisible);
  const [showHorizontalErrorBars, setShowHorizontalErrorBars] = useState(false);
  const [showVerticalErrorBars, setShowVerticalErrorBars] = useState(false);
  const [datasheetLine, setDatasheetLine] = useState<{
    key: string;
    points: DatasheetDataPoint[];
    loading: boolean;
    error: string;
  }>({ key: '', points: [], loading: false, error: '' });
  const [isEditMode, setIsEditMode] = useState(false);
  const [plotConfigs, setPlotConfigs] = useState<string[]>(
    hasRestoredSession ? restoredSession.plotConfigs : []
  );
  // True once the user picks columns via Edit Plots. While false, the grid
  // auto-(re)seeds from the selected test points (first-selected prioritized);
  // once true, those picks are preserved and only NEW columns fill empty cells.
  const [plotsUserEdited, setPlotsUserEdited] = useState(
    hasRestoredSession && restoredSession.plotsUserEdited
  );
  // Right-panel mode: 'tp' overlays selected TPs from t=0; 'full' browses the
  // active test with windowed pyramid reads; 'spectrum' FFT/Welch per column;
  // 'xy' scatters each column against that plot's own X column.
  const [viewMode, setViewMode] = useState<'tp' | 'full' | 'spectrum' | 'xy'>(
    hasRestoredSession ? restoredSession.viewMode : settings.defaultViewMode
  );
  const [fullRange, setFullRange] = useState<[number, number] | null>(
    hasRestoredSession ? restoredSession.fullRange : null
  );
  const [fullPlotMode, setFullPlotMode] = useState<WindowDisplayMode>(
    hasRestoredSession ? restoredSession.fullPlotMode : 'auto'
  );
  const [waterfallWindow, setWaterfallWindow] = useState(restoredSession.waterfallWindow);
  const [waterfallOverlap, setWaterfallOverlap] = useState(restoredSession.waterfallOverlap);
  const [specMode, setSpecMode] = useState<'fft' | 'welch' | 'waterfall'>(
    hasRestoredSession ? restoredSession.specMode : settings.specMode
  );
  const [specXAxis, setSpecXAxis] = useState<SpectrumXAxis>(
    hasRestoredSession ? restoredSession.specXAxis : 'hz'
  );
  const [specRpmCol, setSpecRpmCol] = useState(
    hasRestoredSession ? restoredSession.specRpmCol : ''
  );
  const [specLogY, setSpecLogY] = useState(
    hasRestoredSession ? restoredSession.specLogY : settings.specLogY
  );
  // Spectrum/XY data source: selected test points, or the active test
  const [specSource, setSpecSource] = useState<'tp' | 'full'>(
    hasRestoredSession ? restoredSession.specSource : settings.specMode === 'waterfall' ? 'full' : 'tp'
  );
  // Per-plot DSP filters (TP + Full test modes), optionally overlaid on originals
  // while active. Each grid
  // cell has a ≈ button (next to expand) that opens its own filter row —
  // there is no shared/broadcast filter control.
  const [plotFilters, setPlotFilters] = useState<FilterUi[]>(() =>
    hasRestoredSession
      ? restoredSession.plotFilters
      : Array.from({ length: 9 }, () => ({ ...DEFAULT_FILTER_UI }))
  );
  // Separate display state so switching an overlay never rebuilds filter specs.
  const [annotationsVisible, setAnnotationsVisible] = useState(restoredSession.annotationsVisible);
  const [plotShowOriginal, setPlotShowOriginal] = useState<boolean[]>(
    () => restoredSession.plotShowOriginal
  );
  const [xySource, setXYSource] = useState<'tp' | 'full'>(
    hasRestoredSession ? restoredSession.xySource : 'tp'
  );
  // XY mode is per-plot on BOTH axes, index-aligned with plotConfigs: its own
  // plotted (Y) column ('' = follow the shared grid slot) and its own X. Each
  // XY cell edits both in Edit Plots mode — editing an XY Y never disturbs the
  // time/spectrum grids.
  const [xyYCols, setXYYCols] = useState<string[]>(
    hasRestoredSession ? restoredSession.xyYCols : []
  );
  const [xyXCols, setXYXCols] = useState<string[]>(
    hasRestoredSession ? restoredSession.xyXCols : []
  );
  const [tab, setTab] = useState<AppTab>('analyze');
  const [notice, setNotice] = useState('');
  const [pendingUploadFiles, setPendingUploadFiles] = useState<File[]>([]);
  const [splitDirty, setSplitDirty] = useState(false);
  const [editDirty, setEditDirty] = useState(false);
  const [splitBusy, setSplitBusy] = useState(false);
  const [editBusy, setEditBusy] = useState(false);
  useUnsavedChanges({
    isDirty: settingsDraft !== null || splitBusy || editBusy || pendingUploadFiles.length > 0,
  });
  const [scatterRatio, setScatterRatio] = useState(
    hasRestoredSession ? restoredSession.scatterRatio : 40
  );
  const [scatterCollapsed, setScatterCollapsed] = useState(
    hasRestoredSession && restoredSession.scatterCollapsed
  );
  const [plotDensity, setPlotDensity] = useState<PlotDensity>(
    hasRestoredSession ? restoredSession.plotDensity : 'nine'
  );
  const [isResizingWorkspace, setIsResizingWorkspace] = useState(false);
  const [isStackedWorkspace, setIsStackedWorkspace] = useState(
    () => window.matchMedia('(max-width: 980px)').matches
  );
  const analyzeWorkspaceRef = useRef<HTMLDivElement>(null);
  const {
    uploads,
    startUploads: handleUploadFiles,
    pauseUpload,
    resumeUpload,
    adoptServerUpload,
    cancelUpload,
    dismissUpload,
  } = useUploadManager({
    tests,
    fsHz: parseUploadFs(settings),
    onTestsChanged: setTests,
    onNotice: setNotice,
  });
  const [isDragging, setIsDragging] = useState(false);
  const dragDepth = useRef(0);
  // Set while an /edit rebuild runs; the poller reloads the test when ready.
  const rebuildPending = useRef(false);

  // Dedupe guards for async fetches.
  const metaInFlight = useRef<Set<string>>(new Set());
  const statsInFlight = useRef<Set<string>>(new Set()); // `${test}|${col}`
  const tracesInFlight = useRef<Set<string>>(new Set()); // `${id}|${col}`
  const statsRequestEpoch = useRef(0);
  const statsTestEpoch = useRef<Map<string, number>>(new Map());
  const traceRequestEpoch = useRef(0);
  const traceTestEpoch = useRef<Map<string, number>>(new Map());
  const resetMainZoomRef = useRef<() => void>(() => {});

  // Per-test generation, bumped by invalidateTest. A meta/tp_stats fetch that
  // started before an invalidation (e.g. a rebuild landing) captures the gen at
  // launch and drops its result if the gen has since changed — so a slow,
  // pre-rebuild response can never repopulate the cache with stale schema/stats
  // after invalidateTest cleared it (bug 1.11).
  const testGen = useRef<Map<string, number>>(new Map());

  // Bumped to re-run the meta loader after a transient fetch failure, so a
  // dropped request cannot wedge the app on the loading screen forever (1.9).
  const [metaRetry, setMetaRetry] = useState(0);
  const metaRetryTimer = useRef<number | null>(null);
  const scheduleMetaRetry = useCallback(() => {
    if (metaRetryTimer.current != null) return; // one pending retry at a time
    metaRetryTimer.current = window.setTimeout(() => {
      metaRetryTimer.current = null;
      setMetaRetry((n) => n + 1);
    }, 2500);
  }, []);

  const {
    selectedTPs,
    hiddenTPs,
    toggleTestPoint,
    toggleVisibility,
    removeTP,
    clearAll,
    setSelectedTPs,
    setHiddenTPs,
  } = useTestPointSelection(MAX_SELECTED_TEST_POINTS);
  const [selectionSessionHydrated, setSelectionSessionHydrated] = useState(
    !hasRestoredSession || restoredSession.selections.length === 0
  );
  const [pendingRestoredSelections, setPendingRestoredSelections] = useState(
    hasRestoredSession ? restoredSession.selections : []
  );

  const { timeZoom, setTimeZoom, resetTimeZoom } = useTimeZoom(
    hasRestoredSession ? restoredSession.timeZoom : null
  );
  const [timeYRanges, setTimeYRanges] = useState<(SavedTimeYRange | null)[]>(
    hasRestoredSession ? restoredSession.timeYRanges : []
  );
  const [timeZoomResetVersion, setTimeZoomResetVersion] = useState(0);
  const timeYContexts = useMemo(
    () => timePlotContexts(plotConfigs, selectedTPs, hiddenTPs),
    [plotConfigs, selectedTPs, hiddenTPs]
  );
  const activeTimeYRanges = timeYContexts.map((context, index) =>
    timeYRanges[index]?.context === context ? timeYRanges[index]!.range : null
  );
  const handleTimeYRangeChange = (index: number, range: AxisRange | null) => {
    setTimeYRanges((previous) => {
      const next = [...previous];
      next[index] = range ? { context: timeYContexts[index], range } : null;
      return next;
    });
  };

  /** Per-plot filter specs (null while 'none' or params incomplete). */
  const plotFilterSpecs = useMemo(() => plotFilters.map(buildFilterSpec), [plotFilters]);

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
    Object.entries(metaByTest).forEach(([name, m]) => {
      if (name === settings.datasheetZone) return;
      m.columns.forEach((c) => {
        if (c !== m.time_column) set.add(c);
      });
    });
    return Array.from(set).sort();
  }, [metaByTest, settings.datasheetZone]);

  const columnsByTest = useMemo(() => {
    const out: Record<string, string[]> = {};
    Object.entries(metaByTest).forEach(([name, m]) => {
      if (name === settings.datasheetZone) return;
      out[name] = m.columns.filter((c) => c !== m.time_column);
    });
    return out;
  }, [metaByTest, settings.datasheetZone]);

  /** XY can plot stored time on either axis. Keep its schema separate from
   *  signal-only scatter statistics, time traces and spectrum variables. */
  const xyColumnsByTest = useMemo(() => {
    const out: Record<string, string[]> = {};
    Object.entries(metaByTest).forEach(([name, m]) => {
      if (name !== settings.datasheetZone) out[name] = m.columns;
    });
    return out;
  }, [metaByTest, settings.datasheetZone]);
  const xyUnionColumns = useMemo(
    () => [...new Set(Object.values(xyColumnsByTest).flat())].sort(),
    [xyColumnsByTest]
  );
  const xyGridColumns = useMemo(() => {
    const names = xySource === 'tp' ? [...selectedTPs.map((point) => point.test), currentTest] : [currentTest];
    return [...new Set(names.flatMap((name) => xyColumnsByTest[name] ?? []))];
  }, [xySource, selectedTPs, currentTest, xyColumnsByTest]);

  /** Columns across the selected test points, ordered by selection (the
   *  first-selected TP's columns come first), deduped. Drives the grid's
   *  column priority so the right panel follows what you picked on the scatter. */
  const selectionColumns = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    selectedTPs.forEach((s) => {
      (columnsByTest[s.test] ?? []).forEach((c) => {
        if (!seen.has(c)) {
          seen.add(c);
          out.push(c);
        }
      });
    });
    return out;
  }, [selectedTPs, columnsByTest]);

  /** Views whose grid reads the SELECTED test points (rather than the single
   *  active test). Full-test / full-sourced spectrum+xy browse one test, so
   *  they keep using the active test's columns. */
  const selectionDriven =
    viewMode === 'tp' ||
    (viewMode === 'spectrum' && specSource === 'tp') ||
    (viewMode === 'xy' && xySource === 'tp');

  /** Ordered column universe for the 3x3 grid + its Edit Plots options.
   *  Selection-first (first selected TP leads), then the active test's columns
   *  as filler so the grid stays a full 3x3 whenever enough columns exist.
   *  Falls back to the active test alone when nothing is selected, or in the
   *  active-test views — so a cross-test selection can never inject a column the
   *  browsed test lacks. */
  const gridColumns = useMemo(() => {
    if (!selectionDriven || selectionColumns.length === 0) return dataColumns;
    const seen = new Set(selectionColumns);
    return [...selectionColumns, ...dataColumns.filter((c) => !seen.has(c))];
  }, [selectionDriven, selectionColumns, dataColumns]);

  /** Candidate RPM variables for the current spectrum source. Unlike the plot
   *  grid universe, this intentionally excludes active-test filler columns
   *  when spectra come from selected test points. */
  const spectrumRpmColumns = useMemo(
    () => (specSource === 'tp' && selectionColumns.length > 0 ? selectionColumns : dataColumns),
    [specSource, selectionColumns, dataColumns]
  );

  const traceColumns = useMemo(() => {
    if (viewMode !== 'tp') return [];
    if (expandedPlot !== null) {
      return plotConfigs[expandedPlot] ? [plotConfigs[expandedPlot]] : [];
    }
    const limit = plotDensity === 'single' ? 1 : plotDensity === 'quad' ? 4 : 9;
    return plotConfigs.slice(0, limit);
  }, [expandedPlot, plotConfigs, plotDensity, viewMode]);

  /** Drop every cache for one test (after rebuild/rename/delete/split-save). */
  const invalidateTest = useCallback(
    (name: string) => {
      setSourceCatalog(previous => previous.filter(source => source.name !== name));
      setSourceRefreshVersion(version => version + 1);
      // Bump the generation first so any in-flight fetch for this test drops
      // its result instead of writing pre-invalidation data back (1.11).
      testGen.current.set(name, (testGen.current.get(name) ?? 0) + 1);
      traceTestEpoch.current.set(name, (traceTestEpoch.current.get(name) ?? 0) + 1);
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
      setStatsErrors((prev) =>
        Object.fromEntries(Object.entries(prev).filter(([key]) => !key.startsWith(`${name}|`)))
      );
      setLoadingStats(
        (prev) => new Set(Array.from(prev).filter((key) => !key.startsWith(`${name}|`)))
      );
      setTraceErrors((prev) =>
        Object.fromEntries(Object.entries(prev).filter(([key]) => !key.startsWith(`${name}:`)))
      );
      setLoadingTestPointIds((prev) => {
        const next = new Set(
          Array.from(prev).filter((selectionId) => !selectionId.startsWith(`${name}:`))
        );
        return next.size === prev.size ? prev : next;
      });
      setSelectedTPs((prev) => prev.filter((s) => s.test !== name));
      setPendingRestoredSelections((prev) => prev.filter((selection) => selection.test !== name));
      if (name === currentTest) setFullRange(null);
      // Clear the in-flight guards so the loaders can immediately start a FRESH
      // fetch (the stale one, now a lower generation, will be discarded).
      metaInFlight.current.delete(name);
      Array.from(statsInFlight.current)
        .filter((k) => k.startsWith(`${name}|`))
        .forEach((k) => statsInFlight.current.delete(k));
      Array.from(tracesInFlight.current)
        .filter((k) => k.startsWith(`${name}:`))
        .forEach((k) => tracesInFlight.current.delete(k));
    },
    [currentTest, setSelectedTPs]
  );

  // Resolve persisted identities before hydrating selections or mounting plots.
  // The ref keeps bootstrap independent of the many setters declared below.
  useEffect(() => {
    void recoverSessionRef.current();
    return () => { recoveryAttemptRef.current += 1; };
  }, []);

  // Load meta + test points for every ready test; prune tests that are gone.
  useEffect(() => {
    // The test list can arrive before source identities. Do not load saved
    // names without their verified IDs, or reuse them during recovery retry.
    if (loading || !sessionRecoveryReady) return;
    const readyNames = new Set(tests.filter((t) => t.status === 'ready').map((t) => t.name));
    // Prune only tests that are GONE from the list (deleted/renamed). A test
    // that is merely non-ready (rebuilding/error) keeps its cached meta: the
    // live data is unchanged until the rebuild's atomic swap, so blanking the
    // whole UI to the loading screen mid-rebuild is wrong — rebuild-complete
    // calls invalidateTest to refetch the new schema (1.9).
    const knownNames = new Set(tests.map((t) => t.name));

    setMetaByTest((prev) => {
      const stale = Object.keys(prev).filter((n) => !knownNames.has(n));
      if (!stale.length) return prev;
      const next = { ...prev };
      stale.forEach((n) => delete next[n]);
      return next;
    });
    setTpsByTest((prev) => {
      const stale = Object.keys(prev).filter((n) => !knownNames.has(n));
      if (!stale.length) return prev;
      const next = { ...prev };
      stale.forEach((n) => delete next[n]);
      return next;
    });

    readyNames.forEach((name) => {
      if (metaByTest[name] || metaInFlight.current.has(name)) return;
      metaInFlight.current.add(name);
      const gen = testGen.current.get(name) ?? 0;
      const expectedId = sourceCatalog.find(source => source.name === name)?.id;
      Promise.all([fetchMeta(name, undefined, expectedId), fetchTestPoints(name, expectedId)])
        .then(([m, tps]) => {
          if ((testGen.current.get(name) ?? 0) !== gen) return; // invalidated mid-flight
          setMetaErrors((previous) => {
            if (!previous[name]) return previous;
            const next = { ...previous };
            delete next[name];
            return next;
          });
          setMetaByTest((prev) => ({ ...prev, [name]: m }));
          setTpsByTest((prev) => ({ ...prev, [name]: tps.test_points }));
        })
        .catch((e) => {
          if ((testGen.current.get(name) ?? 0) !== gen) return;
          setMetaErrors((previous) => ({
            ...previous,
            [name]: e instanceof Error ? e.message : 'Test data could not be loaded',
          }));
          console.error(`load failed for ${name}:`, e);
          // Retry transient failures: without this the effect never re-runs
          // (tests/metaByTest unchanged) and the app stays on the loading
          // screen forever with no error and no way to reach Uploads (1.9).
          scheduleMetaRetry();
        })
        .finally(() => metaInFlight.current.delete(name));
    });
  }, [tests, metaByTest, metaRetry, scheduleMetaRetry, sourceCatalog, loading, sessionRecoveryReady]);

  // Rebuild lightweight saved selections as each referenced test becomes
  // available. Busy tests stay pending without blocking persistence for the
  // rest of the workspace, and restored points merge with any selections the
  // user made while metadata was loading.
  useEffect(() => {
    if (loading || !sessionRecoveryReady) return;

    const restored: SelectedTestPoint[] = [];
    const hidden = new Set<string>();
    const stillPending: typeof pendingRestoredSelections = [];
    const selectedIds = new Set(selectedTPs.map((selection) => selection.id));
    let remainingSlots = Math.max(0, MAX_SELECTED_TEST_POINTS - selectedTPs.length);

    pendingRestoredSelections.forEach(({ test, tpId, hidden: wasHidden, color }) => {
      const info = tests.find((candidate) => candidate.name === test);
      if (!info) return; // deleted since the session was saved
      if (info.status !== 'ready') {
        stillPending.push({ test, tpId, hidden: wasHidden, color });
        return;
      }
      if (!(test in metaByTest) || !(test in tpsByTest)) {
        stillPending.push({ test, tpId, hidden: wasHidden, color });
        return;
      }
      const point = tpsByTest[test]?.find((candidate) => candidate.id === tpId);
      const testMeta = metaByTest[test];
      if (!point || !testMeta) return;
      const siblings = tpsByTest[test] ?? [];
      const dataEnd = (testMeta.t_start ?? 0) + (testMeta.duration_s ?? 0);
      const nextStart = siblings
        .filter((candidate) => candidate.id !== point.id && candidate.start_s > point.start_s)
        .reduce<
          number | null
        >((nearest, candidate) => (nearest === null || candidate.start_s < nearest ? candidate.start_s : nearest), null);
      const id = `${test}:${point.id}`;
      if (selectedIds.has(id)) {
        return;
      }
      if (remainingSlots === 0) {
        stillPending.push({ test, tpId, hidden: wasHidden, color });
        return;
      }
      restored.push({
        id,
        test,
        tpId: point.id,
        name: point.name,
        label: point.label,
        color: color ?? assignColor([...selectedTPs, ...restored]),
        tp: point,
        endS: point.end_s ?? nextStart ?? dataEnd,
        traces: {},
      });
      selectedIds.add(id);
      remainingSlots -= 1;
      if (wasHidden) hidden.add(id);
    });
    if (restored.length > 0) {
      const rank = new Map(recoveredOrderRef.current.map((id, index) => [id, index]));
      setSelectedTPs((current) => [...current, ...restored].sort((a, b) =>
        (rank.get(a.id) ?? MAX_SELECTED_TEST_POINTS) - (rank.get(b.id) ?? MAX_SELECTED_TEST_POINTS)));
    }
    if (hidden.size > 0) {
      setHiddenTPs((current) => new Set([...current, ...hidden]));
    }
    const pendingKey = pendingRestoredSelections
      .map(({ test, tpId, hidden: isHidden }) => `${test}:${tpId}:${isHidden}`)
      .join('|');
    const nextPendingKey = stillPending
      .map(({ test, tpId, hidden: isHidden }) => `${test}:${tpId}:${isHidden}`)
      .join('|');
    if (pendingKey !== nextPendingKey) setPendingRestoredSelections(stillPending);
    setSelectionSessionHydrated(true);
  }, [
    loading,
    sessionRecoveryReady,
    metaByTest,
    pendingRestoredSelections,
    selectedTPs,
    setHiddenTPs,
    setSelectedTPs,
    tests,
    tpsByTest,
  ]);

  // If the active test vanishes (deleted/renamed from another window), fall
  // back to a ready test instead of wedging on the loading screen with a
  // dangling currentTest whose meta was just pruned (1.9). A rebuild keeps the
  // test in the list, so this only fires on a true delete/rename.
  useEffect(() => {
    if (!currentTest || !tests.length) return;
    if (tests.some((t) => t.name === currentTest)) return;
    const firstReady = tests.find((t) => t.status === 'ready');
    setFullRange(null);
    setCurrentTest(firstReady ? firstReady.name : '');
  }, [tests, currentTest]);

  /** X/Y pair that maximizes how many tests can plot a point (see bestAxisPair).
   *  Drives the scatter defaults so a narrow test can't hide the rest. */
  const [defaultXAxis, defaultYAxis] = useMemo(() => bestAxisPair(columnsByTest), [columnsByTest]);

  // Scatter axis defaults across the WHOLE library (not just the active test).
  // Precedence: in-session pick (axesUserSet) > saved preference (while its
  // column is loaded) > most-shared pair. Recomputed as tests load.
  useEffect(() => {
    if (!sessionRecoveryReady || !defaultXAxis) return;
    // A delayed schema must not overwrite a saved axis before its source loads.
    if (axesUserSet && [xAxis, yAxis].some(axis => !unionColumns.includes(axis) && sourceCatalog.some(source =>
      source.status === 'ready' && source.columns?.includes(axis) && !metaByTest[source.name]))) return;
    const prefX =
      settings.scatterX && unionColumns.includes(settings.scatterX) ? settings.scatterX : '';
    const prefY =
      settings.scatterY && unionColumns.includes(settings.scatterY) ? settings.scatterY : '';
    const nextX = !axesUserSet
      ? prefX || defaultXAxis
      : unionColumns.includes(xAxis)
        ? xAxis
        : prefX || defaultXAxis;
    const nextY = !axesUserSet
      ? prefY || defaultYAxis
      : unionColumns.includes(yAxis)
        ? yAxis
        : prefY || defaultYAxis;
    if (nextX !== xAxis || nextY !== yAxis) resetMainZoomRef.current();
    if (!axesUserSet) {
      setXAxis(nextX);
      setYAxis(nextY);
      return;
    }
    setXAxis(nextX);
    setYAxis(nextY);
  }, [
    defaultXAxis,
    defaultYAxis,
    axesUserSet,
    unionColumns,
    settings.scatterX,
    settings.scatterY,
    xAxis,
    yAxis,
    sessionRecoveryReady, sourceCatalog, metaByTest,
  ]);

  // Seed the 3x3 grid from the ordered gridColumns universe (selection-first).
  // Precedence per slot: in-session Edit Plots pick (plotsUserEdited) > saved
  // slot preference (while its column is loaded) > auto fill from gridColumns
  // (first selected TP's columns lead, then the active test). Never collapses
  // below the columns actually available.
  useEffect(() => {
    if (loading || !sessionRecoveryReady || gridColumns.length === 0) return;
    // A partial metadata universe must not compact restored positional slots:
    // moving a column here would attach the following slot's saved filter to it.
    // Wait only for ready tests that can contribute to this grid, not unrelated
    // library tests or busy/deleted pending selections.
    const readyNames = new Set(tests.filter((test) => test.status === 'ready').map((test) => test.name));
    if (readyNames.has(currentTest) && !metaByTest[currentTest]) return;
    if (selectionDriven) {
      if (selectedTPs.some((selection) => readyNames.has(selection.test) && !metaByTest[selection.test])) return;
      const selectedIds = new Set(selectedTPs.map((selection) => selection.id));
      // Metadata arriving and the restore effect adding its TPs are separate
      // renders. Do not normalize against the earlier, still-partial selection.
      if (selectedTPs.length < MAX_SELECTED_TEST_POINTS && pendingRestoredSelections.some(
        ({ test, tpId }) => readyNames.has(test) && !selectedIds.has(`${test}:${tpId}`)
      )) return;
    }
    setPlotConfigs((prev) => {
      if (plotsUserEdited) {
        // Slot filters/overlays/Y ranges belong to these exact variables. Keep
        // an unavailable column in place rather than shifting its neighbours.
        const filler = gridColumns.filter((c) => !prev.includes(c));
        return [...prev, ...filler].slice(0, 9);
      }
      // Slot preferences are positional: cell i shows its preferred column when
      // loaded; unset/unavailable slots fill from gridColumns in order.
      const slots = Array.from({ length: 9 }, (_, i) => {
        const c = settings.gridColumns[i] ?? '';
        return c && gridColumns.includes(c) ? c : '';
      });
      const used = new Set(slots.filter(Boolean));
      const filler = gridColumns.filter((c) => !used.has(c));
      let fi = 0;
      return slots.map((c) => c || (fi < filler.length ? filler[fi++] : '')).filter(Boolean);
    });
    // XY Y/X columns are per-slot too; a session pick (still-valid prev) wins.
    // Y falls back to '' = follow the shared grid slot for that cell.
    setXYYCols((prev) =>
      Array.from({ length: 9 }, (_, i) => {
        if (prev[i]) return prev[i];
        const pref = settings.xyYCols[i] ?? '';
        return pref && xyGridColumns.includes(pref) ? pref : '';
      })
    );
    setXYXCols((prev) =>
      Array.from({ length: 9 }, (_, i) => {
        if (prev[i]) return prev[i];
        const pref = settings.xyXCols[i] ?? '';
        return pref && xyGridColumns.includes(pref) ? pref : gridColumns[0] || '';
      })
    );
  }, [gridColumns, xyGridColumns, plotsUserEdited, settings.gridColumns, settings.xyYCols, settings.xyXCols,
    loading, sessionRecoveryReady, tests, currentTest, metaByTest, selectionDriven, selectedTPs, pendingRestoredSelections]);

  // Keep an explicit RPM reference for per-revolution spectra. Exact/case-only
  // matches survive test switches; otherwise choose the strongest RPM-like
  // column in the currently relevant schema and leave it blank if none exists.
  useEffect(() => {
    setSpecRpmCol((previous) => {
      const matching = spectrumRpmColumns.find(
        (column) => column.toLocaleLowerCase() === previous.toLocaleLowerCase()
      );
      return matching ?? (previous || bestRpmColumn(spectrumRpmColumns));
    });
  }, [spectrumRpmColumns]);

  // Poll the test list while any ingest or rebuild runs, or while the
  // Uploads page is open (its whole point is live status); auto-select the
  // first ready test if none is selected, and reload the current test when
  // its rebuild completes.
  const uploadsActive = uploads.some((u) => u.phase !== 'paused' && u.phase !== 'error');
  useEffect(() => {
    const busy = tests.some((t) => isBusyStatus(t.status));
    // uploadsActive: while a body is still streaming up, the new test only
    // exists server-side as status 'receiving' — poll so it appears in the
    // list without waiting for the POST to resolve.
    if (!busy && !uploadsActive && !rebuildPending.current && tab !== 'uploads') return;
    const id = window.setInterval(async () => {
      try {
        const list = await fetchTests();
        setTests(list);
        if (!currentTest && sessionRecoveryReady && !recoveryNeedsReview && !recoveryLegacy) {
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
    // `tab` is read in the bail-out above: without it here, opening the Uploads
    // tab would not (re)start polling unless some other dep also changed (1.17).
  }, [tests, uploadsActive, currentTest, invalidateTest, tab, sessionRecoveryReady, recoveryNeedsReview, recoveryLegacy, setTests]);

  // Auto-clear transient notices
  useEffect(() => {
    if (!notice) return;
    const id = window.setTimeout(() => setNotice(''), 6000);
    return () => window.clearTimeout(id);
  }, [notice]);

  // Settings page SAVE: persist the draft and apply exactly the fields that
  // changed (an untouched field must not disturb the session — e.g. re-saving
  // must not yank the user back to the default view mode). Column-preference
  // changes clear the matching in-session override flag so the seeding effects
  // re-run with the new preference; mode/toggle defaults apply directly.
  const handleSettingsSave = useCallback(
    (next: AppSettings) => {
      const prev = settings;
      saveSettings(next);
      setSettings(next);
      setSettingsDraft(null);
      if (next.scatterX !== prev.scatterX || next.scatterY !== prev.scatterY) {
        setAxesUserSet(false);
      }
      if (next.gridColumns.join('\0') !== prev.gridColumns.join('\0')) {
        setPlotsUserEdited(false);
      }
      if (next.xyYCols.join('\0') !== prev.xyYCols.join('\0')) {
        setXYYCols([]); // reseed all 9 from the new per-slot prefs
      }
      if (next.xyXCols.join('\0') !== prev.xyXCols.join('\0')) {
        setXYXCols([]);
      }
      if (next.defaultViewMode !== prev.defaultViewMode) setViewMode(next.defaultViewMode);
      if (next.specMode !== prev.specMode) {
        setSpecMode(next.specMode);
        if (next.specMode === 'waterfall') setSpecSource('full');
      }
      if (next.specLogY !== prev.specLogY) setSpecLogY(next.specLogY);
      if (next.clustering !== prev.clustering) setClusteringEnabled(next.clustering);
      if (next.datasheetZone !== prev.datasheetZone) {
        setDatasheetVisible(next.datasheetVisible);
        resetMainZoomRef.current();
      } else if (next.datasheetVisible !== prev.datasheetVisible) {
        setDatasheetVisible(next.datasheetVisible);
      }
    },
    [settings]
  );

  const handleMakeSettingsDefault = useCallback(
    async (next: AppSettings) => {
      await putDefaultSettings(next);
      // Publishing the displayed draft should leave this browser showing and
      // using the same values it just made the shared baseline.
      handleSettingsSave(next);
    },
    [handleSettingsSave]
  );

  // Switching back from the split editor: the active test's TP definitions
  // may have changed — refetch it (and its stats).
  const confirmDiscardActiveDraft = useCallback(async (): Promise<boolean> => {
    const busy = tab === 'split' ? splitBusy : tab === 'edit' ? editBusy : false;
    if (busy) {
      await confirmAction({
        title: tab === 'split' ? 'Save still in progress' : 'Data change still in progress',
        description:
          tab === 'split'
            ? 'Test-point changes are still being saved. Wait for the save to finish before leaving Split.'
            : 'A data change is still being submitted. Wait for it to finish before leaving Edit.',
        confirmLabel: 'Got it',
        showCancel: false,
      });
      return false;
    }
    const dirty =
      tab === 'split'
        ? splitDirty
        : tab === 'edit'
          ? editDirty
          : tab === 'uploads'
            ? pendingUploadFiles.length > 0
            : false;
    if (!dirty) return true;
    const label =
      tab === 'split'
        ? 'test-point changes'
        : tab === 'edit'
          ? 'data-edit changes'
          : 'CSV import setup';
    const discard = await confirmAction({
      title: `Discard unsaved ${label}?`,
      description: 'This draft will be cleared before you leave the current page.',
      detail: 'Your saved test data will not be changed.',
      confirmLabel: 'Discard draft',
      tone: 'warning',
    });
    if (discard) {
      if (tab === 'split') setSplitDirty(false);
      if (tab === 'edit') setEditDirty(false);
      if (tab === 'uploads') setPendingUploadFiles([]);
    }
    return discard;
  }, [confirmAction, editBusy, editDirty, pendingUploadFiles.length, splitBusy, splitDirty, tab]);

  const handleTabChange = async (next: AppTab): Promise<boolean> => {
    if (next === tab) return true;
    if (!(await confirmDiscardActiveDraft())) return false;
    setTab(next);
    if (next === 'uploads') {
      // Fresh history immediately; the 2 s poller takes over while open.
      fetchTests()
        .then(setTests)
        .catch(() => {});
    }
    return true;
  };

  // -- Uploads tab callbacks --
  const handleOpenTest = async (name: string, destination: 'analyze' | 'edit' = 'analyze') => {
    if (await handleTestChange(name)) setTab(destination);
  };

  const handleTestDeleted = async (name: string) => {
    // Refresh the list BEFORE pruning caches (batched into one render):
    // dropping metaByTest while `tests` still lists the deleted test makes
    // the meta loader refetch it and 404.
    try {
      const list = await fetchTests();
      setTests(list);
      invalidateTest(name);
      if (currentTest === name) {
        const firstReady = list.find((t) => t.status === 'ready');
        setCurrentTest(firstReady?.name ?? '');
      }
    } catch {
      invalidateTest(name); // poller will fix the list
    }
  };

  const handleTestsChanged = async (restoredName?: string) => {
    try {
      setTests(await fetchTests());
      if (restoredName) invalidateTest(restoredName);
    } catch {
      // poller will catch up
    }
  };

  // Uploads page recomputed a test's TP averages: drop only the stats cache
  // for that test (NOT selection/meta/TPs) so the scatter refetches the fresh
  // numbers on its next render.
  const handleStatsRebuilt = useCallback((name: string) => {
    statsTestEpoch.current.set(name, (statsTestEpoch.current.get(name) ?? 0) + 1);
    setStatsCache((prev) => {
      if (!(name in prev)) return prev;
      const next = { ...prev };
      delete next[name];
      return next;
    });
    Array.from(statsInFlight.current)
      .filter((k) => k.startsWith(`${name}|`))
      .forEach((k) => statsInFlight.current.delete(k));
    setStatsErrors((prev) =>
      Object.fromEntries(Object.entries(prev).filter(([key]) => !key.startsWith(`${name}|`)))
    );
    setLoadingStats((prev) => {
      const next = new Set(Array.from(prev).filter((key) => !key.startsWith(`${name}|`)));
      return next.size === prev.size ? prev : next;
    });
  }, []);

  const handleTestPointsSaved = useCallback(
    (name: string) => {
      const generation = (testGen.current.get(name) ?? 0) + 1;
      testGen.current.set(name, generation);
      resetTimeZoom();
      traceTestEpoch.current.set(name, (traceTestEpoch.current.get(name) ?? 0) + 1);
      handleStatsRebuilt(name);
      setSelectedTPs((prev) => prev.filter((selection) => selection.test !== name));
      setPendingRestoredSelections((prev) => prev.filter((selection) => selection.test !== name));
      setHiddenTPs((prev) => {
        const next = new Set(
          Array.from(prev).filter((selectionId) => !selectionId.startsWith(`${name}:`))
        );
        return next.size === prev.size ? prev : next;
      });
      setTraceErrors((prev) =>
        Object.fromEntries(Object.entries(prev).filter(([key]) => !key.startsWith(`${name}:`)))
      );
      setLoadingTestPointIds((prev) => {
        const next = new Set(
          Array.from(prev).filter((selectionId) => !selectionId.startsWith(`${name}:`))
        );
        return next.size === prev.size ? prev : next;
      });
      Array.from(tracesInFlight.current)
        .filter((key) => key.startsWith(`${name}:`))
        .forEach((key) => tracesInFlight.current.delete(key));
      fetchTestPoints(name)
        .then((file) => {
          if ((testGen.current.get(name) ?? 0) !== generation) return;
          setTpsByTest((prev) => ({ ...prev, [name]: file.test_points }));
          setSourceCatalog(previous => previous.filter(source => source.name !== name));
          setSourceRefreshVersion(version => version + 1);
          setNotice(`${name}: test points refreshed`);
        })
        .catch((refreshError) => {
          console.error(`test-point refresh failed for ${name}:`, refreshError);
          setNotice(`${name}: saved; refresh the analysis to load the new points`);
        });
    },
    [handleStatsRebuilt, resetTimeZoom, setHiddenTPs, setSelectedTPs]
  );

  const stageUploadFiles = async (files: File[]) => {
    const csvFiles = files.filter((file) => file.name.toLowerCase().endsWith('.csv'));
    if (csvFiles.length === 0) {
      setNotice('Choose one or more .csv files to import');
      return;
    }
    if (!(await handleTabChange('uploads'))) return;
    setPendingUploadFiles(csvFiles);
  };

  const startConfiguredUploads = (files: File[], options: UploadDataOptions) => {
    setPendingUploadFiles([]);
    void handleUploadFiles(files, options);
  };

  // Whole-window drag-and-drop routes through the same import setup as the
  // Uploads-page picker, so no path silently bypasses time/Hz choices.
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
      if (files.length) stageUploadFiles(files);
    },
  };

  // Manual reload of everything shown
  const reloadData = async () => {
    if (!sessionRecoveryReady) { await recoverSessionRef.current(); return; }
    try {
      setLoading(true);
      setError(null);
      // Keep the current workspace intact until the catalog request succeeds.
      const list = await fetchTests();
      Object.keys(metaByTest).forEach((name) => invalidateTest(name));
      setTests(list);
      if (!list.some((test) => test.name === currentTest && test.status === 'ready')) {
        setFullRange(null);
        setCurrentTest(list.find((test) => test.status === 'ready')?.name ?? '');
      }
    } catch (err) {
      setTestListStatus('error');
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
      if (test === settings.datasheetZone) return;
      const xStats = statsCache[test]?.[xAxis];
      const yStats = statsCache[test]?.[yAxis];
      if (!xStats || !yStats) return;
      tps.forEach((tp) => {
        const xStat = xStats[tp.id];
        const yStat = yStats[tp.id];
        const x = xStat?.mean;
        const y = yStat?.mean;
        if (
          typeof x !== 'number' ||
          !Number.isFinite(x) ||
          typeof y !== 'number' ||
          !Number.isFinite(y)
        ) {
          return;
        }
        const id = `${test}:${tp.id}`;
        const sel = selectedMap.get(id);
        const xError = tpStatErrorRange(xStat);
        const yError = tpStatErrorRange(yStat);
        data.push({
          x,
          y,
          ...(xError ? { xError } : {}),
          ...(yError ? { yError } : {}),
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
  }, [tpsByTest, xAxis, yAxis, statsCache, selectedTPs, settings.datasheetZone]);

  const {
    filterState,
    setFilterState,
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
  } = useScatterFilter(
    rawScatterData,
    statsCache,
    columnsByTest,
    hasRestoredSession ? restoredSession.filterState : undefined
  );

  // Share exact original TP statistics across scatter, filters and visible
  // time plots. Additional plot columns are scanned only for selected tests.
  useEffect(() => {
    const common = [xAxis, yAxis, ...filterColumns];
    const selectedTests = new Set(selectedTPs.filter((s) => !hiddenTPs.has(s.id)).map((s) => s.test));

    Object.entries(tpsByTest).forEach(([test, tps]) => {
      if (tps.length === 0) return;
      const testCols = columnsByTest[test] ?? [];
      const needed = Array.from(new Set([
        ...common, ...(selectedTests.has(test) ? traceColumns : []),
      ])).filter(Boolean);
      needed.forEach((col) => {
        const key = `${test}|${col}`;
        if (!testCols.includes(col)) return;
        if (statsCache[test]?.[col] || statsInFlight.current.has(key) || statsErrors[key]) return;
        statsInFlight.current.add(key);
        setLoadingStats((prev) => new Set(prev).add(key));
        setStatsErrors((prev) => {
          if (!(key in prev)) return prev;
          const next = { ...prev };
          delete next[key];
          return next;
        });
        const gen = testGen.current.get(test) ?? 0;
        const requestEpoch = statsRequestEpoch.current;
        const testEpoch = statsTestEpoch.current.get(test) ?? 0;
        const requestIsCurrent = () =>
          (testGen.current.get(test) ?? 0) === gen &&
          statsRequestEpoch.current === requestEpoch &&
          (statsTestEpoch.current.get(test) ?? 0) === testEpoch;
        fetchTpStats(test, col)
          .then((stats) => {
            if (!requestIsCurrent()) return;
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
            if (!requestIsCurrent()) return;
            console.error(`tp_stats failed for ${test}/${col}:`, err);
            setStatsErrors((prev) => ({
              ...prev,
              [key]: err instanceof Error ? err.message : String(err),
            }));
          })
          .finally(() => {
            if (!requestIsCurrent()) return;
            statsInFlight.current.delete(key);
            setLoadingStats((prev) => {
              const next = new Set(prev);
              next.delete(key);
              return next;
            });
          });
      });
    });
  }, [tpsByTest, columnsByTest, xAxis, yAxis, filterColumns, statsCache, statsRetry, statsErrors, selectedTPs, hiddenTPs, traceColumns]);

  // Fetch missing traces for selected test points (columns shown in the grid,
  // restricted to what each TP's own test actually has)
  useEffect(() => {
    if (selectedTPs.length === 0) return;

    selectedTPs.forEach((s) => {
      const testCols = columnsByTest[s.test] ?? [];
      const missing = traceColumns.filter(
        (col) =>
          testCols.includes(col) &&
          !(col in s.traces) &&
          !tracesInFlight.current.has(`${s.id}|${col}`)
      );
      if (missing.length === 0) return;

      missing.forEach((col) => tracesInFlight.current.add(`${s.id}|${col}`));
      setTraceErrors((prev) => {
        const next = { ...prev };
        missing.forEach((col) => delete next[`${s.id}|${col}`]);
        return next;
      });
      setLoadingTestPointIds((prev) => new Set(prev).add(s.id));
      const generation = testGen.current.get(s.test) ?? 0;
      const requestEpoch = traceRequestEpoch.current;
      const testEpoch = traceTestEpoch.current.get(s.test) ?? 0;
      const requestIsCurrent = () =>
        (testGen.current.get(s.test) ?? 0) === generation &&
        traceRequestEpoch.current === requestEpoch &&
        (traceTestEpoch.current.get(s.test) ?? 0) === testEpoch;

      fetchTestPointTrace(s.test, s.tpId, missing)
        .then((resp) => {
          if (!requestIsCurrent()) return;
          setTraceErrors((prev) => {
            const next = { ...prev };
            missing.forEach((col) => delete next[`${s.id}|${col}`]);
            return next;
          });
          setSelectedTPs((prev) =>
            prev.map((p) => (p.id === s.id ? { ...p, traces: { ...p.traces, ...resp.series } } : p))
          );
        })
        .catch((err) => {
          if (!requestIsCurrent()) return;
          console.error(`traces failed for ${s.id}:`, err);
          const message = err instanceof Error ? err.message : String(err);
          setTraceErrors((prev) => {
            const next = { ...prev };
            missing.forEach((col) => {
              next[`${s.id}|${col}`] = message;
            });
            return next;
          });
        })
        .finally(() => {
          if (!requestIsCurrent()) return;
          missing.forEach((col) => tracesInFlight.current.delete(`${s.id}|${col}`));
          setLoadingTestPointIds((prev) => {
            const next = new Set(prev);
            next.delete(s.id);
            return next;
          });
        });
    });
  }, [selectedTPs, traceColumns, columnsByTest, setSelectedTPs, traceRetry]);

  const retryTestPointTraces = useCallback(() => {
    traceRequestEpoch.current += 1;
    tracesInFlight.current.clear();
    setLoadingTestPointIds(new Set());
    setTraceErrors({});
    setTraceRetry((attempt) => attempt + 1);
  }, []);

  // Apply filters to get final scatter data
  const scatterData = useMemo(() => {
    return applyFilters(rawScatterData);
  }, [applyFilters, rawScatterData]);

  const datasheetZone = settings.datasheetZone;
  const datasheetMeta = metaByTest[datasheetZone] ?? null;
  const datasheetRequestKey = `${datasheetZone}\0${xAxis}\0${yAxis}`;
  const datasheetHasMatchingAxes = Boolean(
    datasheetMeta &&
    xAxis &&
    yAxis &&
    datasheetMeta.columns.includes(xAxis) &&
    datasheetMeta.columns.includes(yAxis) &&
    xAxis !== datasheetMeta.time_column &&
    yAxis !== datasheetMeta.time_column
  );

  // A datasheet is uploaded through the normal CSV path, but its rows are read
  // directly rather than aggregated through test-point ranges. The ingested
  // time coordinate preserves row order and, for a measured point-ID column,
  // source_time_origin_s reconstructs the original IDs for the tooltip.
  useEffect(() => {
    if (!datasheetVisible || !datasheetZone || !xAxis || !yAxis) {
      setDatasheetLine({ key: '', points: [], loading: false, error: '' });
      return;
    }
    if (!datasheetMeta || !datasheetHasMatchingAxes) {
      setDatasheetLine({
        key: datasheetRequestKey,
        points: [],
        loading: false,
        error: '',
      });
      return;
    }

    const controller = new AbortController();
    let active = true;
    setDatasheetLine({
      key: datasheetRequestKey,
      points: [],
      loading: true,
      error: '',
    });
    fetchWindow(datasheetZone, [xAxis, yAxis], null, null, 4000, controller.signal, 'line')
      .then((response) => {
        if (!active) return;
        if (response.mode !== 'raw') {
          throw new Error('datasheet rows were not returned as a line');
        }
        const xValues = response.series[xAxis] ?? [];
        const yValues = response.series[yAxis] ?? [];
        const pointIdOrigin =
          datasheetMeta.time_source === 'measured' ? (datasheetMeta.source_time_origin_s ?? 0) : 0;
        const points: DatasheetDataPoint[] = [];
        const rowCount = Math.min(response.t.length, xValues.length, yValues.length);
        for (let index = 0; index < rowCount; index += 1) {
          const x = xValues[index];
          const y = yValues[index];
          if (
            typeof x !== 'number' ||
            !Number.isFinite(x) ||
            typeof y !== 'number' ||
            !Number.isFinite(y)
          ) {
            continue;
          }
          const rawPointId = response.t[index];
          const pointId =
            datasheetMeta.time_source === 'generated' || typeof rawPointId !== 'number'
              ? response.i0 + index + 1
              : rawPointId + pointIdOrigin;
          points.push({
            x,
            y,
            id: `datasheet:${datasheetZone}:${response.i0 + index}`,
            pointId,
            zone: datasheetZone,
            isDatasheet: true,
          });
        }
        points.sort((a, b) => a.pointId - b.pointId);
        setDatasheetLine({
          key: datasheetRequestKey,
          points,
          loading: false,
          error: '',
        });
      })
      .catch((requestError) => {
        if (!active || isAbortError(requestError)) return;
        setDatasheetLine({
          key: datasheetRequestKey,
          points: [],
          loading: false,
          error: requestError instanceof Error ? requestError.message : String(requestError),
        });
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [
    datasheetHasMatchingAxes,
    datasheetMeta,
    datasheetRequestKey,
    datasheetVisible,
    datasheetZone,
    xAxis,
    yAxis,
  ]);

  const datasheetData = useMemo(
    () =>
      datasheetVisible && datasheetLine.key === datasheetRequestKey ? datasheetLine.points : [],
    [datasheetLine, datasheetRequestKey, datasheetVisible]
  );
  const scatterDomainData = useMemo(
    () => [...scatterData, ...datasheetData],
    [datasheetData, scatterData]
  );

  const datasheetStatus = useMemo(() => {
    if (!datasheetZone) return '';
    if (!datasheetVisible) return `${datasheetZone} · hidden`;
    const zoneInfo = tests.find((test) => test.name === datasheetZone);
    if (!zoneInfo) return `${datasheetZone} · zone not loaded`;
    if (zoneInfo.status !== 'ready') return `${datasheetZone} · ${zoneInfo.status}`;
    if (!datasheetMeta) return `${datasheetZone} · loading zone`;
    if (!xAxis || !yAxis) return `${datasheetZone} · choose both axes`;
    if (!datasheetHasMatchingAxes) {
      return `${datasheetZone} · no matching ${xAxis} / ${yAxis} columns`;
    }
    if (datasheetLine.loading) return `${datasheetZone} · loading reference rows`;
    if (datasheetLine.error) return `${datasheetZone} · ${datasheetLine.error}`;
    if (datasheetData.length === 0) return `${datasheetZone} · no complete X/Y rows`;
    return `${datasheetZone} · ${datasheetData.length} reference point${
      datasheetData.length === 1 ? '' : 's'
    }`;
  }, [
    datasheetData.length,
    datasheetHasMatchingAxes,
    datasheetLine.error,
    datasheetLine.loading,
    datasheetMeta,
    datasheetVisible,
    datasheetZone,
    tests,
    xAxis,
    yAxis,
  ]);

  const requiredStatsKeys = useMemo(() => {
    const columns = Array.from(new Set([xAxis, yAxis, ...filterColumns])).filter(Boolean);
    const keys: string[] = [];
    Object.entries(tpsByTest).forEach(([test, points]) => {
      if (points.length === 0) return;
      const available = columnsByTest[test] ?? [];
      columns.forEach((column) => {
        if (available.includes(column)) keys.push(`${test}|${column}`);
      });
    });
    return keys;
  }, [columnsByTest, filterColumns, tpsByTest, xAxis, yAxis]);
  const visibleStatsErrors = requiredStatsKeys.filter((key) => statsErrors[key]);
  const scatterStatsLoading = requiredStatsKeys.some((key) => loadingStats.has(key));
  const scatterErrorText =
    visibleStatsErrors.length > 0
      ? `${visibleStatsErrors.length} statistic request${
          visibleStatsErrors.length === 1 ? '' : 's'
        } failed. ${statsErrors[visibleStatsErrors[0]]}`
      : null;
  const retryStatistics = useCallback((keys: string[]) => {
    statsRequestEpoch.current += 1;
    statsInFlight.current.clear();
    setLoadingStats(new Set());
    setStatsErrors((prev) => {
      const next = { ...prev };
      keys.forEach((key) => delete next[key]);
      return next;
    });
    setStatsCache((prev) => {
      const next = { ...prev };
      keys.forEach((key) => {
        const divider = key.indexOf('|');
        const test = key.slice(0, divider), column = key.slice(divider + 1);
        if (next[test]?.[column]) {
          next[test] = { ...next[test] };
          delete next[test][column];
        }
      });
      return next;
    });
    setStatsRetry((attempt) => attempt + 1);
  }, []);
  const retryScatterStats = () => retryStatistics(requiredStatsKeys);
  const scatterEmptyState = useMemo(() => {
    const pointCount = Object.entries(tpsByTest).reduce(
      (count, [test, points]) => (test === settings.datasheetZone ? count : count + points.length),
      0
    );
    if (!xAxis || !yAxis) {
      return {
        title: 'Choose two variables',
        detail: 'The scatter compares test-point means across the loaded test library.',
      };
    }
    if (hasActiveFilters && rawScatterData.length > 0 && scatterData.length === 0) {
      return {
        title: 'No points match these filters',
        detail: 'Adjust or clear the active filters to bring test points back into view.',
      };
    }
    if (pointCount === 0) {
      return {
        title: 'No test points defined',
        detail: 'Open Split to define test points before comparing their mean values.',
      };
    }
    return {
      title: 'No comparable points',
      detail: 'The loaded tests do not contain usable values for both selected variables.',
    };
  }, [
    hasActiveFilters,
    rawScatterData.length,
    scatterData.length,
    settings.datasheetZone,
    tpsByTest,
    xAxis,
    yAxis,
  ]);

  const { mainZoom, setMainZoom, handleMainWheel, handlePan, resetZoom } = useMainPlotZoom(
    scatterDomainData,
    hasRestoredSession ? restoredSession.mainZoom : null,
    {
      horizontal: showHorizontalErrorBars,
      vertical: showVerticalErrorBars,
    }
  );
  resetMainZoomRef.current = resetZoom;

  const applyRecoveredSession = (input: AnalysisSession, recovery: SessionRecovery,
    catalog: AnalysisSource[], list: TestInfo[], reviewed = false) => {
    recoveryInputRef.current = input;
    setTests(list);
    const session = recovery.session;
    setRecoveryMessages(recovery.messages);
    setRecoveryLegacy(recovery.legacy);
    setRecoveryNeedsReview(reviewed ? false : recovery.needsReview);
    // Invalidate outstanding loads too; a newly opened file can refer to
    // a source whose previous metadata request has not completed yet.
    new Set([...Object.keys(metaByTest), ...metaInFlight.current]).forEach(invalidateTest);
    setSelectedTPs([]);
    setHiddenTPs(new Set());
    setCurrentTest(session.currentTest || (referencedNames(input).length === 0
      ? list.find(test => test.status === 'ready')?.name ?? '' : ''));
    recoveredOrderRef.current = session.selections.map(selection => `${selection.test}:${selection.tpId}`);
    setPendingRestoredSelections(session.selections);
    setSelectionSessionHydrated(false);
    setFilterState(session.filterState);
    setXAxis(session.xAxis);
    setYAxis(session.yAxis);
    setMainZoom(session.mainZoom);
    setTimeZoom(session.timeZoom);
    setTimeYRanges(session.timeYRanges);
    setFullRange(session.fullRange);
    setPlotsUserEdited(session.plotsUserEdited);
    setAxesUserSet(session.axesUserSet);
    setPlotConfigs(session.plotConfigs);
    setPlotFilters(session.plotFilters);
    setPlotShowOriginal(session.plotShowOriginal);
    setAnnotationsVisible(session.annotationsVisible);
    setViewMode(session.viewMode);
    setFullPlotMode(session.fullPlotMode);
    setWaterfallWindow(session.waterfallWindow);
    setWaterfallOverlap(session.waterfallOverlap);
    setSpecMode(session.specMode);
    setSpecXAxis(session.specXAxis);
    setSpecRpmCol(session.specRpmCol);
    setSpecLogY(session.specLogY);
    setSpecSource(session.specSource);
    setXYSource(session.xySource);
    setXYYCols(session.xyYCols);
    setXYXCols(session.xyXCols);
    setScatterRatio(session.scatterRatio);
    setScatterCollapsed(session.scatterCollapsed);
    setPlotDensity(session.plotDensity);
    setExpandedPlot(session.expandedPlot);
    setPlotViewports(session.plotViewports);
    setClusteringEnabled(session.clusteringEnabled ?? settings.clustering);
    setDatasheetVisible(session.datasheetVisible ?? settings.datasheetVisible);
    setShowHorizontalErrorBars(session.showHorizontalErrorBars);
    setShowVerticalErrorBars(session.showVerticalErrorBars);
    setSessionEpoch(epoch => epoch + 1);
    setSourceCatalog(retainPendingSourceReferences(catalog, input.sources));
    setSessionRecoveryReady(true);
    setError(null);
    setSourceCatalogError(null);
  };

  recoverSessionRef.current = async (reconnectLegacy = false) => {
    const attempt = ++recoveryAttemptRef.current;
    setLoading(true); setError(null); setSourceCatalogError(null);
    setSessionRecoveryReady(false);
    try {
      // Publish the list as soon as it arrives. A failed/slow identity catalog
      // must not hide valid upload history or make the ready count look empty.
      // Recovery itself still requires both responses and never falls back to
      // matching a saved dataset by its name.
      const [listResult, catalogResult] = await Promise.allSettled([
        fetchTests().then(list => {
          if (attempt === recoveryAttemptRef.current) setTests(list);
          return list;
        }, err => {
          if (attempt === recoveryAttemptRef.current) {
            setTestListStatus('error');
            setError(err instanceof Error ? err.message : 'The test list could not be loaded.');
          }
          throw err;
        }),
        fetchAnalysisSources().catch(err => {
          if (attempt === recoveryAttemptRef.current) {
            setSourceCatalogError(err instanceof Error ? err.message : 'Source identities could not be checked.');
          }
          throw err;
        }),
      ]);
      if (attempt !== recoveryAttemptRef.current) return;
      if (listResult.status !== 'fulfilled' || catalogResult.status !== 'fulfilled') return;
      const list = listResult.value;
      const catalog = catalogResult.value;
      const input = recoveryInputRef.current;
      if (input) {
        applyRecoveredSession(input, resolveSessionSources(input, catalog.sources, reconnectLegacy), catalog.sources, list);
      } else {
        setCurrentTest(list.find(test => test.status === 'ready')?.name ?? '');
        setSourceCatalog(catalog.sources);
        setSessionRecoveryReady(true);
      }
    } catch (err) {
      if (attempt === recoveryAttemptRef.current) setError(err instanceof Error ? err.message : 'Source identities could not be checked. Retry recovery.');
    } finally {
      if (attempt === recoveryAttemptRef.current) setLoading(false);
    }
  };

  const retrySavedRecovery = async (legacy = false) => {
    if (!(await handleTabChange('analyze'))) return;
    await recoverSessionRef.current(legacy);
  };

  // New uploads and lifecycle changes need source references too. Keep a
  // previously loaded reference until that test is invalidated. A background
  // refresh must not bless replaced data or new TP bounds using old plot data.
  useEffect(() => {
    if (!sessionRecoveryReady) return;
    let canceled = false;
    void fetchAnalysisSources().then(catalog => {
      if (canceled) return;
      setSourceCatalog(previous => catalog.sources.map(source => {
        const old = previous.find(item => item.name === source.name);
        return old ?? source;
      }));
    }).catch(() => { /* Retain verified references; unknown new sources save null IDs. */ });
    return () => { canceled = true; };
  }, [sessionRecoveryReady, tests, sourceRefreshVersion]);

  const captureSession = (): AnalysisSession => {
    const session = {
      version: 1,
      plotViewports, expandedPlot, clusteringEnabled, datasheetVisible, showHorizontalErrorBars, showVerticalErrorBars,
      currentTest,
      xAxis,
      yAxis,
      axesUserSet,
      selections: [
        ...selectedTPs.map((selection) => ({
          test: selection.test,
          tpId: selection.tpId,
          hidden: hiddenTPs.has(selection.id),
          color: selection.color,
        })),
        ...pendingRestoredSelections.filter(
          (pending) =>
            !selectedTPs.some(
              (selection) => selection.test === pending.test && selection.tpId === pending.tpId
            )
        ),
      ].sort((a, b) => {
        const rank = (test: string, tpId: number) => {
          const index = recoveredOrderRef.current.indexOf(`${test}:${tpId}`);
          return index < 0 ? MAX_SELECTED_TEST_POINTS : index;
        };
        return rank(a.test, a.tpId) - rank(b.test, b.tpId);
      }).slice(0, MAX_SELECTED_TEST_POINTS),
      filterState,
      mainZoom,
      timeZoom,
      timeYRanges,
      fullRange,
      viewMode,
      fullPlotMode,
      waterfallWindow, waterfallOverlap,
      specMode,
      specXAxis,
      specRpmCol,
      specLogY,
      specSource,
      xySource,
      plotConfigs: plotConfigs.slice(0, 9),
      plotsUserEdited,
      plotFilters,
      plotShowOriginal,
      annotationsVisible,
      xyYCols: xyYCols.slice(0, 9),
      xyXCols: xyXCols.slice(0, 9),
      scatterRatio,
      scatterCollapsed,
      plotDensity,
    } satisfies import('./services/analysisSession').AnalysisSession;
    return { ...session, sources: captureSessionSources(session, sourceCatalog) };
  };
  const liveSession = captureSession();
  const serializedSession = JSON.stringify(liveSession);
  const sessionSaveDisabled = loading || !sessionRecoveryReady ? 'Wait for source recovery to finish.'
    : recoveryLegacy || recoveryNeedsReview ? 'Review session recovery before saving.'
    : !selectionSessionHydrated ? 'Wait for saved selections to load.' : null;
  useEffect(() => {
    if (sessionSaveDisabled) return;
    const timer = window.setTimeout(() => saveAnalysisSession(JSON.parse(serializedSession)), 250);
    return () => window.clearTimeout(timer);
  }, [serializedSession, sessionSaveDisabled]);

  useEffect(() => {
    const media = window.matchMedia('(max-width: 980px)');
    const updateLayoutMode = () => setIsStackedWorkspace(media.matches);
    updateLayoutMode();
    media.addEventListener('change', updateLayoutMode);
    return () => media.removeEventListener('change', updateLayoutMode);
  }, []);

  useEffect(() => {
    if (!isResizingWorkspace) return;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = isStackedWorkspace ? 'row-resize' : 'col-resize';
    document.body.style.userSelect = 'none';

    const resize = (event: PointerEvent) => {
      const workspace = analyzeWorkspaceRef.current;
      if (!workspace) return;
      const bounds = workspace.getBoundingClientRect();
      const available = isStackedWorkspace ? bounds.height : bounds.width;
      if (available <= 0) return;
      const pointer = isStackedWorkspace ? event.clientY - bounds.top : event.clientX - bounds.left;
      const ratio = (pointer / available) * 100;
      setScatterRatio(Math.min(62, Math.max(24, ratio)));
    };
    const stop = () => setIsResizingWorkspace(false);
    window.addEventListener('pointermove', resize);
    window.addEventListener('pointerup', stop, { once: true });
    window.addEventListener('pointercancel', stop, { once: true });
    return () => {
      window.removeEventListener('pointermove', resize);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
    };
  }, [isResizingWorkspace, isStackedWorkspace]);

  const handlePlotDensityChange = (density: PlotDensity) => {
    setPlotDensity(density);
    setExpandedPlot(null);
  };

  // The scatter is global now — switching the active test only affects the
  // per-test views (grid modes, split/edit tabs).
  const handleTestChange = async (test: string): Promise<boolean> => {
    if (test === currentTest) return true;
    if (!(await confirmDiscardActiveDraft())) return false;
    setCurrentTest(test);
    setFullRange(null);
    setExpandedPlot(null);
    return true;
  };

  // The active time zoom depends on the right-panel mode
  const activeTimeZoom = viewMode === 'tp' ? timeZoom : fullRange;
  const handleActiveTimeZoom = (domain: [number, number]) => {
    if (viewMode === 'tp') setTimeZoom(domain);
    else setFullRange(domain);
  };
  const resetActiveTimeZoom = () => {
    if (viewMode === 'tp') {
      resetTimeZoom();
      setTimeYRanges([]);
      setTimeZoomResetVersion((version) => version + 1);
    }
    else setFullRange(null);
  };

  const handleXAxisChange = (axis: string) => {
    setAxesUserSet(true);
    setXAxis(axis);
    resetZoom();
  };

  const handleYAxisChange = (axis: string) => {
    setAxesUserSet(true);
    setYAxis(axis);
    resetZoom();
  };

  const handleToggleExpand = (index: number) => {
    setExpandedPlot(expandedPlot === index ? null : index);
  };

  const handleScatterToggle = useCallback(
    (point: ScatterDataPoint) => {
      if (!point.isSelected && selectedTPs.length >= MAX_SELECTED_TEST_POINTS) {
        setNotice(
          `Selection limit reached (${MAX_SELECTED_TEST_POINTS}). Remove a point to select another.`
        );
        return;
      }

      // An explicit selection action supersedes any still-unavailable points
      // from the restored session; they must not appear later against intent.
      setPendingRestoredSelections([]);
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
    [toggleTestPoint, metaByTest, tpsByTest, selectedTPs.length]
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

  const handleMetaSaved = (saved: TestMeta) => {
    setMetaByTest((prev) => ({ ...prev, [saved.name]: saved }));
    setTests((prev) => prev.map((test) => test.name === saved.name
      ? { ...test, description: saved.description ?? '', components: saved.components } : test));
  };

  const handleTestGone = async (newName: string) => {
    // Same ordering rule as handleTestDeleted: list first, then prune,
    // batched — or the meta loader refetches the vanished name and 404s.
    const old = currentTest;
    if (newName) {
      try {
        setTests(await fetchTests());
      } catch {
        // list refresh is cosmetic here
      }
      invalidateTest(old);
      setCurrentTest(newName);
      return;
    }
    // deleted — fall back to the first ready test
    setTab('analyze');
    try {
      const list = await fetchTests();
      setTests(list);
      invalidateTest(old);
      const firstReady = list.find((t) => t.status === 'ready' && t.name !== old);
      setCurrentTest(firstReady?.name ?? '');
    } catch (e) {
      invalidateTest(old);
      console.error('test list refresh failed:', e);
    }
  };

  const xLabel = xAxis ? `${xAxis} (TP mean)` : '';
  const yLabel = yAxis ? `${yAxis} (TP mean)` : '';

  // Uploads and preferences stay available without active test metadata.
  const uploadView = (
    <UploadView
      tests={tests}
      uploads={uploads}
      pendingFiles={pendingUploadFiles}
      defaultFsHz={parseUploadFs(settings)}
      onStageUploadFiles={stageUploadFiles}
      onStartUpload={startConfiguredUploads}
      onClearPendingFiles={() => setPendingUploadFiles([])}
      onDismissUpload={dismissUpload}
      onPauseUpload={pauseUpload}
      onResumeUpload={resumeUpload}
      onAdoptServerUpload={adoptServerUpload}
      onCancelUpload={cancelUpload}
      onOpenTest={handleOpenTest}
      onEditNotes={(name) => { void handleOpenTest(name, 'edit'); }}
      onTestDeleted={handleTestDeleted}
      onTestsChanged={handleTestsChanged}
      onStatsRebuilt={handleStatsRebuilt}
    />
  );

  // Also reachable from the no-tests screen (preferences may name columns of
  // tests that aren't uploaded yet — they're kept and shown "(not loaded)").
  const settingsView = (
    <SettingsView
      settings={settings}
      draft={settingsDraft}
      onDraftChange={setSettingsDraft}
      onSave={handleSettingsSave}
      onMakeDefault={handleMakeSettingsDefault}
      columns={unionColumns}
      xyColumns={xyUnionColumns}
      zones={tests.filter((test) => test.status === 'ready').map((test) => test.name)}
    />
  );

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
      Drop CSV file(s) to configure import
    </div>
  );

  // Keep navigation and import access available during loading and failures.
  const needsTestData = tab !== 'uploads' && tab !== 'settings' && tab !== 'components';
  const activeMetaError = currentTest && !meta ? metaErrors[currentTest] : null;
  const waitingForData = loading || Boolean(currentTest && !meta);
  const workspaceError = error || sourceCatalogError || activeMetaError;

  return (
    <div {...dragHandlers} className="app-shell">
      {dropOverlay}
      <Header
        sessionControls={<SessionControls onBegin={() => handleTabChange('analyze')}
          getSession={captureSession} saveDisabledReason={sessionSaveDisabled}
          onApply={(input, recovery, sources, list) => {
            ++recoveryAttemptRef.current;
            applyRecoveredSession(input, recovery, sources, list, true);
            setLoading(false);
          }} />}
        tests={tests}
        testListStatus={testListStatus}
        tab={tab}
        onTabChange={handleTabChange}
        onImportFiles={stageUploadFiles}
        uploads={uploads}
        onDismissUpload={dismissUpload}
        onPauseUpload={pauseUpload}
        onResumeUpload={resumeUpload}
        onCancelUpload={cancelUpload}
        notice={notice}
      />
      {sessionRecoveryReady && (recoveryMessages.length > 0 || recoveryLegacy) && (
        <aside className="session-recovery" aria-label="Session recovery">
          <details open={recoveryLegacy || recoveryNeedsReview || undefined}>
            <summary>Session recovery · {recoveryMessages.length} notice{recoveryMessages.length === 1 ? '' : 's'}</summary>
            <ul>{recoveryMessages.map(message => <li key={message}>{message}</li>)}</ul>
          </details>
          {recoveryLegacy && <p>Older sessions saved test names without dataset IDs. Reconnect only if these names still refer to your original tests. Automatic saving is paused until you choose.</p>}
          {recoveryNeedsReview && !recoveryLegacy && <p>Some saved references could not be recovered. Your saved workspace is retained and automatic saving is paused. Restore missing tests and retry, or continue with the recovered workspace.</p>}
          <div className="session-recovery-actions">
            {recoveryLegacy ? <>
              <button className="btn" disabled={loading} onClick={() => void retrySavedRecovery(true)}>Reconnect legacy session by name</button>
              <button className="btn" onClick={() => { setRecoveryLegacy(false); setRecoveryNeedsReview(false); setRecoveryMessages(['Legacy source references were discarded. Choose tests to start a fresh analysis.']); }}>Discard legacy references</button>
            </> : recoveryNeedsReview ? <>
              <button className="btn" disabled={loading} onClick={() => void retrySavedRecovery()}>Retry session recovery</button>
              <button className="btn" onClick={() => setRecoveryNeedsReview(false)}>Continue with recovered workspace</button>
            </> : <button className="btn" disabled={!currentTest} onClick={() => setRecoveryMessages([])}>Dismiss recovery notices</button>}
            {!currentTest && !recoveryLegacy && <label>Active test <select className="input" aria-label="Choose active test after recovery" value=""
              onChange={event => { void handleTestChange(event.target.value); }}>
              <option value="">Choose a test…</option>
              {tests.filter(test => test.status === 'ready').map(test => <option key={test.name} value={test.name}>{test.name}</option>)}
            </select></label>}
          </div>
        </aside>
      )}
      {(error || sourceCatalogError) && !needsTestData && (
        <div className="app-connection-banner" role="alert">
          <span>{error ? 'The test list is unavailable. Check the connection and try again.'
            : 'Analysis source verification is unavailable. Your saved workspace is retained; retry to enable analysis.'}</span>
          <button className="btn" onClick={reloadData} disabled={loading}>
            {error ? 'Retry connection' : 'Retry source check'}
          </button>
        </div>
      )}
      {needsTestData && (workspaceError || waitingForData || !currentTest) ? (
        <main className="workspace-state">
          <div className="workspace-state-content">
            <div
              className={'workspace-state-symbol' + (workspaceError ? ' is-error' : '')}
              aria-hidden="true"
            >
              {workspaceError ? '!' : waitingForData ? '…' : '+'}
            </div>
            {workspaceError ? (
              <div role="alert">
                <h1>{error ? 'Unable to load test data' : sourceCatalogError
                  ? 'Unable to verify analysis sources' : 'This test could not be loaded'}</h1>
                <p>
                  {error
                    ? 'Check that the test data service is running, then try again.'
                    : sourceCatalogError
                    ? 'Analysis is paused until source identities can be checked. Your saved workspace is retained, and upload history is still available.'
                    : 'You can retry, or open Uploads to choose another test.'}
                </p>
                <details className="workspace-error-details">
                  <summary>Error details</summary>
                  <p>{workspaceError}</p>
                </details>
                <div className="workspace-state-actions">
                  <button className="btn btn-primary" onClick={reloadData}>
                    Try again
                  </button>
                  <button className="btn" onClick={() => handleTabChange('uploads')}>
                    Open uploads
                  </button>
                </div>
              </div>
            ) : waitingForData ? (
              <div role="status" aria-live="polite">
                <h1>{loading ? 'Loading your workspace' : 'Loading test data'}</h1>
                <p>{currentTest || 'Fetching available tests and saved preferences.'}</p>
                <div className="workspace-loading-bar" aria-hidden="true" />
              </div>
            ) : (
              <>
                <h1>
                  {tests.some(test => test.status === 'ready') ? 'Choose a test to continue' : tests.length ? 'Your tests are not ready yet' : 'Start with your test data'}
                </h1>
                <p>
                  {tests.some(test => test.status === 'ready')
                    ? 'Review session recovery above, or open a test from Uploads.' : tests.length
                    ? 'Open Uploads to follow processing progress or resolve an interrupted import.'
                    : 'Import a test-rig CSV to explore signals and compare operating points.'}
                </p>
                <div className="workspace-state-actions">
                  <button className="btn btn-primary" onClick={() => handleTabChange('uploads')}>
                    {tests.length ? 'View uploads' : 'Import your first CSV'}
                  </button>
                </div>
                {!tests.length && (
                  <ol className="workspace-start-steps">
                    <li>
                      <strong>Import a CSV</strong>
                      <span>Choose a time column or sample rate.</span>
                    </li>
                    <li>
                      <strong>Define test points</strong>
                      <span>Split the run into operating conditions.</span>
                    </li>
                    <li>
                      <strong>Compare signals</strong>
                      <span>Explore time traces, spectra, and XY plots.</span>
                    </li>
                  </ol>
                )}
              </>
            )}
          </div>
        </main>
      ) : tab === 'uploads' ? (
        uploadView
      ) : tab === 'settings' ? (
        settingsView
      ) : tab === 'components' ? (
        <ComponentStatisticsView onEditTest={name => { void handleOpenTest(name, 'edit'); }} />
      ) : tab === 'split' && meta ? (
        <SplitView
          key={currentTest}
          test={currentTest}
          meta={meta}
          columns={dataColumns}
          tests={tests}
          onTestChange={handleTestChange}
          onDirtyChange={setSplitDirty}
          onBusyChange={setSplitBusy}
          onSaved={() => handleTestPointsSaved(currentTest)}
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
          onDirtyChange={setEditDirty}
          onBusyChange={setEditBusy}
        />
      ) : (
        <div
          ref={analyzeWorkspaceRef}
          className={[
            'analyze-workspace',
            scatterCollapsed ? 'scatter-collapsed' : '',
            isResizingWorkspace ? 'is-resizing' : '',
          ]
            .filter(Boolean)
            .join(' ')}
          style={
            {
              '--scatter-size': scatterCollapsed ? '0px' : `${scatterRatio}%`,
            } as CSSProperties
          }
        >
          {/* Left Panel - Main Scatter Plot (all ready tests) */}
          <div className="analyze-scatter-pane" aria-hidden={scatterCollapsed}>
            <div className="analyze-scatter-panel">
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
                clusteringAvailable={rawScatterData.length >= 2}
                clusteringEnabled={clusteringEnabled}
                onClusteringChange={setClusteringEnabled}
                datasheetZone={datasheetZone}
                datasheetVisible={datasheetVisible}
                datasheetStatus={datasheetStatus}
                onDatasheetVisibilityChange={setDatasheetVisible}
                horizontalErrorBars={showHorizontalErrorBars}
                verticalErrorBars={showVerticalErrorBars}
                onHorizontalErrorBarsChange={setShowHorizontalErrorBars}
                onVerticalErrorBarsChange={setShowVerticalErrorBars}
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
              <div className="scatter-plot-stage">
                <MainScatterPlot
                  scatterData={scatterData}
                  datasheetData={datasheetData}
                  rawDataCount={rawScatterData.length}
                  xLabel={xLabel}
                  yLabel={yLabel}
                  mainZoom={mainZoom}
                  onResetZoom={resetZoom}
                  onToggleTestPoint={handleScatterToggle}
                  onWheel={handleMainWheel}
                  onPan={handlePan}
                  clusteringEnabled={clusteringEnabled}
                  showHorizontalErrorBars={showHorizontalErrorBars}
                  showVerticalErrorBars={showVerticalErrorBars}
                />
                <PlotStateOverlay
                  loading={scatterStatsLoading || datasheetLine.loading}
                  hasData={scatterData.length > 0 || datasheetData.length > 0}
                  error={
                    scatterData.length === 0 && datasheetData.length === 0
                      ? scatterErrorText || datasheetLine.error || null
                      : null
                  }
                  emptyState={scatterEmptyState}
                  onRetry={retryScatterStats}
                  loadingLabel="Loading test-point statistics"
                  updatingLabel="Updating comparison"
                  partialMessage={
                    scatterData.length > 0 && scatterErrorText
                      ? `${visibleStatsErrors.length} data source${
                          visibleStatsErrors.length === 1 ? '' : 's'
                        } could not update`
                      : null
                  }
                />
              </div>
            </div>
          </div>

          <div
            className="analyze-resize-rail"
            role="separator"
            aria-label="Resize scatter and plot panels"
            aria-orientation={isStackedWorkspace ? 'horizontal' : 'vertical'}
            aria-valuemin={24}
            aria-valuemax={62}
            aria-valuenow={Math.round(scatterRatio)}
            tabIndex={scatterCollapsed ? -1 : 0}
            onPointerDown={(event) => {
              if (event.button !== 0 || scatterCollapsed) return;
              event.preventDefault();
              setIsResizingWorkspace(true);
            }}
            onKeyDown={(event) => {
              if (scatterCollapsed) return;
              const decreaseKey = isStackedWorkspace ? 'ArrowUp' : 'ArrowLeft';
              const increaseKey = isStackedWorkspace ? 'ArrowDown' : 'ArrowRight';
              if (event.key === decreaseKey || event.key === increaseKey) {
                event.preventDefault();
                const step = event.shiftKey ? 5 : 2;
                setScatterRatio((ratio) =>
                  Math.min(62, Math.max(24, ratio + (event.key === increaseKey ? step : -step)))
                );
              } else if (event.key === 'Home') {
                event.preventDefault();
                setScatterRatio(24);
              } else if (event.key === 'End') {
                event.preventDefault();
                setScatterRatio(62);
              }
            }}
            onDoubleClick={() => setScatterRatio(40)}
          >
            <span className="analyze-resize-grip" aria-hidden="true" />
            <button
              type="button"
              className="analyze-collapse-button"
              aria-label={scatterCollapsed ? 'Show scatter panel' : 'Hide scatter panel'}
              title={scatterCollapsed ? 'Show scatter panel' : 'Hide scatter panel'}
              onPointerDown={(event) => event.stopPropagation()}
              onDoubleClick={(event) => event.stopPropagation()}
              onClick={() => setScatterCollapsed((collapsed) => !collapsed)}
            >
              {scatterCollapsed ? '›' : '‹'}
            </button>
          </div>

          {/* Right Panel - Time Series Plots */}
          <div className="analyze-plots-pane">
            <SelectedPointsPanel
              selectedTPs={selectedTPs}
              hiddenTPs={hiddenTPs}
              onToggleVisibility={toggleVisibility}
              onRemoveTP={removeTP}
              onClearAll={() => {
                setPendingRestoredSelections([]);
                clearAll();
                resetTimeZoom();
                setTimeYRanges([]);
              }}
              timeZoom={activeTimeZoom}
              hasYZoom={viewMode === 'tp' && activeTimeYRanges.some(Boolean)}
              onResetTimeZoom={resetActiveTimeZoom}
              maxPoints={MAX_SELECTED_TEST_POINTS}
              loadingTestPointIds={loadingTestPointIds}
              isEditMode={isEditMode}
              onToggleEditMode={() => setIsEditMode(!isEditMode)}
              viewMode={viewMode}
              onViewModeChange={setViewMode}
              waterfallWindow={waterfallWindow}
              waterfallOverlap={waterfallOverlap}
              specMode={specMode}
              onWaterfallWindowChange={setWaterfallWindow}
              onWaterfallOverlapChange={setWaterfallOverlap}
              onSpecModeChange={(mode) => {
                setSpecMode(mode);
                if (mode === 'waterfall') setSpecSource('full');
              }}
              specXAxis={specXAxis}
              onSpecXAxisChange={setSpecXAxis}
              specRpmCol={specRpmCol}
              onSpecRpmColChange={setSpecRpmCol}
              spectrumColumns={spectrumRpmColumns}
              specLogY={specLogY}
              onSpecLogYChange={setSpecLogY}
              tests={tests}
              currentTest={currentTest}
              onTestChange={handleTestChange}
              fullPlotMode={fullPlotMode}
              onFullPlotModeChange={setFullPlotMode}
              specSource={specSource}
              onSpecSourceChange={setSpecSource}
              xySource={xySource}
              onXYSourceChange={setXYSource}
              plotDensity={plotDensity}
              onPlotDensityChange={handlePlotDensityChange}
            />
            <TimeSeriesGrid
              key={sessionEpoch}
              viewports={plotViewports}
              sourceCatalog={sourceCatalog}
              onViewportChange={(kind, index, value) => setPlotViewports(previous => ({...previous,
                [kind]: previous[kind].map((item, i) => i === index ? value : item)}))}
              viewMode={viewMode}
              density={plotDensity}
              test={currentTest}
              columns={viewMode === 'xy' ? xyGridColumns : gridColumns}
              selectedTPs={selectedTPs}
              hiddenTPs={hiddenTPs}
              expandedPlot={expandedPlot}
              onToggleExpand={handleToggleExpand}
              timeZoom={activeTimeZoom}
              onTimeZoomChange={handleActiveTimeZoom}
              onTimeZoomReset={resetActiveTimeZoom}
              timeYRanges={activeTimeYRanges}
              timeZoomResetVersion={timeZoomResetVersion}
              onTimeYRangeChange={handleTimeYRangeChange}
              fullPlotMode={fullPlotMode}
              waterfallWindow={waterfallWindow}
              waterfallOverlap={waterfallOverlap}
              specMode={specMode}
              specXAxis={specXAxis}
              specRpmCol={specRpmCol}
              specLogY={specLogY}
              specSource={specSource}
              fs={meta?.fs_hz ?? null}
              plotFilters={plotFilters}
              plotFilterSpecs={plotFilterSpecs}
              plotShowOriginal={plotShowOriginal}
              annotationsVisible={annotationsVisible}
              onAnnotationsVisibleChange={setAnnotationsVisible}
              onPlotShowOriginalChange={(i, show) =>
                setPlotShowOriginal((prev) => prev.map((value, index) => index === i ? show : value))
              }
              onPlotFilterChange={(i, patch) =>
                setPlotFilters((prev) => {
                  const next = [...prev];
                  next[i] = { ...(next[i] ?? DEFAULT_FILTER_UI), ...patch };
                  return next;
                })
              }
              xySource={xySource}
              xyYCols={xyYCols}
              onXYYColChange={(i, c) =>
                setXYYCols((prev) => {
                  const next = [...prev];
                  next[i] = c;
                  return next;
                })
              }
              xyXCols={xyXCols}
              onXYXColChange={(i, c) =>
                setXYXCols((prev) => {
                  const next = [...prev];
                  next[i] = c;
                  return next;
                })
              }
              columnsByTest={viewMode === 'xy' ? xyColumnsByTest : columnsByTest}
              traceErrors={traceErrors}
              onRetryTraces={retryTestPointTraces}
              statsCache={statsCache}
              statsErrors={statsErrors}
              onRetryStatistics={retryStatistics}
              onBrowseFullTest={() => {
                if (viewMode === 'spectrum') setSpecSource('full');
                else if (viewMode === 'xy') setXYSource('full');
                else setViewMode('full');
              }}
              isEditMode={isEditMode}
              plotConfigs={plotConfigs}
              onPlotConfigChange={(configs) => {
                setPlotConfigs(configs);
                setPlotsUserEdited(true);
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
