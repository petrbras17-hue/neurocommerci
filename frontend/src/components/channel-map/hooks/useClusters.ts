import { useState, useEffect, useRef, useCallback } from 'react';
import { apiFetch } from '../../../api';
import { useAuth } from '../../../auth';

export type ClusterPoint = {
  lat: number;
  lng: number;
  count: number;
  dominant_category: string;
  avg_members: number;
};

export type UseClustersReturn = {
  clusters: ClusterPoint[];
  loading: boolean;
  fetchClusters: (zoom: number, category?: string | null) => void;
};

const DEBOUNCE_MS = 400;

export function useClusters(): UseClustersReturn {
  const { accessToken: token } = useAuth();
  const [clusters, setClusters] = useState<ClusterPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchClusters = useCallback(
    (zoom: number, category?: string | null) => {
      if (timerRef.current) clearTimeout(timerRef.current);

      timerRef.current = setTimeout(async () => {
        if (!token) return;

        abortRef.current?.abort();
        const controller = new AbortController();
        abortRef.current = controller;

        setLoading(true);

        const params = new URLSearchParams({ zoom: String(zoom) });
        if (category) params.set('category', category);

        try {
          const data = await apiFetch<{ clusters: ClusterPoint[] }>(
            `/v1/channel-map/clusters?${params}`,
            { accessToken: token },
          );
          if (!controller.signal.aborted) {
            setClusters(data.clusters);
          }
        } catch {
          if (!controller.signal.aborted) {
            setClusters([]);
          }
        } finally {
          if (!controller.signal.aborted) setLoading(false);
        }
      }, DEBOUNCE_MS);
    },
    [token],
  );

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  return { clusters, loading, fetchClusters };
}
