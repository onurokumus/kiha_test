import { useSyncExternalStore } from 'react';
import { DEFAULT_PLOT_APPEARANCE, getPlotAppearance, subscribePlotAppearance } from '../utils/plotAppearance';

export function usePlotAppearance() {
  return useSyncExternalStore(subscribePlotAppearance, getPlotAppearance, () => DEFAULT_PLOT_APPEARANCE);
}
