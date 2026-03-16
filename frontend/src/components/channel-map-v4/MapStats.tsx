// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map v4: MapStats
// Горизонтальная панель статистики (верхний правый угол).
// Показывает: общее кол-во каналов, стран, категорий.
// При активном фильтре: "Фильтр: tech (7.1K каналов)"
// ═══════════════════════════════════════════════════════════════════════════════
import React, { useEffect, useState } from "react";
import { useAuth } from "../../auth";
import { channelMapApi } from "../../api";
import type { ChannelMapStats } from "../../api";
import {
  formatSubscribers,
  getCategoryLabel,
} from "./constants";
import type { FiltersState as MapFilters } from "./MapFilters";

// ─── Design tokens (local) ────────────────────────────────────────────────────

const T = {
  bg: "#0a0a0b",
  bgHover: "#1a1a1b",
  border: "#1a1a1b",
  borderMid: "#2a2a2b",
  accent: "#00ff88",
  text: "#e5e5e5",
  textDim: "#888",
  textMuted: "#555",
  radius: "6px",
} as const;

// ─── Types ────────────────────────────────────────────────────────────────────

export type MapStatsProps = {
  filters: MapFilters;
  /** Кол-во каналов в текущем отфильтрованном виде (от ChannelList) */
  filteredTotal?: number;
};

// ─── Stat item ────────────────────────────────────────────────────────────────

function StatItem({
  value,
  label,
}: {
  value: string | number;
  label: string;
}) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontFamily: "'Geist Sans', system-ui, sans-serif",
      }}
    >
      <span
        style={{
          color: T.accent,
          fontSize: 13,
          fontWeight: 700,
          fontFamily: "'JetBrains Mono', monospace",
        }}
      >
        {value}
      </span>
      <span style={{ color: T.textDim, fontSize: 12 }}>{label}</span>
    </span>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function MapStats({ filters, filteredTotal }: MapStatsProps) {
  const { accessToken: token } = useAuth();
  const [stats, setStats] = useState<ChannelMapStats | null>(null);

  useEffect(() => {
    if (!token) return;
    channelMapApi.stats(token).then(setStats).catch(() => {/* ignore */});
  }, [token]);

  const hasFilter =
    filters.category ||
    filters.language ||
    filters.region ||
    filters.minSubscribers > 0 ||
    filters.search;

  const totalChannels = stats?.total_channels ?? stats?.total ?? 0;
  const countryCount = stats ? Object.keys(stats.by_region ?? {}).length : 0;
  const categoryCount = stats ? Object.keys(stats.by_category ?? {}).length : 0;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "0 16px",
        height: "100%",
        flexWrap: "wrap",
      }}
    >
      {totalChannels > 0 && (
        <StatItem
          value={formatSubscribers(totalChannels)}
          label="каналов"
        />
      )}

      {countryCount > 0 && (
        <>
          <span style={{ color: T.borderMid, fontSize: 14, userSelect: "none" }}>
            |
          </span>
          <StatItem value={`${countryCount}+`} label="стран" />
        </>
      )}

      {categoryCount > 0 && (
        <>
          <span style={{ color: T.borderMid, fontSize: 14, userSelect: "none" }}>
            |
          </span>
          <StatItem value={categoryCount} label="категорий" />
        </>
      )}

      {/* Active filter badge */}
      {hasFilter && filteredTotal !== undefined && (
        <>
          <span style={{ color: T.borderMid, fontSize: 14, userSelect: "none" }}>
            |
          </span>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              background: "rgba(0,255,136,0.08)",
              border: "1px solid rgba(0,255,136,0.22)",
              borderRadius: T.radius,
              padding: "2px 10px",
              fontFamily: "'Geist Sans', sans-serif",
            }}
          >
            <span style={{ color: T.textDim, fontSize: 11 }}>Фильтр:</span>
            {filters.category && (
              <span style={{ color: T.accent, fontSize: 11, fontWeight: 700 }}>
                {getCategoryLabel(filters.category)}
              </span>
            )}
            {filters.search && (
              <span style={{ color: T.accent, fontSize: 11, fontWeight: 700 }}>
                &laquo;{filters.search}&raquo;
              </span>
            )}
            <span
              style={{
                color: T.accent,
                fontSize: 11,
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              ({formatSubscribers(filteredTotal)} каналов)
            </span>
          </span>
        </>
      )}

      {/* Loading placeholder */}
      {!stats && (
        <span style={{ color: T.textMuted, fontSize: 11 }}>Загрузка...</span>
      )}
    </div>
  );
}

export default MapStats;
