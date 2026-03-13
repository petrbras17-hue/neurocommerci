// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map Planet "Mission Control"
// Container: hooks → GlobeView + HUD + Search + DetailPanel + Mobile + Fallbacks
// ═══════════════════════════════════════════════════════════════════════════════

import { useState, useCallback, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Search } from "lucide-react";

import type { GeoPoint, ChannelMapEntry } from "../api";
import { channelMapApi } from "../api";
import { useAuth } from "../auth";
import { GlobeView } from "../components/channel-map/Globe";
import type { RingData } from "../components/channel-map/Globe";
import { HudModeSelector } from "../components/channel-map/HudModeSelector";
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
import { useChannelData } from "../components/channel-map/hooks/useChannelData";
import { useGlobeInteraction } from "../components/channel-map/hooks/useGlobeInteraction";
import { useHudMode } from "../components/channel-map/hooks/useHudMode";
import {
  getCategoryMeta,
  formatNumber,
  DESIGN_TOKENS as T,
} from "../components/channel-map/constants";

// ── Glassmorphism card style ─────────────────────────────────────────────────

const glassStyle: React.CSSProperties = {
  background: T.SURFACE,
  backdropFilter: "blur(12px)",
  WebkitBackdropFilter: "blur(12px)",
  border: `1px solid ${T.BORDER}`,
  borderRadius: 12,
  boxShadow: "0 0 20px rgba(0, 255, 136, 0.05)",
};

// ═════════════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═════════════════════════════════════════════════════════════════════════════

