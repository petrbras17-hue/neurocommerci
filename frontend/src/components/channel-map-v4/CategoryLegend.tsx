// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map v4: CategoryLegend
// Наложение в нижнем левом углу карты.
// Цветные точки с названиями категорий.
// Клик по точке — переключает фильтр категории.
// Сворачивается кнопкой. Показывает max 12 категорий, остальные — под "Ещё..."
// ═══════════════════════════════════════════════════════════════════════════════
import React, { useState } from "react";
import { useAuth } from "../../auth";
import { channelMapApi } from "../../api";
import type { ChannelMapStats } from "../../api";
import {
  CATEGORY_COLORS,
  getCategoryColor,
  getCategoryLabel,
} from "./constants";

// ─── Design tokens (local) ────────────────────────────────────────────────────

const T = {
  bg: "#0a0a0b",
  bgHover: "#1a1a1b",
  border: "#1a1a1b",
  borderMid: "#2a2a2b",
  accent: "#00ff88",
  text: "#e5e5e5",
  textDim: "#888",
  radius: "6px",
  radiusMd: "10px",
} as const;

// ─── Constants ────────────────────────────────────────────────────────────────

const MAX_VISIBLE = 12;

// ─── Types ────────────────────────────────────────────────────────────────────

export type CategoryLegendProps = {
  /** Текущая выбранная категория ("" — все) */
  activeCategory: string;
  /** Колбэк: клик по категории переключает фильтр */
  onCategoryToggle: (category: string) => void;
  /** Список категорий с кол-вом каналов, если известен */
  categoryCounts?: Record<string, number>;
};

// ─── Dot item ─────────────────────────────────────────────────────────────────

function LegendItem({
  category,
  count,
  active,
  onClick,
}: {
  category: string;
  count?: number;
  active: boolean;
  onClick: () => void;
}) {
  const [hovered, setHovered] = useState(false);
  const color = getCategoryColor(category);
  const label = getCategoryLabel(category);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => e.key === "Enter" && onClick()}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      title={count !== undefined ? `${label}: ${count.toLocaleString("ru-RU")} каналов` : label}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: "3px 6px",
        borderRadius: T.radius,
        cursor: "pointer",
        background: active
          ? `${color}18`
          : hovered
          ? "rgba(255,255,255,0.04)"
          : "transparent",
        border: active ? `1px solid ${color}44` : "1px solid transparent",
        transition: "all 0.12s",
        outline: "none",
        opacity: active || !hovered ? 1 : 0.75,
      }}
    >
      {/* Colored dot */}
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: color,
          flexShrink: 0,
          boxShadow: active ? `0 0 6px ${color}88` : "none",
        }}
      />
      {/* Label */}
      <span
        style={{
          color: active ? T.text : T.textDim,
          fontSize: 11,
          fontWeight: active ? 700 : 400,
          whiteSpace: "nowrap",
          fontFamily: "'Geist Sans', sans-serif",
          transition: "color 0.12s",
        }}
      >
        {label}
      </span>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function CategoryLegend({
  activeCategory,
  onCategoryToggle,
  categoryCounts = {},
}: CategoryLegendProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [showAll, setShowAll] = useState(false);

  // Build sorted category list
  const allCategories = Object.keys(CATEGORY_COLORS).filter(
    (k) => k !== "other"
  );

  // Sort by count if available, else alphabetically by label
  const sorted = [...allCategories].sort((a, b) => {
    const ca = categoryCounts[a] ?? 0;
    const cb = categoryCounts[b] ?? 0;
    if (ca !== cb) return cb - ca;
    return getCategoryLabel(a).localeCompare(getCategoryLabel(b), "ru");
  });

  // Always show "other" last
  const categoriesWithOther = [
    ...sorted,
    ...(CATEGORY_COLORS.other ? ["other"] : []),
  ];

  const visible = showAll
    ? categoriesWithOther
    : categoriesWithOther.slice(0, MAX_VISIBLE);

  const hiddenCount = categoriesWithOther.length - MAX_VISIBLE;

  return (
    <div
      style={{
        position: "absolute",
        bottom: 28,
        left: 12,
        zIndex: 900,
        background: "rgba(10,10,11,0.88)",
        backdropFilter: "blur(8px)",
        border: `1px solid ${T.border}`,
        borderRadius: T.radiusMd,
        padding: collapsed ? "6px 10px" : "10px",
        maxWidth: 200,
        boxShadow: "0 4px 20px rgba(0,0,0,0.5)",
        transition: "all 0.15s",
      }}
    >
      {/* Header row */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: collapsed ? 0 : 6,
        }}
      >
        <span
          style={{
            color: T.textDim,
            fontSize: 10,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            fontFamily: "'Geist Sans', sans-serif",
          }}
        >
          Категории
        </span>
        <button
          onClick={() => setCollapsed((v) => !v)}
          title={collapsed ? "Развернуть" : "Свернуть"}
          style={{
            background: "none",
            border: "none",
            color: T.textDim,
            cursor: "pointer",
            padding: "0 2px",
            fontSize: 12,
            lineHeight: 1,
          }}
        >
          {collapsed ? "▲" : "▼"}
        </button>
      </div>

      {/* Items */}
      {!collapsed && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          {/* "Все" item — deselect filter */}
          <LegendItem
            category="other"
            active={activeCategory === ""}
            onClick={() => onCategoryToggle("")}
            count={
              Object.values(categoryCounts).reduce((s, v) => s + v, 0) ||
              undefined
            }
          />
          {/* Separator */}
          <div
            style={{
              borderTop: `1px solid ${T.border}`,
              margin: "2px 0",
            }}
          />

          {visible.map((cat) => (
            <LegendItem
              key={cat}
              category={cat}
              count={categoryCounts[cat]}
              active={activeCategory === cat}
              onClick={() =>
                onCategoryToggle(activeCategory === cat ? "" : cat)
              }
            />
          ))}

          {/* Expand / collapse "Ещё" */}
          {!showAll && hiddenCount > 0 && (
            <button
              onClick={() => setShowAll(true)}
              style={{
                background: "none",
                border: "none",
                color: T.accent,
                cursor: "pointer",
                fontSize: 11,
                padding: "3px 6px",
                textAlign: "left",
                fontFamily: "'Geist Sans', sans-serif",
              }}
            >
              Ещё {hiddenCount}...
            </button>
          )}
          {showAll && hiddenCount > 0 && (
            <button
              onClick={() => setShowAll(false)}
              style={{
                background: "none",
                border: "none",
                color: T.textDim,
                cursor: "pointer",
                fontSize: 11,
                padding: "3px 6px",
                textAlign: "left",
                fontFamily: "'Geist Sans', sans-serif",
              }}
            >
              Скрыть
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default CategoryLegend;
