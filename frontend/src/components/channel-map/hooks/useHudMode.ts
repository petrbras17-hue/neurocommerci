// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — useHudMode
// HUD mode state + telemetry data fetching for channel-map planet view.
// ═══════════════════════════════════════════════════════════════════════════════

import { useState, useEffect, useRef, useCallback } from "react";
import { apiFetch } from "../../../api";
import { useAuth } from "../../../auth";

// ── Types ─────────────────────────────────────────────────────────────────────

export type HudMode = "intel" | "farm" | "analytics";

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
  hudMode: HudMode;
  setHudMode: (mode: HudMode) => void;
  telemetry: TelemetryData | null;
  telemetryLoading: boolean;
};

// ── Fallback data per mode ────────────────────────────────────────────────────

const FALLBACKS: Record<HudMode, TelemetryData> = {
  intel: {
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
  analytics: {
    cards: [
      { title: "ROI Score", value: "\u2014" },
      { title: "Cost per Sub", value: "\u2014" },
      { title: "Weekly Growth", value: "\u2014" },
      { title: "Best Performing", value: "\u2014" },
    ],
  },
};

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useHudMode(): UseHudModeReturn {
  const { accessToken: token } = useAuth();
  const [hudMode, setHudModeRaw] = useState<HudMode>("intel");
  const [telemetry, setTelemetry] = useState<TelemetryData | null>(null);
  const [telemetryLoading, setTelemetryLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const setHudMode = useCallback((mode: HudMode) => {
    setHudModeRaw(mode);
  }, []);

  useEffect(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    if (!token) {
      setTelemetry(FALLBACKS[hudMode]);
      return;
    }

    setTelemetryLoading(true);

    apiFetch<TelemetryData>(`/v1/channel-map/telemetry?mode=${hudMode}`, {
      accessToken: token,
    })
      .then((data) => {
        if (!controller.signal.aborted) setTelemetry(data);
      })
      .catch(() => {
        if (!controller.signal.aborted) setTelemetry(FALLBACKS[hudMode]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setTelemetryLoading(false);
      });

    return () => {
      controller.abort();
    };
  }, [hudMode, token]);

  return { hudMode, setHudMode, telemetry, telemetryLoading };
}
