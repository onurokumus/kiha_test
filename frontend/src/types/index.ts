// Domain types for the PTT frontend. Everything is schema-flexible: column
// names come from each test's meta.json — nothing here hardcodes variables.
import type { ComponentIds, ComponentSet } from '../utils/components';

/** One row of GET /api/tests */
export interface TestInfo {
  component_sets?: ComponentSet[];
  components?: ComponentIds | null;
  description?: string;
  name: string;
  status: string; // 'ready' | 'receiving' | 'ingesting' | 'error' | ...
  error?: string | null;
  data_quality?: DataQualitySummary | null;
  quality_revision?: string | null;
  n_rows?: number | null;
  fs_hz?: number | null;
  duration_s?: number | null;
  n_columns?: number | null;
  time_column?: string | null;
  time_source?: 'measured' | 'generated' | null;
  time_unit?: string | null;
  source_n_rows?: number | null;
  time_gap_count?: number | null;
  missing_rows_inserted?: number | null;
  time_gap_seconds?: number | null;
  source_file?: string | null;
  /** Self-reported attribution; it does not identify an authenticated user. */
  uploader_name?: string | null;
  /** UTC ISO; from meta.json, or dir creation time while receiving/ingesting
   *  (same format — lexicographic sort is chronological). */
  created_at?: string | null;
  edited_at?: string | null;
  ingest_seconds?: number | null;
  /** Ready: total on-disk footprint. Receiving: durably committed CSV bytes. */
  size_bytes?: number | null;
  upload_id?: string | null;
  received_bytes?: number | null;
  total_bytes?: number | null;
  received_chunks?: number | null;
  total_chunks?: number | null;
}

export interface TrashEntry {
  component_sets?: ComponentSet[];
  id: string;
  name: string;
  deleted_at: string | null;
  legacy_time_estimated?: boolean;
  state: 'stored' | 'deleting' | 'unavailable';
  restorable: boolean;
  status: string;
  description?: string;
  source_file?: string | null;
  uploader_name?: string | null;
  components?: ComponentIds | null;
  duration_s?: number | null;
  error?: string;
}

export type UploadPhase =
  | 'queued'
  | 'preparing'
  | 'verifying'
  | 'uploading'
  | 'retrying'
  | 'paused'
  | 'finalizing'
  | 'error';

/** One resumable CSV upload, shown as a header chip and in the active-transfer
 *  tray. `progress` combines server-confirmed bytes with the current
 *  multipart requests; `committedBytes` is the durable/resumable subset. */
export interface UploadItem {
  id: number;
  fileName: string;
  /** Self-reported attribution captured when the upload session was created. */
  uploaderName?: string;
  /** Sanitized server-side test name used to associate pre-session state with
   *  the backend's receiving transfer until an upload ID is available. */
  testName: string;
  progress: number | null; // 0..1 bytes sent; null = length unknown
  phase: UploadPhase;
  sessionId?: string;
  committedBytes: number;
  totalBytes: number;
  completedChunks: number;
  totalChunks: number;
  retryAttempt?: number;
  /** A refresh cannot retain browser permission to the local File. */
  requiresFile?: boolean;
  error?: string;
}

/** meta.json — GET /api/tests/{name} */
export interface TestMeta {
  component_sets?: ComponentSet[];
  component_sets_revision?: number;
  component_rpm_column?: string | null;
  component_rpm_revision?: number;
  components?: ComponentIds;
  components_revision?: number;
  description?: string;
  /** Editable test-level findings, separate from legacy free-form user_meta. */
  notes?: string;
  name: string;
  fs_hz: number;
  n_rows: number;
  n_columns: number;
  columns: string[];
  time_column: string;
  time_unit?: string;
  time_source?: 'measured' | 'generated';
  source_time_origin_s?: number | null;
  source_n_rows?: number;
  source_time_quality?: SourceTimeQuality;
  time_gap_count?: number;
  missing_rows_inserted?: number;
  time_gap_seconds?: number;
  time_gap_ranges?: Array<[number, number]>;
  duration_s: number;
  t_start?: number | null;
  source_file?: string;
  /** Self-reported attribution; absent on tests imported before this field. */
  uploader_name?: string | null;
  nan_counts?: Record<string, number>;
  /** Infinities are invalid numeric samples, counted separately from NaNs. */
  inf_counts?: Record<string, number>;
  nan_policy?: string;
  jitter_warning?: boolean;
  time_quantized?: boolean;
  skipped_columns?: Record<string, string>;
  edited_at?: string;
  /** Free-form user descriptors: prop, motor, ESC, ambient conditions... */
  user_meta?: Record<string, string>;
  /** Equations materialized into this test's working data. Older tests may
   * omit the field until their first derived variable is created. */
  derived_variables?:
    | DerivedVariableProvenance[]
    | Record<string, Omit<DerivedVariableProvenance, 'name'>>;
}

