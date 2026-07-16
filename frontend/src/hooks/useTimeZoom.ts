import { useState, useCallback } from 'react';

export const useTimeZoom = () => {
  const [timeZoom, setTimeZoom] = useState<[number, number] | null>(null);

  const resetTimeZoom = useCallback(() => {
    setTimeZoom(null);
  }, []);

  return {
    timeZoom,
    setTimeZoom,
    resetTimeZoom,
  };
};
