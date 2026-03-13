// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map: ChannelDetailPanel
// Slide-in detail panel for channel inspection (380px right side)
// ═══════════════════════════════════════════════════════════════════════════════
import React from "react";
import { motion } from "framer-motion";
import {
  X, ExternalLink, Plus, Ban, Database, Bell,
  ChevronRight, Users, TrendingUp, Clock,
} from "lucide-react";
import type { ChannelMapEntry } from "../../api";
import { DESIGN_TOKENS as T, getCategoryMeta, getLangFlag, formatNumber, formatER } from "./constants";

export type ChannelDetailPanelProps = {
  channel: ChannelMapEntry | null;
  similarChannels: ChannelMapEntry[];
  loading: boolean;
  onClose: () => void;
  onAddToFarm: (channelId: number) => void;
  onBlacklist: (channelId: number) => void;
  onAddToDb: (channelId: number) => void;
  onTrack: (channelId: number) => void;
  onSelectSimilar: (channel: ChannelMapEntry) => void;
};

const glass: React.CSSProperties = {
  background: T.SURFACE, backdropFilter: "blur(16px)", WebkitBackdropFilter: "blur(16px)",
  border: `1px solid ${T.BORDER}`,
};
const secHdr: React.CSSProperties = {
  color: T.TEXT_SECONDARY, fontSize: 11, fontWeight: 600,
  textTransform: "uppercase", letterSpacing: 1.2, marginBottom: 10,
};
const shimmerBg: React.CSSProperties = {
  background: `linear-gradient(90deg,${T.BORDER} 25%,rgba(0,255,136,0.08) 50%,${T.BORDER} 75%)`,
  backgroundSize: "200% 100%", animation: "shimmer 1.4s infinite", borderRadius: 6, height: 14,
};

