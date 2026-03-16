// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map v4 Main Page
// Layout: MapFilters + MapStats (top) | WorldMap + Sidebar (mid) | ChannelList (bottom)
// Dark Terminal theme: #0a0a0b bg, #00ff88 accent
// ═══════════════════════════════════════════════════════════════════════════════
import React, { useState, useCallback, useRef, useEffect } from "react";
import type { Map as LeafletMap } from "leaflet";

import { WorldMap } from "../components/channel-map-v4/WorldMap";
import type { MapBounds } from "../components/channel-map-v4/WorldMap";
import { MapFilters } from "../components/channel-map-v4/MapFilters";
import type { FiltersState } from "../components/channel-map-v4/MapFilters";
import { ChannelSidebar } from "../components/channel-map-v4/ChannelSidebar";
import { ChannelList } from "../components/channel-map-v4/ChannelList";
import { MapStats } from "../components/channel-map-v4/MapStats";
import { CategoryLegend } from "../components/channel-map-v4/CategoryLegend";
import { ClusterMarker } from "../components/channel-map-v4/ClusterMarker";
import { ChannelMarker } from "../components/channel-map-v4/ChannelMarker";
import { useMapData } from "../components/channel-map-v4/hooks/useMapData";
import type { MapFilters as MapDataFilters } from "../components/channel-map-v4/hooks/useMapData";
import type { ChannelMapEntry } from "../api";
import type { MapFilters as ConstMapFilters } from "../components/channel-map-v4/hooks/useMapData";

// ─── Constants ────────────────────────────────────────────────────────────────

const T = {
  bg: "#0a0a0b",
  bgCard: "#111113",
  border: "#1a1a1b",
  accent: "#00ff88",
  text: "#e5e5e5",
  textDim: "#888",
} as const;

const FILTER_BAR_HEIGHT = 52;
const STATS_WIDTH = 380;
const CHANNEL_LIST_MAX_HEIGHT = 280;

// ─── Helper: FiltersState → MapDataFilters ────────────────────────────────────

function toMapDataFilters(f: FiltersState): MapDataFilters {
  return {
    category: f.category ?? undefined,
    language: f.language ?? undefined,
    region: f.region ?? undefined,
    min_subscribers: f.minSubscribers > 0 ? f.minSubscribers : undefined,
    search: f.searchQuery || undefined,
  };
}

// ─── Helper: FiltersState → ConstMapFilters (for ChannelList / MapStats) ───────

function toConstFilters(f: FiltersState): ConstMapFilters {
  return {
    category: f.category ?? "",
    language: f.language ?? "",
    region: f.region ?? "",
    minSubscribers: f.minSubscribers,
    search: f.searchQuery,
  };
}

// ─── Default FiltersState ─────────────────────────────────────────────────────

const DEFAULT_FILTERS: FiltersState = {
  category: null,
  language: null,
  region: null,
  minSubscribers: 0,
  searchQuery: "",
};

// ─── Mobile breakpoint ────────────────────────────────────────────────────────

