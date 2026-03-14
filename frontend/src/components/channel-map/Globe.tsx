// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map: GlobeView
// Core globe component wrapping react-globe.gl.
// Renders hexagonal cluster bins, individual channel points, and HTML labels
// based on the current displayMode.  Camera flies to globeCenter on change.
// ═══════════════════════════════════════════════════════════════════════════════

import { useRef, useState, useEffect, useMemo, useCallback } from "react";
import ReactGlobe from "react-globe.gl";
import * as THREE from "three";

import type { GeoPoint } from "../../api";
import type { DrillPathEntry } from "./hooks/useGlobeInteraction";
import { getCategoryColor, formatNumber, formatER } from "./constants";

// ── Arc & Ring data types ────────────────────────────────────────────────────

export type ArcData = {
  startLat: number;
  startLng: number;
  endLat: number;
  endLng: number;
  color?: string;
  stroke?: number;
  label?: string;
};

export type RingData = {
  lat: number;
  lng: number;
  color?: string;
  maxRadius?: number;
  propagationSpeed?: number;
  repeatPeriod?: number;
};

// ── Public prop types ─────────────────────────────────────────────────────────

export type GlobeViewProps = {
  geoPoints: GeoPoint[];
  selectedCategory: string | null;
  selectedChannelId: number | null;
  onChannelClick: (point: GeoPoint) => void;
  onHexClick: (hex: { lat: number; lng: number; points: GeoPoint[] }) => void;
  onBackgroundClick: () => void;
  globeCenter: { lat: number; lng: number; altitude: number };
  displayMode: "hex" | "points" | "detailed";
  hudMode: "intel" | "farm" | "analytics";
  isMobile: boolean;
  arcsData?: ArcData[];
  ringsData?: RingData[];
};

// ── Internal hex-bin data shape (react-globe.gl) ──────────────────────────────

type HexBin = {
  points: GeoPoint[];
  sumWeight: number;
  lat: number;
  lng: number;
  center: { lat: number; lng: number };
};

// ── Color helpers ─────────────────────────────────────────────────────────────

/**
 * Interpolates an RGBA color for a hex bin based on its total weight
 * relative to the max density in the current dataset.
 * Low density  → rgba(0,255,136,0.3)
 * High density → rgba(0,255,136,1.0)
 */
function weightedHexColor(hex: HexBin, maxWeight: number): string {
  const ratio = maxWeight > 0 ? Math.min(hex.sumWeight / maxWeight, 1) : 0;
  const alpha = 0.3 + ratio * 0.7;
  return `rgba(0,255,136,${alpha.toFixed(2)})`;
}

// ── Tooltip helpers ───────────────────────────────────────────────────────────

const TOOLTIP_STYLE =
  "background:rgba(10,10,11,0.92);padding:8px 12px;border-radius:8px;" +
  "border:1px solid rgba(0,255,136,0.2);color:#fff;" +
  "font-family:'Geist Sans',sans-serif;font-size:12px;line-height:1.5;" +
  "pointer-events:none;white-space:nowrap;";

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function hexTooltip(hex: HexBin): string {
  const count = hex.points.length;
  const totalMembers = hex.sumWeight;
  return (
    `<div style="${TOOLTIP_STYLE}">` +
    `<b>${count} каналов</b><br/>` +
    `Участников: ${formatNumber(totalMembers)}` +
    `</div>`
  );
}

function pointTooltip(point: GeoPoint): string {
  return (
    `<div style="${TOOLTIP_STYLE}">` +
    `<b>${esc(point.t)}</b><br/>` +
    `@${esc(point.u)}<br/>` +
    `${formatNumber(point.m)} подписчиков` +
    `</div>`
  );
}

/**
 * Creates a DOM element used as an HTML label on the globe.
 * Returned element is owned by react-globe.gl and must not be a React element.
 */
function channelLabel(point: GeoPoint): HTMLElement {
  const el = document.createElement("div");
  el.style.cssText =
    "background:rgba(10,10,11,0.85);border:1px solid rgba(0,255,136,0.25);" +
    "border-radius:4px;padding:2px 6px;color:#fff;" +
    "font-family:'Geist Sans',sans-serif;font-size:10px;" +
    "pointer-events:none;white-space:nowrap;max-width:120px;overflow:hidden;" +
    "text-overflow:ellipsis;";
  el.textContent = point.t;
  return el;
}

// ── Globe material (created once) ─────────────────────────────────────────────

