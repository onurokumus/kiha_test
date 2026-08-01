// Domain types for the PTT frontend. Everything is schema-flexible: column
// names come from each test's meta.json — nothing here hardcodes variables.

/** One row of GET /api/tests */
export interface TestInfo {
  name: string;
  status: string; // 'ready' | 'receiving' | 'ingesting' | 'error' | ...
  error?: string | null;
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
  time_gap_count?: number;
  missing_rows_inserted?: number;
  time_gap_seconds?: number;
  time_gap_ranges?: Array<[number, number]>;
  duration_s: number;
  t_start?: number | null;
  source_file?: string;
  nan_counts?: Record<string, number>;
  nan_policy?: string;
  jitter_warning?: boolean;
  /** Free-form user descriptors: prop, motor, ESC, ambient conditions... */
  user_meta?: Record<string, string>;
}

/** One materialized derived-variable equation. Expressions use
 * Tecplot-style `{column name}` references. */
export interface FormulaSpec {
  name: string;
  expression: string;
  /** Existing data columns are protected unless replacement is explicit. */
  replace?: boolean;
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
  stride: number;
  n_raw: number;
  series: Record<string, { x: number[]; y: number[] }>;
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
}

/** One series trace: parallel t/y arrays, nulls are NaN gaps. */
export interface Trace {
  t: (number | null)[];
  y: (number | null)[];
}

/** GET /api/tests/{name}/data — windowed full-test read.
 *  `mode` is the resolved representation after applying the display request. */
export interface RawWindow {
  mode: 'raw';
  level: number;
  n_raw: number;
  i0: number;
  i1: number;
  t: (number | null)[];
  series: Record<string, (number | null)[]>;
}

export interface EnvelopeWindow {
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

/** GET /api/tests/{name}/spectrum */
export type SpectrumXAxis = 'hz' | 'per_rev';

export interface SpectrumData {
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
  nan_counts: Record<string, number>;
  replacement_counts?: Record<string, number>;
  spike_event_counts?: Record<string, number>;
  boundary_warning?: boolean;
  time_gap_count?: number;
  gap_segment_warning?: boolean;
};
