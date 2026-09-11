import { DEFAULT_FILTER_UI, FilterUi } from '../constants/filters';
import { MAX_SELECTED_TEST_POINTS } from '../constants/selection';
import { AggMode, ScatterFilterState, SpectrumXAxis, WindowDisplayMode } from '../types';
import { normalizeTimeYRanges, SavedTimeYRange } from '../utils/timePlotRanges';
import type { SavedSourceReference } from './sessionSources';
import { emptyPlotViewports, normalizePlotViewports, PlotViewports } from '../utils/plotViewport';

export type AnalysisViewMode = 'tp' | 'full' | 'spectrum' | 'xy';
export type PlotDensity = 'single' | 'quad' | 'nine';

export interface SavedTestPointSelection {
  test: string;
  tpId: number;
  hidden: boolean;
  color?: string;
}

export interface AnalysisSession {
  version: 1;
  plotViewports: PlotViewports;
  expandedPlot: number | null;
  clusteringEnabled?: boolean;
  datasheetVisible?: boolean;
  showHorizontalErrorBars: boolean;
  showVerticalErrorBars: boolean;
  /** Absent only in pre-identity sessions. Empty is deliberately different. */
  sources?: SavedSourceReference[];
  currentTest: string;
  xAxis: string;
  yAxis: string;
  axesUserSet: boolean;
  selections: SavedTestPointSelection[];
  filterState: ScatterFilterState;
  mainZoom: [number, number, number, number] | null;
  timeZoom: [number, number] | null;
  timeYRanges: (SavedTimeYRange | null)[];
  fullRange: [number, number] | null;
  viewMode: AnalysisViewMode;
  fullPlotMode: WindowDisplayMode;
  specMode: 'fft' | 'welch';
  specXAxis: SpectrumXAxis;
  specRpmCol: string;
  specLogY: boolean;
  specSource: 'tp' | 'full';
  xySource: 'tp' | 'full';
  plotConfigs: string[];
  plotsUserEdited: boolean;
  plotFilters: FilterUi[];
  /** Display-only per-grid-slot choice, independent of DSP settings. */
  plotShowOriginal: boolean[];
  annotationsVisible: boolean;
  xyYCols: string[];
  xyXCols: string[];
  scatterRatio: number;
  scatterCollapsed: boolean;
  plotDensity: PlotDensity;
}

const STORAGE_KEY = 'ptt.analysis-session.v1';

export const defaultAnalysisSession = (): AnalysisSession => ({
  version: 1,
  plotViewports: emptyPlotViewports(),
  expandedPlot: null,
  showHorizontalErrorBars: false,
  showVerticalErrorBars: false,
  currentTest: '',
  xAxis: '',
  yAxis: '',
  axesUserSet: false,
  selections: [],
  filterState: { tpKeys: [], labels: [], parameterFilters: [] },
  mainZoom: null,
  timeZoom: null,
  timeYRanges: normalizeTimeYRanges(null),
  fullRange: null,
  viewMode: 'tp',
  fullPlotMode: 'auto',
  specMode: 'fft',
  specXAxis: 'hz',
  specRpmCol: '',
  specLogY: false,
  specSource: 'tp',
  xySource: 'tp',
  plotConfigs: [],
  plotsUserEdited: false,
  plotFilters: Array.from({ length: 9 }, () => ({ ...DEFAULT_FILTER_UI })),
  plotShowOriginal: Array(9).fill(false),
  annotationsVisible: true,
  xyYCols: [],
  xyXCols: [],
  scatterRatio: 40,
  scatterCollapsed: false,
  plotDensity: 'nine',
});

const isObject = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);

const stringValue = (value: unknown): string => (typeof value === 'string' ? value : '');

const numericTextValue = (value: unknown): string => {
  if (typeof value === 'string') return value;
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
};

const stringArray = (value: unknown, max = 1000): string[] =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string').slice(0, max)
    : [];

const finiteTuple = <T extends 2 | 4>(value: unknown, length: T): number[] | null => {
  if (!Array.isArray(value) || value.length !== length) return null;
  const values = value.map(Number);
  return values.every(Number.isFinite) ? values : null;
};

