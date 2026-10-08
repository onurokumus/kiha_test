import { createContext, useContext } from 'react';
import type { PlotHoverGroup } from './plotHoverGroup';

export const PlotHoverContext = createContext<PlotHoverGroup | null>(null);
export function usePlotHoverGroup() { return useContext(PlotHoverContext); }
