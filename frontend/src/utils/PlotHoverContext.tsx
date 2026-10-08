import { useLayoutEffect, useMemo, type ReactNode } from 'react';
import { createPlotHoverGroup, type PlotHoverMode } from './plotHoverGroup';
import { PlotHoverContext } from './usePlotHoverGroup';

export function PlotHoverProvider({ mode, children }: { mode: PlotHoverMode; children: ReactNode }) {
  const group = useMemo(createPlotHoverGroup, []);
  useLayoutEffect(() => { group.setMode(mode); }, [group, mode]);
  useLayoutEffect(() => () => group.clear(), [group]);
  return <PlotHoverContext.Provider value={group}>{children}</PlotHoverContext.Provider>;
}