const pair = (value: unknown): [number, number] | null => {
  const values = finiteTuple(value, 2);
  return values && values[0] < values[1] ? [values[0], values[1]] : null;
};

const zoom = (value: unknown): [number, number, number, number] | null => {
  const values = finiteTuple(value, 4);
  return values && values[0] < values[1] && values[2] < values[3]
    ? [values[0], values[1], values[2], values[3]]
    : null;
};

const source = (value: unknown): 'tp' | 'full' => (value === 'full' ? 'full' : 'tp');

const viewMode = (value: unknown): AnalysisViewMode =>
  value === 'full' || value === 'spectrum' || value === 'xy' ? value : 'tp';

const windowDisplayMode = (value: unknown): WindowDisplayMode =>
  value === 'line' || value === 'envelope' ? value : 'auto';

const density = (value: unknown): PlotDensity =>
  value === 'single' || value === 'quad' ? value : 'nine';

const normalizeFilterUi = (value: unknown): FilterUi => {
  const raw = isObject(value) ? value : {};
  const kind =
    raw.kind === 'lowpass' ||
    raw.kind === 'highpass' ||
    raw.kind === 'bandpass' ||
    raw.kind === 'bandstop' ||
    raw.kind === 'moving_avg' ||
    raw.kind === 'despike' ||
    raw.kind === 'detrend'
      ? raw.kind
      : '';
  const legacyWindowS = Number(raw.window_s);
  const legacyMaxSpikeS = Number(raw.max_spike_s);
  return {
    kind,
    order: stringValue(raw.order) || DEFAULT_FILTER_UI.order,
    f1: stringValue(raw.f1),
    f2: stringValue(raw.f2),
    winS: stringValue(raw.winS) || DEFAULT_FILTER_UI.winS,
    despikeWindowMs:
      numericTextValue(raw.despikeWindowMs) ||
      (Number.isFinite(legacyWindowS) && legacyWindowS > 0
        ? String(legacyWindowS * 1000)
        : DEFAULT_FILTER_UI.despikeWindowMs),
    maxSpikeMs:
      numericTextValue(raw.maxSpikeMs) ||
      (Number.isFinite(legacyMaxSpikeS) && legacyMaxSpikeS > 0
        ? String(legacyMaxSpikeS * 1000)
        : DEFAULT_FILTER_UI.maxSpikeMs),
    threshold: numericTextValue(raw.threshold) || DEFAULT_FILTER_UI.threshold,
    absFloor:
      numericTextValue(raw.absFloor) ||
      numericTextValue(raw.abs_floor) ||
      DEFAULT_FILTER_UI.absFloor,
    replacement: raw.replacement === 'median' ? 'median' : 'linear',
  };
};

const normalizeFilterState = (value: unknown): ScatterFilterState => {
  const raw = isObject(value) ? value : {};
  const parameterFilters = Array.isArray(raw.parameterFilters)
    ? raw.parameterFilters.flatMap((item, index) => {
        if (!isObject(item)) return [];
        const mode: AggMode =
          item.mode === 'min' || item.mode === 'max' || item.mode === 'any' ? item.mode : 'mean';
        const min = item.min === null || item.min === undefined ? null : Number(item.min);
        const max = item.max === null || item.max === undefined ? null : Number(item.max);
        return [
          {
            id: stringValue(item.id) || `restored-filter-${index}`,
            column: stringValue(item.column),
            mode,
            min: min !== null && Number.isFinite(min) ? min : null,
            max: max !== null && Number.isFinite(max) ? max : null,
          },
        ];
      })
    : [];
  return {
    tpKeys: stringArray(raw.tpKeys),
    labels: stringArray(raw.labels),
    parameterFilters,
  };
};