function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 768);
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, []);
  return isMobile;
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function ChannelMapPage() {
  const isMobile = useIsMobile();

  // State
  const [filters, setFilters] = useState<FiltersState>(DEFAULT_FILTERS);
  const [bounds, setBounds] = useState<MapBounds | null>(null);
  const [zoom, setZoom] = useState(3);
  const [selectedChannelId, setSelectedChannelId] = useState<number | null>(null);
  const [filteredTotal, setFilteredTotal] = useState<number | undefined>(undefined);

  // Ref to Leaflet map for fly-to
  const mapRef = useRef<LeafletMap | null>(null);

  // Map data (clusters / individual channels from API)
  const mapDataFilters = toMapDataFilters(filters);
  const { mode, clusters, channels, loading: mapLoading } = useMapData(
    bounds,
    zoom,
    mapDataFilters
  );

  // ── Callbacks ──────────────────────────────────────────────────────────────

  const handleBoundsChange = useCallback((b: MapBounds, z: number) => {
    setBounds(b);
    setZoom(z);
  }, []);

  const handleFiltersChange = useCallback((newFilters: FiltersState) => {
    setFilters(newFilters);
  }, []);

  const handleChannelSelect = useCallback((ch: ChannelMapEntry) => {
    setSelectedChannelId(ch.id);
    // Fly to channel location if coords are available
    if (ch.lat != null && ch.lng != null && mapRef.current) {
      const currentZoom = mapRef.current.getZoom();
      mapRef.current.flyTo([ch.lat, ch.lng], Math.max(currentZoom, 10), {
        duration: 0.8,
      });
    }
  }, []);

  const handleChannelSelectById = useCallback((id: number) => {
    setSelectedChannelId(id);
  }, []);

  const handleSearchResultClick = useCallback((ch: ChannelMapEntry) => {
    setSelectedChannelId(ch.id);
    if (ch.lat != null && ch.lng != null && mapRef.current) {
      mapRef.current.flyTo([ch.lat, ch.lng], 12, { duration: 0.8 });
    }
  }, []);

  const handleCategoryToggle = useCallback((cat: string) => {
    setFilters((prev) => ({
      ...prev,
      category: cat || null,
    }));
  }, []);

  const handleSidebarClose = useCallback(() => {
    setSelectedChannelId(null);
  }, []);

  const handleSelectSimilar = useCallback((ch: ChannelMapEntry) => {
    setSelectedChannelId(ch.id);
  }, []);

  // ── Layout ─────────────────────────────────────────────────────────────────

  const constFilters = toConstFilters(filters);

  // Map height: full viewport minus filter bar and channel list
  const mapHeight = isMobile
    ? `calc(100vh - ${FILTER_BAR_HEIGHT}px - ${CHANNEL_LIST_MAX_HEIGHT}px - 48px)`
    : `calc(100vh - ${FILTER_BAR_HEIGHT}px - ${CHANNEL_LIST_MAX_HEIGHT}px - 48px)`;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        background: T.bg,
        fontFamily: "'Geist Sans', system-ui, sans-serif",
        overflow: "hidden",
      }}
    >
      {/* ── Top bar: Filters + Stats ─────────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "stretch",
          borderBottom: `1px solid ${T.border}`,
          flexShrink: 0,
          minHeight: FILTER_BAR_HEIGHT,
        }}
      >
        {/* Filters take all available space */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <MapFilters
            onChange={handleFiltersChange}
            onSearchResultClick={handleSearchResultClick}
          />
        </div>

        {/* Stats — right side, hidden on mobile */}
        {!isMobile && (
          <div
            style={{
              width: STATS_WIDTH,
              borderLeft: `1px solid ${T.border}`,
              flexShrink: 0,
              background: T.bg,
              display: "flex",
              alignItems: "center",
            }}
          >
            <MapStats filters={constFilters} filteredTotal={filteredTotal} />
          </div>
        )}
      </div>

      {/* ── Middle: Map + Sidebar ────────────────────────────────────────── */}
      <div
        style={{
          flex: 1,
          position: "relative",
          overflow: "hidden",
          minHeight: 0,
        }}
      >
        {/* WorldMap — full width */}
        <WorldMap
          onBoundsChange={handleBoundsChange}
          mapRef={mapRef}
          height="100%"
        >
          {/* Render clusters or individual channel markers */}
          {mode === "clusters" &&
            clusters.map((cluster, idx) => (
              <ClusterMarker
                key={`cluster-${idx}-${cluster.latitude}-${cluster.longitude}`}
                cluster={cluster}
                onClick={() => {
                  if (mapRef.current) {
                    const currentZoom = mapRef.current.getZoom();
                    mapRef.current.flyTo(
                      [cluster.latitude, cluster.longitude],
                      Math.min(currentZoom + 3, 10),
                      { duration: 0.6 }
                    );
                  }
                }}
              />
            ))}

          {mode === "channels" &&
            channels.map((ch) => (
              <ChannelMarker
                key={`channel-${ch.id}`}
                channel={ch}
                zoom={zoom}
                onClick={() => handleChannelSelectById(ch.id)}
              />
            ))}
        </WorldMap>

        {/* CategoryLegend — absolute overlay bottom-left of map */}
        <CategoryLegend
          activeCategory={filters.category ?? ""}
          onCategoryToggle={handleCategoryToggle}
        />

        {/* Loading indicator overlay */}
        {mapLoading && (
          <div
            style={{
              position: "absolute",
              top: 12,
              left: "50%",
              transform: "translateX(-50%)",
              background: "rgba(10,10,11,0.85)",
              border: `1px solid ${T.border}`,
              borderRadius: 6,
              padding: "5px 14px",
              fontSize: 12,
              color: T.accent,
              zIndex: 950,
              pointerEvents: "none",
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            ⟳ загрузка...
          </div>
        )}

        {/* Sidebar — absolute overlay on the right */}
        {!isMobile && (
          <ChannelSidebar
            channelId={selectedChannelId}
            onClose={handleSidebarClose}
            onSelectSimilar={handleSelectSimilar}
          />
        )}

        {/* Mobile: bottom sheet sidebar */}
        {isMobile && selectedChannelId !== null && (
          <div
            style={{
              position: "absolute",
              bottom: 0,
              left: 0,
              right: 0,
              maxHeight: "60vh",
              overflow: "hidden",
              zIndex: 1000,
              borderRadius: "14px 14px 0 0",
              background: T.bgCard,
              border: `1px solid ${T.border}`,
              boxShadow: "0 -4px 24px rgba(0,0,0,0.6)",
            }}
          >
            <ChannelSidebar
              channelId={selectedChannelId}
              onClose={handleSidebarClose}
              onSelectSimilar={handleSelectSimilar}
            />
          </div>
        )}
      </div>

      {/* ── Bottom: Channel list ─────────────────────────────────────────── */}
      <div
        style={{
          flexShrink: 0,
          borderTop: `1px solid ${T.border}`,
        }}
      >
        <ChannelList
          filters={constFilters}
          onChannelSelect={handleChannelSelect}
          maxHeight={CHANNEL_LIST_MAX_HEIGHT}
        />
      </div>
    </div>
  );
}
