// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — useGlobeInteraction
// Globe interaction state: zoom levels, drill-down breadcrumbs, selection,
// camera positioning, display mode derivation, and mobile sheet positioning.
// ═══════════════════════════════════════════════════════════════════════════════

import {
  useState,
  useCallback,
  useMemo,
  useEffect,
} from "react";

// ── Types ─────────────────────────────────────────────────────────────────────

export type DrillPathEntry = {
  /** Human-readable label shown in breadcrumb. e.g. "CIS", "Russia", "Crypto" */
  label: string;
  /** Optional emoji/icon for the breadcrumb pill. e.g. "🇷🇺", "₿" */
  icon?: string;
  /** Geographic latitude for the globe camera target */
  lat: number;
  /** Geographic longitude for the globe camera target */
  lng: number;
  /** Camera altitude at this drill level (lower = more zoomed in) */
  altitude: number;
  /** Optional filter context applied at this drill level */
  filter?: {
    category?: string;
    language?: string;
    region?: string;
  };
};

export type DisplayMode = "hex" | "points" | "detailed";

export type BottomSheetPosition = "peek" | "half" | "full";

export type GlobeCenter = {
  lat: number;
  lng: number;
  altitude: number;
};

// ── Constants ─────────────────────────────────────────────────────────────────

const DEFAULT_CENTER: GlobeCenter = { lat: 20, lng: 0, altitude: 2.5 };

const DEFAULT_ZOOM_LEVEL = 0;

const MOBILE_BREAKPOINT = 768;

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Derive a zoom level (0–6) from a camera altitude.
 * Lower altitude = higher zoom level.
 * Altitude thresholds are tuned to match the three display-mode bands:
 *   altitude > 2.0  → zoom 0–1  (hex / continent scale)
 *   altitude 1.0–2.0 → zoom 2–3 (points / country scale)
 *   altitude < 1.0  → zoom 4–6  (detailed / city/channel scale)
 */
function altitudeToZoomLevel(altitude: number): number {
  if (altitude >= 2.5) return 0;
  if (altitude >= 2.0) return 1;
  if (altitude >= 1.5) return 2;
  if (altitude >= 1.0) return 3;
  if (altitude >= 0.5) return 4;
  if (altitude >= 0.2) return 5;
  return 6;
}

/**
 * Derive the rendering display mode from the zoom level.
 *   0–2  → 'hex'      (hexagonal cluster bins, continent/planet view)
 *   2–4  → 'points'   (individual dots, country/region view)
 *   4+   → 'detailed' (rich channel cards, city/topic view)
 */
function zoomToDisplayMode(zoom: number): DisplayMode {
  if (zoom <= 2) return "hex";
  if (zoom <= 4) return "points";
  return "detailed";
}

// ── Return type ───────────────────────────────────────────────────────────────