export default function ChannelMapPageV2() {
  // ── Hooks ────────────────────────────────────────────────────────────────
  const { accessToken: token } = useAuth();
  const data = useChannelData();
  const globe = useGlobeInteraction();
  const { hudMode, setHudMode, telemetry } = useHudMode();
  const webGLAvailable = useWebGLCheck();
  const [searchOpen, setSearchOpen] = useState(false);

  // Detail panel state
  const [channelDetail, setChannelDetail] = useState<ChannelMapEntry | null>(
    null,
  );
  const [similarChannels, setSimilarChannels] = useState<ChannelMapEntry[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const detailAbortRef = useRef<AbortController | null>(null);

  // Cmd+K shortcut to open search
  useSearchShortcut(useCallback(() => setSearchOpen(true), []));

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
    (point: GeoPoint) => {
      globe.setSelectedChannelId(point.id);
    },
    [globe],
  );

  const handleHexClick = useCallback(
    (hex: { lat: number; lng: number; points: GeoPoint[] }) => {
      const count = hex.points.length;
      const dominantCat = hex.points.reduce(
        (acc, p) => {
          acc[p.cat] = (acc[p.cat] || 0) + 1;
          return acc;
        },
        {} as Record<string, number>,
      );
      const topCat =
        Object.entries(dominantCat).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";
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

  const handleBackgroundClick = useCallback(() => {
    globe.setSelectedChannelId(null);
  }, [globe]);

  const handleClosePanel = useCallback(() => {
    globe.setSelectedChannelId(null);
  }, [globe]);

  // ── Search handlers ────────────────────────────────────────────────────

  const handleSelectChannel = useCallback(
    (ch: ChannelMapEntry) => {
      globe.setSelectedChannelId(ch.id);
      if (ch.lat != null && ch.lng != null) {
        globe.flyTo(ch.lat, ch.lng, 0.8);
      }
    },
    [globe],
  );

  const handleSelectCategory = useCallback(
    (cat: string) => {
      data.setSelectedCategory(cat);
    },
    [data],
  );

  const handleSelectRegion = useCallback(
    (region: { label: string; lat: number; lng: number; altitude: number }) => {
      globe.flyTo(region.lat, region.lng, region.altitude);
    },
    [globe],
  );

  // ── Detail panel action handlers ──────────────────────────────────────

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
      if (ch.lat != null && ch.lng != null) {
        globe.flyTo(ch.lat, ch.lng, 0.8);
      }
    },
    [globe],
  );

  // ── Fallback states ──────────────────────────────────────────────────

  if (!webGLAvailable) {
    return <WebGLFallback channelCount={data.geoPoints.length} />;
  }

  if (!data.loading && !data.error && data.geoPoints.length === 0) {
    return <EmptyGlobe />;
  }

  if (data.error && data.geoPoints.length === 0) {
    return <NetworkError error={data.error} onRetry={data.refetch} />;
  }

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: "100vh",
        overflow: "hidden",
        background: T.BG,
        fontFamily: "'Geist Sans', system-ui, sans-serif",
      }}
    >
      {/* ── HUD Top Bar ──────────────────────────────────────────────── */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          zIndex: 20,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: globe.isMobile ? "8px 12px" : "12px 20px",
          ...glassStyle,
          borderRadius: 0,
          borderTop: "none",
          borderLeft: "none",
          borderRight: "none",
        }}
      >
        <HudModeSelector mode={hudMode} onChange={setHudMode} />

        {/* Stats summary — hidden on mobile */}
        {!globe.isMobile && (
          <div style={{ display: "flex", gap: 20, alignItems: "center" }}>
            {data.stats && (
              <>
                <Stat
                  label="Каналов"
                  value={formatNumber(data.stats.total_channels)}
                />
                <Stat
                  label="Категорий"
                  value={String(Object.keys(data.stats.by_category).length)}
                />
                <Stat
                  label="Языков"
                  value={String(Object.keys(data.stats.by_language).length)}
                />
              </>
            )}
          </div>
        )}

        {/* Search trigger */}
        <button
          onClick={() => setSearchOpen(true)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "7px 14px",
            borderRadius: 8,
            border: `1px solid ${T.BORDER}`,
            background: "rgba(255,255,255,0.05)",
            color: T.TEXT_SECONDARY,
            fontSize: 13,
            cursor: "pointer",
            fontFamily: "inherit",
          }}
        >
          <Search size={14} />
          {globe.isMobile ? "" : "Поиск..."}
          {!globe.isMobile && (
            <kbd
              style={{
                fontSize: 11,
                background: "rgba(255,255,255,0.06)",
                padding: "1px 5px",
                borderRadius: 3,
                border: `1px solid ${T.BORDER}`,
                marginLeft: 4,
              }}
            >
              ⌘K
            </kbd>
          )}
        </button>
      </div>

      {/* ── Telemetry Cards (left side, desktop only) ────────────────── */}
      {!globe.isMobile && (
        <div
          style={{
            position: "absolute",
            top: 70,
            left: 16,
            zIndex: 15,
            display: "flex",
            flexDirection: "column",
            gap: 10,
            maxWidth: 200,
          }}
        >
          {telemetry?.cards.map((card, i) => (
            <TelemetryCard
              key={`${hudMode}-${i}`}
              title={card.title}
              value={card.value}
              accent={card.accent}
              subtitle={card.subtitle}
              trend={card.trend}
            />
          ))}
        </div>
      )}

      {/* ── Breadcrumb ───────────────────────────────────────────────── */}
      <BreadcrumbNav drillPath={globe.drillPath} onReset={globe.drillReset} />

      {/* ── Globe ────────────────────────────────────────────────────── */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: globe.detailPanelOpen && !globe.isMobile ? 380 : 0,
          bottom: globe.isMobile ? "30vh" : 0,
          transition: "right 0.3s ease, bottom 0.3s ease",
        }}
      >
        {data.loading ? (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: T.ACCENT,
              fontSize: 16,
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
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
            hudMode={hudMode}
            isMobile={globe.isMobile}
            ringsData={ringsData}
          />
        )}
      </div>

      {/* ── Desktop: Detail Panel (slide-in right) ───────────────────── */}
      <AnimatePresence>
        {globe.detailPanelOpen && !globe.isMobile && (
          <motion.div
            initial={{ x: 380 }}
            animate={{ x: 0 }}
            exit={{ x: 380 }}
            transition={{ type: "spring", damping: 25, stiffness: 200 }}
            style={{
              position: "absolute",
              top: 0,
              right: 0,
              width: 380,
              height: "100%",
              zIndex: 25,
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

      {/* ── Mobile: Bottom Sheet ─────────────────────────────────────── */}
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

      {/* ── Search Overlay ──────────────────────────────────────────── */}
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

      {/* ── Error overlay (non-fatal, shows when globe still has data) ── */}
      {data.error && data.geoPoints.length > 0 && (
        <div
          style={{
            position: "absolute",
            bottom: globe.isMobile ? "32vh" : 20,
            right: 20,
            zIndex: 30,
            ...glassStyle,
            padding: "10px 16px",
            borderColor: "rgba(239,68,68,0.3)",
            color: "#ef4444",
            fontSize: 13,
            maxWidth: 320,
          }}
        >
          {data.error}
        </div>
      )}
    </div>
  );
}

// ── Small helper component ──────────────────────────────────────────────────

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ textAlign: "center" }}>
      <div
        style={{
          color: T.TEXT_PRIMARY,
          fontSize: 15,
          fontWeight: 600,
          fontFamily: "'JetBrains Mono', monospace",
        }}
      >
        {value}
      </div>
      <div style={{ color: T.TEXT_SECONDARY, fontSize: 11 }}>{label}</div>
    </div>
  );
}
