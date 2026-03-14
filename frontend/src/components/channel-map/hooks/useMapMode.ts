import { useState, useCallback } from 'react';
import type { MapMode } from '../constants';

export function useMapMode() {
  const [mode, setMode] = useState<MapMode>('discovery');
  const switchMode = useCallback((m: MapMode) => setMode(m), []);
  return { mode, switchMode } as const;
}
