// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — useHudMode
// Telemetry data fetching for channel-map planet view.
// Accepts an external MapMode and fetches telemetry cards for that mode.
// ═══════════════════════════════════════════════════════════════════════════════

import { useState, useEffect, useRef } from "react";
import { apiFetch } from "../../../api";
import { useAuth } from "../../../auth";
import type { MapMode } from "../constants";

// ── Types ─────────────────────────────────────────────────────────────────────

export type TelemetryCard = {
  title: string;
  value: string;
  accent?: boolean;
  subtitle?: string;
  trend?: "up" | "down" | "flat";
};

export type TelemetryData = {
  cards: TelemetryCard[];
};

export type UseHudModeReturn = {
  telemetry: TelemetryData | null;
  telemetryLoading: boolean;
};

// ── Fallback data per mode ────────────────────────────────────────────────────

const FALLBACKS: Record<MapMode, TelemetryData> = {
  discovery: {
    cards: [
      { title: "Total Channels", value: "\u2014", accent: true },
      { title: "Top Category", value: "\u2014" },
      { title: "Avg ER", value: "\u2014" },
      { title: "Coverage", value: "\u2014" },
    ],
  },
  farm: {
    cards: [
      { title: "Active Threads", value: "\u2014" },
      { title: "Comments/hr", value: "\u2014" },
      { title: "Delivery Rate", value: "\u2014" },
      { title: "Account Health", value: "\u2014" },
    ],
  },
  intelligence: {
    cards: [
      { title: "ROI Score", value: "\u2014" },
      { title: "Cost per Sub", value: "\u2014" },
      { title: "Weekly Growth", value: "\u2014" },
      { title: "Best Performing", value: "\u2014" },
    ],
  },
};

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useHudMode(mode: MapMode): UseHudModeReturn {
  const { accessToken: token } = useAuth();
  const [telemetry, setTelemetry] = useState<TelemetryData | null>(null);
  const [telemetryLoading, setTelemetryLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    if (!token) {
      setTelemetry(FALLBACKS[mode]);
      return;
    }

    setTelemetryLoading(true);

    apiFetch<TelemetryData>(`/v1/channel-map/telemetry?mode=${mode}`, {
      accessToken: token,
    })
      .then((data) => {
        if (!controller.signal.aborted) setTelemetry(data);
      })
      .catch(() => {
        if (!controller.signal.aborted) setTelemetry(FALLBACKS[mode]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setTelemetryLoading(false);
      });

    return () => {
      controller.abort();
    };
  }, [mode, token]);

  return { telemetry, telemetryLoading };
}
