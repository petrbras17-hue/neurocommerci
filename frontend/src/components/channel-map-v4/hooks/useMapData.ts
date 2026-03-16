import { useCallback, useEffect, useRef, useState } from 'react';
import type { MapBounds } from '../WorldMap';
import { ZOOM_LEVELS } from '../constants';

// ──────────────────────────────────────────────
// Типы данных API
// ──────────────────────────────────────────────

/** Отдельный канал (из /v1/channel-map/viewport) */
export interface ChannelPoint {
  id: number;
  username: string;
  title: string;
  category: string | null;
  subscribers: number;
  latitude: number | null;
  longitude: number | null;
  language: string | null;
  region: string | null;
  country_code: string | null;
  avatar_url: string | null;
  description: string | null;
  engagement_rate: number | null;
  topic_tags: string[] | null;
}

/** Кластер каналов (из /v1/channel-map/geo-clusters) */
export interface ClusterPoint {
  /** Для кластера — lat центроида */
  latitude: number;
  /** Для кластера — lng центроида */
  longitude: number;
  /** Число каналов в кластере */
  count: number;
  /** Суммарные подписчики */
  total_subscribers: number;
  /** Доминирующая категория */
  dominant_category: string | null;
  /** Метка (страна или gridKey) */
  label: string | null;
}

/** Режим данных: кластеры или отдельные каналы */
export type MapDataMode = 'clusters' | 'channels';

export interface MapFilters {
  category?: string;
  language?: string;
  region?: string;
  min_subscribers?: number;
  search?: string;
}

export interface UseMapDataResult {
  mode: MapDataMode;
  clusters: ClusterPoint[];
  channels: ChannelPoint[];
  loading: boolean;
  error: string | null;
  /** Принудительное обновление данных */
  refresh: () => void;
}

// ──────────────────────────────────────────────
// Вспомогательные функции
// ──────────────────────────────────────────────

function buildQueryString(
  bounds: MapBounds,
  zoom: number,
  filters: MapFilters,
): string {
  const params = new URLSearchParams({
    zoom: String(zoom),
    bounds_sw_lat: String(bounds.sw_lat),
    bounds_sw_lng: String(bounds.sw_lng),
    bounds_ne_lat: String(bounds.ne_lat),
    bounds_ne_lng: String(bounds.ne_lng),
  });

  if (filters.category) params.set('category', filters.category);
  if (filters.language) params.set('language', filters.language);
  if (filters.region) params.set('region', filters.region);
  if (filters.min_subscribers && filters.min_subscribers > 0) {
    params.set('min_subscribers', String(filters.min_subscribers));
  }

  return params.toString();
}

function getAuthToken(): string | null {
  return localStorage.getItem('access_token');
}

async function fetchWithAuth<T>(
  url: string,
  signal: AbortSignal,
): Promise<T> {
  const token = getAuthToken();
  const headers: HeadersInit = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(url, { headers, signal });

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }

  return res.json() as Promise<T>;
}

// ──────────────────────────────────────────────
// Основной хук
// ──────────────────────────────────────────────

const DEBOUNCE_MS = 300;

export function useMapData(
  bounds: MapBounds | null,
  zoom: number,
  filters: MapFilters = {},
): UseMapDataResult {
  const [clusters, setClusters] = useState<ClusterPoint[]>([]);
  const [channels, setChannels] = useState<ChannelPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Определяем режим по уровню зума
  const mode: MapDataMode = zoom >= ZOOM_LEVELS.CITY ? 'channels' : 'clusters';

  // AbortController для отмены предыдущего запроса
  const abortRef = useRef<AbortController | null>(null);
  // Таймер дебаунса
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  useEffect(() => {
    if (!bounds) return;

    // Отменяем предыдущий дебаунс
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }

    debounceRef.current = setTimeout(async () => {
      // Отменяем предыдущий in-flight запрос
      if (abortRef.current) {
        abortRef.current.abort();
      }
      const controller = new AbortController();
      abortRef.current = controller;

      setLoading(true);
      setError(null);

      try {
        if (mode === 'clusters') {
          // Кластеры: используем geo-clusters endpoint
          const qs = buildQueryString(bounds, zoom, filters);
          const data = await fetchWithAuth<{ clusters: ClusterPoint[] }>(
            `/v1/channel-map/geo-clusters?${qs}`,
            controller.signal,
          );
          setClusters(data.clusters ?? []);
          setChannels([]);
        } else {
          // Отдельные каналы: используем viewport endpoint
          const params = new URLSearchParams({
            sw_lat: String(bounds.sw_lat),
            sw_lng: String(bounds.sw_lng),
            ne_lat: String(bounds.ne_lat),
            ne_lng: String(bounds.ne_lng),
            limit: '500',
          });
          if (filters.category) params.set('category', filters.category);
          if (filters.language) params.set('language', filters.language);
          if (filters.min_subscribers && filters.min_subscribers > 0) {
            params.set('min_subscribers', String(filters.min_subscribers));
          }

          const data = await fetchWithAuth<{ channels: ChannelPoint[] }>(
            `/v1/channel-map/viewport?${params.toString()}`,
            controller.signal,
          );
          setChannels(data.channels ?? []);
          setClusters([]);
        }
      } catch (err: unknown) {
        // Игнорируем ошибки отмены запроса
        if (err instanceof DOMException && err.name === 'AbortError') return;
        console.error('[useMapData] fetch error:', err);
        setError(
          err instanceof Error ? err.message : 'Ошибка загрузки данных карты',
        );
      } finally {
        setLoading(false);
      }
    }, DEBOUNCE_MS);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    bounds?.sw_lat,
    bounds?.sw_lng,
    bounds?.ne_lat,
    bounds?.ne_lng,
    zoom,
    mode,
    filters.category,
    filters.language,
    filters.region,
    filters.min_subscribers,
    refreshKey,
  ]);

  // Cleanup при размонтировании
  useEffect(() => {
    return () => {
      if (abortRef.current) abortRef.current.abort();
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  return { mode, clusters, channels, loading, error, refresh };
}
