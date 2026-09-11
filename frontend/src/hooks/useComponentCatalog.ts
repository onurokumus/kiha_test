import { useCallback, useEffect, useRef, useState } from 'react';
import { createComponent, fetchComponents, isAbortError } from '../services/api';
import type { ComponentKind, HardwareComponent } from '../utils/components';

export function useComponentCatalog() {
  const [items, setItems] = useState<HardwareComponent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const alive = useRef(true);
  const reload = useCallback(async (signal?: AbortSignal) => {
    const version = ++generation.current;
    setLoading(true); setError('');
    try {
      const document = await fetchComponents(signal);
      if (alive.current && version === generation.current) setItems(document.components);
    } catch (e) {
      if (alive.current && version === generation.current && !isAbortError(e)) setError(e instanceof Error ? e.message : String(e));
    } finally { if (alive.current && version === generation.current) setLoading(false); }
  }, []);
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void reload(controller.signal);
    return () => { alive.current = false; controller.abort(); };
  }, [reload]);
  const add = async (kind: ComponentKind, name: string) => {
    const component = await createComponent(kind, name);
    if (alive.current) {
      // A prior GET cannot remove a just-created option.
      generation.current++; setLoading(false);
      setItems((prior) => [...prior.filter((item) => item.id !== component.id), component]);
    }
    return component;
  };
  return { items, loading, error, reload, add };
}
export type ComponentCatalogState = ReturnType<typeof useComponentCatalog>;
