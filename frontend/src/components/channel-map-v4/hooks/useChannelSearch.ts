// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map v4: useChannelSearch hook
// Debounced search (300ms) via POST /v1/channel-map/search
// ═══════════════════════════════════════════════════════════════════════════════
import { useState, useEffect, useRef, useCallback } from "react";
import { useAuth } from "../../../auth";
import type { ChannelMapEntry } from "../../../api";

const DEBOUNCE_MS = 300;
const MAX_RESULTS = 10;

export type UseChannelSearchReturn = {
  query: string;
  setQuery: (q: string) => void;
  results: ChannelMapEntry[];
  loading: boolean;
  clear: () => void;
};

export function useChannelSearch(): UseChannelSearchReturn {
  const { accessToken: token } = useAuth();

  const [query, setQueryRaw] = useState("");
  const [results, setResults] = useState<ChannelMapEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Stable setter — does NOT fire the search itself (effect handles that)
  const setQuery = useCallback((q: string) => {
    setQueryRaw(q);
  }, []);

  const clear = useCallback(() => {
    setQueryRaw("");
    setResults([]);
    setLoading(false);
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  // Debounced search effect
  useEffect(() => {
    // Clear pending timer on every query change
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }

    const trimmed = query.trim();

    if (!trimmed) {
      setResults([]);
      setLoading(false);
      return;
    }

    timerRef.current = setTimeout(async () => {
      if (!token) return;

      // Cancel any in-flight request
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      setLoading(true);

      try {
        const resp = await fetch("/v1/channel-map/search", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ query: trimmed, limit: MAX_RESULTS }),
          signal: ctrl.signal,
        });

        if (ctrl.signal.aborted) return;

        if (!resp.ok) {
          setResults([]);
          return;
        }

        const data = await resp.json();
        if (ctrl.signal.aborted) return;

        setResults(Array.isArray(data.items) ? data.items : []);
      } catch {
        // Abort or network error — silently ignore
        setResults([]);
      } finally {
        if (!ctrl.signal.aborted) {
          setLoading(false);
        }
      }
    }, DEBOUNCE_MS);
  }, [query, token]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      abortRef.current?.abort();
    };
  }, []);

  return { query, setQuery, results, loading, clear };
}
