import React, { useMemo } from 'react';
import { Marker, Tooltip } from 'react-leaflet';
import L from 'leaflet';
import type { ChannelPoint } from './hooks/useMapData';
import {
  getCategoryColor,
  getCategoryLabel,
  formatSubscribers,
  ZOOM_LEVELS,
  MARKER_SIZES,
} from './constants';

// ──────────────────────────────────────────────
// Типы
// ──────────────────────────────────────────────

interface ChannelMarkerProps {
  channel: ChannelPoint;
  zoom: number;
  onClick?: (channel: ChannelPoint) => void;
}

// ──────────────────────────────────────────────
// Иконка: маленькая цветная точка (zoom < DETAIL)
// ──────────────────────────────────────────────

function createDotIcon(category: string | null, zoom: number): L.DivIcon {
  const color = getCategoryColor(category);
  const size =
    zoom >= ZOOM_LEVELS.CITY + 2
      ? MARKER_SIZES.DOT_LARGE
      : MARKER_SIZES.DOT_SMALL;

  const html = `
    <div style="
      width: ${size}px;
      height: ${size}px;
      border-radius: 50%;
      background: ${color};
      border: 1.5px solid rgba(0,0,0,0.4);
      box-shadow: 0 0 6px ${color}60;
      cursor: pointer;
      transition: transform 0.15s;
    " class="channel-dot"></div>
    <style>
      .channel-dot:hover {
        transform: scale(1.5);
        box-shadow: 0 0 10px ${color};
      }
    </style>
  `;

  return L.divIcon({
    html,
    className: '',
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

// ──────────────────────────────────────────────
// Иконка: аватар-пузырь (zoom >= DETAIL)
// ──────────────────────────────────────────────

function createAvatarIcon(
  channel: ChannelPoint,
  zoom: number,
): L.DivIcon {
  const color = getCategoryColor(channel.category);
  const size =
    zoom >= 16 ? MARKER_SIZES.AVATAR_LARGE : MARKER_SIZES.AVATAR;
  const fontSize = size >= MARKER_SIZES.AVATAR_LARGE ? 20 : 16;

  // Буква для фоллбэка если нет аватара
  const initial = (channel.title ?? channel.username ?? '?')
    .charAt(0)
    .toUpperCase();

  let innerContent: string;
  if (channel.avatar_url) {
    innerContent = `
      <img
        src="${channel.avatar_url}"
        alt="${initial}"
        style="
          width: ${size}px;
          height: ${size}px;
          border-radius: 50%;
          object-fit: cover;
          display: block;
        "
        onerror="this.style.display='none';this.nextSibling.style.display='flex'"
      />
      <div style="
        display: none;
        width: ${size}px;
        height: ${size}px;
        border-radius: 50%;
        background: #1a1a1b;
        align-items: center;
        justify-content: center;
        font-size: ${fontSize}px;
        font-weight: 700;
        color: ${color};
        font-family: 'JetBrains Mono', monospace;
      ">${initial}</div>
    `;
  } else {
    innerContent = `
      <div style="
        width: ${size}px;
        height: ${size}px;
        border-radius: 50%;
        background: #1a1a1b;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: ${fontSize}px;
        font-weight: 700;
        color: ${color};
        font-family: 'JetBrains Mono', monospace;
      ">${initial}</div>
    `;
  }

  // Имя канала под аватаром
  const nameLabel =
    zoom >= 15
      ? `<div style="
          position: absolute;
          top: ${size + 4}px;
          left: 50%;
          transform: translateX(-50%);
          background: rgba(10,10,11,0.8);
          color: #e5e7eb;
          font-size: 10px;
          font-family: 'Geist Sans', sans-serif;
          padding: 1px 4px;
          border-radius: 3px;
          white-space: nowrap;
          max-width: 100px;
          overflow: hidden;
          text-overflow: ellipsis;
          pointer-events: none;
        ">${channel.title ?? '@' + channel.username}</div>`
      : '';

  const html = `
    <div style="
      position: relative;
      cursor: pointer;
      display: inline-flex;
      flex-direction: column;
      align-items: center;
    ">
      <div style="
        border-radius: 50%;
        border: 2.5px solid ${color};
        box-shadow: 0 0 10px ${color}50, 0 2px 8px rgba(0,0,0,0.6);
        overflow: hidden;
        width: ${size}px;
        height: ${size}px;
        flex-shrink: 0;
      ">
        ${innerContent}
      </div>
      ${nameLabel}
    </div>
  `;

  const totalHeight = zoom >= 15 ? size + 18 : size;

  return L.divIcon({
    html,
    className: '',
    iconSize: [size, totalHeight],
    iconAnchor: [size / 2, size / 2],
  });
}

// ──────────────────────────────────────────────
// Компонент ChannelMarker
// ──────────────────────────────────────────────

export function ChannelMarker({ channel, zoom, onClick }: ChannelMarkerProps) {
  // Позиция: требует корректных координат
  const lat = channel.latitude;
  const lng = channel.longitude;

  if (lat == null || lng == null) return null;
  if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;

  const position: [number, number] = [lat, lng];
  const isDetailZoom = zoom >= ZOOM_LEVELS.DETAIL;

  const icon = useMemo(
    () =>
      isDetailZoom
        ? createAvatarIcon(channel, zoom)
        : createDotIcon(channel.category, zoom),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [channel.id, channel.avatar_url, channel.category, zoom, isDetailZoom],
  );

  const color = getCategoryColor(channel.category);

  return (
    <Marker
      position={position}
      icon={icon}
      eventHandlers={{
        click: () => onClick?.(channel),
      }}
    >
      {/* Tooltip при hover: компактная карточка */}
      <Tooltip
        direction="top"
        offset={[0, isDetailZoom ? -MARKER_SIZES.AVATAR / 2 - 4 : -6]}
        opacity={0.97}
        sticky={false}
      >
        <div
          style={{
            background: '#111113',
            color: '#e5e7eb',
            padding: '8px 12px',
            borderRadius: '8px',
            fontSize: '12px',
            fontFamily: 'Geist Sans, sans-serif',
            border: `1px solid ${color}30`,
            minWidth: '160px',
            maxWidth: '240px',
            boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
          }}
        >
          {/* Заголовок */}
          <div
            style={{
              fontWeight: 700,
              color: '#f9fafb',
              fontSize: '13px',
              marginBottom: 3,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {channel.title ?? `@${channel.username}`}
          </div>

          {/* @username */}
          {channel.username && (
            <div
              style={{
                color: '#6b7280',
                fontSize: '11px',
                marginBottom: 5,
              }}
            >
              @{channel.username}
            </div>
          )}

          {/* Мета-строка */}
          <div
            style={{
              display: 'flex',
              gap: 8,
              alignItems: 'center',
              flexWrap: 'wrap',
            }}
          >
            {/* Подписчики */}
            <span
              style={{
                color: '#00ff88',
                fontWeight: 600,
                fontSize: '11px',
                fontFamily: 'JetBrains Mono, monospace',
              }}
            >
              {formatSubscribers(channel.subscribers)}
            </span>

            {/* Категория */}
            {channel.category && (
              <span
                style={{
                  background: `${color}20`,
                  color,
                  padding: '1px 6px',
                  borderRadius: '10px',
                  fontSize: '10px',
                  fontWeight: 600,
                  border: `1px solid ${color}40`,
                }}
              >
                {getCategoryLabel(channel.category)}
              </span>
            )}

            {/* Язык */}
            {channel.language && (
              <span style={{ color: '#4b5563', fontSize: '10px' }}>
                {channel.language.toUpperCase()}
              </span>
            )}
          </div>
        </div>
      </Tooltip>
    </Marker>
  );
}

export default ChannelMarker;
