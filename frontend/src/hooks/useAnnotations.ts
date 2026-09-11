import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchAnnotations, saveAnnotations, isAbortError } from '../services/api';
import type { AnnotationDocument, TimeAnnotation } from '../utils/plotAnnotations';

export interface AnnotationState { document?: AnnotationDocument; error?: string; loading?: boolean }
export interface AnnotationManager {
  states: Record<string, AnnotationState>;
  reload: (test: string) => Promise<AnnotationDocument>;
  save: (document: AnnotationDocument, items: TimeAnnotation[]) => Promise<AnnotationDocument>;
}

/** One request per source test shared by all grid cells. No sample cache invalidation. */
export function useAnnotations(tests: string[]): AnnotationManager {
  const [states, setStates] = useState<Record<string, AnnotationState>>({});
  const versions = useRef<Record<string, number>>(Object.create(null));
  const alive = useRef(true);
  const key = JSON.stringify([...new Set(tests.filter(Boolean))].sort());
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const reload = useCallback(async (test: string, signal?: AbortSignal) => {
    const version = (versions.current[test] ?? 0) + 1;
    versions.current[test] = version;
    setStates((prev) => ({ ...prev, [test]: { loading: true } }));
    try {
      const document = await fetchAnnotations(test, signal);
      if (document.test !== test) throw new Error('Annotation response belongs to another test.');
      if (alive.current && version === versions.current[test]) setStates((prev) => ({ ...prev, [test]: { document } }));
      return document;
    } catch (error) {
      if (alive.current && !isAbortError(error) && version === versions.current[test]) {
        setStates((prev) => ({ ...prev, [test]: { error: error instanceof Error ? error.message : String(error) } }));
      }
      throw error;
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    for (const test of JSON.parse(key) as string[]) void reload(test, controller.signal).catch(() => {});
    return () => controller.abort();
  }, [key, reload]);
  const save = useCallback(async (document: AnnotationDocument, items: TimeAnnotation[]) => {
    const saved = await saveAnnotations(document, items);
    // A prior GET must not overwrite a completed mutation.
    versions.current[document.test] = (versions.current[document.test] ?? 0) + 1;
    if (alive.current) setStates((prev) => ({ ...prev, [document.test]: { document: saved } }));
    return saved;
  }, []);
  return { states, reload, save };
}
