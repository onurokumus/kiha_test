/** Stable source-column colors: editing one slot cannot recolor another. */
export function fullTestVariableColor(key: string, allConfigs: readonly { key: string }[]): string {
  const index = Math.max(0, allConfigs.findIndex(config => config.key === key));
  const palette = ['#d55e00', '#21834a', '#9333b8', '#c72c65', '#a17c00', '#c73b32'];
  if (index < palette.length) return palette[index];
  // Do not repeat a short palette for tests with hundreds of columns. Return
  // hex so envelope alpha and the subdued original share the exact same hue.
  // Keep blue for the overview and maintain contrast against white charts.
  const huePosition = ((index - palette.length) * 137.508 + 24) % 240;
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
