export type WaterfallBand = 'low' | 'full';

export const WATERFALL_WINDOWS = [64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384];
export const WATERFALL_OVERLAPS = [0, 25, 50, 75];
export const WATERFALL_RESOLUTIONS = [
  { hz: 0.5, label: '0.5 Hz · 2 s' },
  { hz: 0.25, label: '0.25 Hz · 4 s' },
  { hz: 0.1, label: '0.1 Hz · 10 s' },
];

export interface WaterfallSettings {
  waterfallWindow: number;
  waterfallOverlap: number;
  /** Null preserves the explicitly selected sample-window size. */
  waterfallResolution: number | null;
  waterfallBand: WaterfallBand;
}

export const DEFAULT_WATERFALL_SETTINGS: WaterfallSettings = {
  waterfallWindow: 1024,
  waterfallOverlap: 75,
  waterfallResolution: 0.25,
  waterfallBand: 'low',
};

/** Old sessions record only a window/overlap. Keep their analysis meaning;
 * new workspaces and sessions predating Waterfall use the fine-band preset. */
export function normalizeWaterfallSettings(raw: Record<string, unknown>): WaterfallSettings {
  const legacy = 'waterfallWindow' in raw;
  return {
    waterfallWindow: WATERFALL_WINDOWS.includes(Number(raw.waterfallWindow))
      ? Number(raw.waterfallWindow) : DEFAULT_WATERFALL_SETTINGS.waterfallWindow,
    waterfallOverlap: raw.waterfallOverlap != null && WATERFALL_OVERLAPS.includes(Number(raw.waterfallOverlap))
      ? Number(raw.waterfallOverlap) : legacy ? 50 : DEFAULT_WATERFALL_SETTINGS.waterfallOverlap,
    waterfallResolution: raw.waterfallResolution === null
      ? null
      : typeof raw.waterfallResolution === 'number' && WATERFALL_RESOLUTIONS.some(item => item.hz === raw.waterfallResolution)
        ? raw.waterfallResolution : legacy ? null : DEFAULT_WATERFALL_SETTINGS.waterfallResolution,
    waterfallBand: raw.waterfallBand === 'low' || raw.waterfallBand === 'full'
      ? raw.waterfallBand : legacy ? 'full' : DEFAULT_WATERFALL_SETTINGS.waterfallBand,
  };
}