function makeGlobeMaterial(): THREE.MeshPhongMaterial {
  return new THREE.MeshPhongMaterial({
    color: "#1a1a2e",
    emissive: "#0a0a0b",
    shininess: 25,
    transparent: true,
    opacity: 0.95,
  });
}

// ── Component ─────────────────────────────────────────────────────────────────

export function GlobeView(props: GlobeViewProps) {
  const {
    geoPoints,
    selectedCategory,
    selectedChannelId: _selectedChannelId,
    onChannelClick,
    onHexClick,
    onBackgroundClick,
    globeCenter,
    displayMode,
    isMobile,
    arcsData,
    ringsData,
  } = props;

  // Ref to access the react-globe.gl imperative API (pointOfView, etc.)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const globeEl = useRef<any>(null);

  // Container ref + measured dimensions — react-globe.gl defaults to
  // window.innerWidth which breaks raycasting when the globe is in a sidebar layout.
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState<{ w: number; h: number }>({ w: 0, h: 0 });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      setDims({ w: Math.round(width), h: Math.round(height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Stable globe material — never recreated across renders.
  const globeMaterial = useMemo(() => makeGlobeMaterial(), []);

  // ── Filtered points ──────────────────────────────────────────────────────────

  const filteredPoints = useMemo<GeoPoint[]>(() => {
    if (!selectedCategory) return geoPoints;
    return geoPoints.filter((p) => p.cat === selectedCategory);
  }, [geoPoints, selectedCategory]);

  // ── Top-N points for HTML labels (detailed mode) ──────────────────────────

  const topPoints = useMemo<GeoPoint[]>(() => {
    if (isMobile || displayMode !== "detailed") return [];
    return [...filteredPoints]
      .sort((a, b) => b.m - a.m)
      .slice(0, 50);
  }, [filteredPoints, displayMode, isMobile]);

  // ── Max hex weight for color interpolation ────────────────────────────────

  // We expose this through a closure to weightedHexColor; calculate lazily.
  // react-globe.gl constructs hex bins internally, so we track a running max
  // using the per-bin sumWeight values we receive in callbacks.
  const maxHexWeightRef = useRef<number>(1);

  // Memoize bound color functions so react-globe.gl gets stable references.
  const getHexColor = useCallback(
    (obj: object): string => {
      const hex = obj as HexBin;
      if (hex.sumWeight > maxHexWeightRef.current) {
        maxHexWeightRef.current = hex.sumWeight;
      }
      return weightedHexColor(hex, maxHexWeightRef.current);
    },
    [],
  );

  const getHexAltitude = useCallback(
    (obj: object): number => {
      const w = (obj as HexBin).sumWeight;
      // Log scale: gentle rise from 0.01 to 0.12 max — no more "nuclear rods"
      return Math.min(0.12, Math.max(0.01, Math.log10(w + 1) / 60));
    },
    [],
  );

  const getHexLabel = useCallback(
    (obj: object): string => hexTooltip(obj as HexBin),
    [],
  );

  // ── Point accessors ───────────────────────────────────────────────────────

  const getPointRadius = useCallback(
    (obj: object): number => {
      const d = obj as GeoPoint;
      // Log scale for point radius — visible even for 5K channels
      return Math.max(0.15, Math.min(0.6, Math.log10(d.m + 1) / 12));
    },
    [],
  );

  const getPointColor = useCallback(
    (obj: object): string => getCategoryColor((obj as GeoPoint).cat),
    [],
  );

  const getPointLabel = useCallback(
    (obj: object): string => pointTooltip(obj as GeoPoint),
    [],
  );

  const getHtmlElement = useCallback(
    (d: object): HTMLElement => channelLabel(d as GeoPoint),
    [],
  );

  // ── Stable accessors for geo fields (avoids re-triggering scene rebuilds) ──

  const getLat = useCallback((d: object): number => (d as GeoPoint).lat, []);
  const getLng = useCallback((d: object): number => (d as GeoPoint).lng, []);
  const getWeight = useCallback((d: object): number => (d as GeoPoint).m, []);
  const getArcStartLat = useCallback((d: object): number => (d as ArcData).startLat, []);
  const getArcStartLng = useCallback((d: object): number => (d as ArcData).startLng, []);
  const getArcEndLat = useCallback((d: object): number => (d as ArcData).endLat, []);
  const getArcEndLng = useCallback((d: object): number => (d as ArcData).endLng, []);
  const getArcColor = useCallback((d: object): string => (d as ArcData).color ?? "rgba(0,255,136,0.3)", []);
  const getArcStroke = useCallback((d: object): number => (d as ArcData).stroke ?? 0.5, []);
  const getArcLabel = useCallback((d: object): string => {
    const arc = d as ArcData;
    return arc.label ? `<div style="${TOOLTIP_STYLE}">${esc(arc.label)}</div>` : "";
  }, []);
  const getRingLat = useCallback((d: object): number => (d as RingData).lat, []);
  const getRingLng = useCallback((d: object): number => (d as RingData).lng, []);
  const getRingColor = useCallback((d: object) => () => (d as RingData).color ?? "rgba(0,255,136,0.6)", []);
  const getRingMaxRadius = useCallback((d: object): number => (d as RingData).maxRadius ?? 3, []);
  const getRingSpeed = useCallback((d: object): number => (d as RingData).propagationSpeed ?? 2, []);
  const getRingRepeat = useCallback((d: object): number => (d as RingData).repeatPeriod ?? 800, []);

  // ── Click handlers ────────────────────────────────────────────────────────

  const handlePointClick = useCallback(
    (point: object) => {
      onChannelClick(point as GeoPoint);
    },
    [onChannelClick],
  );

  const handleHexClick = useCallback(
    (hex: object) => {
      const h = hex as HexBin;
      onHexClick({ lat: h.center.lat, lng: h.center.lng, points: h.points });
    },
    [onHexClick],
  );

  // ── Camera animation on globeCenter change ────────────────────────────────

  useEffect(() => {
    if (!globeEl.current) return;
    globeEl.current.pointOfView(
      { lat: globeCenter.lat, lng: globeCenter.lng, altitude: globeCenter.altitude },
      1000,
    );
  }, [globeCenter]);

  // ── Hex-bin resolution (reduced on mobile) ────────────────────────────────

  const hexBinResolution = isMobile ? 2 : 3;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div ref={containerRef} style={{ width: "100%", height: "100%" }} onClick={onBackgroundClick}>
      {dims.w > 0 && <ReactGlobe
        ref={globeEl}
        width={dims.w}
        height={dims.h}
        // Globe appearance
        globeImageUrl=""
        globeMaterial={globeMaterial}
        backgroundColor="#0a0a0b"
        atmosphereColor="#00ff88"
        atmosphereAltitude={0.18}
        animateIn={true}
        // ── Hex-bin layer (far/medium zoom) ──────────────────────────────────
        hexBinPointsData={displayMode === "hex" ? filteredPoints : []}
        hexBinPointLat={getLat}
        hexBinPointLng={getLng}
        hexBinPointWeight={getWeight}
        hexBinResolution={hexBinResolution}
        hexBinMerge={true}
        hexAltitude={getHexAltitude}
        hexTopColor={getHexColor}
        hexSideColor={getHexColor}
        hexLabel={getHexLabel}
        onHexClick={handleHexClick}
        // ── Points layer (medium/close zoom) ──────────────────────────────────
        pointsData={displayMode !== "hex" ? filteredPoints : []}
        pointLat={getLat}
        pointLng={getLng}
        pointAltitude={0.01}
        pointRadius={getPointRadius}
        pointColor={getPointColor}
        pointsMerge={displayMode === "points"}
        pointLabel={getPointLabel}
        onPointClick={handlePointClick}
        // ── HTML labels (detailed only, desktop only) ──────────────────────────
        htmlElementsData={topPoints}
        htmlLat={getLat}
        htmlLng={getLng}
        htmlAltitude={0.02}
        htmlElement={getHtmlElement}
        // ── Arcs layer (channel relationships) ──────────────────────────────────
        arcsData={arcsData ?? []}
        arcStartLat={getArcStartLat}
        arcStartLng={getArcStartLng}
        arcEndLat={getArcEndLat}
        arcEndLng={getArcEndLng}
        arcColor={getArcColor}
        arcStroke={getArcStroke}
        arcDashLength={0.4}
        arcDashGap={0.2}
        arcDashAnimateTime={1500}
        arcLabel={getArcLabel}
        // ── Rings layer (pulse on active channels) ──────────────────────────────
        ringsData={ringsData ?? []}
        ringLat={getRingLat}
        ringLng={getRingLng}
        ringColor={getRingColor}
        ringMaxRadius={getRingMaxRadius}
        ringPropagationSpeed={getRingSpeed}
        ringRepeatPeriod={getRingRepeat}
        // ── Mobile perf ────────────────────────────────────────────────────────
        {...(isMobile ? { pointResolution: 8 } : {})}
      />}
    </div>
  );
}
