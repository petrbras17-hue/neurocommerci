import {
  useEffect,
  useState,
  useMemo,
  useCallback,
  useRef,
  Suspense,
  Component,
} from "react";
import type { ReactNode } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls, Html, Text } from "@react-three/drei";
import * as THREE from "three";
import {
  channelMapApi,
  ChannelMapEntry,
  ChannelMapStats,
  farmApi,
  FarmConfig,
  channelDbApi,
  ChannelDatabase,
  campaignsApi,
  Campaign,
} from "../api";
import { useAuth } from "../auth";
import { motion, AnimatePresence } from "framer-motion";
import {
  Search,
  Filter,
  Globe,
  Users,
  MessageCircle,
  TrendingUp,
  ExternalLink,
  X,
  RotateCcw,
  Tractor,
  Megaphone,
  Eye,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Building2,
  MapPin,
} from "lucide-react";

// ── helpers ──────────────────────────────────────────────────────────────────

function formatNumber(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function erColor(rate: number | null): string {
  if (rate == null) return "var(--muted)";
  if (rate >= 0.05) return "var(--accent)";
  if (rate >= 0.02) return "var(--warning)";
  return "var(--danger)";
}

function erLabel(rate: number | null): string {
  if (rate == null) return "—";
  return `${(rate * 100).toFixed(2)}%`;
}

function langFlag(lang: string | null): string {
  const map: Record<string, string> = {
    ru: "🇷🇺", en: "🇺🇸", uk: "🇺🇦", kz: "🇰🇿",
    de: "🇩🇪", fr: "🇫🇷", es: "🇪🇸", zh: "🇨🇳", ar: "🇸🇦",
  };
  if (!lang) return "🌐";
  return map[lang.toLowerCase()] ?? "🌐";
}

// ── WebGL detection ──────────────────────────────────────────────────────────

function isWebGLAvailable(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return !!(
      window.WebGLRenderingContext &&
      (canvas.getContext("webgl") || canvas.getContext("experimental-webgl"))
    );
  } catch {
    return false;
  }
}

class WebGLErrorBoundary extends Component<
  { fallback: ReactNode; children: ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false };
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  componentDidCatch(error: Error) {
    console.warn("WebGL/Three.js error:", error.message);
  }
  render() {
    return this.state.hasError ? this.props.fallback : this.props.children;
  }
}

// ── category config ──────────────────────────────────────────────────────────

const CATEGORY_META: Record<string, { icon: string; color: string }> = {
  Crypto: { icon: "₿", color: "#ffaa00" },
  Marketing: { icon: "📢", color: "#4488ff" },
  "E-commerce": { icon: "🛒", color: "#00ff88" },
  EdTech: { icon: "🎓", color: "#44aaff" },
  News: { icon: "📰", color: "#888888" },
  Entertainment: { icon: "🎬", color: "#ec4899" },
  Tech: { icon: "💻", color: "#8844ff" },
  Finance: { icon: "📉", color: "#44ddff" },
  Lifestyle: { icon: "✨", color: "#f97316" },
  Health: { icon: "🏥", color: "#44ffaa" },
  Gaming: { icon: "🎮", color: "#ff44aa" },
  "18+": { icon: "🔞", color: "#ff4444" },
  Politics: { icon: "🏛️", color: "#ff4488" },
  Sports: { icon: "⚽", color: "#ffdd44" },
  Travel: { icon: "✈️", color: "#44ffdd" },
  Business: { icon: "💼", color: "#88ff44" },
  Science: { icon: "🔬", color: "#aa44ff" },
  Music: { icon: "🎵", color: "#ff88aa" },
  Food: { icon: "🍴", color: "#88ffaa" },
  "AI/ML": { icon: "🤖", color: "#ff00ff" },
  Cybersecurity: { icon: "🔒", color: "#00ffff" },
};

const DEFAULT_CATEGORY_META = { icon: "📌", color: "#4488ff" };

function getCategoryMeta(cat: string | null | undefined): { icon: string; color: string } {
  if (!cat) return DEFAULT_CATEGORY_META;
  return CATEGORY_META[cat] ?? DEFAULT_CATEGORY_META;
}

function getCategoryColor(cat: string | null | undefined): string {
  if (!cat) return "#4488ff";
  return CATEGORY_META[cat]?.color ?? "#4488ff";
}

// ── city layout algorithm ────────────────────────────────────────────────────

type BuildingData = {
  ch: ChannelMapEntry;
  x: number;
  z: number;
  width: number;
  depth: number;
  height: number;
  color: string;
  category: string;
  emissive: number; // 0-1 based on engagement
};

type DistrictData = {
  category: string;
  color: string;
  icon: string;
  cx: number;
  cz: number;
  halfW: number;
  halfD: number;
  count: number;
  totalReach: number;
};

type CityLayout = {
  buildings: BuildingData[];
  districts: DistrictData[];
  bounds: { minX: number; maxX: number; minZ: number; maxZ: number };
};

const BUILDING_SIZE = 6;
const BUILDING_GAP = 3;
const DISTRICT_GAP = 24;
const STREET_WIDTH = 8;
const MIN_HEIGHT = 2;
const MAX_HEIGHT = 120;