export function normalizeAnalysisSession(value: unknown): AnalysisSession {
  const defaults = defaultAnalysisSession();
  if (!isObject(value)) return defaults;

  const selections = Array.isArray(value.selections)
    ? value.selections.flatMap((item) => {
        if (!isObject(item)) return [];
        const test = stringValue(item.test);
        const tpId = Number(item.tpId);
        return test && Number.isInteger(tpId) ? [{ test, tpId, hidden: !!item.hidden,
          color: typeof item.color === 'string' && /^#[0-9a-f]{6}$/i.test(item.color) ? item.color : undefined }] : [];
      })
    : [];
  const rawRatio = Number(value.scatterRatio);
  const scatterRatio = Number.isFinite(rawRatio)
    ? Math.min(62, Math.max(24, rawRatio))
    : defaults.scatterRatio;

  return {
    version: 1,
    plotViewports: normalizePlotViewports(value.plotViewports),
    expandedPlot: typeof value.expandedPlot === 'number' && Number.isInteger(value.expandedPlot) && value.expandedPlot >= 0 && value.expandedPlot < 9 ? value.expandedPlot : null,
    clusteringEnabled: typeof value.clusteringEnabled === 'boolean' ? value.clusteringEnabled : undefined,
    datasheetVisible: typeof value.datasheetVisible === 'boolean' ? value.datasheetVisible : undefined,
    showHorizontalErrorBars: value.showHorizontalErrorBars === true,
    showVerticalErrorBars: value.showVerticalErrorBars === true,
    sources: Array.isArray(value.sources) ? value.sources.flatMap((item) => {
      if (!isObject(item) || typeof item.name !== 'string') return [];
      return [{ name: item.name, id: typeof item.id === 'string' ? item.id : null,
        revision: typeof item.revision === 'string' ? item.revision : undefined,
        test_points: Array.isArray(item.test_points) ? item.test_points.flatMap((point) =>
          isObject(point) && typeof point.id === 'number' && Number.isInteger(point.id) && typeof point.revision === 'string'
            ? [{ id: point.id, revision: point.revision }] : []) : [] }];
    }).slice(0, 1000) : undefined,
    currentTest: stringValue(value.currentTest),
    xAxis: stringValue(value.xAxis),
    yAxis: stringValue(value.yAxis),
    axesUserSet: !!value.axesUserSet,
    selections: selections.slice(0, MAX_SELECTED_TEST_POINTS),
    filterState: normalizeFilterState(value.filterState),
    mainZoom: zoom(value.mainZoom),
    timeZoom: pair(value.timeZoom),
    timeYRanges: normalizeTimeYRanges(value.timeYRanges),
    fullRange: pair(value.fullRange),
    viewMode: viewMode(value.viewMode),
    fullPlotMode: windowDisplayMode(value.fullPlotMode),
    specMode: value.specMode === 'welch' ? 'welch' : 'fft',
    specXAxis: value.specXAxis === 'per_rev' ? 'per_rev' : 'hz',
    specRpmCol: stringValue(value.specRpmCol),
    specLogY: !!value.specLogY,
    specSource: source(value.specSource),
    xySource: source(value.xySource),
    plotConfigs: stringArray(value.plotConfigs, 9),
    plotsUserEdited: !!value.plotsUserEdited,
    plotFilters: Array.from({ length: 9 }, (_, index) =>
      normalizeFilterUi(Array.isArray(value.plotFilters) ? value.plotFilters[index] : null)
    ),
    plotShowOriginal: Array.from({ length: 9 }, (_, index) =>
      Array.isArray(value.plotShowOriginal) && value.plotShowOriginal[index] === true
    ),
    xyYCols: stringArray(value.xyYCols, 9),
    annotationsVisible: value.annotationsVisible !== false,
    xyXCols: stringArray(value.xyXCols, 9),
    scatterRatio,
    scatterCollapsed: !!value.scatterCollapsed,
    plotDensity: density(value.plotDensity),
  };
}

export function loadAnalysisSession(): AnalysisSession {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored ? normalizeAnalysisSession(JSON.parse(stored)) : defaultAnalysisSession();
  } catch {
    return defaultAnalysisSession();
  }
}

export function hasSavedAnalysisSession(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== null;
  } catch {
    return false;
  }
}

export function saveAnalysisSession(session: AnalysisSession): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    // Session persistence is an enhancement; storage restrictions must not
    // prevent analysis from continuing in the current tab.
  }
}