/** Counts are exact; timestamp examples are capped at eight per issue. */
export interface SourceTimeStep {
  /** One-based source data row; previous row is row - 1. Header excluded. */
  row: number;
  previous_s: number;
  time_s: number;
  missing_rows?: number;
}

export interface SourceTimeQuality {
  version: 1;
  checked: boolean;
  column: string | null;
  n_rows: number;
  axis_reason: 'measured' | 'generated_requested' | 'no_time_column' | 'invalid_source_time';
  duplicate_steps?: { count: number; examples: SourceTimeStep[] };
  backward_steps?: { count: number; examples: SourceTimeStep[] };
  invalid_timestamps?: { count: number; example_rows: number[] };
  gap_count?: number | null;
  gap_examples?: SourceTimeStep[];
}

export interface DataQualitySummary {
  warnings: string[];
  partial: boolean;
}

/** One materialized derived-variable equation. Expressions use
 * Tecplot-style `{column name}` references. */
export interface FormulaSpec {
  name: string;
  expression: string;
  /** Existing data columns are protected unless replacement is explicit. */
  replace?: boolean;
}

/** Persisted formula metadata returned inside TestMeta. */
export interface DerivedVariableProvenance extends FormulaSpec {
  dependencies?: string[];
  missing_dependencies?: string[];
  engine?: string;
  replaced_existing?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface FormulaPreviewItem extends FormulaSpec {
  dependencies: string[];
  replaces_existing: boolean;
  stats: {
    count: number;
    valid_count: number;
    nan_count: number;
    min: number | null;
    max: number | null;
    mean: number | null;
  };
  values: (number | null)[];
}

/** POST /api/tests/{name}/formulas/preview */
export interface FormulaPreview {
  valid: true;
  sample_size: number;
  row_indices: number[];
  time: (number | null)[];
  formulas: FormulaPreviewItem[];
}

/** One reusable, server-stored equation recipe. */
export interface FormulaRecipe {
  name: string;
  description?: string;
  formulas: FormulaSpec[];
  updated_at?: string | null;
}

export interface FormulaRecipeList {
  version: 1;
  recipes: FormulaRecipe[];
}

/** POST /api/tests/{name}/edit — destructive rebuild operations */
export interface EditOps {
  rename?: Record<string, string>;
  drop?: string[];
  trim_t0?: number | null;
  trim_t1?: number | null;
  nan_policy?: string | null;
  formulas?: FormulaSpec[];
}

/** GET /api/tests/{name}/xy — NaN pairs already dropped server-side */
export interface XYData {
  /** Source/method snapshot returned with these exact loaded values. */
  analysis?: Record<string, unknown>;
  stride: number;
  n_raw: number;
  series: Record<string, { x: number[]; y: number[]; sample_indices?: number[];
    finite_count?: number; missing_pair_count?: number; fallback_first_finite?: boolean }>;
  method_version?: string;
  source?: 'stored';
  prefilter?: 'none';
  missing_values?: 'omit_nonfinite_pairs';
  reduction?: 'stride';
  i0?: number;
  i1?: number;
  tp_id?: number | null;
  time_column?: string;
  time_start_s?: number | null;
  time_end_s?: number | null;
  fs_hz?: number | null;
  x_col?: string;
}

/** One entry of testpoints.json */
export interface TestPoint {
  id: number;
  name: string;
  label: string;
  start_s: number;
  end_s: number | null;
  start_idx?: number | null;
  end_idx?: number | null;
  notes?: string;
}

export interface TestPointsFile {
  version: number;
  test: string;
  source_file?: string;
  fs_hz?: number | null;
  test_points: TestPoint[];
}

/** Per-TP aggregate of one column — GET /api/tests/{name}/tp_stats?col= */
export interface TpStat {
  id: number;
  name: string;
  label: string;
  n: number;
  n_valid: number;
  mean: number | null;
  min: number | null;
  max: number | null;
  /** Unrounded original-data statistics; absent on older servers. */
  summary?: {
    method: 'finite-population-v1';
    mean: number | null;
    std_population: number | null;
    /** Exact zero-based half-open saved TP bounds, null for invalid ranges. */
    i0: number | null;
    i1: number | null;
  };
}

/** One series trace: parallel t/y arrays, nulls are NaN gaps. */
export interface Trace {
  /** Source/method snapshot returned with these exact loaded values. */
  analysis?: Record<string, unknown>;
  t: (number | null)[];
  y: (number | null)[];
}

/** GET /api/tests/{name}/data — windowed full-test read.
 *  `mode` is the resolved representation after applying the display request. */
export interface RawWindow {
  /** Source/method snapshot returned with these exact loaded values. */
  analysis?: Record<string, unknown>;
  mode: 'raw';
  level: number;
  n_raw: number;
  i0: number;
  i1: number;
  t: (number | null)[];
  series: Record<string, (number | null)[]>;
}

export interface EnvelopeWindow {
  /** Source/method snapshot returned with these exact loaded values. */
  analysis?: Record<string, unknown>;
  mode: 'envelope';
  level: number;
  n_raw: number;
  i0: number;
  i1: number;
  t: (number | null)[];
  series: Record<string, { min: (number | null)[]; max: (number | null)[] }>;
}

export type DataWindow = RawWindow | EnvelopeWindow;
export type WindowDisplayMode = 'auto' | 'line' | 'envelope';

export type PlotExportData = 'original' | 'filtered' | 'both';
export interface PlotExportSource {
  test: string;
  tp_id?: number;
  t0?: number | null;
  t1?: number | null;
  px: number;
  display: WindowDisplayMode;
  expected_i0?: number;
  expected_i1?: number;
}
export interface PlotExportRequest {
  include_metadata?: boolean;
  column: string;
  data: PlotExportData;
  sources: PlotExportSource[];
  filter: FilterSpec | null;
  /** Actual inclusive X sample centers, relative for TP, absolute for Full. */
  x_range: [number, number] | null;
}

export interface PlotExportBundleRequest {
  include_metadata?: boolean;
  layout: '2x2' | '3x3';
  plots: { slot: number; request: AnyPlotExportRequest }[];
}

export interface SpectrumExportSource {
  test: string;
  tp_id?: number;
  t0?: number | null;
  t1?: number | null;
  expected_i0: number;
  expected_i1: number;
  expected_fs_hz: number;
  nperseg: number;
  rpm_col?: string;
  expected_mean_rpm?: number;
}
export interface SpectrumExportRequest {
  include_metadata?: boolean;
  kind: 'spectrum';
  column: string;
  mode: 'fft' | 'welch';
  axis: SpectrumXAxis;
  method_version: 'kiha-spectrum-v2';
  sources: SpectrumExportSource[];
  x_range: [number, number] | null;
}
export interface XYExportSource {
  test: string;
  tp_id?: number;
  t0?: number | null;
  t1?: number | null;
  expected_i0: number;
  expected_i1: number;
  expected_time_column: string;
}
export interface XYExportRequest {
  include_metadata?: boolean;
  kind: 'xy';
  column: string;
  x_column: string;
  method_version: 'kiha-xy-v2';
  sources: XYExportSource[];
  x_range: [number, number] | null;
  y_range: [number, number] | null;
}
export interface WaterfallData {
  col: string; tp_id: number | null; i0: number; i1: number; fs_hz: number;
  n_samples: number; nan_count: number; time_start_s: number; time_end_s: number;
  frequency_edges_hz: number[]; time_edges_s: number[]; magnitude: number[][];
  source_frame_start_s: number[]; source_frame_end_s: number[];
  method: { version: 'kiha-waterfall-v1' | 'kiha-waterfall-v2'; nperseg: number; noverlap: number; hop_samples: number;
    window_seconds: number; step_seconds: number; bin_spacing_hz: number; frame_count: number;
    trailing_samples: number; [key: string]: unknown };
  reduction: { method: 'max' | 'none'; time_factor: number; frequency_factor: number; native_frames: number; native_bins: number };
  grid?: {
    frequency_range_hz: [number, number] | null;
    time_range_s: [number, number] | null;
    full_frequency_range_hz: [number, number];
    full_time_range_s: [number, number];
    [key: string]: unknown;
  };
  quality: Record<string, unknown>;
  analysis: Record<string, unknown>;
}
export interface WaterfallExportRequest {
  kind: 'waterfall'; column: string; method_version: 'kiha-waterfall-v1' | 'kiha-waterfall-v2';
  nperseg: number; overlap: number; include_metadata?: boolean;
  resolution_hz?: number | null;
  grid_frequency_range_hz?: [number, number] | null;
  grid_time_range_s?: [number, number] | null;
  x_range: [number, number] | null; y_range: [number, number] | null;
  sources: { test: string; tp_id?: number; t0?: number | null; t1?: number | null;
    expected_i0: number; expected_i1: number; expected_fs_hz: number }[];
}
export type AnyPlotExportRequest = PlotExportRequest | SpectrumExportRequest | XYExportRequest | WaterfallExportRequest;

/** GET /api/tests/{name}/testpoints/{tp_id}/data */
export interface TpTraceResponse {
  mode: 'raw' | 'envelope';
  level: number;
  n_raw: number;
  i0: number;
  i1: number;
  point_budget: number;
  time_origin_s: number;
  duration_s: number;
  test: string;
  test_point: { id: number; name: string; label: string };
  series: Record<string, Trace>;
}

export interface SelectedTestPoint {
  id: string; // `${test}:${tp.id}` — globally unique across tests
  test: string;
  tpId: number;
  name: string;
  label: string;
  color: string;
  tp: TestPoint;
  /** Resolved end time (open-ended TPs run to the next TP / end of data),
   *  computed at selection time — TP-sourced spectra/XY need a real range. */
  endS: number;
  /** Column traces fetched on demand (relative time from TP start). */
  traces: Record<string, Trace>;
}

export interface ScatterDataPoint {
  x: number;
  y: number;
  /** Asymmetric distances from the X mean to [minimum, maximum]. */
  xError?: [number, number];
  /** Asymmetric distances from the Y mean to [minimum, maximum]. */
  yError?: [number, number];
  id: string; // `${test}:${tp.id}`
  test: string;
  name: string;
  label: string;
  tp: TestPoint;
  color: string;
  isSelected: boolean;
}

/** One valid X/Y row from the configured datasheet zone. The source CSV uses
 * its time column as an ordered point ID; rows missing either selected axis
 * are omitted before this reaches the scatter plot. */
export interface DatasheetDataPoint {
  x: number;
  y: number;
  id: string;
  pointId: number;
  zone: string;
  isDatasheet: true;
}

/** Aggregation mode for test-point range filters.
 *  'any' = keep the TP if any sample can fall in range (min/max overlap). */
export type AggMode = 'mean' | 'min' | 'max' | 'any';

export interface ParameterFilter {
  id: string; // unique ID for React keys
  column: string;
  mode: AggMode;
  min: number | null;
  max: number | null;
}

export interface ScatterFilterState {
  /** Selected TP keys (`${test}:${tpId}`); empty = all included. */
  tpKeys: string[];
  labels: string[];
  parameterFilters: ParameterFilter[];
}

export interface TestTreeNode {
  test: string;
  tps: { key: string; name: string; label: string }[];
}

export interface FilterOptions {
  testTree: TestTreeNode[];
  labels: string[];
}

/** Column stats lookup: column -> (tp id -> stat). */
export type StatsByColumn = Record<string, Record<number, TpStat>>;

/** Multi-test stats cache: test -> column -> (tp id -> stat). */
export type StatsCache = Record<string, StatsByColumn>;

export interface TimePlotConfig {
  key: string; // column name; units are encoded in the name (e.g. thrust_n)
  label: string;
}

/** GET /api/tests/{name}/split/candidates — ID-like columns for auto-split */
export interface IdCandidate {
  col: string;
  n_unique: number;
}

/** Read-only auto-split preview; applying it still requires the normal TP Save. */
export interface AutoSplitOptions {
  columns: string[];
  ignore_zero: boolean;
  min_len_s: number;
}

export interface AutoSplitProposal extends AutoSplitOptions {
  method: 'value_changes';
  fs_hz: number;
  test_points: TestPoint[];
  sample_count: number;
  excluded: {
    missing_samples: number;
    zero_samples: number;
    /** Absent in older preview responses. Single samples cannot prove constancy. */
    isolated_samples?: number;
    short_runs: number;
  };
}

/** GET /api/tests/{name}/spectrum */
export type SpectrumXAxis = 'hz' | 'per_rev';

export interface SpectrumData {
  /** Source/method snapshot returned with these exact loaded values. */
  analysis?: Record<string, unknown>;
  mode: 'fft' | 'welch';
  col: string;
  fs_hz: number;
  n_samples: number;
  nan_count: number;
  freqs: (number | null)[];
  mag: (number | null)[];
  /** Present when the request includes rpm_col. The speed statistics use the
   *  exact spectrum window and absolute RPM so reverse rotation still yields
   *  a positive cycles-per-revolution scale. */
  rpm_col?: string;
  mean_rpm?: number;
  min_rpm?: number;
  max_rpm?: number;
  rpm_finite_count?: number;
  rpm_nan_count?: number;
  /** Exact saved half-open rows; time fields are actual first/last centers. */
  i0?: number;
  i1?: number;
  tp_id?: number | null;
  time_start_s?: number;
  time_end_s?: number;
  finite_count?: number;
  bin_indices?: number[];
  method?: {
    version: string;
    source: 'stored';
    prefilter: 'none';
    sampling: 'metadata_fs';
    missing_values: 'linear_by_index_hold_edges';
    detrend: 'constant';
    window: 'rectangular' | 'hann_periodic';
    nfft: number;
    bin_spacing_hz: number;
    onesided: boolean;
    scaling: 'peak_amplitude' | 'density';
    units: 'U' | 'U²/Hz';
    nperseg: number | null;
    noverlap: number | null;
    average: 'mean' | null;
    segment_count: number;
    used_samples: number;
    trailing_samples: number;
  };
  reduction?: {
    method: 'none' | 'max-bin';
    factor: number;
    n_bins_original: number;
    n_bins_returned: number;
  };
  quality?: {
    known_gap_count: number;
    gap_metadata_available: boolean;
    time_source: string | null;
    time_quantized: boolean | null;
    jitter_warning: boolean | null;
  };
  peak?: { frequency_hz: number; magnitude: number | null; bin_index: number };
}

export type FilterKind =
  | 'lowpass'
  | 'highpass'
  | 'bandpass'
  | 'bandstop'
  | 'moving_avg'
  | 'despike'
  | 'detrend';

export interface FilterSpec {
  kind: FilterKind;
  order?: number;
  f1?: number;
  f2?: number;
  windowS?: number;
  maxSpikeS?: number;
  threshold?: number;
  absFloor?: number;
  replacement?: 'linear' | 'median';
}

/** GET /api/tests/{name}/filter — same window shapes as /data plus warnings */
export type FilteredWindow = DataWindow & {
  /** Exact TP filter response: origin is the first stored sample, not start_s. */
  time_origin_s?: number;
  tp_id?: number;
  /** Subtract exact TP origin before plot serialization rounds timestamps. */
  relative_t?: (number | null)[];
  nan_counts: Record<string, number>;
  replacement_counts?: Record<string, number>;
  spike_event_counts?: Record<string, number>;
  boundary_warning?: boolean;
  time_gap_count?: number;
  gap_segment_warning?: boolean;
};
