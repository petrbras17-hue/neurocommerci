import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
} from "react";
import {
  channelMapApi,
  type ChannelMapEntry,
  type GeoPoint,
  type ChannelMapStats,
} from "../../../api";
import { useAuth } from "../../../auth";

// ─── Public contract ─────────────────────────────────────────────────────────

export type UseChannelDataReturn = {
  geoPoints: GeoPoint[];
  stats: ChannelMapStats | null;
  categories: string[];
  loading: boolean;
  error: string | null;
  selectedCategory: string | null;
  setSelectedCategory: (cat: string | null) => void;
  searchQuery: string;
  setSearchQuery: (q: string) => void;
  searchResults: ChannelMapEntry[];
  searchLoading: boolean;
  refetch: () => void;
  getChannelDetail: (id: number) => Promise<ChannelMapEntry | null>;
};

// ─── Constants ────────────────────────────────────────────────────────────────

const GEO_LIMIT = 50_000;
const SEARCH_DEBOUNCE_MS = 300;

// ─── Hook ────────────────────────────────────────────────────────────────────

export function useChannelData(): UseChannelDataReturn {
  const { accessToken: token } = useAuth();

  // Primary data
  const [geoPoints, setGeoPoints] = useState<GeoPoint[]>([]);
  const [stats, setStats] = useState<ChannelMapStats | null>(null);
  const [categories, setCategories] = useState<string[]>([]);

  // UI state
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategoryRaw] = useState<string | null>(null);

  // Search state
  const [searchQuery, setSearchQueryRaw] = useState("");
  const [searchResults, setSearchResults] = useState<ChannelMapEntry[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);

  // Stable ref for abort controller used by the primary fetch
  const primaryAbortRef = useRef<AbortController | null>(null);
  // Stable ref for search debounce timer
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Search abort controller
  const searchAbortRef = useRef<AbortController | null>(null);

  // ── Primary fetch ──────────────────────────────────────────────────────────

  const fetchPrimary = useCallback(
    async (category: string | null, signal: AbortSignal) => {
      if (!token) return;

      setLoading(true);
      setError(null);

      try {
        const [geoResp, statsResp, catsResp] = await Promise.all([
          channelMapApi.geo(token, GEO_LIMIT, category ?? undefined),
          channelMapApi.stats(token),
          channelMapApi.categories(token),
        ]);

        if (signal.aborted) return;

        setGeoPoints(geoResp.points);
        setStats(statsResp);
        setCategories(catsResp.categories);
      } catch (err) {
        if (signal.aborted) return;
        const msg =
          err instanceof Error ? err.message : "Ошибка загрузки данных карты";
        setError(msg);
      } finally {
        if (!signal.aborted) {
          setLoading(false);
        }
      }
    },
    [token],
  );

  // Trigger primary fetch on mount and when category changes
  useEffect(() => {
    // Cancel any in-flight request
    primaryAbortRef.current?.abort();
    const controller = new AbortController();
    primaryAbortRef.current = controller;

    fetchPrimary(selectedCategory, controller.signal);

    return () => {
      controller.abort();
    };
  }, [fetchPrimary, selectedCategory]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      primaryAbortRef.current?.abort();
      searchAbortRef.current?.abort();
      if (searchTimerRef.current !== null) {
        clearTimeout(searchTimerRef.current);
      }
    };
  }, []);

  // ── Search (debounced) ─────────────────────────────────────────────────────

  useEffect(() => {
    // Clear previous timer
    if (searchTimerRef.current !== null) {
      clearTimeout(searchTimerRef.current);
    }

    if (!searchQuery.trim()) {
      setSearchResults([]);
      setSearchLoading(false);
      return;
    }

    searchTimerRef.current = setTimeout(async () => {
      if (!token) return;

      // Cancel previous search request
      searchAbortRef.current?.abort();
      const controller = new AbortController();
      searchAbortRef.current = controller;

      setSearchLoading(true);

      try {
        const resp = await channelMapApi.search(token, {
          query: searchQuery.trim(),
          category: selectedCategory ?? undefined,
          limit: 50,
        });

        if (controller.signal.aborted) return;
        setSearchResults(resp.items);
      } catch (err) {
        if (controller.signal.aborted) return;
        // Search errors are non-fatal; clear results silently
        setSearchResults([]);
      } finally {
        if (!controller.signal.aborted) {
          setSearchLoading(false);
        }
      }
    }, SEARCH_DEBOUNCE_MS);
  }, [searchQuery, selectedCategory, token]);

  // ── Public setters (stable references) ────────────────────────────────────

  const setSelectedCategory = useCallback((cat: string | null) => {
    setSelectedCategoryRaw(cat);
    // Clear search state when switching category
    setSearchResults([]);
    setSearchQueryRaw("");
  }, []);

  const setSearchQuery = useCallback((q: string) => {
    setSearchQueryRaw(q);
  }, []);

  // ── refetch ────────────────────────────────────────────────────────────────

  const refetch = useCallback(() => {
    primaryAbortRef.current?.abort();
    const controller = new AbortController();
    primaryAbortRef.current = controller;
    fetchPrimary(selectedCategory, controller.signal);
  }, [fetchPrimary, selectedCategory]);

  // ── getChannelDetail ───────────────────────────────────────────────────────

  const getChannelDetail = useCallback(
    async (id: number): Promise<ChannelMapEntry | null> => {
      if (!token) return null;
      try {
        const resp = await channelMapApi.list(token, {
          limit: 1,
          offset: 0,
          search: String(id),
        });
        // Prefer exact id match, fall back to first result
        const exact = resp.items.find((ch) => ch.id === id);
        return exact ?? resp.items[0] ?? null;
      } catch {
        return null;
      }
    },
    [token],
  );

  // ── Derived / memoised values ──────────────────────────────────────────────

  const stableGeoPoints = useMemo(() => geoPoints, [geoPoints]);
  const stableCategories = useMemo(() => categories, [categories]);

  // ── Return ─────────────────────────────────────────────────────────────────

  return {
    geoPoints: stableGeoPoints,
    stats,
    categories: stableCategories,
    loading,
    error,
    selectedCategory,
    setSelectedCategory,
    searchQuery,
    setSearchQuery,
    searchResults,
    searchLoading,
    refetch,
    getChannelDetail,
  };
}
