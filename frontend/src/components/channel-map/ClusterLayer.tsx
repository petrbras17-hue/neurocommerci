import { useMemo } from 'react';
import type { ClusterPoint } from './hooks/useClusters';
import { getCategoryMeta, DESIGN_TOKENS as T } from './constants';

interface Props {
  clusters: ClusterPoint[];
}

/**
 * Creates an HTML element for a numeric cluster bubble to be rendered
 * via react-globe.gl's htmlElementsData layer.
 */
export function createClusterElement(cluster: ClusterPoint): HTMLElement {
  const meta = getCategoryMeta(cluster.dominant_category);
  const size = clusterSize(cluster.count);

  const el = document.createElement('div');
  el.style.cssText =
    `width:${size}px;height:${size}px;border-radius:50%;` +
    `display:flex;align-items:center;justify-content:center;` +
    `background:${meta.color}30;border:2px solid ${meta.color}90;` +
    `color:#fff;font-family:'JetBrains Mono',monospace;` +
    `font-size:${size > 40 ? 13 : 11}px;font-weight:600;` +
    `cursor:pointer;pointer-events:auto;` +
    `transition:transform 0.15s ease;` +
    `transform:translate(-50%,-50%);`;

  el.textContent = formatClusterCount(cluster.count);

  el.onmouseenter = () => { el.style.transform = 'translate(-50%,-50%) scale(1.15)'; };
  el.onmouseleave = () => { el.style.transform = 'translate(-50%,-50%)'; };

  return el;
}

function clusterSize(count: number): number {
  if (count >= 50) return 52;
  if (count >= 20) return 44;
  if (count >= 10) return 38;
  if (count >= 5) return 32;
  return 28;
}

function formatClusterCount(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(0)}K`;
  return String(n);
}

/**
 * Stable cluster data for react-globe.gl htmlElementsData.
 * Converts ClusterPoint[] to objects with lat/lng/el accessors.
 */
export function useClusterElements(clusters: ClusterPoint[]) {
  return useMemo(
    () => clusters.map((c) => ({
      lat: c.lat,
      lng: c.lng,
      count: c.count,
      dominant_category: c.dominant_category,
      avg_members: c.avg_members,
    })),
    [clusters],
  );
}