export type UseGlobeInteractionReturn = {
  // Zoom & drill-down
  zoomLevel: number;
  setZoomLevel: (level: number) => void;
  drillPath: DrillPathEntry[];
  drillDown: (entry: DrillPathEntry) => void;
  drillUp: () => void;
  drillReset: () => void;

  // Selection
  selectedChannelId: number | null;
  setSelectedChannelId: (id: number | null) => void;
  detailPanelOpen: boolean;

  // Globe camera
  globeCenter: GlobeCenter;
  flyTo: (lat: number, lng: number, altitude?: number) => void;

  // Display mode derived from zoomLevel
  displayMode: DisplayMode;

  // Mobile
  isMobile: boolean;
  bottomSheetPosition: BottomSheetPosition;
  setBottomSheetPosition: (pos: BottomSheetPosition) => void;
};

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useGlobeInteraction(): UseGlobeInteractionReturn {
  // --- Zoom level (0–6) -------------------------------------------------------
  const [zoomLevel, setZoomLevel] = useState<number>(DEFAULT_ZOOM_LEVEL);

  // --- Drill path (breadcrumb stack) ------------------------------------------
  const [drillPath, setDrillPath] = useState<DrillPathEntry[]>([]);

  // --- Globe camera position --------------------------------------------------
  const [globeCenter, setGlobeCenter] = useState<GlobeCenter>(DEFAULT_CENTER);

  // --- Selection state --------------------------------------------------------
  const [selectedChannelId, setSelectedChannelIdRaw] = useState<number | null>(null);

  // --- Mobile detection -------------------------------------------------------
  const [isMobile, setIsMobile] = useState<boolean>(
    typeof window !== "undefined"
      ? window.innerWidth < MOBILE_BREAKPOINT
      : false
  );

  // --- Bottom sheet (mobile only) --------------------------------------------
  const [bottomSheetPosition, setBottomSheetPosition] =
    useState<BottomSheetPosition>("peek");

  // ── Effects ──────────────────────────────────────────────────────────────────

  // Track mobile breakpoint on window resize
  useEffect(() => {
    if (typeof window === "undefined") return;

    const handleResize = () => {
      setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
    };

    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
    };
  }, []);

  // ── Derived state ────────────────────────────────────────────────────────────

  // detailPanelOpen is true whenever a channel is selected
  const detailPanelOpen = selectedChannelId !== null;

  // displayMode is derived purely from zoomLevel — no extra state needed
  const displayMode = useMemo<DisplayMode>(
    () => zoomToDisplayMode(zoomLevel),
    [zoomLevel]
  );

  // ── Callbacks ────────────────────────────────────────────────────────────────

  /**
   * flyTo — move the globe camera to the given coordinates.
   * Uses the supplied altitude or falls back to the current globeCenter altitude.
   */
  const flyTo = useCallback(
    (lat: number, lng: number, altitude?: number) => {
      setGlobeCenter((prev) => ({
        lat,
        lng,
        altitude: altitude !== undefined ? altitude : prev.altitude,
      }));
    },
    []
  );

  /**
   * drillDown — push a new entry onto the breadcrumb path.
   * Moves the camera to the entry's position and updates zoom accordingly.
   */
  const drillDown = useCallback(
    (entry: DrillPathEntry) => {
      setDrillPath((prev) => [...prev, entry]);
      setGlobeCenter({ lat: entry.lat, lng: entry.lng, altitude: entry.altitude });
      setZoomLevel(altitudeToZoomLevel(entry.altitude));
    },
    []
  );

  /**
   * drillUp — pop the last entry from the breadcrumb path.
   * Flies back to the previous entry's position, or to the default root if the
   * path becomes empty.
   */
  const drillUp = useCallback(() => {
    setDrillPath((prev) => {
      if (prev.length === 0) return prev;

      const next = prev.slice(0, -1);

      if (next.length > 0) {
        const parent = next[next.length - 1];
        setGlobeCenter({ lat: parent.lat, lng: parent.lng, altitude: parent.altitude });
        setZoomLevel(altitudeToZoomLevel(parent.altitude));
      } else {
        // Back to planet root
        setGlobeCenter(DEFAULT_CENTER);
        setZoomLevel(DEFAULT_ZOOM_LEVEL);
      }

      return next;
    });
  }, []);

  /**
   * drillReset — clear the entire breadcrumb path and fly back to the planet
   * root view.
   */
  const drillReset = useCallback(() => {
    setDrillPath([]);
    setGlobeCenter(DEFAULT_CENTER);
    setZoomLevel(DEFAULT_ZOOM_LEVEL);
  }, []);

  /**
   * setSelectedChannelId — wraps the raw setter so that:
   *  - setting a non-null id opens the detail panel
   *  - setting null closes the detail panel (detailPanelOpen becomes false)
   */
  const setSelectedChannelId = useCallback((id: number | null) => {
    setSelectedChannelIdRaw(id);
  }, []);

  // ── Return ───────────────────────────────────────────────────────────────────

  return {
    // Zoom & drill-down
    zoomLevel,
    setZoomLevel,
    drillPath,
    drillDown,
    drillUp,
    drillReset,

    // Selection
    selectedChannelId,
    setSelectedChannelId,
    detailPanelOpen,

    // Globe camera
    globeCenter,
    flyTo,

    // Display mode
    displayMode,

    // Mobile
    isMobile,
    bottomSheetPosition,
    setBottomSheetPosition,
  };
}
