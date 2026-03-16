// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map v4: ChannelSidebar
// Right slide-in detail panel (380px). Opens when a channel is selected.
// Fetches: GET /v1/channel-map/{id}, GET /v1/channel-map/{id}/similar,
//          GET /v1/channel-map/{id}/history
// ═══════════════════════════════════════════════════════════════════════════════
import React, { useState, useEffect, useCallback } from "react";
import { useAuth } from "../../auth";
import {
  DESIGN_TOKENS as T,
  getCategoryMeta,
  getLangFlag,
  formatNumber,
  formatER,
} from "../channel-map/constants";
import type { ChannelMapEntry } from "../../api";

// ─── Design helpers ───────────────────────────────────────────────────────────

const glass: React.CSSProperties = {
  background: T.SURFACE,
  borderLeft: `1px solid rgba(0,255,136,0.15)`,
  boxShadow: "0 0 40px rgba(0,255,136,0.06)",
};

const secHdr: React.CSSProperties = {
  color: T.TEXT_SECONDARY,
  fontSize: 11,
  fontWeight: 600,
  textTransform: "uppercase" as const,
  letterSpacing: 1.2,
  marginBottom: 10,
};

const dividerStyle: React.CSSProperties = {
  height: 1,
  background: "rgba(0,255,136,0.1)",
  margin: "2px -18px",
  width: "calc(100% + 36px)",
};

const shimmerBg: React.CSSProperties = {
  background: `linear-gradient(90deg, rgba(0,255,136,0.08) 25%, rgba(0,255,136,0.15) 50%, rgba(0,255,136,0.08) 75%)`,
  backgroundSize: "200% 100%",
  borderRadius: 6,
  height: 14,
};

// ─── Sub-components ───────────────────────────────────────────────────────────

function Sk({ w = "100%", h = 14 }: { w?: string | number; h?: number }) {
  return <div style={{ ...shimmerBg, width: w, height: h }} />;
}

function Divider() {
  return <div style={dividerStyle} />;
}

interface StatCellProps {
  label: string;
  value: string;
}
function StatCell({ label, value }: StatCellProps) {
  return (
    <div>
      <div style={{ color: T.TEXT_SECONDARY, fontSize: 10, marginBottom: 2 }}>{label}</div>
      <div
        style={{
          color: T.TEXT_PRIMARY,
          fontSize: 13,
          fontWeight: 500,
          fontFamily: "'JetBrains Mono', monospace",
        }}
      >
        {value}
      </div>
    </div>
  );
}

// ─── Sparkline ────────────────────────────────────────────────────────────────

interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
}

function Sparkline({ data, width = 310, height = 40, color = T.ACCENT }: SparklineProps) {
  if (!data || data.length < 2) return null;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const points = data
    .map(
      (v, i) =>
        `${(i / (data.length - 1)) * width},${height - ((v - min) / range) * (height - 4) - 2}`
    )
    .join(" ");

  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <polyline fill="none" stroke={color} strokeWidth="1.5" points={points} />
    </svg>
  );
}

// ─── Avatar ───────────────────────────────────────────────────────────────────

interface AvatarProps {
  channel: ChannelMapEntry;
  size?: number;
  color: string;
}

function Avatar({ channel, size = 64, color }: AvatarProps) {
  const [imgFailed, setImgFailed] = useState(false);
  const title = channel.title || channel.username || "?";
  const letter = title.charAt(0).toUpperCase();

  if (channel.avatar_url && !imgFailed) {
    return (
      <img
        src={channel.avatar_url}
        alt={title}
        style={{
          width: size,
          height: size,
          borderRadius: "50%",
          border: `2px solid ${color}55`,
          objectFit: "cover",
          flexShrink: 0,
        }}
        onError={() => setImgFailed(true)}
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
        border: `2px solid ${color}55`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: size * 0.38,
        fontWeight: 700,
        color,
        fontFamily: "'Geist Sans', sans-serif",
        flexShrink: 0,
      }}
    >
      {letter}
    </div>
  );
}

// ─── Action button ────────────────────────────────────────────────────────────

interface ActionButtonProps {
  label: string;
  color: string;
  onClick: () => void;
  disabled?: boolean;
}

