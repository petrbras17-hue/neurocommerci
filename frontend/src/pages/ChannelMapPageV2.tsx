// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map v3 "Marketing Intelligence"
// 3-panel layout: Left Panel + Globe + Right Panel, 3 modes
// ═══════════════════════════════════════════════════════════════════════════════

import { useState, useCallback, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Search } from "lucide-react";

import type { GeoPoint, ChannelMapEntry } from "../api";
import { channelMapApi } from "../api";
import { useAuth } from "../auth";
import { GlobeView } from "../components/channel-map/Globe";
import type { RingData } from "../components/channel-map/Globe";
import ModeTabBar from "../components/channel-map/ModeTabBar";
import { TelemetryCard } from "../components/channel-map/TelemetryCard";
import { BreadcrumbNav } from "../components/channel-map/BreadcrumbNav";
import { ChannelDetailPanel } from "../components/channel-map/ChannelDetailPanel";
import { MobileBottomSheet } from "../components/channel-map/MobileBottomSheet";
import {
  useWebGLCheck,
  WebGLFallback,
  EmptyGlobe,
  NetworkError,
} from "../components/channel-map/FallbackStates";
import {
  SearchOverlay,
  useSearchShortcut,
} from "../components/channel-map/SearchOverlay";
import { DiscoveryPanel } from "../components/channel-map/discovery/DiscoveryPanel";
import { useChannelData } from "../components/channel-map/hooks/useChannelData";
import { useGlobeInteraction } from "../components/channel-map/hooks/useGlobeInteraction";
import { useMapMode } from "../components/channel-map/hooks/useMapMode";
import { useHudMode } from "../components/channel-map/hooks/useHudMode";
import { useClusters, type ClusterPoint } from "../components/channel-map/hooks/useClusters";
import {
  getCategoryMeta,
  formatNumber,
  DESIGN_TOKENS as T,
} from "../components/channel-map/constants";

// ═════════════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═════════════════════════════════════════════════════════════════════════════

