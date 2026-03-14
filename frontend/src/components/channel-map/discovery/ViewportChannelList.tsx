import { useMemo } from 'react';
import { getCategoryMeta, formatNumber, DESIGN_TOKENS as T } from '../constants';
import type { GeoPoint } from '../../../api';

interface Props {
  geoPoints: GeoPoint[];
  onChannelSelect: (id: number, lat?: number, lng?: number) => void;
}

const MAX_VISIBLE = 10;

export default function ViewportChannelList({ geoPoints, onChannelSelect }: Props) {
  // Show top channels sorted by member count (memoized to avoid re-sorting on every render)
  const topChannels = useMemo(
    () => [...geoPoints].sort((a, b) => b.m - a.m).slice(0, MAX_VISIBLE),
    [geoPoints],
  );

  if (topChannels.length === 0) {
    return (
      <div style={{
        padding: '24px 16px',
        textAlign: 'center',
        color: T.TEXT_MUTED,
        fontSize: 12,
      }}>
        Нет каналов для отображения
      </div>
    );
  }

  return (
    <div style={{ padding: '0 8px 8px' }}>
      {/* Header */}
      <div style={{
        padding: '10px 8px 6px',
        fontSize: 10,
        fontWeight: 600,
        color: T.TEXT_MUTED,
        letterSpacing: 1.2,
        textTransform: 'uppercase',
      }}>
        Топ каналы ({geoPoints.length} всего)
      </div>

      {/* Channel rows */}
      {topChannels.map((ch) => {
        const meta = getCategoryMeta(ch.cat);
        return (
          <button
            key={ch.id}
            onClick={() => onChannelSelect(ch.id, ch.lat, ch.lng)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              width: '100%',
              padding: '7px 8px',
              borderRadius: 6,
              border: 'none',
              cursor: 'pointer',
              background: 'transparent',
              transition: 'background 0.15s ease',
              fontFamily: 'inherit',
              textAlign: 'left',
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLElement).style.background = T.SURFACE_ELEVATED;
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.background = 'transparent';
            }}
          >
            {/* Category dot */}
            <span style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: meta.color,
              flexShrink: 0,
            }} />

            {/* Title */}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{
                fontSize: 12,
                color: T.TEXT_PRIMARY,
                fontWeight: 500,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}>
                {ch.t || ch.u || `#${ch.id}`}
              </div>
              {ch.u && ch.t && (
                <div style={{
                  fontSize: 10,
                  color: T.TEXT_MUTED,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}>
                  @{ch.u}
                </div>
              )}
            </div>

            {/* Members */}
            <span style={{
              fontSize: 11,
              fontFamily: "'JetBrains Mono', monospace",
              color: T.ACCENT,
              flexShrink: 0,
            }}>
              {formatNumber(ch.m)}
            </span>

            {/* Comments indicator */}
            {ch.c && (
              <span style={{ fontSize: 10, opacity: 0.5 }} title="Комментарии вкл">
                💬
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