function computeCityLayout(channels: ChannelMapEntry[]): CityLayout {
  if (channels.length === 0) {
    return { buildings: [], districts: [], bounds: { minX: 0, maxX: 0, minZ: 0, maxZ: 0 } };
  }

  // Group by category
  const groups = new Map<string, ChannelMapEntry[]>();
  for (const ch of channels) {
    const cat = ch.category ?? "Другое";
    if (!groups.has(cat)) groups.set(cat, []);
    groups.get(cat)!.push(ch);
  }

  // Sort categories by total member count (biggest district = center)
  const sortedCats = [...groups.entries()]
    .map(([cat, chs]) => ({
      cat,
      chs,
      totalMembers: chs.reduce((s, c) => s + (c.member_count ?? 0), 0),
    }))
    .sort((a, b) => b.totalMembers - a.totalMembers);

  // Find max member count for height normalization
  const maxMembers = Math.max(
    ...channels.map((c) => c.member_count ?? 0),
    1
  );

  // Arrange districts in a spiral pattern from center
  const spiralPositions = generateSpiralGrid(sortedCats.length);

  const buildings: BuildingData[] = [];
  const districts: DistrictData[] = [];

  let globalMinX = Infinity, globalMaxX = -Infinity;
  let globalMinZ = Infinity, globalMaxZ = -Infinity;

  for (let di = 0; di < sortedCats.length; di++) {
    const { cat, chs } = sortedCats[di];
    const meta = getCategoryMeta(cat);

    // Sort buildings within district: tallest in center
    const sorted = [...chs].sort(
      (a, b) => (b.member_count ?? 0) - (a.member_count ?? 0)
    );

    // Compute grid dimensions for this district
    const cols = Math.max(1, Math.ceil(Math.sqrt(sorted.length)));
    const rows = Math.max(1, Math.ceil(sorted.length / cols));

    const districtW = cols * (BUILDING_SIZE + BUILDING_GAP) - BUILDING_GAP + STREET_WIDTH * 2;
    const districtD = rows * (BUILDING_SIZE + BUILDING_GAP) - BUILDING_GAP + STREET_WIDTH * 2;

    // Position district using spiral
    const { gx, gz } = spiralPositions[di];
    const districtCenterX = gx * (80 + DISTRICT_GAP);
    const districtCenterZ = gz * (80 + DISTRICT_GAP);

    // Arrange buildings in spiral within district (tallest at center)
    const innerSpiral = generateSpiralGrid(sorted.length);

    for (let bi = 0; bi < sorted.length; bi++) {
      const ch = sorted[bi];
      const members = ch.member_count ?? 0;

      // Height: logarithmic scale
      const heightNorm = Math.log1p(members) / Math.log1p(maxMembers);
      const height = MIN_HEIGHT + heightNorm * (MAX_HEIGHT - MIN_HEIGHT);

      // Width/depth: slight variation for visual interest
      const sizeVariation = 0.8 + (((ch.id * 2654435761) >>> 0) % 100) / 250;
      const bw = BUILDING_SIZE * sizeVariation;
      const bd = BUILDING_SIZE * sizeVariation;

      const { gx: bx, gz: bz } = innerSpiral[bi];
      const x = districtCenterX + bx * (BUILDING_SIZE + BUILDING_GAP);
      const z = districtCenterZ + bz * (BUILDING_SIZE + BUILDING_GAP);

      // Engagement → emissive glow intensity
      const er = ch.engagement_rate ?? 0;
      const emissive = Math.min(er / 0.08, 1);

      buildings.push({
        ch,
        x,
        z,
        width: bw,
        depth: bd,
        height,
        color: meta.color,
        category: cat,
        emissive,
      });

      globalMinX = Math.min(globalMinX, x - bw / 2);
      globalMaxX = Math.max(globalMaxX, x + bw / 2);
      globalMinZ = Math.min(globalMinZ, z - bd / 2);
      globalMaxZ = Math.max(globalMaxZ, z + bd / 2);
    }

    districts.push({
      category: cat,
      color: meta.color,
      icon: meta.icon,
      cx: districtCenterX,
      cz: districtCenterZ,
      halfW: districtW / 2,
      halfD: districtD / 2,
      count: chs.length,
      totalReach: sortedCats[di].totalMembers,
    });
  }

  return {
    buildings,
    districts,
    bounds: {
      minX: globalMinX - 20,
      maxX: globalMaxX + 20,
      minZ: globalMinZ - 20,
      maxZ: globalMaxZ + 20,
    },
  };
}

function generateSpiralGrid(count: number): Array<{ gx: number; gz: number }> {
  const result: Array<{ gx: number; gz: number }> = [];
  if (count === 0) return result;

  result.push({ gx: 0, gz: 0 });
  let x = 0, z = 0;
  let dx = 1, dz = 0;
  let stepsInDir = 1, stepsTaken = 0, turnsInSpiral = 0;

  for (let i = 1; i < count; i++) {
    x += dx;
    z += dz;
    result.push({ gx: x, gz: z });
    stepsTaken++;

    if (stepsTaken >= stepsInDir) {
      stepsTaken = 0;
      // Turn 90 degrees
      const tmp = dx;
      dx = -dz;
      dz = tmp;
      turnsInSpiral++;
      if (turnsInSpiral % 2 === 0) stepsInDir++;
    }
  }
  return result;
}

// ── 3D scene components ──────────────────────────────────────────────────────

function CityGround({ bounds }: { bounds: CityLayout["bounds"] }) {
  const w = Math.max(bounds.maxX - bounds.minX, 200) + 100;
  const d = Math.max(bounds.maxZ - bounds.minZ, 200) + 100;
  const cx = (bounds.minX + bounds.maxX) / 2;
  const cz = (bounds.minZ + bounds.maxZ) / 2;
  const gridSize = Math.max(w, d);

  return (
    <group>
      {/* Main ground */}
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[cx, -0.1, cz]}
        receiveShadow
      >
        <planeGeometry args={[w * 1.5, d * 1.5]} />
        <meshStandardMaterial
          color="#080a0c"
          metalness={0.2}
          roughness={0.9}
        />
      </mesh>

      {/* Grid lines */}
      <gridHelper
        args={[gridSize, Math.floor(gridSize / 20), "#0d1a14", "#0a120e"]}
        position={[cx, 0, cz]}
      />

      {/* Subtle ground fog plane */}
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[cx, 0.05, cz]}
      >
        <planeGeometry args={[w * 2, d * 2]} />
        <meshBasicMaterial
          color="#00ff88"
          transparent
          opacity={0.015}
        />
      </mesh>
    </group>
  );
}

// ── District floor plates ────────────────────────────────────────────────────

function DistrictPlates({ districts }: { districts: DistrictData[] }) {
  return (
    <group>
      {districts.map((d) => (
        <group key={d.category}>
          {/* District base plate */}
          <mesh
            rotation={[-Math.PI / 2, 0, 0]}
            position={[d.cx, 0.02, d.cz]}
            receiveShadow
          >
            <planeGeometry args={[d.halfW * 2 + 10, d.halfD * 2 + 10]} />
            <meshStandardMaterial
              color={d.color}
              transparent
              opacity={0.06}
              metalness={0.5}
              roughness={0.8}
            />
          </mesh>

          {/* District border */}
          <lineSegments position={[d.cx, 0.1, d.cz]}>
            <edgesGeometry
              args={[new THREE.PlaneGeometry(d.halfW * 2 + 10, d.halfD * 2 + 10)]}
            />
            <lineBasicMaterial color={d.color} transparent opacity={0.25} />
          </lineSegments>

          {/* District label */}
          <Text
            position={[d.cx, 1, d.cz - d.halfD - 8]}
            fontSize={5}
            color={d.color}
            anchorX="center"
            anchorY="middle"
            font="/fonts/JetBrainsMono-Regular.woff"
            fillOpacity={0.7}
          >
            {d.icon} {d.category}
          </Text>
          <Text
            position={[d.cx, 1, d.cz - d.halfD - 3]}
            fontSize={2.5}
            color={d.color}
            anchorX="center"
            anchorY="middle"
            font="/fonts/JetBrainsMono-Regular.woff"
            fillOpacity={0.4}
          >
            {d.count} каналов · {formatNumber(d.totalReach)} подписчиков
          </Text>
        </group>
      ))}
    </group>
  );
}

// ── Buildings (instanced mesh) ───────────────────────────────────────────────

type BuildingsProps = {
  layout: CityLayout;
  hoveredIdx: number | null;
  selectedIdx: number | null;
  filterCategory: string;
  filterQuery: string;
  onHover: (idx: number | null) => void;
  onSelect: (idx: number | null) => void;
};

