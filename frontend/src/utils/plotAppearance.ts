export interface PlotAppearance {
  lineScale: number;
  pointScale: number;
}

export const PLOT_APPEARANCE_KEY = 'ptt.plot-appearance.v1';
export const DEFAULT_PLOT_APPEARANCE: Readonly<PlotAppearance> = Object.freeze({ lineScale: 1, pointScale: 1 });

function validScale(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0.5 && value <= 3
    ? value : 1;
}

/** Appearance is a browser preference, independent of source data and analysis. */
export function parsePlotAppearance(raw: string | null): PlotAppearance {
  try {
    const value: unknown = JSON.parse(raw ?? 'null');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return { ...DEFAULT_PLOT_APPEARANCE };
    const fields = value as Record<string, unknown>;
    return { lineScale: validScale(fields.lineScale), pointScale: validScale(fields.pointScale) };
  } catch { return { ...DEFAULT_PLOT_APPEARANCE }; }
}

const listeners = new Set<() => void>();
let current: Readonly<PlotAppearance> = DEFAULT_PLOT_APPEARANCE;
let initialized = false;

function readSaved(): PlotAppearance {
  try { return parsePlotAppearance(localStorage.getItem(PLOT_APPEARANCE_KEY)); }
  catch { return { ...DEFAULT_PLOT_APPEARANCE }; }
}

function publish(next: PlotAppearance) {
  if (next.lineScale === current.lineScale && next.pointScale === current.pointScale) return;
  current = Object.freeze(next);
  listeners.forEach(listener => listener());
}

function initialize() {
  if (initialized || typeof window === 'undefined') return;
  initialized = true;
  current = Object.freeze(readSaved());
  window.addEventListener('storage', event => {
    if (event.key === PLOT_APPEARANCE_KEY || event.key === null) publish(readSaved());
  });
}

export function getPlotAppearance(): Readonly<PlotAppearance> {
  initialize();
  return current;
}

export function subscribePlotAppearance(listener: () => void): () => void {
  initialize();
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function setPlotAppearance(patch: Partial<PlotAppearance>) {
  const previous = getPlotAppearance();
  const next = {
    lineScale: validScale(patch.lineScale ?? previous.lineScale),
    pointScale: validScale(patch.pointScale ?? previous.pointScale),
  };
  try { localStorage.setItem(PLOT_APPEARANCE_KEY, JSON.stringify(next)); }
  catch { /* Controls still work when browser storage is unavailable. */ }
  publish(next);
}

export function resetPlotAppearance() {
  setPlotAppearance(DEFAULT_PLOT_APPEARANCE);
}
