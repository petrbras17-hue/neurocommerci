// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map v4: ChannelList
// Скроллируемый список каналов ниже карты.
// Синхронизирован с текущими фильтрами, пагинация через "Показать ещё".
// ═══════════════════════════════════════════════════════════════════════════════
import React, { useState, useEffect, useCallback, useRef } from "react";
import type { ChannelMapEntry, ChannelMapListParams } from "../../api";
import { channelMapApi } from "../../api";
import { useAuth } from "../../auth";
import {
  formatSubscribers,
  getCategoryColor,
  getCategoryLabel,
} from "./constants";
import type { MapFilters } from "./constants";

// ─── Design tokens (local) ────────────────────────────────────────────────────

const T = {
  bg: "#0a0a0b",
  bgCard: "#111113",
  bgHover: "#1a1a1b",
  border: "#1a1a1b",
  borderMid: "#2a2a2b",
  accent: "#00ff88",
  text: "#e5e5e5",
  textDim: "#888",
  textMuted: "#555",
  radius: "6px",
  radiusMd: "10px",
} as const;

// ─── Constants ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 30;

// ─── Avatar placeholder ───────────────────────────────────────────────────────

function ChannelAvatar({
  channel,
  size = 32,
}: {
  channel: ChannelMapEntry;
  size?: number;
}) {
  const [broken, setBroken] = useState(false);
  const color = getCategoryColor(channel.category);
  const title = channel.title || channel.username || "#";
  const letter = title.charAt(0).toUpperCase();

  if (channel.avatar_url && !broken) {
    return (
      <img
        src={channel.avatar_url}
        alt={title}
        width={size}
        height={size}
        onError={() => setBroken(true)}
        style={{
          width: size,
          height: size,
          borderRadius: "50%",
          objectFit: "cover",
          border: `1.5px solid ${color}44`,
          flexShrink: 0,
        }}
      />
    );
  }

  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        background: `${color}22`,
        border: `1.5px solid ${color}55`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: size * 0.4,
        fontWeight: 700,
        color,
        flexShrink: 0,
        fontFamily: "'Geist Sans', sans-serif",
      }}
    >
      {letter}
    </div>
  );
}

// ─── Category badge ───────────────────────────────────────────────────────────

function CategoryBadge({ category }: { category: string | null }) {
  const color = getCategoryColor(category);
  const label = getCategoryLabel(category);
  return (
    <span
      style={{
        background: `${color}18`,
        color,
        fontSize: 10,
        fontWeight: 600,
        padding: "2px 7px",
        borderRadius: 100,
        border: `1px solid ${color}33`,
        whiteSpace: "nowrap",
        fontFamily: "'Geist Sans', sans-serif",
      }}
    >
      {label}
    </span>
  );
}

// ─── Row ──────────────────────────────────────────────────────────────────────

type RowProps = {
  channel: ChannelMapEntry;
  onClick: (ch: ChannelMapEntry) => void;
};

