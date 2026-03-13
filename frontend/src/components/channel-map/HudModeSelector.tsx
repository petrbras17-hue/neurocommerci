// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map: HudModeSelector
// Tab selector for Intel / Farm Ops / Analytics HUD modes
// ═══════════════════════════════════════════════════════════════════════════════

import React from "react";
import { Globe as GlobeIcon, Tractor, BarChart3 } from "lucide-react";
import { DESIGN_TOKENS as T } from "./constants";

export type HudMode = "intel" | "farm" | "analytics";

export type HudModeSelectorProps = {
  mode: HudMode;
  onChange: (mode: HudMode) => void;
};

const MODES: { key: HudMode; label: string; icon: typeof GlobeIcon }[] = [
  { key: "intel",     label: "Channels",  icon: GlobeIcon },
  { key: "farm",      label: "Farm Ops",  icon: Tractor },
  { key: "analytics", label: "Analytics", icon: BarChart3 },
];

export function HudModeSelector({ mode, onChange }: HudModeSelectorProps) {
  return (
    <div style={{ display: "flex", gap: 4 }}>
      {MODES.map((m) => {
        const Icon = m.icon;
        const active = mode === m.key;
        return (
          <button
            key={m.key}
            onClick={() => onChange(m.key)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "6px 14px",
              borderRadius: 8,
              border: "none",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: active ? 600 : 400,
              background: active ? "rgba(0,255,136,0.15)" : "transparent",
              color: active ? T.ACCENT : T.TEXT_SECONDARY,
              transition: "all 0.2s",
            }}
          >
            <Icon size={14} />
            {m.label}
          </button>
        );
      })}
    </div>
  );
}
