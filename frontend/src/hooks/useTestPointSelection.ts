import { useState, useCallback } from 'react';
import { SelectedTestPoint, TestPoint } from '../types';
import { assignColor } from '../utils/colorManager';

export const useTestPointSelection = (maxPoints: number = 6) => {
  const [selectedTPs, setSelectedTPs] = useState<SelectedTestPoint[]>([]);
  const [hiddenTPs, setHiddenTPs] = useState<Set<string>>(new Set());

  const toggleTestPoint = useCallback(
    (test: string, tp: TestPoint, endS: number) => {
      const id = `${test}:${tp.id}`;
      setSelectedTPs((prev) => {
        const existing = prev.find((s) => s.id === id);
        if (existing) {
          return prev.filter((s) => s.id !== id);
        }

        // Check if we've reached the max limit
        if (prev.length >= maxPoints) {
          return prev;
        }

        const color = assignColor(prev);
        return [
          ...prev,
          {
            id,
            test,
            tpId: tp.id,
            name: tp.name,
            label: tp.label,
            color,
            tp,
            endS,
            traces: {},
          },
        ];
      });
    },
    [maxPoints]
  );

  const toggleVisibility = useCallback((id: string) => {
    setHiddenTPs((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(id)) {
        newSet.delete(id);
      } else {
        newSet.add(id);
      }
      return newSet;
    });
  }, []);

  const removeTP = useCallback((id: string) => {
    setSelectedTPs((prev) => prev.filter((s) => s.id !== id));
    setHiddenTPs((prev) => {
      const newSet = new Set(prev);
      newSet.delete(id);
      return newSet;
    });
  }, []);

  const clearAll = useCallback(() => {
    setSelectedTPs([]);
    setHiddenTPs(new Set());
  }, []);

  return {
    selectedTPs,
    setSelectedTPs,
    hiddenTPs,
    toggleTestPoint,
    toggleVisibility,
    removeTP,
    clearAll,
  };
};
