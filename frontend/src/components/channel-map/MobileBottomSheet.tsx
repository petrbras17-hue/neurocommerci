// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map: Mobile Bottom Sheet
// ═══════════════════════════════════════════════════════════════════════════════
import { useRef } from "react";
import { motion, useAnimation, PanInfo } from "framer-motion";
import type { ChannelMapEntry, GeoPoint } from "../../api";
import { DESIGN_TOKENS as T, getCategoryMeta, formatNumber, formatER } from "./constants";

export type MobileBottomSheetProps = {
  position: "peek" | "half" | "full";
  onPositionChange: (pos: "peek" | "half" | "full") => void;
  selectedChannel: ChannelMapEntry | null;
  similarChannels: ChannelMapEntry[];
  detailLoading: boolean;
  geoPoints: GeoPoint[];
  onSelectChannel: (point: GeoPoint) => void;
  onCloseDetail: () => void;
  onAddToFarm: (channelId: number) => void;
  onBlacklist: (channelId: number) => void;
};

const SNAP: Record<string, number> = { peek: 30, half: 60, full: 90 };
const ORDERED: Array<"peek" | "half" | "full"> = ["peek", "half", "full"];

function nearest(curVh: number, vel: number): "peek" | "half" | "full" {
  const b = curVh - vel * 0.15;
  let best = ORDERED[0], bestD = Math.abs(b - SNAP[best]);
  for (const k of ORDERED) { const d = Math.abs(b - SNAP[k]); if (d < bestD) { best = k; bestD = d; } }
  return best;
}

export function MobileBottomSheet(props: MobileBottomSheetProps) {
  const {
    position, onPositionChange, selectedChannel: ch, detailLoading,
    geoPoints, onSelectChannel, onCloseDetail, onAddToFarm, onBlacklist,
  } = props;
  const controls = useAnimation();
  const ref = useRef<HTMLDivElement>(null);
  const heightVh = SNAP[position];

  const handleDragEnd = (_: unknown, info: PanInfo) => {
    const el = ref.current;
    if (!el) return;
    const vhNow = (el.getBoundingClientRect().height / window.innerHeight) * 100;
    const next = nearest(vhNow, info.velocity.y);
    onPositionChange(next);
    controls.start({ height: `${SNAP[next]}vh` });
  };

  const sorted = [...geoPoints].sort((a, b) => b.m - a.m).slice(0, 50);
  const cm = ch ? getCategoryMeta(ch.category) : null;
  const summary = ch
    ? `${cm?.icon} ${ch.title ?? ch.username ?? "Канал"}`
    : `${geoPoints.length} каналов на карте`;

  return (
    <motion.div
      ref={ref} drag="y" dragConstraints={{ top: 0, bottom: 0 }}
      dragElastic={0.15} onDragEnd={handleDragEnd} animate={controls}
      initial={{ height: `${heightVh}vh` }}
      transition={{ type: "spring", stiffness: 350, damping: 35 }}
      style={{
        position: "fixed", bottom: 0, left: 0, right: 0, zIndex: 900,
        background: T.BG, borderTop: `1px solid ${T.BORDER}`,
        borderRadius: "16px 16px 0 0", overflow: "hidden",
        display: "flex", flexDirection: "column", touchAction: "none",
        height: `${heightVh}vh`,
      }}
    >
      {/* Drag handle */}
      <div style={{ display: "flex", justifyContent: "center", padding: "10px 0 6px", cursor: "grab", flexShrink: 0 }}>
        <div style={{ width: 40, height: 4, borderRadius: 2, background: T.TEXT_SECONDARY }} />
      </div>

      {/* Summary line */}
      <div style={{
        padding: "0 16px 8px", fontSize: 14, fontWeight: 600, color: T.TEXT_PRIMARY,
        display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0,
      }}>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{summary}</span>
        {ch && <span onClick={onCloseDetail} style={{ cursor: "pointer", color: T.TEXT_SECONDARY, fontSize: 18, marginLeft: 8 }}>×</span>}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflowY: "auto", padding: "0 16px 16px" }}>
        {detailLoading && <div style={{ color: T.TEXT_SECONDARY, fontSize: 13, padding: 12 }}>Загрузка...</div>}

        {ch && !detailLoading && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <span style={{
                padding: "2px 8px", borderRadius: 6, fontSize: 11, fontWeight: 600,
                background: `${cm!.color}22`, color: cm!.color,
              }}>{cm!.label}</span>
              {ch.username && <span style={{ color: T.TEXT_SECONDARY, fontSize: 12 }}>@{ch.username}</span>}
            </div>
            <div style={{ display: "flex", gap: 16, fontSize: 13, color: T.TEXT_SECONDARY }}>
              <span>Подписчиков: <b style={{ color: T.TEXT_PRIMARY }}>{formatNumber(ch.member_count)}</b></span>
              <span>ER: <b style={{ color: T.ACCENT }}>{formatER(ch.engagement_rate)}</b></span>
            </div>
            {ch.description && position === "full" && (
              <p style={{ margin: 0, fontSize: 12, color: T.TEXT_SECONDARY, lineHeight: 1.5 }}>{ch.description}</p>
            )}
            <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
              <button onClick={() => onAddToFarm(ch.id)} style={{
                flex: 1, padding: "8px 0", border: "none", borderRadius: 8,
                background: T.ACCENT + "33", color: T.TEXT_PRIMARY, fontSize: 13, fontWeight: 600, cursor: "pointer",
              }}>В ферму</button>
              <button onClick={() => onBlacklist(ch.id)} style={{
                flex: 1, padding: "8px 0", border: "none", borderRadius: 8,
                background: "#ef444433", color: T.TEXT_PRIMARY, fontSize: 13, fontWeight: 600, cursor: "pointer",
              }}>Черный список</button>
            </div>
          </div>
        )}

        {!ch && !detailLoading && sorted.map((p) => {
          const meta = getCategoryMeta(p.cat);
          return (
            <div key={p.id} onClick={() => onSelectChannel(p)} style={{
              display: "flex", alignItems: "center", gap: 10, padding: "10px 0",
              borderBottom: `1px solid ${T.BORDER}`, cursor: "pointer",
            }}>
              <span style={{ fontSize: 18, width: 28, textAlign: "center" }}>{meta.icon}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  fontSize: 13, fontWeight: 600, color: T.TEXT_PRIMARY,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>{p.t}</div>
                {p.u && <div style={{ fontSize: 11, color: T.TEXT_SECONDARY }}>@{p.u}</div>}
              </div>
              <span style={{ fontSize: 12, color: T.TEXT_SECONDARY, flexShrink: 0 }}>{formatNumber(p.m)}</span>
            </div>
          );
        })}
      </div>
    </motion.div>
  );
}
