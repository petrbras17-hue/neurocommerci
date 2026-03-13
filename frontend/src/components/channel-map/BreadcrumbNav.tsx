// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map: BreadcrumbNav
// Drill-down breadcrumb pill for globe navigation
// ═══════════════════════════════════════════════════════════════════════════════

import React from "react";
import { ChevronRight } from "lucide-react";
import type { DrillPathEntry } from "./hooks/useGlobeInteraction";
import { DESIGN_TOKENS as T } from "./constants";

export type BreadcrumbNavProps = {
  drillPath: DrillPathEntry[];
  onReset: () => void;
};

const glassStyle: React.CSSProperties = {
  background: T.SURFACE,
  backdropFilter: "blur(12px)",
  WebkitBackdropFilter: "blur(12px)",
  border: `1px solid ${T.BORDER}`,
  borderRadius: 12,
  boxShadow: "0 0 20px rgba(0, 255, 136, 0.05)",
};

export function BreadcrumbNav({ drillPath, onReset }: BreadcrumbNavProps) {
  if (drillPath.length === 0) return null;

  return (
    <div
      style={{
        position: "absolute",
        bottom: 20,
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 15,
        display: "flex",
        alignItems: "center",
        gap: 4,
        ...glassStyle,
        padding: "8px 16px",
      }}
    >
      <button
        onClick={onReset}
        style={{
          background: "none",
          border: "none",
          color: T.ACCENT,
          cursor: "pointer",
          fontSize: 13,
          padding: "2px 4px",
        }}
      >
        {"\uD83C\uDF0D"} Planet
      </button>
      {drillPath.map((entry, i) => (
        <span key={i} style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <ChevronRight size={12} color={T.TEXT_SECONDARY} />
          <span style={{ color: T.TEXT_PRIMARY, fontSize: 13 }}>
            {entry.icon} {entry.label}
          </span>
        </span>
      ))}
    </div>
  );
}
