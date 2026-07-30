import { useState, useCallback } from 'react';

export const useTimeZoom = (initialZoom: [number, number] | null = null) => {
  const [timeZoom, setTimeZoom] = useState<[number, number] | null>(initialZoom);

  const resetTimeZoom = useCallback(() => {
    setTimeZoom(null);
  }, []);

  return {
    timeZoom,
    setTimeZoom,
    resetTimeZoom,
  };
};