function Buildings({
  layout,
  hoveredIdx,
  selectedIdx,
  filterCategory,
  filterQuery,
  onHover,
  onSelect,
}: BuildingsProps) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const outlineRef = useRef<THREE.InstancedMesh>(null);
  const { camera, gl } = useThree();
  const raycaster = useMemo(() => new THREE.Raycaster(), []);

  const { buildings } = layout;
  const count = buildings.length;

  // Filter matching
  const matchFlags = useMemo(() => {
    const q = filterQuery.trim().toLowerCase();
    return buildings.map((b) => {
      if (!filterCategory && !q) return true;
      const catMatch = !filterCategory || b.category === filterCategory;
      const qMatch =
        !q ||
        (b.ch.title?.toLowerCase().includes(q) ?? false) ||
        (b.ch.username?.toLowerCase().includes(q) ?? false);
      return catMatch && qMatch;
    });
  }, [buildings, filterCategory, filterQuery]);

  // Set instance matrices and colors
  useEffect(() => {
    const mesh = meshRef.current;
    const outline = outlineRef.current;
    if (!mesh || count === 0) return;

    const dummy = new THREE.Object3D();
    const color = new THREE.Color();

    for (let i = 0; i < count; i++) {
      const b = buildings[i];
      const isMatch = matchFlags[i];

      dummy.position.set(b.x, b.height / 2, b.z);
      dummy.scale.set(b.width, b.height, b.depth);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);

      if (outline) {
        dummy.scale.set(b.width + 0.4, b.height + 0.4, b.depth + 0.4);
        dummy.updateMatrix();
        outline.setMatrixAt(i, dummy.matrix);
      }

      const alpha = isMatch ? 1 : 0.15;
      color.set(b.color);
      if (!isMatch) {
        color.multiplyScalar(0.3);
      }
      mesh.setColorAt(i, color);

      if (outline) {
        const outlineColor = new THREE.Color(b.color);
        outlineColor.multiplyScalar(isMatch ? 0.3 : 0.05);
        outline.setColorAt(i, outlineColor);
      }
    }

    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    if (outline) {
      outline.instanceMatrix.needsUpdate = true;
      if (outline.instanceColor) outline.instanceColor.needsUpdate = true;
    }
  }, [buildings, count, matchFlags]);

  // Animate hovered/selected building
  useFrame((state) => {
    const mesh = meshRef.current;
    if (!mesh || count === 0) return;

    if (hoveredIdx != null && hoveredIdx < count) {
      const b = buildings[hoveredIdx];
      const pulse = 1 + Math.sin(state.clock.elapsedTime * 4) * 0.03;
      const dummy = new THREE.Object3D();
      dummy.position.set(b.x, b.height / 2, b.z);
      dummy.scale.set(b.width * pulse, b.height * pulse, b.depth * pulse);
      dummy.updateMatrix();
      mesh.setMatrixAt(hoveredIdx, dummy.matrix);

      const c = new THREE.Color(b.color);
      c.lerp(new THREE.Color("#ffffff"), 0.3);
      mesh.setColorAt(hoveredIdx, c);

      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }

    if (selectedIdx != null && selectedIdx < count) {
      const b = buildings[selectedIdx];
      const glow = 1 + Math.sin(state.clock.elapsedTime * 3) * 0.05;
      const dummy = new THREE.Object3D();
      dummy.position.set(b.x, (b.height * glow) / 2, b.z);
      dummy.scale.set(b.width * 1.05, b.height * glow, b.depth * 1.05);
      dummy.updateMatrix();
      mesh.setMatrixAt(selectedIdx, dummy.matrix);

      const c = new THREE.Color("#ffffff");
      mesh.setColorAt(selectedIdx, c);

      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
  });

  // Raycasting
  const handlePointerMove = useCallback(
    (e: PointerEvent) => {
      const rect = gl.domElement.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      const y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(new THREE.Vector2(x, y), camera);
      const mesh = meshRef.current;
      if (!mesh) return;
      const hits = raycaster.intersectObject(mesh);
      if (hits.length > 0 && hits[0].instanceId != null) {
        onHover(hits[0].instanceId);
        gl.domElement.style.cursor = "pointer";
      } else {
        onHover(null);
        gl.domElement.style.cursor = "grab";
      }
    },
    [camera, gl, raycaster, onHover]
  );

  const handlePointerDown = useCallback(
    (e: PointerEvent) => {
      const rect = gl.domElement.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      const y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(new THREE.Vector2(x, y), camera);
      const mesh = meshRef.current;
      if (!mesh) return;
      const hits = raycaster.intersectObject(mesh);
      if (hits.length > 0 && hits[0].instanceId != null) {
        onSelect(hits[0].instanceId);
      }
    },
    [camera, gl, raycaster, onSelect]
  );

  useEffect(() => {
    const canvas = gl.domElement;
    canvas.addEventListener("pointermove", handlePointerMove);
    canvas.addEventListener("pointerdown", handlePointerDown);
    return () => {
      canvas.removeEventListener("pointermove", handlePointerMove);
      canvas.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [gl, handlePointerMove, handlePointerDown]);

  if (count === 0) return null;

  return (
    <group>
      {/* Building outlines (slightly larger, darker) */}
      <instancedMesh
        ref={outlineRef}
        args={[undefined, undefined, count]}
        frustumCulled
      >
        <boxGeometry args={[1, 1, 1]} />
        <meshBasicMaterial
          vertexColors
          transparent
          opacity={0.4}
          wireframe
        />
      </instancedMesh>

      {/* Main buildings */}
      <instancedMesh
        ref={meshRef}
        args={[undefined, undefined, count]}
        frustumCulled
        castShadow
        receiveShadow
      >
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial
          vertexColors
          metalness={0.7}
          roughness={0.25}
          envMapIntensity={1.2}
        />
      </instancedMesh>
    </group>
  );
}

// ── Building top glow (emissive caps for high-ER channels) ───────────────────

function BuildingGlows({ layout }: { layout: CityLayout }) {
  const glowBuildings = useMemo(
    () => layout.buildings.filter((b) => b.emissive > 0.3),
    [layout.buildings]
  );

  const meshRef = useRef<THREE.InstancedMesh>(null);

  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh || glowBuildings.length === 0) return;

    const dummy = new THREE.Object3D();
    const color = new THREE.Color();

    for (let i = 0; i < glowBuildings.length; i++) {
      const b = glowBuildings[i];
      dummy.position.set(b.x, b.height + 0.5, b.z);
      dummy.scale.set(b.width * 0.8, 1, b.depth * 0.8);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);

      color.set(b.color);
      mesh.setColorAt(i, color);
    }

    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [glowBuildings]);

  useFrame((state) => {
    const mesh = meshRef.current;
    if (!mesh || glowBuildings.length === 0) return;
    const mat = mesh.material as THREE.MeshStandardMaterial;
    mat.emissiveIntensity = 0.5 + Math.sin(state.clock.elapsedTime * 2) * 0.3;
  });

  if (glowBuildings.length === 0) return null;

  return (
    <instancedMesh
      ref={meshRef}
      args={[undefined, undefined, glowBuildings.length]}
    >
      <boxGeometry args={[1, 1, 1]} />
      <meshStandardMaterial
        vertexColors
        emissive="#ffffff"
        emissiveIntensity={0.5}
        transparent
        opacity={0.6}
        metalness={0.9}
        roughness={0.1}
      />
    </instancedMesh>
  );
}