function ChannelRow({ channel, onClick }: RowProps) {
  const [hovered, setHovered] = useState(false);
  const title = channel.title || channel.username || `#${channel.id}`;
  const handle = channel.username ? `@${channel.username}` : null;
  const subs = formatSubscribers(channel.member_count);

  // Country flag from region
  function flagEmoji(region: string | null | undefined): string {
    if (!region) return "";
    const code = region.toUpperCase().slice(0, 2);
    if (code.length !== 2 || !/^[A-Z]{2}$/.test(code)) return "";
    const offset = 0x1f1e6;
    return String.fromCodePoint(
      code.charCodeAt(0) - 65 + offset,
      code.charCodeAt(1) - 65 + offset
    );
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onClick(channel)}
      onKeyDown={(e) => e.key === "Enter" && onClick(channel)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 14px",
        cursor: "pointer",
        background: hovered ? T.bgHover : "transparent",
        borderBottom: `1px solid ${T.border}`,
        transition: "background 0.12s",
        outline: "none",
      }}
    >
      {/* Avatar */}
      <ChannelAvatar channel={channel} size={32} />

      {/* Name + handle */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            color: T.text,
            fontSize: 13,
            fontWeight: 600,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            lineHeight: 1.3,
          }}
        >
          {title}
        </div>
        {handle && (
          <div
            style={{
              color: T.textDim,
              fontSize: 11,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {handle}
          </div>
        )}
      </div>

      {/* Subscribers */}
      <span
        style={{
          color: T.accent,
          fontSize: 12,
          fontWeight: 700,
          fontFamily: "'JetBrains Mono', monospace",
          flexShrink: 0,
        }}
      >
        {subs}
      </span>

      {/* Category badge */}
      <CategoryBadge category={channel.category} />

      {/* Country flag */}
      {channel.region && (
        <span
          style={{ fontSize: 16, flexShrink: 0 }}
          title={channel.region}
        >
          {flagEmoji(channel.region)}
        </span>
      )}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export type ChannelListProps = {
  filters: MapFilters;
  onChannelSelect: (channel: ChannelMapEntry) => void;
  /** Максимальная высота контейнера, по умолчанию 300px */
  maxHeight?: number;
};

export function ChannelList({
  filters,
  onChannelSelect,
  maxHeight = 300,
}: ChannelListProps) {
  const { accessToken: token } = useAuth();

  const [channels, setChannels] = useState<ChannelMapEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // ── Fetch helpers ──────────────────────────────────────────────────────────

  const buildParams = useCallback(
    (currentOffset: number): ChannelMapListParams => ({
      category: filters.category || undefined,
      language: filters.language || undefined,
      region: filters.region || undefined,
      min_members: filters.minSubscribers || undefined,
      search: filters.search || undefined,
      limit: PAGE_SIZE,
      offset: currentOffset,
    }),
    [filters]
  );

  // Initial / filter-change load — resets list
  useEffect(() => {
    if (!token) return;

    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    setLoading(true);
    setError(null);
    setOffset(0);
    setChannels([]);

    channelMapApi
      .list(token, buildParams(0))
      .then((res) => {
        if (ctrl.signal.aborted) return;
        setChannels(res.items);
        setTotal(res.total);
      })
      .catch((err: Error) => {
        if (ctrl.signal.aborted) return;
        setError(err.message);
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });

    return () => ctrl.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, filters.category, filters.language, filters.region, filters.minSubscribers, filters.search]);

  // ── Load more ──────────────────────────────────────────────────────────────

  const handleLoadMore = useCallback(async () => {
    if (!token || loadingMore) return;
    const nextOffset = offset + PAGE_SIZE;
    setLoadingMore(true);
    try {
      const res = await channelMapApi.list(token, buildParams(nextOffset));
      setChannels((prev) => [...prev, ...res.items]);
      setTotal(res.total);
      setOffset(nextOffset);
    } catch {
      // ignore
    } finally {
      setLoadingMore(false);
    }
  }, [token, offset, loadingMore, buildParams]);

  const hasMore = channels.length < total;

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div
      style={{
        background: T.bg,
        borderTop: `1px solid ${T.border}`,
        fontFamily: "'Geist Sans', system-ui, sans-serif",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 14px 6px",
          borderBottom: `1px solid ${T.border}`,
        }}
      >
        <span
          style={{
            color: T.textDim,
            fontSize: 11,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
          }}
        >
          Каналы
        </span>
        {!loading && (
          <span
            style={{
              color: T.accent,
              fontSize: 11,
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            {total.toLocaleString("ru-RU")}
          </span>
        )}
      </div>

      {/* List */}
      <div
        style={{
          maxHeight,
          overflowY: "auto",
          overflowX: "hidden",
        }}
      >
        {loading && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: 24,
              color: T.textDim,
              fontSize: 13,
            }}
          >
            <span style={{ marginRight: 8, color: T.accent }}>⟳</span>
            Загрузка...
          </div>
        )}

        {!loading && error && (
          <div
            style={{
              padding: 16,
              color: "#ef4444",
              fontSize: 13,
              textAlign: "center",
            }}
          >
            {error}
          </div>
        )}

        {!loading && !error && channels.length === 0 && (
          <div
            style={{
              padding: 24,
              color: T.textDim,
              fontSize: 13,
              textAlign: "center",
            }}
          >
            Каналы не найдены
          </div>
        )}

        {channels.map((ch) => (
          <ChannelRow key={ch.id} channel={ch} onClick={onChannelSelect} />
        ))}

        {/* Load more button */}
        {!loading && hasMore && (
          <div style={{ padding: "10px 14px" }}>
            <button
              onClick={handleLoadMore}
              disabled={loadingMore}
              style={{
                width: "100%",
                padding: "8px 0",
                background: T.bgHover,
                border: `1px solid ${T.borderMid}`,
                borderRadius: T.radius,
                color: loadingMore ? T.textDim : T.accent,
                fontSize: 12,
                fontWeight: 600,
                cursor: loadingMore ? "not-allowed" : "pointer",
                fontFamily: "'Geist Sans', sans-serif",
                transition: "background 0.12s",
              }}
            >
              {loadingMore
                ? "Загрузка..."
                : `Показать ещё (${(total - channels.length).toLocaleString("ru-RU")} каналов)`}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default ChannelList;