function relativeDate(iso: string | null): string {
  if (!iso) return "\u2014";
  const d = Date.now() - new Date(iso).getTime(), m = Math.floor(d / 60_000);
  if (m < 60) return `${m} мин назад`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} ч назад`;
  return `${Math.floor(h / 24)} дн назад`;
}
const Sk = ({ w = "100%", h = 14 }: { w?: string | number; h?: number }) =>
  <div style={{ ...shimmerBg, width: w, height: h }} />;
const Div = () =>
  <div style={{ height: 1, background: T.BORDER, margin: "0 -18px", width: "calc(100% + 36px)" }} />;
const Chip = ({ icon, value }: { icon: React.ReactNode; value: string }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 4, color: T.TEXT_PRIMARY,
    fontSize: 12, fontWeight: 600, fontFamily: "'JetBrains Mono', monospace" }}>
    <span style={{ color: T.ACCENT }}>{icon}</span>{value}
  </div>
);
const Stat = ({ label, value }: { label: string; value: string }) => (
  <div>
    <div style={{ color: T.TEXT_SECONDARY, fontSize: 10, marginBottom: 2 }}>{label}</div>
    <div style={{ color: T.TEXT_PRIMARY, fontSize: 13, fontWeight: 500,
      fontFamily: "'JetBrains Mono', monospace" }}>{value}</div>
  </div>
);

type ActionDef = { label: string; Icon: typeof Plus; color: string;
  handler: keyof Pick<ChannelDetailPanelProps, "onAddToFarm" | "onBlacklist" | "onAddToDb" | "onTrack"> };
const ACTIONS: ActionDef[] = [
  { label: "В ферму",    Icon: Plus,     color: T.ACCENT,           handler: "onAddToFarm" },
  { label: "Blacklist",   Icon: Ban,      color: "#ef4444",          handler: "onBlacklist" },
  { label: "В базу",      Icon: Database, color: T.ACCENT_SECONDARY, handler: "onAddToDb" },
  { label: "Отслеживать", Icon: Bell,     color: "#eab308",          handler: "onTrack" },
];

export function ChannelDetailPanel(props: ChannelDetailPanelProps) {
  const { channel, similarChannels, loading, onClose, onSelectSimilar } = props;
  if (!channel) return null;
  const cat = getCategoryMeta(channel.category);

  return (
    <div style={{ ...glass, width: 380, height: "100%", overflowY: "auto", overflowX: "hidden",
      borderRadius: "16px 0 0 16px", padding: "20px 18px", display: "flex", flexDirection: "column",
      gap: 18, boxShadow: "0 0 40px rgba(0,255,136,0.06)" }}>
      <style>{`@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}`}</style>

      {/* Header */}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ color: T.TEXT_SECONDARY, fontSize: 12 }}>ID {channel.id}</span>
          <div style={{ display: "flex", gap: 8 }}>
            {channel.username && (
              <a href={`https://t.me/${channel.username}`} target="_blank" rel="noopener noreferrer"
                style={{ color: T.TEXT_SECONDARY, cursor: "pointer" }} title="Открыть в Telegram">
                <ExternalLink size={16} />
              </a>
            )}
            <button onClick={onClose} style={{ background: "none", border: "none",
              color: T.TEXT_SECONDARY, cursor: "pointer", padding: 0, lineHeight: 1 }}>
              <X size={16} />
            </button>
          </div>
        </div>
        {loading ? <Sk w="70%" h={20} /> : (
          <div style={{ color: T.TEXT_PRIMARY, fontSize: 18, fontWeight: 700, marginTop: 6, lineHeight: 1.3 }}>
            {channel.title || "Без названия"}
          </div>
        )}
        {!loading && channel.username && (
          <div style={{ color: T.TEXT_SECONDARY, fontSize: 13, marginTop: 2 }}>@{channel.username}</div>
        )}
        <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <span style={{ background: `${cat.color}22`, color: cat.color, fontSize: 11, fontWeight: 600,
            padding: "3px 10px", borderRadius: 100, border: `1px solid ${cat.color}44` }}>
            {cat.icon} {cat.label}
          </span>
          <span style={{ background: "rgba(255,255,255,0.05)", color: T.TEXT_SECONDARY, fontSize: 11,
            padding: "3px 10px", borderRadius: 100, border: `1px solid ${T.BORDER}` }}>
            {getLangFlag(channel.language)} {channel.language?.toUpperCase() || "N/A"}
          </span>
        </div>
        <div style={{ display: "flex", gap: 14, marginTop: 12 }}>
          <Chip icon={<Users size={12} />} value={formatNumber(channel.member_count)} />
          <Chip icon={<TrendingUp size={12} />} value={`ER ${formatER(channel.engagement_rate)}`} />
          <Chip icon={<Clock size={12} />} value={`${channel.post_frequency_daily ?? "\u2014"}/д`} />
        </div>
      </div>

      <Div />

      {/* Stats */}
      <div>
        <div style={secHdr}>Статистика</div>
        {loading ? (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {Array.from({ length: 6 }).map((_, i) => <Sk key={i} h={16} />)}
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 12px" }}>
            <Stat label="Avg Views" value={formatNumber(channel.avg_post_reach)} />
            <Stat label="Comments" value={channel.has_comments ? "Включены" : "Выключены"} />
            <Stat label="Avg Comments" value={formatNumber(channel.avg_comments_per_post)} />
            <Stat label="Source" value={channel.source || "local"} />
            <Stat label="Verified" value={channel.verified ? "\u2713 Да" : "\u2014"} />
            <Stat label="Indexed" value={relativeDate(channel.last_indexed_at)} />
          </div>
        )}
      </div>

      <Div />

      {/* Similar Channels */}
      <div>
        <div style={secHdr}>Похожие каналы</div>
        {loading ? (
          <div style={{ color: T.TEXT_SECONDARY, fontSize: 12 }}>Загрузка...</div>
        ) : similarChannels.length === 0 ? (
          <div style={{ color: T.TEXT_SECONDARY, fontSize: 12 }}>Нет данных</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {similarChannels.slice(0, 5).map((sc) => (
              <motion.button key={sc.id} whileHover={{ x: 3 }} transition={{ duration: 0.15 }}
                onClick={() => onSelectSimilar(sc)}
                style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                  background: "rgba(255,255,255,0.03)", border: `1px solid ${T.BORDER}`,
                  borderRadius: 8, padding: "7px 10px", cursor: "pointer", width: "100%", textAlign: "left" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ color: T.TEXT_PRIMARY, fontSize: 13, fontWeight: 600,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {sc.title || sc.username || `#${sc.id}`}
                  </div>
                  <div style={{ color: T.TEXT_SECONDARY, fontSize: 11, marginTop: 1 }}>
                    {formatNumber(sc.member_count)} &middot; ER {formatER(sc.engagement_rate)}
                  </div>
                </div>
                <ChevronRight size={14} color={T.TEXT_SECONDARY} />
              </motion.button>
            ))}
          </div>
        )}
      </div>

      <Div />

      {/* Actions */}
      <div>
        <div style={secHdr}>Действия</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          {ACTIONS.map(({ label, Icon, color, handler }) => (
            <motion.button key={handler} whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.96 }}
              onClick={() => props[handler](channel.id)}
              style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                background: "transparent", border: `1px solid ${color}55`, borderRadius: 8,
                padding: "8px 0", cursor: "pointer", color, fontSize: 12, fontWeight: 600 }}>
              <Icon size={14} /> {label}
            </motion.button>
          ))}
        </div>
      </div>
    </div>
  );
}