// ── Lighting setup (inspired by KMD project) ────────────────────────────────

function CityLights() {
  return (
    <group>
      {/* Ambient base */}
      <ambientLight intensity={0.25} color="#b0c4de" />

      {/* Key light — warm directional */}
      <directionalLight
        position={[200, 300, 150]}
        intensity={0.8}
        color="#fff5e6"
        castShadow
        shadow-mapSize-width={2048}
        shadow-mapSize-height={2048}
        shadow-camera-near={10}
        shadow-camera-far={1500}
        shadow-camera-left={-500}
        shadow-camera-right={500}
        shadow-camera-top={500}
        shadow-camera-bottom={-500}
      />

      {/* Fill light — cool blue */}
      <directionalLight
        position={[-150, 200, -100]}
        intensity={0.3}
        color="#4488ff"
      />

      {/* Accent rim light from below-ish */}
      <pointLight
        position={[0, 5, 0]}
        intensity={0.4}
        color="#00ff88"
        distance={400}
        decay={2}
      />
    </group>
  );
}

// ── Fog & atmosphere ─────────────────────────────────────────────────────────

function CityAtmosphere() {
  const { scene } = useThree();

  useEffect(() => {
    scene.fog = new THREE.FogExp2("#050808", 0.0012);
    return () => {
      scene.fog = null;
    };
  }, [scene]);

  return null;
}

// ── Hover tooltip (HTML overlay in 3D) ───────────────────────────────────────

function BuildingTooltip({
  building,
}: {
  building: BuildingData | null;
}) {
  if (!building) return null;
  const { ch, x, height, z, color } = building;

  return (
    <Html
      position={[x, height + 8, z]}
      center
      style={{ pointerEvents: "none", userSelect: "none" }}
    >
      <div
        style={{
          background: "rgba(8, 10, 12, 0.95)",
          border: `1px solid ${color}`,
          borderRadius: 10,
          padding: "10px 16px",
          minWidth: 200,
          maxWidth: 280,
          boxShadow: `0 0 24px ${color}44, 0 4px 12px rgba(0,0,0,0.6)`,
          backdropFilter: "blur(12px)",
          whiteSpace: "nowrap",
        }}
      >
        <div style={{ fontWeight: 700, fontSize: 13, color, fontFamily: "'Geist Sans', system-ui, sans-serif" }}>
          {ch.title ?? `@${ch.username}`}
        </div>
        {ch.username && (
          <div style={{ fontSize: 11, color: "#666", fontFamily: "'JetBrains Mono', monospace", marginTop: 2 }}>
            @{ch.username}
          </div>
        )}
        <div style={{ display: "flex", gap: 14, marginTop: 8, fontSize: 11, color: "#aaa" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <Users size={10} /> {formatNumber(ch.member_count)}
          </span>
          {ch.engagement_rate != null && (
            <span style={{ color: erColor(ch.engagement_rate), display: "flex", alignItems: "center", gap: 4 }}>
              <TrendingUp size={10} /> {erLabel(ch.engagement_rate)}
            </span>
          )}
          {ch.has_comments && (
            <span style={{ color: "#00ff88", display: "flex", alignItems: "center", gap: 4 }}>
              <MessageCircle size={10} /> Комменты
            </span>
          )}
        </div>
      </div>
    </Html>
  );
}

// ── Main 3D scene ────────────────────────────────────────────────────────────

type CitySceneProps = {
  layout: CityLayout;
  filterCategory: string;
  filterQuery: string;
  onSelect: (ch: ChannelMapEntry | null) => void;
};

function CitySceneInner({ layout, filterCategory, filterQuery, onSelect }: CitySceneProps) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);

  const hoveredBuilding = hoveredIdx != null ? layout.buildings[hoveredIdx] ?? null : null;

  const handleSelect = useCallback(
    (idx: number | null) => {
      setSelectedIdx(idx);
      if (idx != null && idx < layout.buildings.length) {
        onSelect(layout.buildings[idx].ch);
      }
    },
    [layout.buildings, onSelect]
  );

  return (
    <>
      <CityAtmosphere />
      <CityLights />
      <CityGround bounds={layout.bounds} />
      <DistrictPlates districts={layout.districts} />
      <Buildings
        layout={layout}
        hoveredIdx={hoveredIdx}
        selectedIdx={selectedIdx}
        filterCategory={filterCategory}
        filterQuery={filterQuery}
        onHover={setHoveredIdx}
        onSelect={handleSelect}
      />
      <BuildingGlows layout={layout} />
      <BuildingTooltip building={hoveredBuilding} />
    </>
  );
}

// ── Channel detail panel ─────────────────────────────────────────────────────

