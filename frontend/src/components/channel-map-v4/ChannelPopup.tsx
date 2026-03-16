// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map v4: ChannelPopup
// Lightweight hover popup for Leaflet markers.
// Rendered as a plain div — caller embeds it inside a Leaflet Popup's content
// or uses renderToStaticMarkup() for DivIcon. This component is deliberately
// dependency-free (no framer-motion) so it stays small.
// ═══════════════════════════════════════════════════════════════════════════════
import React from "react";
import { DESIGN_TOKENS as T, getCategoryMeta, formatNumber } from "../channel-map/constants";
import type { ChannelMapEntry } from "../../api";

// ─── Design constants ─────────────────────────────────────────────────────────

const BG = T.SURFACE;
const BORDER_COLOR = "rgba(0, 255, 136, 0.18)";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function AvatarPlaceholder({ title, color }: { title: string; color: string }) {
  const letter = (title || "?").charAt(0).toUpperCase();
  return (
    <div
      style={{
        width: 40,
        height: 40,
        borderRadius: "50%",
        background: `${color}22`,
        border: `2px solid ${color}55`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        fontSize: 16,
        fontWeight: 700,
        color,
        fontFamily: "'Geist Sans', sans-serif",
      }}
    >
      {letter}
    </div>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export type ChannelPopupProps = {
  channel: ChannelMapEntry;
};

export function ChannelPopup({ channel }: ChannelPopupProps) {
  const cat = getCategoryMeta(channel.category);
  const title = channel.title || channel.username || `#${channel.id}`;
  const handle = channel.username ? `@${channel.username}` : null;
  const subs = formatNumber(channel.member_count);

  return (
    <div
      style={{
        background: BG,
        border: `1px solid ${BORDER_COLOR}`,
        borderRadius: 10,
        padding: "10px 12px",
        display: "flex",
        alignItems: "center",
        gap: 10,
        minWidth: 200,
        maxWidth: 280,
        boxShadow: "0 4px 20px rgba(0,0,0,0.6)",
        fontFamily: "'Geist Sans', system-ui, sans-serif",
        // Prevent Leaflet's default popup chrome from adding extra padding
        pointerEvents: "auto",
      }}
    >
      {/* Avatar */}
      {channel.avatar_url ? (
        <img
          src={channel.avatar_url}
          alt={title}
          style={{
            width: 40,
            height: 40,
            borderRadius: "50%",
            border: `2px solid ${cat.color}55`,
            objectFit: "cover",
            flexShrink: 0,
          }}
          onError={(e) => {
            // Fallback if image fails — hide broken img
            (e.target as HTMLImageElement).style.display = "none";
          }}
        />
      ) : (
        <AvatarPlaceholder title={title} color={cat.color} />
      )}

      {/* Text block */}
      <div style={{ minWidth: 0, flex: 1 }}>
        {/* Channel name */}
        <div
          style={{
            color: T.TEXT_PRIMARY,
            fontSize: 13,
            fontWeight: 700,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            lineHeight: 1.3,
          }}
        >
          {title}
        </div>

        {/* Username */}
        {handle && (
          <div
            style={{
              color: T.TEXT_SECONDARY,
              fontSize: 11,
              marginTop: 1,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {handle}
          </div>
        )}

        {/* Bottom row: subscribers + category */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            marginTop: 5,
          }}
        >
          <span
            style={{
              color: T.ACCENT,
              fontSize: 11,
              fontWeight: 600,
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            {subs}
          </span>
          <span
            style={{
              background: `${cat.color}22`,
              color: cat.color,
              fontSize: 10,
              fontWeight: 600,
              padding: "2px 7px",
              borderRadius: 100,
              border: `1px solid ${cat.color}44`,
              whiteSpace: "nowrap",
            }}
          >
            {cat.icon} {cat.label}
          </span>
        </div>
      </div>
    </div>
  );
}
