import React, { useMemo } from 'react';
import { Marker, Tooltip, useMap } from 'react-leaflet';
import L from 'leaflet';
import type { ClusterPoint } from './hooks/useMapData';
import {
  getCategoryColor,
  formatSubscribers,
  CLUSTER_SIZES,
} from './constants';

// ──────────────────────────────────────────────
// Типы
// ──────────────────────────────────────────────

interface ClusterMarkerProps {
  cluster: ClusterPoint;
  onClick?: (cluster: ClusterPoint) => void;
}

// ──────────────────────────────────────────────
// Вспомогательные функции
// ──────────────────────────────────────────────

function formatCount(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${Math.round(count / 1_000)}K`;
  return String(count);
}

function getClusterSize(count: number): number {
  if (count > 1_000) return CLUSTER_SIZES.LARGE;
  if (count > 100) return CLUSTER_SIZES.MEDIUM;
  return CLUSTER_SIZES.SMALL;
}

// ──────────────────────────────────────────────
// Создание DivIcon для кластера
// ──────────────────────────────────────────────

function createClusterIcon(
  count: number,
  totalSubscribers: number,
  dominantCategory: string | null,
): L.DivIcon {
  const size = getClusterSize(count);
  const color = getCategoryColor(dominantCategory);
  const label = formatCount(count);
  const subLabel =
    totalSubscribers > 0 ? formatSubscribers(totalSubscribers) : '';

  // Пульсирующее кольцо для крупных кластеров
  const pulse =
    count > 500
      ? `<div style="
          position: absolute;
          top: 50%;
          left: 50%;
          transform: translate(-50%, -50%);
          width: ${size + 16}px;
          height: ${size + 16}px;
          border-radius: 50%;
          border: 2px solid ${color};
          opacity: 0.3;
          animation: pulse 2s infinite;
        "></div>`
      : '';

  const html = `
    <div style="
      position: relative;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      width: ${size}px;
      height: ${size}px;
      border-radius: 50%;
      background: rgba(10, 10, 11, 0.85);
      border: 2px solid ${color};
      box-shadow: 0 0 12px ${color}40, 0 2px 8px rgba(0,0,0,0.6);
      cursor: pointer;
      font-family: 'JetBrains Mono', monospace;
      user-select: none;
    ">
      ${pulse}
      <span style="
        color: ${color};
        font-size: ${size >= CLUSTER_SIZES.LARGE ? 13 : size >= CLUSTER_SIZES.MEDIUM ? 11 : 10}px;
        font-weight: 700;
        line-height: 1;
        letter-spacing: -0.02em;
      ">${label}</span>
      ${subLabel ? `<span style="
        color: #6b7280;
        font-size: 8px;
        line-height: 1;
        margin-top: 2px;
      ">${subLabel}</span>` : ''}
    </div>
    <style>
      @keyframes pulse {
        0% { transform: translate(-50%, -50%) scale(1); opacity: 0.3; }
        50% { transform: translate(-50%, -50%) scale(1.2); opacity: 0.15; }
        100% { transform: translate(-50%, -50%) scale(1); opacity: 0.3; }
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
// Компонент ClusterMarker
// ──────────────────────────────────────────────

export function ClusterMarker({ cluster, onClick }: ClusterMarkerProps) {
  const map = useMap();

  const icon = useMemo(
    () =>
      createClusterIcon(
        cluster.count,
        cluster.total_subscribers,
        cluster.dominant_category,
      ),
    [cluster.count, cluster.total_subscribers, cluster.dominant_category],
  );

  const position: [number, number] = [cluster.latitude, cluster.longitude];
  const color = getCategoryColor(cluster.dominant_category);

  const handleClick = () => {
    if (onClick) {
      onClick(cluster);
    } else {
      // По умолчанию: zoom in на 2 уровня к центру кластера
      const currentZoom = map.getZoom();
      map.flyTo(position, Math.min(currentZoom + 2, 10), {
        animate: true,
        duration: 0.6,
      });
    }
  };

  return (
    <Marker
      position={position}
      icon={icon}
      eventHandlers={{ click: handleClick }}
    >
      <Tooltip
        direction="top"
        offset={[0, -getClusterSize(cluster.count) / 2 - 4]}
        opacity={0.95}
      >
        <div
          style={{
            background: '#111113',
            color: '#e5e7eb',
            padding: '6px 10px',
            borderRadius: '6px',
            fontSize: '12px',
            fontFamily: 'Geist Sans, sans-serif',
            border: `1px solid ${color}40`,
            minWidth: '120px',
          }}
        >
          <div style={{ color, fontWeight: 700, marginBottom: 2 }}>
            {cluster.label ?? 'Кластер'}
          </div>
          <div style={{ color: '#9ca3af', fontSize: '11px' }}>
            {formatCount(cluster.count)} каналов
          </div>
          {cluster.total_subscribers > 0 && (
            <div style={{ color: '#6b7280', fontSize: '11px' }}>
              {formatSubscribers(cluster.total_subscribers)} подписчиков
            </div>
          )}
        </div>
      </Tooltip>
    </Marker>
  );
}

export default ClusterMarker;