function ActionButton({ label, color, onClick, disabled = false }: ActionButtonProps) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        background: hover ? `${color}18` : "transparent",
        border: `1px solid ${color}55`,
        borderRadius: 8,
        padding: "8px 0",
        cursor: disabled ? "not-allowed" : "pointer",
        color: disabled ? T.TEXT_MUTED : color,
        fontSize: 12,
        fontWeight: 600,
        fontFamily: "'Geist Sans', sans-serif",
        transition: "background 0.15s, border-color 0.15s",
        opacity: disabled ? 0.5 : 1,
        width: "100%",
      }}
    >
      {label}
    </button>
  );
}

// ─── Similar channel row ──────────────────────────────────────────────────────

interface SimilarRowProps {
  channel: ChannelMapEntry;
  onClick: (ch: ChannelMapEntry) => void;
}

function SimilarRow({ channel, onClick }: SimilarRowProps) {
  const [hover, setHover] = useState(false);
  const cat = getCategoryMeta(channel.category);
  const title = channel.title || channel.username || `#${channel.id}`;

  return (
    <button
      onClick={() => onClick(channel)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        background: hover ? "rgba(0,255,136,0.04)" : "rgba(255,255,255,0.02)",
        border: `1px solid ${hover ? "rgba(0,255,136,0.2)" : "rgba(255,255,255,0.06)"}`,
        borderRadius: 8,
        padding: "7px 10px",
        cursor: "pointer",
        width: "100%",
        textAlign: "left",
        transition: "background 0.12s, border-color 0.12s",
        gap: 8,
      }}
    >
      <Avatar channel={channel} size={28} color={cat.color} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            color: T.TEXT_PRIMARY,
            fontSize: 12,
            fontWeight: 600,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {title}
        </div>
        <div style={{ color: T.TEXT_SECONDARY, fontSize: 10, marginTop: 1 }}>
          {formatNumber(channel.member_count)} · ER {formatER(channel.engagement_rate)}
        </div>
      </div>
      <span
        style={{
          color: T.TEXT_SECONDARY,
          fontSize: 14,
          lineHeight: 1,
          flexShrink: 0,
        }}
      >
        ›
      </span>
    </button>
  );
}

// ─── Types ────────────────────────────────────────────────────────────────────

export type ChannelSidebarProps = {
  channelId: number | null;
  onClose: () => void;
  onSelectSimilar?: (ch: ChannelMapEntry) => void;
  onAddToFarm?: (id: number) => void;
  onBlacklist?: (id: number) => void;
  onTrack?: (id: number) => void;
};

type HistoryPoint = { subscribers: number; recorded_at?: string };

// ─── Main component ───────────────────────────────────────────────────────────