export default function ChannelMapPageV2() {
  const { accessToken: token } = useAuth();
  const data = useChannelData();
  const globe = useGlobeInteraction();
  const { mode, switchMode } = useMapMode();
  const { telemetry } = useHudMode(mode);
  const { clusters, fetchClusters } = useClusters();
  const webGLAvailable = useWebGLCheck();
  const [searchOpen, setSearchOpen] = useState(false);
  const [leftPanelOpen, setLeftPanelOpen] = useState(true);

  // Detail panel state
  const [channelDetail, setChannelDetail] = useState<ChannelMapEntry | null>(null);
  const [similarChannels, setSimilarChannels] = useState<ChannelMapEntry[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const detailAbortRef = useRef<AbortController | null>(null);

  useSearchShortcut(useCallback(() => setSearchOpen(true), []));

  // ── Fetch clusters when zoom or category changes ───────────────────────
  useEffect(() => {
    if (globe.displayMode === "hex") {
      fetchClusters(globe.zoomLevel, data.selectedCategory);
    }
  }, [globe.displayMode, globe.zoomLevel, data.selectedCategory, fetchClusters]);

  const handleClusterClick = useCallback(
    (cluster: ClusterPoint) => {
      // Zoom into the cluster location
      const nextAltitude = globe.zoomLevel <= 1 ? 1.5 : 0.8;
      globe.drillDown({
        label: `${cluster.count} каналов`,
        icon: getCategoryMeta(cluster.dominant_category).icon,
        lat: cluster.lat,
        lng: cluster.lng,
        altitude: nextAltitude,
        filter: cluster.dominant_category ? { category: cluster.dominant_category } : undefined,
      });
    },
    [globe],
  );

  // ── Fetch channel detail + similar when selection changes ──────────────
  useEffect(() => {
    detailAbortRef.current?.abort();
    if (!globe.selectedChannelId || !token) {
      setChannelDetail(null);
      setSimilarChannels([]);
      return;
    }

    const controller = new AbortController();
    detailAbortRef.current = controller;
    setDetailLoading(true);

    Promise.all([
      channelMapApi.detail(token, globe.selectedChannelId),
      channelMapApi.similar(token, globe.selectedChannelId, 5),
    ])
      .then(([detail, similar]) => {
        if (controller.signal.aborted) return;
        setChannelDetail(detail);
        setSimilarChannels(similar.items);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setChannelDetail(null);
        setSimilarChannels([]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });

    return () => controller.abort();
  }, [globe.selectedChannelId, token]);

  // ── Rings: pulse on selected channel ──────────────────────────────────
  const ringsData: RingData[] =
    channelDetail?.lat != null && channelDetail?.lng != null
      ? [{ lat: channelDetail.lat, lng: channelDetail.lng }]
      : [];

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleChannelClick = useCallback(
    (point: GeoPoint) => { globe.setSelectedChannelId(point.id); },
    [globe],
  );

  const handleHexClick = useCallback(
    (hex: { lat: number; lng: number; points: GeoPoint[] }) => {
      const count = hex.points.length;
      const dominantCat = hex.points.reduce(
        (acc, p) => { acc[p.cat] = (acc[p.cat] || 0) + 1; return acc; },
        {} as Record<string, number>,
      );
      const topCat = Object.entries(dominantCat).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";
      const meta = getCategoryMeta(topCat);
      globe.drillDown({
        label: `${count} каналов`,
        icon: meta.icon,
        lat: hex.lat,
        lng: hex.lng,
        altitude: 1.2,
        filter: topCat ? { category: topCat } : undefined,
      });
    },
    [globe],
  );

  const handleBackgroundClick = useCallback(() => { globe.setSelectedChannelId(null); }, [globe]);
  const handleClosePanel = useCallback(() => { globe.setSelectedChannelId(null); }, [globe]);

  const handleSelectChannel = useCallback(
    (ch: ChannelMapEntry) => {
      globe.setSelectedChannelId(ch.id);
      if (ch.lat != null && ch.lng != null) globe.flyTo(ch.lat, ch.lng, 0.8);
    },
    [globe],
  );

  const handleSelectCategory = useCallback(
    (cat: string) => { data.setSelectedCategory(cat); },
    [data],
  );

  const handleSelectRegion = useCallback(
    (region: { label: string; lat: number; lng: number; altitude: number }) => {
      globe.flyTo(region.lat, region.lng, region.altitude);
    },
    [globe],
  );

  const handleBulkAction = useCallback(
    (action: string) => (channelId: number) => {
      if (!token) return;
      channelMapApi.bulkAction(token, [channelId], action).catch(() => {});
    },
    [token],
  );

  const handleSelectSimilar = useCallback(
    (ch: ChannelMapEntry) => {
      globe.setSelectedChannelId(ch.id);
      if (ch.lat != null && ch.lng != null) globe.flyTo(ch.lat, ch.lng, 0.8);
    },
    [globe],
  );

  // ── Category filter from left panel ──────────────────────────────────
  const handleCategoryFilter = useCallback(
    (cat: string | null) => {
      data.setSelectedCategory(cat ?? '');
    },
    [data],
  );

  const handleChannelSelect = useCallback(
    (id: number, lat?: number, lng?: number) => {
      globe.setSelectedChannelId(id);
      if (lat != null && lng != null) globe.flyTo(lat, lng, 0.8);
    },
    [globe],
  );

  // ── Fallback states ──────────────────────────────────────────────────
  if (!webGLAvailable) return <WebGLFallback channelCount={data.geoPoints.length} />;
  if (!data.loading && !data.error && data.geoPoints.length === 0) return <EmptyGlobe />;
  if (data.error && data.geoPoints.length === 0) return <NetworkError error={data.error} onRetry={data.refetch} />;

  const showRightPanel = globe.detailPanelOpen && !globe.isMobile;
  const showLeftPanel = leftPanelOpen && !globe.isMobile;

  // ═════════════════════════════════════════════════════════════════════════════
  // RENDER — 3-panel layout
  // ═════════════════════════════════════════════════════════════════════════════

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      width: '100%',
      height: '100vh',
      overflow: 'hidden',
      background: T.BG,
      fontFamily: "'Geist Sans', system-ui, sans-serif",
    }}>
      {/* ═══ HEADER (56px) ═══════════════════════════════════════════════════ */}
      <header style={{
        display: 'flex',
        alignItems: 'center',
        height: 56,
        padding: '0 20px',
        background: T.SURFACE,
        borderBottom: `1px solid ${T.BORDER_SUBTLE}`,
        gap: 16,
        zIndex: 30,
        flexShrink: 0,
      }}>
        {/* Logo */}
        <span style={{
          color: T.ACCENT,
          fontSize: 13,
          fontWeight: 700,
          letterSpacing: 1.5,
          whiteSpace: 'nowrap',
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          CHANNEL MAP
        </span>

        {/* Separator */}
        <div style={{ width: 1, height: 24, background: T.BORDER_SUBTLE }} />

        {/* Mode tabs */}
        <ModeTabBar mode={mode} onSwitch={switchMode} />

        {/* Spacer */}
        <div style={{ flex: 1 }} />

        {/* Stats summary */}
        {!globe.isMobile && data.stats && (
          <div style={{ display: 'flex', gap: 20, alignItems: 'center' }}>
            <Stat label="Каналов" value={formatNumber(data.stats.total_channels)} />
            <Stat label="Категорий" value={String(Object.keys(data.stats.by_category).length)} />
          </div>
        )}

        {/* Search button */}
        <button
          onClick={() => setSearchOpen(true)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '7px 14px',
            borderRadius: 8,
            border: `1px solid ${T.BORDER_SUBTLE}`,
            background: T.SURFACE_ELEVATED,
            color: T.TEXT_MUTED,
            fontSize: 13,
            cursor: 'pointer',
            fontFamily: 'inherit',
          }}
        >
          <Search size={14} />
          {globe.isMobile ? "" : "Поиск..."}
          {!globe.isMobile && (
            <kbd style={{
              fontSize: 11,
              background: 'rgba(255,255,255,0.06)',
              padding: '1px 5px',
              borderRadius: 3,
              border: `1px solid ${T.BORDER_SUBTLE}`,
              marginLeft: 4,
            }}>
              ⌘K
            </kbd>
          )}
        </button>
      </header>

      {/* ═══ BODY (flex row) ═══════════════════════════════════════════════════ */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* ── LEFT PANEL (320px) ────────────────────────────────────────────── */}
        {showLeftPanel && (
          <div style={{
            width: 320,
            flexShrink: 0,
            background: T.SURFACE,
            borderRight: `1px solid ${T.BORDER_SUBTLE}`,
            overflowY: 'auto',
            overflowX: 'hidden',
          }}>
            {mode === 'discovery' && (
              <DiscoveryPanel
                categories={data.categories ?? []}
                stats={data.stats}
                selectedCategory={data.selectedCategory}
                onCategoryFilter={handleCategoryFilter}
                onChannelSelect={handleChannelSelect}
                geoPoints={data.geoPoints}
              />
            )}
            {mode === 'farm' && (
              <PlaceholderPanel icon="🌾" title="Farm Control" subtitle="Coming in Sprint 2" />
            )}
            {mode === 'intelligence' && (
              <PlaceholderPanel icon="📊" title="Intelligence" subtitle="Coming in Sprint 3" />
            )}
          </div>
        )}

        {/* ── GLOBE ZONE (flex) ─────────────────────────────────────────────── */}
        <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
          {/* Telemetry cards overlay */}
          {!globe.isMobile && telemetry?.cards && (
            <div style={{
              position: 'absolute',
              top: 12,
              left: 12,
              zIndex: 15,
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              maxWidth: 180,
            }}>
              {telemetry.cards.map((card, i) => (
                <TelemetryCard
                  key={`${mode}-${i}`}
                  title={card.title}
                  value={card.value}
                  accent={card.accent}
                  subtitle={card.subtitle}
                  trend={card.trend}
                />
              ))}
            </div>
          )}

          {/* Breadcrumb */}
          <BreadcrumbNav drillPath={globe.drillPath} onReset={globe.drillReset} />

          {/* Globe */}
          {data.loading ? (
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              color: T.ACCENT,
              fontSize: 16,
              fontFamily: "'JetBrains Mono', monospace",
            }}>
              Загрузка карты каналов...
            </div>
          ) : (
            <GlobeView
              geoPoints={data.geoPoints}
              selectedCategory={data.selectedCategory}
              selectedChannelId={globe.selectedChannelId}
              onChannelClick={handleChannelClick}
              onHexClick={handleHexClick}
              onBackgroundClick={handleBackgroundClick}
              globeCenter={globe.globeCenter}
              displayMode={globe.displayMode}
              hudMode={mode}
              isMobile={globe.isMobile}
              ringsData={ringsData}
              clusterData={globe.displayMode === "hex" ? clusters : undefined}
              onClusterClick={handleClusterClick}
            />
          )}
        </div>

        {/* ── RIGHT PANEL (380px, slide-in) ─────────────────────────────────── */}
        <AnimatePresence>
          {showRightPanel && (
            <motion.div
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: 380, opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ type: "spring", damping: 25, stiffness: 200 }}
              style={{
                flexShrink: 0,
                overflow: 'hidden',
                borderLeft: `1px solid ${T.BORDER_SUBTLE}`,
              }}
            >
              <ChannelDetailPanel
                channel={channelDetail}
                similarChannels={similarChannels}
                loading={detailLoading}
                onClose={handleClosePanel}
                onAddToFarm={handleBulkAction("add_to_farm")}
                onBlacklist={handleBulkAction("blacklist")}
                onAddToDb={handleBulkAction("add_to_db")}
                onTrack={handleBulkAction("track")}
                onSelectSimilar={handleSelectSimilar}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* ── Mobile: Bottom Sheet ─────────────────────────────────────────── */}
      {globe.isMobile && (
        <MobileBottomSheet
          position={globe.bottomSheetPosition}
          onPositionChange={globe.setBottomSheetPosition}
          selectedChannel={channelDetail}
          similarChannels={similarChannels}
          detailLoading={detailLoading}
          geoPoints={data.geoPoints}
          onSelectChannel={handleChannelClick}
          onCloseDetail={handleClosePanel}
          onAddToFarm={handleBulkAction("add_to_farm")}
          onBlacklist={handleBulkAction("blacklist")}
        />
      )}

      {/* ── Search Overlay ────────────────────────────────────────────────── */}
      <SearchOverlay
        isOpen={searchOpen}
        onClose={() => setSearchOpen(false)}
        onSelectChannel={handleSelectChannel}
        onSelectCategory={handleSelectCategory}
        onSelectRegion={handleSelectRegion}
        searchQuery={data.searchQuery}
        onSearchChange={data.setSearchQuery}
        searchResults={data.searchResults ?? []}
        categories={data.categories ?? []}
      />

      {/* ── Error overlay (non-fatal) ─────────────────────────────────────── */}
      {data.error && data.geoPoints.length > 0 && (
        <div style={{
          position: 'absolute',
          bottom: 20,
          right: 20,
          zIndex: 30,
          background: T.SURFACE,
          border: '1px solid rgba(239,68,68,0.3)',
          borderRadius: 8,
          padding: '10px 16px',
          color: '#ef4444',
          fontSize: 13,
          maxWidth: 320,
        }}>
          {data.error}
        </div>
      )}
    </div>
  );
}

// ── Helper components ─────────────────────────────────────────────────────────

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{
        color: T.TEXT_PRIMARY,
        fontSize: 15,
        fontWeight: 600,
        fontFamily: "'JetBrains Mono', monospace",
      }}>
        {value}
      </div>
      <div style={{ color: T.TEXT_MUTED, fontSize: 11 }}>{label}</div>
    </div>
  );
}

function PlaceholderPanel({ icon, title, subtitle }: { icon: string; title: string; subtitle: string }) {
  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      height: '100%',
      padding: 32,
      gap: 12,
    }}>
      <span style={{ fontSize: 48 }}>{icon}</span>
      <span style={{ color: T.TEXT_PRIMARY, fontSize: 16, fontWeight: 600 }}>{title}</span>
      <span style={{ color: T.TEXT_MUTED, fontSize: 13 }}>{subtitle}</span>
    </div>
  );
}
