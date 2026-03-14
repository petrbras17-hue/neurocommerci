import { useState, useCallback, useMemo } from 'react';
import { getCategoryMeta, DESIGN_TOKENS as T } from '../constants';

interface Props {
  categories: string[];
  categoryCounts: Record<string, number>;
  selectedCategory: string | null;
  onCategorySelect: (cat: string | null) => void;
}

export default function CategoryAccordion({
  categories,
  categoryCounts,
  selectedCategory,
  onCategorySelect,
}: Props) {
  const [expanded, setExpanded] = useState(true);

  const handleClick = useCallback(
    (cat: string) => {
      onCategorySelect(selectedCategory === cat ? null : cat);
    },
    [selectedCategory, onCategorySelect],
  );

  // Sort by count descending (memoized)
  const sorted = useMemo(
    () => [...categories].sort((a, b) => (categoryCounts[b] ?? 0) - (categoryCounts[a] ?? 0)),
    [categories, categoryCounts],
  );

  return (
    <div style={{ borderBottom: `1px solid ${T.BORDER_SUBTLE}` }}>
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          width: '100%',
          padding: '10px 16px',
          border: 'none',
          background: 'transparent',
          cursor: 'pointer',
          fontFamily: 'inherit',
        }}
      >
        <span style={{
          fontSize: 10,
          fontWeight: 600,
          color: T.TEXT_MUTED,
          letterSpacing: 1.2,
          textTransform: 'uppercase',
        }}>
          Категории ({categories.length})
        </span>
        <span style={{
          color: T.TEXT_MUTED,
          fontSize: 10,
          transition: 'transform 0.2s ease',
          transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
        }}>
          ▼
        </span>
      </button>

      {/* Category list */}
      {expanded && (
        <div style={{
          padding: '0 8px 8px',
          maxHeight: 320,
          overflowY: 'auto',
        }}>
          {/* "All" option */}
          <CategoryRow
            label="Все категории"
            icon="🌐"
            color={T.ACCENT}
            count={Object.values(categoryCounts).reduce((s, c) => s + c, 0)}
            active={selectedCategory === null}
            onClick={() => onCategorySelect(null)}
          />

          {sorted.map((cat) => {
            const meta = getCategoryMeta(cat);
            return (
              <CategoryRow
                key={cat}
                label={meta.label}
                icon={meta.icon}
                color={meta.color}
                count={categoryCounts[cat] ?? 0}
                active={selectedCategory === cat}
                onClick={() => handleClick(cat)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

function CategoryRow({
  label,
  icon,
  color,
  count,
  active,
  onClick,
}: {
  label: string;
  icon: string;
  color: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        width: '100%',
        padding: '6px 8px',
        borderRadius: 6,
        border: 'none',
        cursor: 'pointer',
        background: active ? `${color}15` : 'transparent',
        transition: 'background 0.15s ease',
        fontFamily: 'inherit',
      }}
    >
      {/* Color dot */}
      <span style={{
        width: 8,
        height: 8,
        borderRadius: '50%',
        background: color,
        flexShrink: 0,
        boxShadow: active ? `0 0 6px ${color}60` : 'none',
      }} />

      {/* Icon + label */}
      <span style={{ fontSize: 13 }}>{icon}</span>
      <span style={{
        flex: 1,
        fontSize: 12,
        color: active ? T.TEXT_PRIMARY : T.TEXT_SECONDARY,
        fontWeight: active ? 600 : 400,
        textAlign: 'left',
      }}>
        {label}
      </span>

      {/* Count badge */}
      <span style={{
        fontSize: 10,
        fontFamily: "'JetBrains Mono', monospace",
        color: T.TEXT_MUTED,
        background: T.SURFACE_ELEVATED,
        padding: '1px 6px',
        borderRadius: 3,
      }}>
        {count}
      </span>
    </button>
  );
}
