import 'leaflet/dist/leaflet.css';
import React, { useCallback, useEffect, useRef } from 'react';
import {
  MapContainer,
  TileLayer,
  useMap,
  useMapEvents,
} from 'react-leaflet';
import type { Map as LeafletMap, LatLngBounds } from 'leaflet';
import {
  MAP_CONFIG,
  TILE_URL,
  TILE_ATTRIBUTION,
} from './constants';

// ──────────────────────────────────────────────
// Типы
// ──────────────────────────────────────────────

export interface MapBounds {
  sw_lat: number;
  sw_lng: number;
  ne_lat: number;
  ne_lng: number;
}

export interface WorldMapProps {
  /** Вызывается при изменении границ/зума карты */
  onBoundsChange?: (bounds: MapBounds, zoom: number) => void;
  /** Проброс ссылки на экземпляр Leaflet Map */
  mapRef?: React.MutableRefObject<LeafletMap | null>;
  /** Контент карты (маркеры, кластеры и т.д.) */
  children?: React.ReactNode;
  /** Высота контейнера, по умолчанию 100% */
  height?: string;
}

// ──────────────────────────────────────────────
// Внутренний хук: отслеживает события карты
// ──────────────────────────────────────────────

interface MapEventsProps {
  onBoundsChange?: (bounds: MapBounds, zoom: number) => void;
  mapRef?: React.MutableRefObject<LeafletMap | null>;
}

function MapEvents({ onBoundsChange, mapRef }: MapEventsProps) {
  const map = useMap();

  // Сохраняем ссылку на карту для внешнего использования
  useEffect(() => {
    if (mapRef) {
      mapRef.current = map;
    }
  }, [map, mapRef]);

  const emitBounds = useCallback(
    (leafletMap: LeafletMap) => {
      if (!onBoundsChange) return;
      const b: LatLngBounds = leafletMap.getBounds();
      onBoundsChange(
        {
          sw_lat: b.getSouth(),
          sw_lng: b.getWest(),
          ne_lat: b.getNorth(),
          ne_lng: b.getEast(),
        },
        leafletMap.getZoom(),
      );
    },
    [onBoundsChange],
  );

  // Первоначальная эмиссия при монтировании
  useEffect(() => {
    emitBounds(map);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useMapEvents({
    moveend: (e) => emitBounds(e.target as LeafletMap),
    zoomend: (e) => emitBounds(e.target as LeafletMap),
  });

  return null;
}

// ──────────────────────────────────────────────
// Компонент для исправления иконок Leaflet
// (стандартная проблема с Vite/webpack bundlers)
// ──────────────────────────────────────────────

function FixLeafletIcons() {
  useEffect(() => {
    // Leaflet пытается загрузить иконки через URL, которые Vite не разрешает.
    // Используем import (уже загружен) вместо require.
    import('leaflet').then((L) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      delete (L.Icon.Default.prototype as any)._getIconUrl;
    L.Icon.Default.mergeOptions({
      iconRetinaUrl: new URL(
        'leaflet/dist/images/marker-icon-2x.png',
        import.meta.url,
      ).href,
      iconUrl: new URL(
        'leaflet/dist/images/marker-icon.png',
        import.meta.url,
      ).href,
      shadowUrl: new URL(
        'leaflet/dist/images/marker-shadow.png',
        import.meta.url,
      ).href,
    });
    });
  }, []);
  return null;
}

// ──────────────────────────────────────────────
// Главный компонент WorldMap
// ──────────────────────────────────────────────

export function WorldMap({
  onBoundsChange,
  mapRef,
  children,
  height = '100%',
}: WorldMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  return (
    <div
      ref={containerRef}
      style={{
        width: '100%',
        height,
        background: '#0a0a0b',
        position: 'relative',
      }}
    >
      <MapContainer
        center={MAP_CONFIG.center}
        zoom={MAP_CONFIG.zoom}
        minZoom={MAP_CONFIG.minZoom}
        maxZoom={MAP_CONFIG.maxZoom}
        style={{ width: '100%', height: '100%', background: '#0a0a0b' }}
        zoomControl={true}
        attributionControl={true}
        // Отключаем двойной клик для зума — будем управлять через кластеры
        doubleClickZoom={true}
      >
        {/* Тёмные тайлы CARTO Dark Matter */}
        <TileLayer
          url={TILE_URL}
          attribution={TILE_ATTRIBUTION}
          subdomains="abcd"
          maxZoom={19}
        />

        {/* Исправление иконок Leaflet для Vite */}
        <FixLeafletIcons />

        {/* Обработчик событий карты */}
        <MapEvents onBoundsChange={onBoundsChange} mapRef={mapRef} />

        {/* Слои маркеров и кластеров */}
        {children}
      </MapContainer>

      {/* CSS переопределения для тёмной темы */}
      <style>{`
        .leaflet-container {
          background: #0a0a0b !important;
          font-family: 'Geist Sans', sans-serif;
        }
        .leaflet-control-zoom {
          border: 1px solid #1a1a1b !important;
          box-shadow: none !important;
        }
        .leaflet-control-zoom a {
          background: #111113 !important;
          color: #00ff88 !important;
          border-color: #1a1a1b !important;
          font-size: 16px !important;
          line-height: 26px !important;
        }
        .leaflet-control-zoom a:hover {
          background: #1a1a1b !important;
          color: #00ff88 !important;
        }
        .leaflet-control-attribution {
          background: rgba(10, 10, 11, 0.7) !important;
          color: #4b5563 !important;
          font-size: 10px !important;
        }
        .leaflet-control-attribution a {
          color: #6b7280 !important;
        }
        .leaflet-popup-content-wrapper {
          background: #111113 !important;
          color: #e5e7eb !important;
          border: 1px solid #1a1a1b !important;
          border-radius: 8px !important;
          box-shadow: 0 4px 24px rgba(0,0,0,0.6) !important;
        }
        .leaflet-popup-tip {
          background: #111113 !important;
        }
        .leaflet-popup-close-button {
          color: #6b7280 !important;
        }
        .leaflet-popup-close-button:hover {
          color: #00ff88 !important;
        }
        /* Скрываем стандартную тень маркера */
        .leaflet-marker-shadow {
          display: none !important;
        }
      `}</style>
    </div>
  );
}

export default WorldMap;
