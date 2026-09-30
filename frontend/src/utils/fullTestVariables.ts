import { themeSeriesColor } from '../constants/uplotTheme';

const SOURCE_PALETTE = ['#d55e00', '#21834a', '#9333b8', '#c72c65', '#a17c00', '#c73b32'];

function sourceColor(index: number): string {
  if (index < SOURCE_PALETTE.length) return SOURCE_PALETTE[index];
  // Spread preferred hues through large catalogs; conflicts are resolved when
  // variables share a plot. Hex keeps envelope/original alpha on the same hue.
  // Keep blue for the overview and maintain contrast against white charts.
  const huePosition = ((index - SOURCE_PALETTE.length) * 137.508 + 24) % 240;
  const hue = huePosition <= 165 ? huePosition : huePosition + 120;
  const saturation = .65, lightness = .38;
  const a = saturation * Math.min(lightness, 1 - lightness);
  const channel = (n: number) => {
    const k = (n + hue / 30) % 12;
    return Math.round(255 * (lightness - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))))
      .toString(16).padStart(2, '0');
  };
  return `#${channel(0)}${channel(8)}${channel(4)}`;
}

/** Preferred source color, retained for single-variable plots and safe pairs. */
export function fullTestVariableColor(key: string, allConfigs: readonly { key: string }[]): string {
  return sourceColor(Math.max(0, allConfigs.findIndex(config => config.key === key)));
}

type Lab = readonly [number, number, number];
interface ColorCandidate { color: string; light: Lab; dark: Lab }

// Oklab's sRGB conversion, https://bottosson.github.io/posts/oklab/ (public domain).
// Distance checks include the actual dark-theme whitening, not just light hues.
function oklab(color: string): Lab {
  const [r, g, b] = [1, 3, 5].map(offset => {
    const value = parseInt(color.slice(offset, offset + 2), 16) / 255;
    return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
  });
  const l = Math.cbrt(.4122214708 * r + .5363325363 * g + .0514459929 * b);
  const m = Math.cbrt(.2119034982 * r + .6806995451 * g + .1073969566 * b);
  const s = Math.cbrt(.0883024619 * r + .2817188376 * g + .6299787005 * b);
  return [.2104542553 * l + .7936177850 * m - .0040720468 * s,
    1.9779984951 * l - 2.4285922050 * m + .4505937099 * s,
    .0259040371 * l + .7827717662 * m - .8086757660 * s];
}

const describeColor = (color: string): ColorCandidate => ({
  color, light: oklab(color), dark: oklab(themeSeriesColor(color, true)),
});
const distance = (a: Lab, b: Lab) => (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2;
const separation = (a: ColorCandidate, b: ColorCandidate) =>
  Math.min(distance(a.light, b.light), distance(a.dark, b.dark));

// Broader chroma keeps several overlaid traces distinct after dark-theme lifting.
// Every candidate contrasts at least 3:1 against white; blue stays with the overview.
// Score the bounded palette against all assigned variables, not just the last one.
const FALLBACK_COLORS = [
  '#d55e00', '#ff00ff', '#ff4000', '#009999', '#ff0080', '#009900',
  '#b38c19', '#ff0040', '#8c19b3', '#ff0000', '#9333b8', '#999900',
  '#864d13', '#997300', '#730099', '#9900cc', '#a17c00', '#cc6600',
  '#739900', '#56606c', '#7900d6', '#e600c3', '#e00024', '#008a00',
].map(describeColor);
const MIN_SEPARATION = .12;

export type FullTestColorResolver = (keys: readonly string[]) => string[];

/** Resolve a comparison in order. Appending never recolors existing traces;
 * identical catalog/selection regenerates identical colors on session reopen.
 * Removing/replacing a variable can release its preferred hue for later ones. */
export function createFullTestColorResolver(allConfigs: readonly { key: string }[]): FullTestColorResolver {
  // Cache geometry once per catalog, including dropdown previews for many columns.
  const preferred = new Map(allConfigs.map((config, index) => [config.key, describeColor(sourceColor(index))]));
  return keys => {
    const assigned = new Map<string, ColorCandidate>();
    return keys.map(key => {
      const existing = assigned.get(key);
      if (existing) return existing.color;
      let chosen = preferred.get(key) ?? FALLBACK_COLORS[0];
      const score = (candidate: ColorCandidate) => {
        let minimum = Infinity;
        for (const other of assigned.values()) minimum = Math.min(minimum, separation(candidate, other));
        return minimum;
      };
      let bestScore = score(chosen);
      if (bestScore < MIN_SEPARATION ** 2) {
        for (const candidate of FALLBACK_COLORS) {
          const candidateScore = score(candidate);
          if (candidateScore > bestScore) { chosen = candidate; bestScore = candidateScore; }
        }
      }
      assigned.set(key, chosen);
      return chosen.color;
    });
  };
}