function ChannelDetailPanel({
  ch,
  onClose,
  onAddToFarm,
  onAddToCampaign,
}: {
  ch: ChannelMapEntry;
  onClose: () => void;
  onAddToFarm: () => void;
  onAddToCampaign: () => void;
}) {
  const meta = getCategoryMeta(ch.category);

  return (
    <motion.div
      initial={{ opacity: 0, x: 40 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 40 }}
      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
      style={{
        position: "fixed",
        top: 80,
        right: 24,
        width: 360,
        maxHeight: "calc(100vh - 120px)",
        overflowY: "auto",
        background: "rgba(8, 10, 12, 0.96)",
        border: `1px solid ${meta.color}44`,
        borderRadius: 16,
        padding: 24,
        zIndex: 100,
        boxShadow: `0 0 40px ${meta.color}22, 0 8px 32px rgba(0,0,0,0.6)`,
        backdropFilter: "blur(16px)",
        fontFamily: "'Geist Sans', system-ui, sans-serif",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#fff", lineHeight: 1.3 }}>
            {ch.title ?? `@${ch.username}`}
          </div>
          {ch.username && (
            <div style={{ fontSize: 12, color: "#666", fontFamily: "'JetBrains Mono', monospace", marginTop: 4 }}>
              @{ch.username}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          style={{
            all: "unset", cursor: "pointer", padding: 6,
            color: "#666", display: "flex",
          }}
        >
          <X size={16} />
        </button>
      </div>

      {/* Category badge */}
      <div
        style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "4px 12px", borderRadius: 20,
          background: `${meta.color}15`, border: `1px solid ${meta.color}33`,
          color: meta.color, fontSize: 12, fontWeight: 600, marginBottom: 16,
        }}
      >
        {meta.icon} {ch.category ?? "Другое"}
      </div>

      {/* Stats grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 16 }}>
        {[
          { label: "Подписчики", value: formatNumber(ch.member_count), icon: <Users size={12} /> },
          { label: "ER", value: erLabel(ch.engagement_rate), icon: <TrendingUp size={12} />, color: erColor(ch.engagement_rate) },
          { label: "Охват постов", value: formatNumber(ch.avg_post_reach), icon: <Eye size={12} /> },
          { label: "Комменты/пост", value: formatNumber(ch.avg_comments_per_post), icon: <MessageCircle size={12} /> },
          { label: "Язык", value: ch.language ? `${langFlag(ch.language)} ${ch.language.toUpperCase()}` : "—", icon: <Globe size={12} /> },
          { label: "Комментарии", value: ch.has_comments ? "Открыты" : "Закрыты", icon: <MessageCircle size={12} />, color: ch.has_comments ? "#00ff88" : "#666" },
        ].map((s) => (
          <div
            key={s.label}
            style={{
              background: "rgba(255,255,255,0.03)",
              border: "1px solid rgba(255,255,255,0.06)",
              borderRadius: 10, padding: "10px 12px",
            }}
          >
            <div style={{ fontSize: 10, color: "#555", display: "flex", alignItems: "center", gap: 4, marginBottom: 4 }}>
              {s.icon} {s.label}
            </div>
            <div style={{ fontSize: 15, fontWeight: 700, color: s.color ?? "#fff", fontFamily: "'JetBrains Mono', monospace" }}>
              {s.value}
            </div>
          </div>
        ))}
      </div>

      {/* Description */}
      {ch.description && (
        <div style={{ fontSize: 12, color: "#888", lineHeight: 1.5, marginBottom: 16, borderTop: "1px solid rgba(255,255,255,0.06)", paddingTop: 12 }}>
          {ch.description.length > 200 ? ch.description.slice(0, 200) + "…" : ch.description}
        </div>
      )}

      {/* Actions */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {ch.username && (
          <a
            href={`https://t.me/${ch.username}`}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
              padding: "10px 16px", borderRadius: 10,
              background: "rgba(0,255,136,0.08)", border: "1px solid rgba(0,255,136,0.25)",
              color: "#00ff88", fontSize: 13, fontWeight: 600, textDecoration: "none",
            }}
          >
            <ExternalLink size={14} /> Открыть в Telegram
          </a>
        )}
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            onClick={onAddToFarm}
            style={{
              flex: 1, all: "unset", cursor: "pointer", textAlign: "center" as const,
              display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
              padding: "9px 12px", borderRadius: 10,
              background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)",
              color: "#aaa", fontSize: 12, fontWeight: 500,
            }}
          >
            <Tractor size={13} /> В ферму
          </button>
          <button
            type="button"
            onClick={onAddToCampaign}
            style={{
              flex: 1, all: "unset", cursor: "pointer", textAlign: "center" as const,
              display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
              padding: "9px 12px", borderRadius: 10,
              background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)",
              color: "#aaa", fontSize: 12, fontWeight: 500,
            }}
          >
            <Megaphone size={13} /> В кампанию
          </button>
        </div>
      </div>

      {/* Last indexed */}
      {ch.last_indexed_at && (
        <div style={{ fontSize: 10, color: "#444", marginTop: 12, textAlign: "center" as const }}>
          Последняя индексация: {new Date(ch.last_indexed_at).toLocaleDateString("ru-RU")}
        </div>
      )}
    </motion.div>
  );
}

// ── Selection modal (farm / campaign) ────────────────────────────────────────

function SelectionModal({
  variant,
  farms,
  channelDbs,
  campaigns,
  onConfirm,
  onClose,
  busy,
}: {
  variant: "farm" | "campaign";
  farms: FarmConfig[];
  channelDbs: ChannelDatabase[];
  campaigns: Campaign[];
  onConfirm: (id: number, dbId?: number) => void;
  onClose: () => void;
  busy: boolean;
}) {
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const items = variant === "farm" ? channelDbs : campaigns;
  const label = variant === "farm" ? "база каналов фермы" : "кампания";

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      style={{
        position: "fixed", inset: 0, zIndex: 200,
        background: "rgba(0,0,0,0.7)", backdropFilter: "blur(8px)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }}
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "#0c0e10", border: "1px solid rgba(255,255,255,0.08)",
          borderRadius: 16, padding: 24, width: 400, maxHeight: "60vh", overflowY: "auto",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
          <h3 style={{ margin: 0, fontSize: 16, color: "#fff" }}>
            Выберите {label}
          </h3>
          <button type="button" onClick={onClose} style={{ all: "unset", cursor: "pointer", color: "#666" }}>
            <X size={16} />
          </button>
        </div>

        {items.length === 0 ? (
          <div style={{ color: "#666", fontSize: 13, textAlign: "center", padding: 20 }}>
            Нет доступных {variant === "farm" ? "баз каналов" : "кампаний"}
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {items.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setSelectedId(item.id)}
                style={{
                  all: "unset", cursor: "pointer", padding: "10px 14px",
                  borderRadius: 10, fontSize: 13, color: "#ddd",
                  background: selectedId === item.id ? "rgba(0,255,136,0.1)" : "rgba(255,255,255,0.03)",
                  border: selectedId === item.id ? "1px solid rgba(0,255,136,0.3)" : "1px solid rgba(255,255,255,0.06)",
                }}
              >
                {item.name}
              </button>
            ))}
          </div>
        )}

        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <button
            type="button"
            disabled={selectedId == null || busy}
            onClick={() => selectedId != null && onConfirm(selectedId)}
            style={{
              flex: 1, padding: "10px 16px", borderRadius: 10, border: "none",
              background: selectedId != null ? "rgba(0,255,136,0.15)" : "rgba(255,255,255,0.05)",
              color: selectedId != null ? "#00ff88" : "#555",
              fontSize: 13, fontWeight: 600, cursor: selectedId != null ? "pointer" : "not-allowed",
            }}
          >
            {busy ? "Добавляем…" : "Добавить"}
          </button>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: "10px 16px", borderRadius: 10, border: "1px solid rgba(255,255,255,0.08)",
              background: "transparent", color: "#888", fontSize: 13, cursor: "pointer",
            }}
          >
            Отмена
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

// ── Filter language options ──────────────────────────────────────────────────

const LANGUAGE_OPTIONS = [
  { value: "", label: "Все языки" },
  { value: "ru", label: "Русский" },
  { value: "en", label: "English" },
  { value: "uk", label: "Українська" },
  { value: "kz", label: "Қазақша" },
];

// ── Main page ────────────────────────────────────────────────────────────────

