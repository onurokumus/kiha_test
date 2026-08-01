/** User preferences. Personal choices persist in localStorage; a small JSON
 *  file on the server supplies page defaults to browsers without a personal
 *  override (still no database).
 *
 *  Column preferences are stored as plain names and applied only while the
 *  column exists in a loaded test — a preference naming a not-yet-uploaded
 *  column is kept verbatim and simply dormant (the Settings page shows it as
 *  "(not loaded)"). Bootstrap precedence is built-in < server-wide < personal.
 *  Runtime precedence is auto < active preference < explicit in-session pick.
 *  Settings only take effect through Save (or the publish-default action,
 *  which also saves locally); edits are a draft until then.
 */
export interface AppSettings {
  /** Preferred scatter axes; '' = auto (the most-shared pair). */
  scatterX: string;
  scatterY: string;
  /** Uploaded test/data zone whose rows form the scatter reference line.
   *  Its time column is interpreted as the ordered datasheet point ID. */
  datasheetZone: string;
  /** Initial visibility of the datasheet reference line. */
  datasheetVisible: boolean;
  /** Preferred plotted (Y) column by SLOT (index 0..8 = grid cells
   *  left-to-right, top-to-bottom), shared by every grid view mode;
   *  '' = auto for that slot (selection-driven fill). */
  gridColumns: string[];
  /** XY mode is fully independent per SLOT: its own plotted (Y) column
   *  ('' = follow the gridColumns slot) and its own X column
   *  ('' = auto, the first grid column). */
  xyYCols: string[];
  xyXCols: string[];
  /** Right-panel view mode on load. */
  defaultViewMode: 'tp' | 'full' | 'spectrum' | 'xy';
  specMode: 'fft' | 'welch';
  specLogY: boolean;
  /** Scatter overlap-clustering on load. */
  clustering: boolean;
  /** Default shown in per-upload time setup. Auto/column modes use it only
   *  when the source clock is unusable; generated mode makes it authoritative.
   *  Raw string ('' = backend default, 2048 Hz); parsed at upload time. */
  uploadFsHz: string;
}

export const DEFAULT_SETTINGS: AppSettings = {
  scatterX: '',
  scatterY: '',
  datasheetZone: '',
  datasheetVisible: true,
  gridColumns: Array.from({ length: 9 }, () => ''),
  xyYCols: Array.from({ length: 9 }, () => ''),
  xyXCols: Array.from({ length: 9 }, () => ''),
  defaultViewMode: 'tp',
  specMode: 'fft',
  specLogY: false,
  clustering: true,
  uploadFsHz: '',
};

const STORAGE_KEY = 'ptt.settings.v1';

// Set once during application bootstrap. A personal browser setting remains
// stronger; the built-in defaults remain the final offline fallback.
let pageDefaultSettings: AppSettings | null = null;

const str = (v: unknown): string => (typeof v === 'string' ? v : '');

const slots9 = (v: unknown): string[] =>
  Array.from({ length: 9 }, (_, i) => (Array.isArray(v) ? str(v[i]) : ''));

/** Coerce arbitrary parsed JSON (stored blob OR an imported file) into a valid
 *  AppSettings — unknown keys dropped, bad values fall back to defaults. Never
 *  throws: settings must not be able to wedge the app. */
export function normalizeSettings(raw: unknown): AppSettings {
  const p = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  // Legacy shape (pre per-slot XY): a single `xyXCol` string for all 9 cells.
  const xyXCols =
    !Array.isArray(p.xyXCols) && str(p.xyXCol)
      ? Array.from({ length: 9 }, () => str(p.xyXCol))
      : slots9(p.xyXCols);
  return {
    scatterX: str(p.scatterX),
    scatterY: str(p.scatterY),
    datasheetZone: str(p.datasheetZone),
    datasheetVisible:
      p.datasheetVisible === undefined
        ? DEFAULT_SETTINGS.datasheetVisible
        : !!p.datasheetVisible,
    gridColumns: slots9(p.gridColumns),
    xyYCols: slots9(p.xyYCols),
    xyXCols,
    defaultViewMode: (['tp', 'full', 'spectrum', 'xy'] as const).includes(
      p.defaultViewMode as never
    )
      ? (p.defaultViewMode as AppSettings['defaultViewMode'])
      : DEFAULT_SETTINGS.defaultViewMode,
    specMode: p.specMode === 'welch' ? 'welch' : 'fft',
    specLogY: !!p.specLogY,
    clustering: p.clustering === undefined ? DEFAULT_SETTINGS.clustering : !!p.clustering,
    uploadFsHz: str(p.uploadFsHz),
  };
}

function loadPersonalSettings(): AppSettings | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
    return normalizeSettings(parsed);
  } catch {
    return null;
  }
}

/** True only for a usable personal document. Invalid storage must not mask a
 * valid server-wide default. */
export function hasPersonalSettings(): boolean {
  return loadPersonalSettings() !== null;
}

/** Install the server response before React renders so every initial state is
 * seeded consistently (view mode, spectrum mode, grid preferences, etc.). */
export function setPageDefaultSettings(raw: unknown): void {
  pageDefaultSettings = normalizeSettings(raw);
}

export function loadSettings(): AppSettings {
  return loadPersonalSettings() ?? pageDefaultSettings ?? DEFAULT_SETTINGS;
}

export function saveSettings(s: AppSettings): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    // storage full/blocked — settings just won't persist this session
  }
}

/** settings.uploadFsHz parsed for the upload query param (undefined = omit). */
export function parseUploadFs(s: AppSettings): number | undefined {
  const fs = Number(s.uploadFsHz);
  return Number.isFinite(fs) && fs > 0 ? fs : undefined;
}
