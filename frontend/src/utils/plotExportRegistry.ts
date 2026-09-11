import { useLayoutEffect } from 'react';
import type { PlotExportData, AnyPlotExportRequest } from '../types';
import type { PlotPngCapture } from './plotPngExport';

/** Both export entry points use the mounted plot's current source and canvas. */
export interface PlotExportHandle {
  label: string;
  scope: string;
  defaultData: PlotExportData;
  originalReason: string | null;
  filteredReason: string | null;
  pngReason: string | null;
  buildCsvRequest: (data: PlotExportData) => AnyPlotExportRequest;
  capturePng: () => PlotPngCapture;
}
export type RegisterPlotExport = (handle: PlotExportHandle | null) => void;
export interface RegisteredPlotExport { slot: number; handle: PlotExportHandle }

/** Only the dialog subscribes: plot readiness changes must not rerender the grid. */
export function createPlotExportRegistry() {
  let snapshot: RegisteredPlotExport[] = [];
  const listeners = new Set<() => void>();
  const registrations = Array.from({ length: 9 }, (_, index): RegisterPlotExport => (handle) => {
    const slot = index + 1;
    if (snapshot.find((entry) => entry.slot === slot)?.handle === (handle ?? undefined)) return;
    snapshot = snapshot.filter((entry) => entry.slot !== slot);
    if (handle) snapshot = [...snapshot, { slot, handle }].sort((a, b) => a.slot - b.slot);
    listeners.forEach((listener) => listener());
  });
  return {
    registrations,
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}
export type PlotExportRegistry = ReturnType<typeof createPlotExportRegistry>;

export function usePlotExportRegistration(register: RegisterPlotExport | undefined, handle: PlotExportHandle) {
  useLayoutEffect(() => { register?.(handle); });
  useLayoutEffect(() => () => { register?.(null); }, [register]);
}