export function ChannelMapPage() {
  const { accessToken } = useAuth();

  const [items, setItems] = useState<ChannelMapEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [categories, setCategories] = useState<string[]>([]);
  const [stats, setStats] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const [query, setQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("");
  const [selectedLanguage, setSelectedLanguage] = useState("");
  const [minMembers, setMinMembers] = useState(0);
  const [hasCommentsOnly, setHasCommentsOnly] = useState(false);

  const [selectedChannel, setSelectedChannel] = useState<ChannelMapEntry | null>(null);

  // Farm / campaign data for modals
  const [farms, setFarms] = useState<FarmConfig[]>([]);
  const [channelDbs, setChannelDbs] = useState<ChannelDatabase[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [modalVariant, setModalVariant] = useState<"farm" | "campaign" | null>(null);
  const [pendingChannels, setPendingChannels] = useState<ChannelMapEntry[]>([]);
  const [modalBusy, setModalBusy] = useState(false);
  const [actionMsg, setActionMsg] = useState("");

  // Filter panel visibility
  const [showFilters, setShowFilters] = useState(false);

  // Computed
  const byCategory = (stats.by_category as Record<string, number> | undefined) ?? {};
  const totalIndexed = typeof stats.total === "number" ? stats.total : total;

  const totalReach = useMemo(
    () => items.reduce((sum, ch) => sum + (ch.member_count ?? 0), 0),
    [items]
  );

  const displayItems = useMemo(() => {
    let filtered = items;
    if (hasCommentsOnly) filtered = filtered.filter((ch) => ch.has_comments);
    return filtered;
  }, [items, hasCommentsOnly]);

  const allCategoryNames = useMemo(() => {
    const cats = new Set<string>();
    for (const ch of items) {
      if (ch.category) cats.add(ch.category);
    }
    return [...cats].sort();
  }, [items]);

  const cityLayout = useMemo(
    () => computeCityLayout(displayItems),
    [displayItems]
  );

  // ── Data loading ────────────────────────────────────────────────────────

  const loadAll = useCallback(async () => {
    if (!accessToken) return;
    setBusy(true);
    setError("");
    try {
      const [listRes, catsRes, statsRes] = await Promise.all([
        channelMapApi.list(accessToken, { limit: 5000 }),
        channelMapApi.categories(accessToken),
        channelMapApi.stats(accessToken),
      ]);
      setItems(listRes.items ?? []);
      setTotal(listRes.total ?? 0);
      setCategories(catsRes.categories ?? []);
      setStats(statsRes as Record<string, unknown>);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Ошибка загрузки";
      setError(msg);
    } finally {
      setBusy(false);
    }
  }, [accessToken]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const doSearch = useCallback(async () => {
    if (!accessToken) return;
    setBusy(true);
    setError("");
    try {
      const res = await channelMapApi.search(accessToken, {
        query: query || undefined,
        category: selectedCategory || undefined,
        language: selectedLanguage || undefined,
        min_members: minMembers > 0 ? minMembers : undefined,
        limit: 5000,
      });
      setItems(res.items ?? []);
      setTotal(res.total ?? 0);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Ошибка поиска";
      setError(msg);
    } finally {
      setBusy(false);
    }
  }, [accessToken, query, selectedCategory, selectedLanguage, minMembers]);

  const handleSearch = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      void doSearch();
    },
    [doSearch]
  );

  const handleReset = useCallback(() => {
    setQuery("");
    setSelectedCategory("");
    setSelectedLanguage("");
    setMinMembers(0);
    setHasCommentsOnly(false);
    void loadAll();
  }, [loadAll]);

  // Farm/campaign modal handlers
  const loadFarmData = useCallback(async () => {
    if (!accessToken) return;
    try {
      const [farmsRes, dbsRes] = await Promise.all([
        farmApi.list(accessToken),
        channelDbApi.list(accessToken),
      ]);
      setFarms(farmsRes.items ?? []);
      setChannelDbs(dbsRes.items ?? []);
    } catch {}
  }, [accessToken]);

  const loadCampaignData = useCallback(async () => {
    if (!accessToken) return;
    try {
      const res = await campaignsApi.list(accessToken);
      setCampaigns(res.items ?? []);
    } catch {}
  }, [accessToken]);

  const openFarmModal = useCallback((channels: ChannelMapEntry[]) => {
    setPendingChannels(channels);
    setActionMsg("");
    setModalVariant("farm");
  }, []);

  const openCampaignModal = useCallback((channels: ChannelMapEntry[]) => {
    setPendingChannels(channels);
    setActionMsg("");
    setModalVariant("campaign");
  }, []);

  useEffect(() => {
    if (modalVariant === "farm") void loadFarmData();
    if (modalVariant === "campaign") void loadCampaignData();
  }, [modalVariant, loadFarmData, loadCampaignData]);

  const handleModalConfirm = useCallback(
    async (id: number) => {
      if (!accessToken || pendingChannels.length === 0) return;
      setModalBusy(true);
      try {
        const usernames = pendingChannels
          .map((ch) => ch.username)
          .filter((u): u is string => !!u);
        if (usernames.length === 0) {
          setActionMsg("Нет каналов с username для добавления.");
          setModalVariant(null);
          return;
        }
        if (modalVariant === "farm") {
          const res = await channelDbApi.importChannels(accessToken, id, usernames);
          setActionMsg(`Добавлено ${res.imported} каналов (пропущено: ${res.skipped}).`);
        } else if (modalVariant === "campaign") {
          const campaign = campaigns.find((c) => c.id === id);
          if (campaign?.channel_database_id) {
            const res = await channelDbApi.importChannels(accessToken, campaign.channel_database_id, usernames);
            setActionMsg(`Добавлено ${res.imported} каналов в "${campaign.name}" (пропущено: ${res.skipped}).`);
          } else {
            setActionMsg("У кампании нет привязанной базы каналов.");
          }
        }
      } catch (err) {
        setActionMsg(err instanceof Error ? err.message : "Ошибка");
      } finally {
        setModalBusy(false);
        setModalVariant(null);
      }
    },
    [accessToken, pendingChannels, modalVariant, campaigns]
  );

  // ── Render ──────────────────────────────────────────────────────────────

  const webglOk = useMemo(() => isWebGLAvailable(), []);

  const canvasCenter = useMemo(() => {
    if (cityLayout.buildings.length === 0) return [0, 60, 150] as [number, number, number];
    const b = cityLayout.bounds;
    const cx = (b.minX + b.maxX) / 2;
    const cz = (b.minZ + b.maxZ) / 2;
    const span = Math.max(b.maxX - b.minX, b.maxZ - b.minZ, 100);
    return [cx, span * 0.6, cz + span * 0.5] as [number, number, number];
  }, [cityLayout]);

  const canvasTarget = useMemo(() => {
    const b = cityLayout.bounds;
    return [(b.minX + b.maxX) / 2, 0, (b.minZ + b.maxZ) / 2] as [number, number, number];
  }, [cityLayout]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0, height: "100vh", overflow: "hidden" }}>

      {/* ── HUD top bar ─────────────────────────────────────────── */}
      <div
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "12px 20px", background: "rgba(8, 10, 12, 0.9)",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          backdropFilter: "blur(12px)", zIndex: 30,
        }}
      >
        {/* Left: title + stats */}
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Building2 size={18} style={{ color: "#00ff88" }} />
            <span style={{ fontSize: 16, fontWeight: 700, color: "#fff", fontFamily: "'Geist Sans', system-ui" }}>
              Channel City
            </span>
          </div>

          <div style={{ display: "flex", gap: 12, fontSize: 11, color: "#666" }}>
            <span>
              <span style={{ color: "#00ff88", fontWeight: 700, fontFamily: "monospace" }}>
                {displayItems.length}
              </span>{" "}
              каналов
            </span>
            <span>
              <span style={{ color: "#4488ff", fontWeight: 700, fontFamily: "monospace" }}>
                {allCategoryNames.length}
              </span>{" "}
              районов
            </span>
            <span>
              <span style={{ color: "#ffaa00", fontWeight: 700, fontFamily: "monospace" }}>
                {formatNumber(totalReach)}
              </span>{" "}
              подписчиков
            </span>
          </div>
        </div>

        {/* Right: controls */}
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {/* Search input */}
          <div style={{ position: "relative" }}>
            <Search
              size={13}
              style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "#444" }}
            />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void doSearch(); }}
              placeholder="Поиск каналов..."
              style={{
                width: 200, padding: "7px 12px 7px 32px", fontSize: 12,
                background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)",
                borderRadius: 8, color: "#ddd", outline: "none",
                fontFamily: "'Geist Sans', system-ui",
              }}
            />
          </div>

          {/* Filter toggle */}
          <button
            type="button"
            onClick={() => setShowFilters(!showFilters)}
            style={{
              all: "unset", cursor: "pointer", display: "flex", alignItems: "center", gap: 6,
              padding: "6px 14px", borderRadius: 8, fontSize: 12,
              background: showFilters ? "rgba(0,255,136,0.1)" : "rgba(255,255,255,0.04)",
              border: showFilters ? "1px solid rgba(0,255,136,0.3)" : "1px solid rgba(255,255,255,0.08)",
              color: showFilters ? "#00ff88" : "#888",
            }}
          >
            <Filter size={13} /> Фильтры
          </button>

          {/* Refresh */}
          <button
            type="button"
            onClick={() => void loadAll()}
            disabled={busy}
            style={{
              all: "unset", cursor: "pointer", display: "flex", padding: 6,
              borderRadius: 6, color: "#666",
            }}
          >
            <RotateCcw size={14} className={busy ? "spin" : ""} />
          </button>
        </div>
      </div>

      {/* ── Filter panel (collapsible) ─────────────────────────── */}
      <AnimatePresence>
        {showFilters && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            style={{
              overflow: "hidden", background: "rgba(8, 10, 12, 0.95)",
              borderBottom: "1px solid rgba(255,255,255,0.06)",
              zIndex: 25,
            }}
          >
            <form
              onSubmit={handleSearch}
              style={{
                display: "flex", flexWrap: "wrap", gap: 12,
                padding: "12px 20px", alignItems: "flex-end",
              }}
            >
              {/* Category */}
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span style={{ fontSize: 10, color: "#555" }}>Категория</span>
                <select
                  value={selectedCategory}
                  onChange={(e) => setSelectedCategory(e.target.value)}
                  style={{
                    padding: "6px 10px", fontSize: 12, borderRadius: 6,
                    background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)",
                    color: "#ddd", outline: "none",
                  }}
                >
                  <option value="">Все категории</option>
                  {allCategoryNames.map((c) => (
                    <option key={c} value={c}>
                      {getCategoryMeta(c).icon} {c}
                    </option>
                  ))}
                </select>
              </label>

              {/* Language */}
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span style={{ fontSize: 10, color: "#555" }}>Язык</span>
                <select
                  value={selectedLanguage}
                  onChange={(e) => setSelectedLanguage(e.target.value)}
                  style={{
                    padding: "6px 10px", fontSize: 12, borderRadius: 6,
                    background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)",
                    color: "#ddd", outline: "none",
                  }}
                >
                  {LANGUAGE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </label>

              {/* Min members */}
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span style={{ fontSize: 10, color: "#555" }}>
                  Мин. подписчиков:{" "}
                  <span style={{ color: "#00ff88", fontFamily: "monospace" }}>
                    {minMembers > 0 ? formatNumber(minMembers) : "—"}
                  </span>
                </span>
                <input
                  type="range"
                  min={0}
                  max={1_000_000}
                  step={5_000}
                  value={minMembers}
                  onChange={(e) => setMinMembers(Number(e.target.value))}
                  style={{ width: 150, accentColor: "#00ff88" }}
                />
              </label>

              {/* Comments only */}
              <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12, color: "#888", paddingBottom: 4 }}>
                <input
                  type="checkbox"
                  checked={hasCommentsOnly}
                  onChange={(e) => setHasCommentsOnly(e.target.checked)}
                  style={{ accentColor: "#00ff88" }}
                />
                <MessageCircle size={12} /> Только с комментариями
              </label>

              {/* Buttons */}
              <button
                type="submit"
                disabled={busy}
                style={{
                  padding: "6px 16px", borderRadius: 8, border: "none",
                  background: "rgba(0,255,136,0.15)", color: "#00ff88",
                  fontSize: 12, fontWeight: 600, cursor: "pointer",
                }}
              >
                {busy ? "Ищем…" : "Найти"}
              </button>
              <button
                type="button"
                onClick={handleReset}
                style={{
                  padding: "6px 16px", borderRadius: 8,
                  border: "1px solid rgba(255,255,255,0.08)", background: "transparent",
                  color: "#666", fontSize: 12, cursor: "pointer",
                }}
              >
                Сбросить
              </button>
            </form>

            {/* Category chips */}
            {allCategoryNames.length > 0 && (
              <div
                style={{
                  display: "flex", flexWrap: "wrap", gap: 6,
                  padding: "0 20px 12px",
                }}
              >
                {allCategoryNames.map((cat) => {
                  const meta = getCategoryMeta(cat);
                  const isSelected = selectedCategory === cat;
                  const count = byCategory[cat] ?? items.filter((c) => c.category === cat).length;
                  return (
                    <button
                      key={cat}
                      type="button"
                      onClick={() => setSelectedCategory(isSelected ? "" : cat)}
                      style={{
                        all: "unset", cursor: "pointer",
                        display: "inline-flex", alignItems: "center", gap: 4,
                        padding: "3px 10px", borderRadius: 20, fontSize: 11,
                        background: isSelected ? `${meta.color}20` : "rgba(255,255,255,0.03)",
                        border: `1px solid ${isSelected ? `${meta.color}44` : "rgba(255,255,255,0.06)"}`,
                        color: isSelected ? meta.color : "#777",
                        fontWeight: isSelected ? 600 : 400,
                        transition: "all 150ms",
                      }}
                    >
                      {meta.icon} {cat}
                      <span style={{ fontSize: 9, opacity: 0.6, fontFamily: "monospace" }}>
                        {count}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── 3D Canvas (full remaining height) ──────────────────── */}
      <div style={{ flex: 1, position: "relative", background: "#050808" }}>
        {busy && (
          <div
            style={{
              position: "absolute", inset: 0, zIndex: 20,
              display: "flex", alignItems: "center", justifyContent: "center",
              background: "rgba(5,8,8,0.8)",
            }}
          >
            <div style={{ textAlign: "center" }}>
              <div
                style={{
                  width: 36, height: 36, border: "3px solid #00ff8844",
                  borderTopColor: "#00ff88", borderRadius: "50%",
                  animation: "spin 0.8s linear infinite", margin: "0 auto 12px",
                }}
              />
              <div style={{ color: "#00ff88", fontSize: 13 }}>Загружаем город каналов…</div>
            </div>
          </div>
        )}

        {error && (
          <div
            style={{
              position: "absolute", top: 16, left: "50%", transform: "translateX(-50%)",
              zIndex: 20, padding: "10px 20px", borderRadius: 10,
              background: "rgba(255,0,0,0.1)", border: "1px solid rgba(255,0,0,0.3)",
              color: "#ff4444", fontSize: 13, maxWidth: 400,
            }}
          >
            {error}
          </div>
        )}

        {!busy && displayItems.length === 0 && !error && (
          <div
            style={{
              position: "absolute", inset: 0, zIndex: 20,
              display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "center", gap: 16,
            }}
          >
            <MapPin size={48} style={{ color: "#222" }} />
            <div style={{ fontSize: 18, fontWeight: 600, color: "#444" }}>
              Каналы не найдены
            </div>
            <div style={{ fontSize: 13, color: "#555", maxWidth: 350, textAlign: "center", lineHeight: 1.6 }}>
              Добавьте каналы через парсер или импортируйте базу данных каналов.
            </div>
            <a
              href="/app/parser"
              style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                padding: "8px 20px", borderRadius: 10,
                background: "rgba(0,255,136,0.08)", border: "1px solid rgba(0,255,136,0.25)",
                color: "#00ff88", fontSize: 13, textDecoration: "none", fontWeight: 600,
              }}
            >
              Перейти к парсеру
            </a>
          </div>
        )}

        {webglOk ? (
          <WebGLErrorBoundary
            fallback={
              <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "#ff8800" }}>
                3D не поддерживается в этом браузере
              </div>
            }
          >
            <Canvas
              camera={{
                position: canvasCenter,
                fov: 50,
                near: 1,
                far: 10000,
              }}
              shadows
              gl={{
                antialias: true,
                alpha: false,
                toneMapping: THREE.ACESFilmicToneMapping,
                toneMappingExposure: 1.1,
              }}
              style={{ width: "100%", height: "100%" }}
            >
              <Suspense fallback={null}>
                <CitySceneInner
                  layout={cityLayout}
                  filterCategory={selectedCategory}
                  filterQuery={query}
                  onSelect={setSelectedChannel}
                />
                <OrbitControls
                  target={canvasTarget}
                  enableDamping
                  dampingFactor={0.06}
                  minDistance={20}
                  maxDistance={2000}
                  maxPolarAngle={Math.PI / 2 - 0.05}
                  minPolarAngle={0.1}
                />
              </Suspense>
            </Canvas>
          </WebGLErrorBoundary>
        ) : (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "#ff8800", fontSize: 14 }}>
            WebGL не поддерживается. Используйте современный браузер.
          </div>
        )}

        {/* ── Legend overlay (bottom-left) ─────────────────────── */}
        {cityLayout.districts.length > 0 && (
          <div
            style={{
              position: "absolute", bottom: 16, left: 16, zIndex: 10,
              background: "rgba(8, 10, 12, 0.9)", border: "1px solid rgba(255,255,255,0.06)",
              borderRadius: 12, padding: "12px 16px", maxHeight: 300, overflowY: "auto",
              backdropFilter: "blur(8px)", minWidth: 160,
            }}
          >
            <div style={{ fontSize: 10, color: "#555", marginBottom: 8, fontWeight: 600, letterSpacing: 1 }}>
              РАЙОНЫ
            </div>
            {cityLayout.districts
              .sort((a, b) => b.count - a.count)
              .slice(0, 15)
              .map((d) => (
                <button
                  key={d.category}
                  type="button"
                  onClick={() =>
                    setSelectedCategory(
                      selectedCategory === d.category ? "" : d.category
                    )
                  }
                  style={{
                    all: "unset", cursor: "pointer", display: "flex", alignItems: "center",
                    gap: 8, padding: "3px 0", width: "100%", fontSize: 11,
                    color: selectedCategory === d.category ? d.color : "#888",
                    fontWeight: selectedCategory === d.category ? 600 : 400,
                  }}
                >
                  <div
                    style={{
                      width: 8, height: 8, borderRadius: 2,
                      background: d.color,
                      opacity: selectedCategory === d.category ? 1 : 0.5,
                    }}
                  />
                  <span style={{ flex: 1 }}>{d.icon} {d.category}</span>
                  <span style={{ fontFamily: "monospace", fontSize: 10, color: "#555" }}>
                    {d.count}
                  </span>
                </button>
              ))}
          </div>
        )}

        {/* ── Controls hint (bottom-right) ─────────────────────── */}
        <div
          style={{
            position: "absolute", bottom: 16, right: 16, zIndex: 10,
            fontSize: 10, color: "#444", background: "rgba(8,10,12,0.8)",
            padding: "6px 10px", borderRadius: 8, lineHeight: 1.6,
          }}
        >
          Колёсико — зум · Перетащить — вращение · ПКМ — панорама · Клик — детали
        </div>
      </div>

      {/* ── Action result toast ─────────────────────────────────── */}
      <AnimatePresence>
        {actionMsg && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            style={{
              position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)",
              background: "#0a0a0b", border: "1px solid #00ff88",
              borderRadius: 10, padding: "12px 20px", zIndex: 500,
              fontSize: 13, color: "#00ff88",
              boxShadow: "0 0 20px rgba(0,255,136,0.2)",
              display: "flex", alignItems: "center", gap: 10,
            }}
          >
            {actionMsg}
            <button
              type="button"
              onClick={() => setActionMsg("")}
              style={{ all: "unset", cursor: "pointer", color: "#666", display: "flex" }}
            >
              <X size={14} />
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Selection modal ──────────────────────────────────────── */}
      <AnimatePresence>
        {modalVariant && (
          <SelectionModal
            key={modalVariant}
            variant={modalVariant}
            farms={farms}
            channelDbs={channelDbs}
            campaigns={campaigns}
            onConfirm={handleModalConfirm}
            onClose={() => setModalVariant(null)}
            busy={modalBusy}
          />
        )}
      </AnimatePresence>

      {/* ── Channel detail panel ─────────────────────────────────── */}
      <AnimatePresence>
        {selectedChannel && (
          <ChannelDetailPanel
            key={selectedChannel.id}
            ch={selectedChannel}
            onClose={() => setSelectedChannel(null)}
            onAddToFarm={() => openFarmModal([selectedChannel])}
            onAddToCampaign={() => openCampaignModal([selectedChannel])}
          />
        )}
      </AnimatePresence>

      {/* Spinner keyframe */}
      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
