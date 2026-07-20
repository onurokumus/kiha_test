/** User preferences, persisted in localStorage (no backend involvement — the
 *  hard "no database" rule; a JSON blob per browser is plenty for UI prefs).
 *
 *  Column preferences are stored as plain names and applied only while the
 *  column exists in a loaded test — a preference naming a not-yet-uploaded
 *  column is kept verbatim and simply dormant (the Settings page shows it as
 *  "(not loaded)"). Precedence, weakest to strongest, everywhere:
 *    auto default  <  saved preference  <  explicit in-session pick.
 *  Settings only take effect via the page's SAVE button (App.handleSettingsSave
 *  applies exactly the fields that changed); edits are a draft until then.
 */
export interface AppSettings {
  /** Preferred scatter axes; '' = auto (the most-shared pair). */
  scatterX: string;
  scatterY: string;
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
  /** Assumed sample rate sent as ?fs= on upload — ONLY used when the CSV's
   *  time column is unusable and the backend must generate a uniform axis.
   *  Raw string ('' = backend default, 2048 Hz); parsed at upload time. */
  uploadFsHz: string;
}

export const DEFAULT_SETTINGS: AppSettings = {
  scatterX: '',
  scatterY: '',
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

export function loadSettings(): AppSettings {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    return normalizeSettings(JSON.parse(raw));
  } catch {
    return DEFAULT_SETTINGS;
  }
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
