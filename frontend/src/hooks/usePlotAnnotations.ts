import { useEffect, useRef, type RefObject } from 'react';
import type uPlot from 'uplot';
import type { AnnotationManager } from './useAnnotations';
import { projectAnnotations, type AnnotationSource, type PlotAnnotation } from '../utils/plotAnnotations';

export function annotationAvailability(sources: AnnotationSource[], manager: AnnotationManager, visible: boolean) {
  if (!visible) return null;
  const failed = sources.find((source) => !manager.states[source.test]?.document);
  return failed ? `Time notes for ${failed.test} are unavailable. Open Notes to reload them, or hide time notes before exporting.` : null;
}

/** Read current sources and legend state even during uPlot's local gestures. */
export function usePlotAnnotations(sources: AnnotationSource[], manager: AnnotationManager,
  visible: boolean, plotRef: RefObject<uPlot | null>) {
  const getItems = (plot: uPlot): PlotAnnotation[] => visible ? sources.flatMap((source) => {
    const document = manager.states[source.test]?.document;
    if (!document || !source.seriesIndices.some((index) => plot.series[index] && plot.series[index].show !== false)) return [];
    return projectAnnotations(source, document).filter((item) =>
      item.displayStart <= (plot.scales.x.max ?? Infinity) &&
      (item.displayEnd ?? item.displayStart) >= (plot.scales.x.min ?? -Infinity));
  }) : [];
  const itemsRef = useRef(getItems);
  itemsRef.current = getItems;
  useEffect(() => { plotRef.current?.redraw(); }, [sources, manager.states, visible, plotRef]);
  return itemsRef;
}
