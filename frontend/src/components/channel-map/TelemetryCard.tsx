// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map: TelemetryCard
// Glassmorphism stat card for the Mission Control HUD
// ═══════════════════════════════════════════════════════════════════════════════

import React from "react";
import { DESIGN_TOKENS as T } from "./constants";

export type TelemetryCardProps = {
  title: string;
  value: string;
  accent?: boolean;
  subtitle?: string;
  trend?: "up" | "down" | "flat";
};

const glassStyle: React.CSSProperties = {
  background: T.SURFACE,
  backdropFilter: "blur(12px)",
  WebkitBackdropFilter: "blur(12px)",
  border: `1px solid ${T.BORDER}`,
  borderRadius: 12,
  boxShadow: "0 0 20px rgba(0, 255, 136, 0.05)",
};

const TREND_INDICATORS: Record<"up" | "down" | "flat", { symbol: string; color: string }> = {
  up:   { symbol: "\u25B2", color: "#22c55e" },
  down: { symbol: "\u25BC", color: "#ef4444" },
  flat: { symbol: "\u2500", color: "#6b7280" },
};

export function TelemetryCard({ title, value, accent, subtitle, trend }: TelemetryCardProps) {
  return (
    <div style={{ ...glassStyle, padding: "10px 14px" }}>
      <div style={{ color: T.TEXT_SECONDARY, fontSize: 11, marginBottom: 4 }}>
        {title}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <div
          style={{
            color: accent ? T.ACCENT : T.TEXT_PRIMARY,
            fontSize: 18,
            fontWeight: 700,
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          {value}
        </div>
        {trend && (
          <span style={{ color: TREND_INDICATORS[trend].color, fontSize: 12 }}>
            {TREND_INDICATORS[trend].symbol}
          </span>
        )}
      </div>
      {subtitle && (
        <div style={{ color: T.TEXT_SECONDARY, fontSize: 11, marginTop: 2 }}>
          {subtitle}
        </div>
      )}
    </div>
  );
}