export function ChannelSidebar({
  channelId,
  onClose,
  onSelectSimilar,
  onAddToFarm,
  onBlacklist,
  onTrack,
}: ChannelSidebarProps) {
  const { accessToken: token } = useAuth();

  const [channel, setChannel] = useState<ChannelMapEntry | null>(null);
  const [similar, setSimilar] = useState<ChannelMapEntry[]>([]);
  const [history, setHistory] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);

  // Slide-in animation state
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (channelId !== null) {
      setVisible(true);
    } else {
      setVisible(false);
    }
  }, [channelId]);

  const fetchAll = useCallback(
    async (id: number) => {
      if (!token) return;

      setLoading(true);
      setChannel(null);
      setSimilar([]);
      setHistory([]);

      const headers = { Authorization: `Bearer ${token}` };

      const [chCtrl, simCtrl, histCtrl] = [
        new AbortController(),
        new AbortController(),
        new AbortController(),
      ];

      try {
        const [chResp, simResp, histResp] = await Promise.allSettled([
          fetch(`/v1/channel-map/${id}`, { headers, signal: chCtrl.signal }),
          fetch(`/v1/channel-map/${id}/similar?limit=5`, {
            headers,
            signal: simCtrl.signal,
          }),
          fetch(`/v1/channel-map/${id}/history?days=30`, {
            headers,
            signal: histCtrl.signal,
          }),
        ]);

        if (chResp.status === "fulfilled" && chResp.value.ok) {
          const data: ChannelMapEntry = await chResp.value.json();
          setChannel(data);
        }

        if (simResp.status === "fulfilled" && simResp.value.ok) {
          const data: { items: ChannelMapEntry[] } = await simResp.value.json();
          setSimilar(data.items ?? []);
        }

        if (histResp.status === "fulfilled" && histResp.value.ok) {
          const data: { history: HistoryPoint[] } = await histResp.value.json();
          setHistory((data.history ?? []).map((h) => h.subscribers));
        }
      } catch {
        // Non-critical — sidebar still renders with partial data
      } finally {
        setLoading(false);
      }

      return () => {
        chCtrl.abort();
        simCtrl.abort();
        histCtrl.abort();
      };
    },
    [token]
  );

  useEffect(() => {
    if (channelId === null) {
      setChannel(null);
      setSimilar([]);
      setHistory([]);
      return;
    }
    fetchAll(channelId);
  }, [channelId, fetchAll]);

  // Do not render when hidden (keeps DOM clean, avoids stale data flash)
  if (channelId === null && !visible) return null;

  const cat = channel ? getCategoryMeta(channel.category) : getCategoryMeta(null);
  const title = channel?.title || channel?.username || `#${channelId}`;

  return (
    <div
      style={{
        ...glass,
        position: "absolute",
        top: 0,
        right: 0,
        bottom: 0,
        width: 380,
        zIndex: 1000,
        overflowY: "auto",
        overflowX: "hidden",
        display: "flex",
        flexDirection: "column",
        gap: 18,
        padding: "20px 18px",
        transform: visible ? "translateX(0)" : "translateX(100%)",
        transition: "transform 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
        fontFamily: "'Geist Sans', system-ui, sans-serif",
      }}
    >
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div>
        {/* Top row: ID + links + close */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 12,
          }}
        >
          <span style={{ color: T.TEXT_MUTED, fontSize: 11 }}>
            {channel ? `ID ${channel.id}` : "Загрузка..."}
          </span>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            {channel?.username && (
              <a
                href={`https://t.me/${channel.username}`}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  color: T.ACCENT,
                  fontSize: 11,
                  fontWeight: 600,
                  textDecoration: "none",
                  border: `1px solid rgba(0,255,136,0.3)`,
                  borderRadius: 6,
                  padding: "3px 8px",
                }}
                title="Открыть в Telegram"
              >
                t.me ↗
              </a>
            )}
            <button
              onClick={onClose}
              style={{
                background: "rgba(255,255,255,0.05)",
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: 6,
                color: T.TEXT_SECONDARY,
                cursor: "pointer",
                width: 26,
                height: 26,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 16,
                lineHeight: 1,
                padding: 0,
              }}
              title="Закрыть"
            >
              ×
            </button>
          </div>
        </div>

        {/* Avatar + name row */}
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          {loading ? (
            <div
              style={{
                width: 64,
                height: 64,
                borderRadius: "50%",
                ...shimmerBg,
                flexShrink: 0,
              }}
            />
          ) : (
            <Avatar channel={channel ?? ({ title: title } as ChannelMapEntry)} size={64} color={cat.color} />
          )}
          <div style={{ minWidth: 0, flex: 1 }}>
            {loading ? (
              <>
                <Sk w="80%" h={18} />
                <div style={{ marginTop: 6 }}>
                  <Sk w="50%" h={13} />
                </div>
              </>
            ) : (
              <>
                <div
                  style={{
                    color: T.TEXT_PRIMARY,
                    fontSize: 16,
                    fontWeight: 700,
                    lineHeight: 1.3,
                    wordBreak: "break-word",
                  }}
                >
                  {title}
                </div>
                {channel?.username && (
                  <div
                    style={{
                      color: T.TEXT_SECONDARY,
                      fontSize: 13,
                      marginTop: 3,
                    }}
                  >
                    @{channel.username}
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {/* Category + language badges */}
        {!loading && channel && (
          <div
            style={{
              display: "flex",
              gap: 6,
              marginTop: 10,
              flexWrap: "wrap",
            }}
          >
            <span
              style={{
                background: `${cat.color}22`,
                color: cat.color,
                fontSize: 11,
                fontWeight: 600,
                padding: "3px 10px",
                borderRadius: 100,
                border: `1px solid ${cat.color}44`,
              }}
            >
              {cat.icon} {cat.label}
            </span>
            <span
              style={{
                background: "rgba(255,255,255,0.05)",
                color: T.TEXT_SECONDARY,
                fontSize: 11,
                padding: "3px 10px",
                borderRadius: 100,
                border: "1px solid rgba(255,255,255,0.1)",
              }}
            >
              {getLangFlag(channel.language ?? null)}{" "}
              {channel.language?.toUpperCase() || "N/A"}
            </span>
          </div>
        )}
      </div>

      <Divider />

      {/* ── Stats grid ─────────────────────────────────────────────────────── */}
      <div>
        <div style={secHdr}>Статистика</div>
        {loading ? (
          <div
            style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}
          >
            {Array.from({ length: 6 }).map((_, i) => (
              <Sk key={i} h={32} />
            ))}
          </div>
        ) : channel ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "8px 12px",
            }}
          >
            <StatCell
              label="Подписчики"
              value={formatNumber(channel.member_count)}
            />
            <StatCell
              label="ER"
              value={`${formatER(channel.engagement_rate)}`}
            />
            <StatCell
              label="Avg охват"
              value={formatNumber(channel.avg_post_reach)}
            />
            <StatCell
              label="Постов/день"
              value={
                channel.post_frequency_daily != null
                  ? String(channel.post_frequency_daily)
                  : "—"
              }
            />
            <StatCell
              label="Комментарии"
              value={channel.has_comments ? "Включены" : "Выключены"}
            />
            <StatCell label="Источник" value={channel.source || "local"} />
          </div>
        ) : (
          <div style={{ color: T.TEXT_MUTED, fontSize: 12 }}>
            Нет данных
          </div>
        )}
      </div>

      {/* ── Sparkline ──────────────────────────────────────────────────────── */}
      {history.length > 1 ? (
        <>
          <Divider />
          <div>
            <div style={secHdr}>Динамика подписчиков (30 дней)</div>
            <Sparkline data={history} width={310} height={44} color={T.ACCENT} />
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                marginTop: 4,
              }}
            >
              <span style={{ fontSize: 10, color: T.TEXT_SECONDARY }}>
                {formatNumber(Math.min(...history))}
              </span>
              <span style={{ fontSize: 10, color: T.TEXT_SECONDARY }}>
                {formatNumber(Math.max(...history))}
              </span>
            </div>
          </div>
        </>
      ) : null}

      {/* ── Description ────────────────────────────────────────────────────── */}
      {!loading && channel?.description && (
        <>
          <Divider />
          <div>
            <div style={secHdr}>Описание</div>
            <div
              style={{
                color: T.TEXT_SECONDARY,
                fontSize: 12,
                lineHeight: 1.7,
                maxHeight: 88,
                overflowY: "auto",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {channel.description}
            </div>
          </div>
        </>
      )}

      {/* ── Topic tags ─────────────────────────────────────────────────────── */}
      {!loading && channel && (channel as unknown as Record<string, unknown>)["topic_tags"] && (
        <>
          <Divider />
          <div>
            <div style={secHdr}>Теги</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
              {(
                ((channel as unknown as Record<string, string[]>)["topic_tags"] ?? []) as string[]
              ).map((tag: string) => (
                <span
                  key={tag}
                  style={{
                    background: "rgba(0,255,136,0.07)",
                    color: T.ACCENT,
                    fontSize: 10,
                    fontWeight: 600,
                    padding: "3px 8px",
                    borderRadius: 100,
                    border: "1px solid rgba(0,255,136,0.2)",
                  }}
                >
                  #{tag}
                </span>
              ))}
            </div>
          </div>
        </>
      )}

      {/* ── Similar channels ───────────────────────────────────────────────── */}
      <Divider />
      <div>
        <div style={secHdr}>Похожие каналы</div>
        {loading ? (
          <div style={{ color: T.TEXT_MUTED, fontSize: 12 }}>Загрузка...</div>
        ) : similar.length === 0 ? (
          <div style={{ color: T.TEXT_MUTED, fontSize: 12 }}>Нет данных</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            {similar.slice(0, 5).map((sc) => (
              <SimilarRow
                key={sc.id}
                channel={sc}
                onClick={(ch) => onSelectSimilar?.(ch)}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── Action buttons ─────────────────────────────────────────────────── */}
      <Divider />
      <div>
        <div style={secHdr}>Действия</div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 8,
          }}
        >
          <ActionButton
            label="В ферму"
            color={T.ACCENT}
            onClick={() => channel && onAddToFarm?.(channel.id)}
            disabled={!channel}
          />
          <ActionButton
            label="Blacklist"
            color="#ef4444"
            onClick={() => channel && onBlacklist?.(channel.id)}
            disabled={!channel}
          />
          <ActionButton
            label="Отслеживать"
            color="#eab308"
            onClick={() => channel && onTrack?.(channel.id)}
            disabled={!channel}
          />
          {channel?.username && (
            <ActionButton
              label="Открыть Telegram"
              color={T.ACCENT_SECONDARY}
              onClick={() =>
                window.open(
                  `https://t.me/${channel.username}`,
                  "_blank",
                  "noopener,noreferrer"
                )
              }
            />
          )}
        </div>
      </div>
    </div>
  );
}
