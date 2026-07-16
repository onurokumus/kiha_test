import { COLORS } from '../constants/colors';
import { SelectedTestPoint } from '../types';

export const assignColor = (selectedPoints: SelectedTestPoint[]): string => {
  const usedColors = new Set(selectedPoints.map((s) => s.color));
  const availableColor = COLORS.find((c) => !usedColors.has(c));
  return availableColor || COLORS[selectedPoints.length % COLORS.length];
};
