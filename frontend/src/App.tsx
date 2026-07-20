import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import { AppTab, Header } from './components/layout/Header';
import { AxisControls } from './components/controls/AxisControls';
import { FilterControls } from './components/controls/FilterControls';
import { SelectedPointsPanel } from './components/controls/SelectedPointsPanel';
import { MainScatterPlot } from './components/plots/MainScatterPlot';
import { TimeSeriesGrid } from './components/plots/TimeSeriesGrid';
import SplitView from './components/split/SplitView';
import EditView from './components/edit/EditView';
import UploadView from './components/upload/UploadView';
import SettingsView from './components/settings/SettingsView';
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
import { DEFAULT_FILTER_UI, FilterUi, buildFilterSpec } from './constants/filters';
import {
  AppSettings,
  loadSettings,
  saveSettings,
  parseUploadFs,
} from './constants/settings';
import { isBusyStatus } from './constants/status';
import {
  ScatterDataPoint,
  StatsCache,
  TestInfo,
  TestMeta,
  TestPoint,
  TpStat,
  UploadItem,
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
    (a, b) => (testCount.get(b)! - testCount.get(a)!) || (a < b ? -1 : 1)
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
  // Saved preferences (localStorage) — seed the defaults below and feed the
  // Settings tab. Declared first: several states initialize from it.
  const [settings, setSettings] = useState<AppSettings>(loadSettings);
  // Unsaved Settings-page edits (null = none). Hoisted here so switching tabs
  // doesn't silently discard them; nothing applies until Save.
  const [settingsDraft, setSettingsDraft] = useState<AppSettings | null>(null);
  const [xAxis, setXAxis] = useState('');
  const [yAxis, setYAxis] = useState('');
  // True once the user picks a scatter axis. While false, the axes auto-default
  // to the preferred/most-shared pair (recomputed as tests load); once the
  // user chooses, their pick is kept as long as it exists in any test.
  const [axesUserSet, setAxesUserSet] = useState(false);
  const [expandedPlot, setExpandedPlot] = useState<number | null>(null);
  const [clusteringEnabled, setClusteringEnabled] = useState(settings.clustering);
  const [isEditMode, setIsEditMode] = useState(false);
  const [plotConfigs, setPlotConfigs] = useState<string[]>([]);
  // True once the user picks columns via Edit Plots. While false, the grid
  // auto-(re)seeds from the selected test points (first-selected prioritized);
  // once true, those picks are preserved and only NEW columns fill empty cells.
  const [plotsUserEdited, setPlotsUserEdited] = useState(false);
  // Right-panel mode: 'tp' overlays selected TPs from t=0; 'full' browses the
  // active test with windowed pyramid reads; 'spectrum' FFT/Welch per column;
  // 'xy' scatters each column against that plot's own X column.
  const [viewMode, setViewMode] = useState<'tp' | 'full' | 'spectrum' | 'xy'>(
    settings.defaultViewMode
  );
  const [fullRange, setFullRange] = useState<[number, number] | null>(null);
  const [specMode, setSpecMode] = useState<'fft' | 'welch'>(settings.specMode);
  const [specLogY, setSpecLogY] = useState(settings.specLogY);
  // Spectrum/XY data source: selected test points, or the active test
  const [specSource, setSpecSource] = useState<'tp' | 'full'>('tp');
  // Per-plot DSP filters (TP + Full test modes), dashed overlays. Each grid
  // cell has a ≈ button (next to expand) that opens its own filter row —
  // there is no shared/broadcast filter control.
  const [plotFilters, setPlotFilters] = useState<FilterUi[]>(() =>
    Array.from({ length: 9 }, () => DEFAULT_FILTER_UI)
  );
  const [xySource, setXYSource] = useState<'tp' | 'full'>('tp');
  // XY mode is per-plot on BOTH axes, index-aligned with plotConfigs: its own
  // plotted (Y) column ('' = follow the shared grid slot) and its own X. Each
  // XY cell edits both in Edit Plots mode — editing an XY Y never disturbs the
  // time/spectrum grids.
  const [xyYCols, setXYYCols] = useState<string[]>([]);
  const [xyXCols, setXYXCols] = useState<string[]>([]);
  const [tab, setTab] = useState<AppTab>('analyze');
  const [notice, setNotice] = useState('');
  // In-flight/failed uploads as persistent header chips — the transient
  // notice auto-clears after 6 s, which must never hide a running upload.
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const uploadSeq = useRef(0);
  const [isDragging, setIsDragging] = useState(false);
  const dragDepth = useRef(0);
  // Set while an /edit rebuild runs; the poller reloads the test when ready.
  const rebuildPending = useRef(false);

  // Dedupe guards for async fetches.
  const metaInFlight = useRef<Set<string>>(new Set());
  const statsInFlight = useRef<Set<string>>(new Set()); // `${test}|${col}`
  const tracesInFlight = useRef<Set<string>>(new Set()); // `${id}|${col}`

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

  const MAX_SELECTED_POINTS = 6;

  const { selectedTPs, hiddenTPs, toggleTestPoint, toggleVisibility, removeTP, clearAll, setSelectedTPs } =
    useTestPointSelection(MAX_SELECTED_POINTS);

  const { timeZoom, setTimeZoom, resetTimeZoom } = useTimeZoom();

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

  /** Drop every cache for one test (after rebuild/rename/delete/split-save). */
  const invalidateTest = useCallback(
    (name: string) => {
      // Bump the generation first so any in-flight fetch for this test drops
      // its result instead of writing pre-invalidation data back (1.11).
      testGen.current.set(name, (testGen.current.get(name) ?? 0) + 1);
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
      // Clear the in-flight guards so the loaders can immediately start a FRESH
      // fetch (the stale one, now a lower generation, will be discarded).
      metaInFlight.current.delete(name);
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
      Promise.all([fetchMeta(name), fetchTestPoints(name)])
        .then(([m, tps]) => {
          if ((testGen.current.get(name) ?? 0) !== gen) return; // invalidated mid-flight
          setMetaByTest((prev) => ({ ...prev, [name]: m }));
          setTpsByTest((prev) => ({ ...prev, [name]: tps.test_points }));
        })
        .catch((e) => {
          console.error(`load failed for ${name}:`, e);
          // Retry transient failures: without this the effect never re-runs
          // (tests/metaByTest unchanged) and the app stays on the loading
          // screen forever with no error and no way to reach Uploads (1.9).
          scheduleMetaRetry();
        })
        .finally(() => metaInFlight.current.delete(name));
    });
  }, [tests, metaByTest, metaRetry, scheduleMetaRetry]);

  // If the active test vanishes (deleted/renamed from another window), fall
  // back to a ready test instead of wedging on the loading screen with a
  // dangling currentTest whose meta was just pruned (1.9). A rebuild keeps the
  // test in the list, so this only fires on a true delete/rename.
  useEffect(() => {
    if (!currentTest || !tests.length) return;
    if (tests.some((t) => t.name === currentTest)) return;
    const firstReady = tests.find((t) => t.status === 'ready');
    setCurrentTest(firstReady ? firstReady.name : '');
  }, [tests, currentTest]);

  /** X/Y pair that maximizes how many tests can plot a point (see bestAxisPair).
   *  Drives the scatter defaults so a narrow test can't hide the rest. */
  const [defaultXAxis, defaultYAxis] = useMemo(
    () => bestAxisPair(columnsByTest),
    [columnsByTest]
  );

  // Scatter axis defaults across the WHOLE library (not just the active test).
  // Precedence: in-session pick (axesUserSet) > saved preference (while its
  // column is loaded) > most-shared pair. Recomputed as tests load.
  useEffect(() => {
    if (!defaultXAxis) return;
    const prefX =
      settings.scatterX && unionColumns.includes(settings.scatterX) ? settings.scatterX : '';
    const prefY =
      settings.scatterY && unionColumns.includes(settings.scatterY) ? settings.scatterY : '';
    if (!axesUserSet) {
      setXAxis(prefX || defaultXAxis);
      setYAxis(prefY || defaultYAxis);
      return;
    }
    setXAxis((prev) => (unionColumns.includes(prev) ? prev : prefX || defaultXAxis));
    setYAxis((prev) => (unionColumns.includes(prev) ? prev : prefY || defaultYAxis));
  }, [defaultXAxis, defaultYAxis, axesUserSet, unionColumns, settings.scatterX, settings.scatterY]);

  // Seed the 3x3 grid from the ordered gridColumns universe (selection-first).
  // Precedence per slot: in-session Edit Plots pick (plotsUserEdited) > saved
  // slot preference (while its column is loaded) > auto fill from gridColumns
  // (first selected TP's columns lead, then the active test). Never collapses
  // below the columns actually available.
  useEffect(() => {
    if (gridColumns.length === 0) return;
    setPlotConfigs((prev) => {
      if (plotsUserEdited) {
        const valid = prev.filter((c) => gridColumns.includes(c));
        const filler = gridColumns.filter((c) => !valid.includes(c));
        return [...valid, ...filler].slice(0, 9);
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
      return slots
        .map((c) => c || (fi < filler.length ? filler[fi++] : ''))
        .filter(Boolean);
    });
    // XY Y/X columns are per-slot too; a session pick (still-valid prev) wins.
    // Y falls back to '' = follow the shared grid slot for that cell.
    setXYYCols((prev) =>
      Array.from({ length: 9 }, (_, i) => {
        if (prev[i] && gridColumns.includes(prev[i])) return prev[i];
        const pref = settings.xyYCols[i] ?? '';
        return pref && gridColumns.includes(pref) ? pref : '';
      })
    );
    setXYXCols((prev) =>
      Array.from({ length: 9 }, (_, i) => {
        if (prev[i] && gridColumns.includes(prev[i])) return prev[i];
        const pref = settings.xyXCols[i] ?? '';
        return pref && gridColumns.includes(pref) ? pref : gridColumns[0] || '';
      })
    );
  }, [gridColumns, plotsUserEdited, settings.gridColumns, settings.xyYCols, settings.xyXCols]);

  // A fresh selection session starts un-edited, so it re-prioritizes to its
  // first test point's columns instead of clinging to the previous picks.
  useEffect(() => {
    if (selectedTPs.length === 0) setPlotsUserEdited(false);
  }, [selectedTPs.length]);

  // Poll the test list while any ingest or rebuild runs, or while the
  // Uploads page is open (its whole point is live status); auto-select the
  // first ready test if none is selected, and reload the current test when
  // its rebuild completes.
  const uploadsActive = uploads.some((u) => !u.error);
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
    // `tab` is read in the bail-out above: without it here, opening the Uploads
    // tab would not (re)start polling unless some other dep also changed (1.17).
  }, [tests, uploadsActive, currentTest, invalidateTest, tab]);

  // Auto-clear transient notices
  useEffect(() => {
    if (!notice) return;
    const id = window.setTimeout(() => setNotice(''), 6000);
    return () => window.clearTimeout(id);
  }, [notice]);

  const handleUploadFiles = useCallback(async (files: File[]) => {
    // Duplicate names must be caught BEFORE any bytes are sent: the backend
    // 409s without reading the body, which aborts the connection mid-stream
    // and surfaces to XHR as an opaque "network error" (and would waste a
    // multi-GB transfer). The backend check stays authoritative for races.
    let existing: Set<string>;
    try {
      existing = new Set((await fetchTests()).map((t) => t.name));
    } catch {
      existing = new Set(tests.map((t) => t.name));
    }
    for (const f of files) {
      if (!f.name.toLowerCase().endsWith('.csv')) {
        setNotice(`${f.name}: only .csv files can be uploaded`);
        continue;
      }
      // Backend test names allow [A-Za-z0-9._-] only; sanitize instead of
      // letting "my test (1).csv" die with a 400 the user may never read.
      const testName = f.name
        .replace(/\.[^.]+$/, '')
        .replace(/[^A-Za-z0-9._-]+/g, '_');
      const id = ++uploadSeq.current;
      if (existing.has(testName)) {
        setUploads((prev) => [
          ...prev,
          {
            id,
            fileName: f.name,
            testName,
            progress: 0,
            error: `test '${testName}' already exists — delete or rename it first`,
          },
        ]);
        continue;
      }
      existing.add(testName);
      setUploads((prev) => [...prev, { id, fileName: f.name, testName, progress: 0 }]);
      try {
        const res = await uploadTest(
          f,
          testName,
          (fraction) =>
            setUploads((prev) =>
              prev.map((u) => (u.id === id ? { ...u, progress: fraction } : u))
            ),
          parseUploadFs(settings) // fallback rate, only used for bad time columns
        );
        setUploads((prev) => prev.filter((u) => u.id !== id));
        setNotice(`${res.name}: upload complete, ingesting…`);
      } catch (e) {
        // Keep the chip (with the reason) until the user dismisses it.
        const message = e instanceof Error ? e.message : String(e);
        setUploads((prev) =>
          prev.map((u) => (u.id === id ? { ...u, error: message } : u))
        );
      }
      try {
        setTests(await fetchTests());
      } catch {
        // list refresh failure is non-fatal; polling will catch up
      }
    }
  }, [tests, settings]);

  const dismissUpload = useCallback((id: number) => {
    setUploads((prev) => prev.filter((u) => u.id !== id));
  }, []);

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
      if (next.specMode !== prev.specMode) setSpecMode(next.specMode);
      if (next.specLogY !== prev.specLogY) setSpecLogY(next.specLogY);
      if (next.clustering !== prev.clustering) setClusteringEnabled(next.clustering);
    },
    [settings]
  );

  // Switching back from the split editor: the active test's TP definitions
  // may have changed — refetch it (and its stats).
  const handleTabChange = (next: AppTab) => {
    if (next === tab) return;
    setTab(next);
    if (next === 'analyze' && currentTest) {
      invalidateTest(currentTest);
    }
    if (next === 'uploads') {
      // Fresh history immediately; the 2 s poller takes over while open.
      fetchTests().then(setTests).catch(() => {});
    }
  };

  // -- Uploads tab callbacks --
  const handleOpenTest = (name: string) => {
    handleTestChange(name);
    setTab('analyze');
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

  const handleTestsChanged = async () => {
    try {
      setTests(await fetchTests());
    } catch {
      // poller will catch up
    }
  };

  // Uploads page recomputed a test's TP averages: drop only the stats cache
  // for that test (NOT selection/meta/TPs) so the scatter refetches the fresh
  // numbers on its next render.
  const handleStatsRebuilt = useCallback((name: string) => {
    setStatsCache((prev) => {
      if (!(name in prev)) return prev;
      const next = { ...prev };
      delete next[name];
      return next;
    });
    Array.from(statsInFlight.current)
      .filter((k) => k.startsWith(`${name}|`))
      .forEach((k) => statsInFlight.current.delete(k));
  }, []);

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
        const gen = testGen.current.get(test) ?? 0;
        fetchTpStats(test, col)
          .then((stats) => {
            if ((testGen.current.get(test) ?? 0) !== gen) return; // invalidated mid-flight
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

  // Rendered from two places: the normal tab switch and the no-tests screen
  // (the Uploads page must work before the first test exists).
  const uploadView = (
    <UploadView
      tests={tests}
      uploads={uploads}
      onUploadFiles={handleUploadFiles}
      onDismissUpload={dismissUpload}
      onOpenTest={handleOpenTest}
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
      columns={unionColumns}
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
      Drop CSV file(s) to upload
    </div>
  );

  // Loading state (before the active test's meta is available). The Uploads
  // page never needs meta — blanking it during a refetch would hide live
  // status exactly when the user is watching it.
  if ((loading || (currentTest && !meta)) && !error && tab !== 'uploads' && tab !== 'settings') {
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
          uploads={uploads}
          onDismissUpload={dismissUpload}
          notice={notice}
        />
        {tab === 'uploads' ? (
          uploadView
        ) : tab === 'settings' ? (
          settingsView
        ) : (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ textAlign: 'center', maxWidth: 500 }}>
              <div style={{ marginBottom: 12, fontSize: 16 }}>No test data available</div>
              <div style={{ color: '#909090', fontSize: 12 }}>
                Upload a test CSV with the button above, or drop a .csv file anywhere in this window.
              </div>
            </div>
          </div>
        )}
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
        uploads={uploads}
        onDismissUpload={dismissUpload}
        notice={notice}
      />

      {tab === 'uploads' ? (
        uploadView
      ) : tab === 'settings' ? (
        settingsView
      ) : tab === 'split' && meta ? (
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
              {rawScatterData.length >= 2 && (
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
            columns={gridColumns}
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
            fs={meta?.fs_hz ?? null}
            plotFilters={plotFilters}
            plotFilterSpecs={plotFilterSpecs}
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
            columnsByTest={columnsByTest}
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
