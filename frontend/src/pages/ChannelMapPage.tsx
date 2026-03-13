import {
  useEffect,
  useState,
  useMemo,
  useCallback,
  useRef,
  Suspense,
} from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import {
  channelMapApi,
  ChannelMapEntry,
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
  Map,
  Filter,
  Grid3X3,
  List,
  Globe,
  Users,
  MessageCircle,
  TrendingUp,
  ExternalLink,
  X,
  Layers,
  RotateCcw,
  CheckSquare,
  Square,
  Tractor,
  Megaphone,
  Ban,
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
    ru: "🇷🇺",
    en: "🇺🇸",
    uk: "🇺🇦",
    kz: "🇰🇿",
    de: "🇩🇪",
    fr: "🇫🇷",
    es: "🇪🇸",
    zh: "🇨🇳",
    ar: "🇸🇦",
  };
  if (!lang) return "🌐";
  return map[lang.toLowerCase()] ?? "🌐";
}

function buildHighlightRe(query: string): RegExp | null {
  const q = query.trim();
  if (!q) return null;
  const escaped = q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(${escaped})`, "gi");
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function highlight(text: string, re: RegExp | null): string {
  const safe = escapeHtml(text);
  if (!re) return safe;
  return safe.replace(re, "<mark>$1</mark>");
}

function getInitials(
  title: string | null | undefined,
  username: string | null | undefined
): string {
  const src = (title ?? username ?? "??").trim();
  const words = src.split(/\s+/);
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase();
  }
  return src.slice(0, 2).toUpperCase();
}

// lat/lng → 3D point on unit sphere
function latLngToVec3(
  lat: number,
  lng: number,
  radius: number
): [number, number, number] {
  const phi = ((90 - lat) * Math.PI) / 180;
  const theta = ((lng + 180) * Math.PI) / 180;
  return [
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  ];
}

// ── constants ─────────────────────────────────────────────────────────────────

const REGION_CONFIG: Array<{ label: string; key: string; languages: string[] }> =
  [
    { label: "🇷🇺 RU", key: "ru", languages: ["ru"] },
    { label: "🏳️ СНГ", key: "cis", languages: ["ru", "uk", "kz"] },
    { label: "🇺🇦 UA", key: "uk", languages: ["uk"] },
    { label: "🇰🇿 KZ", key: "kz", languages: ["kz"] },
    { label: "🇺🇸 EN", key: "en", languages: ["en"] },
    { label: "🌐 Все", key: "", languages: [] },
  ];

const CATEGORY_META: Record<string, { icon: string; color: string }> = {
  Crypto: { icon: "₿", color: "var(--warning)" },
  Marketing: { icon: "📢", color: "var(--info)" },
  "E-commerce": { icon: "🛒", color: "var(--accent)" },
  EdTech: { icon: "🎓", color: "#4488ff" },
  News: { icon: "📰", color: "var(--text-secondary)" },
  Entertainment: { icon: "🎬", color: "#ec4899" },
  Tech: { icon: "💻", color: "#8b5cf6" },
  Finance: { icon: "📉", color: "var(--accent-dim)" },
  Lifestyle: { icon: "✨", color: "#f97316" },
  Health: { icon: "🏥", color: "#14b8a6" },
  Gaming: { icon: "🎮", color: "#7c3aed" },
  "18+": { icon: "🔞", color: "var(--danger)" },
  Politics: { icon: "🏛️", color: "#0ea5e9" },
  Sports: { icon: "⚽", color: "#84cc16" },
  Travel: { icon: "✈️", color: "#06b6d4" },
  Business: { icon: "💼", color: "#f59e0b" },
  Science: { icon: "🔬", color: "#aa44ff" },
  Music: { icon: "🎵", color: "#ff88aa" },
  Food: { icon: "🍴", color: "#88ffaa" },
  "AI/ML": { icon: "🤖", color: "#ff00ff" },
  Cybersecurity: { icon: "🔒", color: "#00ffff" },
};

const DEFAULT_CATEGORY_META = { icon: "📌", color: "var(--info)" };

function getCategoryMeta(
  cat: string | null | undefined
): { icon: string; color: string } {
  if (!cat) return DEFAULT_CATEGORY_META;
  return CATEGORY_META[cat] ?? DEFAULT_CATEGORY_META;
}

const CATEGORY_COLORS_RESOLVED: Record<string, string> = {
  Crypto: "#ffaa00",
  Marketing: "#4488ff",
  "E-commerce": "#00ff88",
  EdTech: "#44aaff",
  News: "#888888",
  Entertainment: "#ec4899",
  Tech: "#8844ff",
  Finance: "#44ddff",
  Lifestyle: "#f97316",
  Health: "#44ffaa",
  Gaming: "#ff44aa",
  "18+": "#ff4444",
  Politics: "#ff4488",
  Sports: "#ffdd44",
  Travel: "#44ffdd",
  Business: "#88ff44",
  Science: "#aa44ff",
  Music: "#ff88aa",
  Food: "#88ffaa",
  "AI/ML": "#ff00ff",
  Cybersecurity: "#00ffff",
  Другое: "#4488ff",
};

function getCategoryColor(cat: string | null | undefined): string {
  if (!cat) return "#4488ff";
  return CATEGORY_COLORS_RESOLVED[cat] ?? "#4488ff";
}

const LANGUAGE_OPTIONS = [
  { value: "", label: "Все языки" },
  { value: "ru", label: "Русский" },
  { value: "en", label: "English" },
  { value: "uk", label: "Українська" },
  { value: "kz", label: "Қазақша" },
];

const MEMBER_RANGES = [
  { label: "0–1K", min: 0, max: 1_000 },
  { label: "1K–10K", min: 1_000, max: 10_000 },
  { label: "10K–100K", min: 10_000, max: 100_000 },
  { label: "100K–1M", min: 100_000, max: 1_000_000 },
  { label: "1M+", min: 1_000_000, max: Infinity },
];

const cardVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: {
      delay: i * 0.03,
      duration: 0.3,
      ease: [0.16, 1, 0.3, 1] as const,
    },
  }),
};

const viewSwitchVariants = {
  initial: { opacity: 0, scale: 0.97 },
  animate: { opacity: 1, scale: 1, transition: { duration: 0.25 } },
  exit: { opacity: 0, scale: 0.97, transition: { duration: 0.15 } },
};

// ── Bubble Map types ──────────────────────────────────────────────────────────

type BubbleNode = {
  ch: ChannelMapEntry;
  x: number;
  y: number;
  r: number;
  color: string;
  category: string;
};

type ClusterLabel = {
  category: string;
  cx: number;
  cy: number;
  color: string;
  icon: string;
};

type BubbleLayout = {
  nodes: BubbleNode[];
  labels: ClusterLabel[];
  width: number;
  height: number;
};

function logScale(
  val: number,
  minVal: number,
  maxVal: number,
  minR: number,
  maxR: number
): number {
  if (maxVal <= minVal) return (minR + maxR) / 2;
  const logMin = Math.log1p(minVal);
  const logMax = Math.log1p(maxVal);
  const logVal = Math.log1p(val);
  const t = logMax > logMin ? (logVal - logMin) / (logMax - logMin) : 0.5;
  return minR + t * (maxR - minR);
}

function packCircles(
  items: ChannelMapEntry[],
  minR: number,
  maxR: number
): Array<{ ch: ChannelMapEntry; lx: number; ly: number; r: number }> {
  if (items.length === 0) return [];
  const sorted = [...items].sort(
    (a, b) => (b.member_count ?? 0) - (a.member_count ?? 0)
  );
  const members = sorted.map((c) => c.member_count ?? 0);
  const minM = Math.min(...members);
  const maxM = Math.max(...members);
  const circles: Array<{
    ch: ChannelMapEntry;
    lx: number;
    ly: number;
    r: number;
  }> = [];

  for (const ch of sorted) {
    const r = logScale(ch.member_count ?? 0, minM, maxM, minR, maxR);
    if (circles.length === 0) {
      circles.push({ ch, lx: 0, ly: 0, r });
      continue;
    }
    let bestX = 0,
      bestY = 0,
      bestDist = Infinity;
    let placed = false;
    const angleSteps = Math.min(24, 8 + circles.length);
    for (let ci = 0; ci < Math.min(circles.length, 20); ci++) {
      const ref = circles[ci];
      const gap = r + ref.r + 3;
      for (let ai = 0; ai < angleSteps; ai++) {
        const a = (ai / angleSteps) * Math.PI * 2;
        const tx = ref.lx + Math.cos(a) * gap;
        const ty = ref.ly + Math.sin(a) * gap;
        let ok = true;
        for (const c of circles) {
          const dx = tx - c.lx,
            dy = ty - c.ly;
          if (dx * dx + dy * dy < (r + c.r + 2) * (r + c.r + 2)) {
            ok = false;
            break;
          }
        }
        if (ok) {
          const d = tx * tx + ty * ty;
          if (d < bestDist) {
            bestX = tx;
            bestY = ty;
            bestDist = d;
            placed = true;
          }
        }
      }
      if (placed) break;
    }
    if (!placed) {
      const angle = circles.length * 2.4;
      const rad = r * 2.5 + circles.length * 3;
      bestX = Math.cos(angle) * rad;
      bestY = Math.sin(angle) * rad;
    }
    circles.push({ ch, lx: bestX, ly: bestY, r });
  }
  return circles;
}

function computeLayout(items: ChannelMapEntry[]): BubbleLayout {
  if (items.length === 0)
    return { nodes: [], labels: [], width: 800, height: 600 };
  const groups: Record<string, ChannelMapEntry[]> = {};
  for (const ch of items) {
    const cat = ch.category ?? "Другое";
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(ch);
  }
  const catKeys = Object.keys(groups);
  const totalCats = catKeys.length;
  const clusterRadius = Math.max(200, totalCats * 60);
  const nodes: BubbleNode[] = [];
  const labels: ClusterLabel[] = [];

  for (let ci = 0; ci < catKeys.length; ci++) {
    const cat = catKeys[ci];
    const chItems = groups[cat];
    const theta = (ci / totalCats) * 2 * Math.PI - Math.PI / 2;
    const cx = Math.cos(theta) * clusterRadius;
    const cy = Math.sin(theta) * clusterRadius;
    const color = getCategoryColor(cat);
    const meta = getCategoryMeta(cat);
    const packed = packCircles(chItems, 8, 34);
    let bx = 0,
      by = 0;
    if (packed.length > 0) {
      bx = packed.reduce((s, p) => s + p.lx, 0) / packed.length;
      by = packed.reduce((s, p) => s + p.ly, 0) / packed.length;
    }
    for (const p of packed) {
      nodes.push({
        ch: p.ch,
        x: cx + (p.lx - bx),
        y: cy + (p.ly - by),
        r: p.r,
        color,
        category: cat,
      });
    }
    labels.push({ category: cat, cx, cy: cy - 10, color, icon: meta.icon });
  }

  const pad = 80;
  const allX = nodes.map((n) => n.x - n.r);
  const allY = nodes.map((n) => n.y - n.r);
  const allXR = nodes.map((n) => n.x + n.r);
  const allYR = nodes.map((n) => n.y + n.r);
  const minX = Math.min(...allX);
  const minY = Math.min(...allY);
  const maxX = Math.max(...allXR);
  const maxY = Math.max(...allYR);
  const shiftX = -minX + pad;
  const shiftY = -minY + pad;
  for (const n of nodes) {
    n.x += shiftX;
    n.y += shiftY;
  }
  for (const l of labels) {
    l.cx += shiftX;
    l.cy += shiftY;
  }
  return {
    nodes,
    labels,
    width: maxX - minX + pad * 2,
    height: maxY - minY + pad * 2,
  };
}

// ── BubbleMap canvas ──────────────────────────────────────────────────────────

type Tooltip = { x: number; y: number; ch: ChannelMapEntry } | null;
type AnimTarget = {
  zoom: number;
  panX: number;
  panY: number;
  startZoom: number;
  startPanX: number;
  startPanY: number;
  startTime: number;
  duration: number;
} | null;

const MINIMAP_W = 200;
const MINIMAP_H = 150;

function BubbleMapCanvas({
  layout,
  filterCategory,
  filterQuery,
  onSelect,
}: {
  layout: BubbleLayout;
  filterCategory: string;
  filterQuery: string;
  onSelect: (ch: ChannelMapEntry | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const minimapRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const animRef = useRef<number>(0);
  const stateRef = useRef({
    zoom: 1,
    panX: 0,
    panY: 0,
    dragging: false,
    lastX: 0,
    lastY: 0,
  });
  const animTargetRef = useRef<AnimTarget>(null);
  const initViewRef = useRef({ zoom: 1, panX: 0, panY: 0 });
  const [tooltip, setTooltip] = useState<Tooltip>(null);
  const [hoveredId, setHoveredId] = useState<number | null>(null);
  const hoveredRef = useRef<number | null>(null);
  const [focusedChannelId, setFocusedChannelId] = useState<number | null>(null);
  const nodesRef = useRef<BubbleNode[]>([]);
  nodesRef.current = layout.nodes;

  const easeOutCubic = (t: number): number => 1 - Math.pow(1 - t, 3);

  const drawMinimap = useCallback(() => {
    const minimap = minimapRef.current;
    if (!minimap || layout.nodes.length === 0) return;
    const mctx = minimap.getContext("2d");
    if (!mctx) return;
    const dpr = window.devicePixelRatio || 1;
    mctx.clearRect(0, 0, minimap.width, minimap.height);
    mctx.fillStyle = "rgba(10,10,11,0.8)";
    mctx.fillRect(0, 0, minimap.width, minimap.height);
    const mw = MINIMAP_W * dpr;
    const mh = MINIMAP_H * dpr;
    const pad = 8 * dpr;
    const scaleX = (mw - pad * 2) / layout.width;
    const scaleY = (mh - pad * 2) / layout.height;
    const sc = Math.min(scaleX, scaleY);
    const offX = pad + (mw - pad * 2 - layout.width * sc) / 2;
    const offY = pad + (mh - pad * 2 - layout.height * sc) / 2;
    for (const node of layout.nodes) {
      const mx = offX + node.x * sc;
      const my = offY + node.y * sc;
      const mr = Math.max(node.r * sc, 1.5 * dpr);
      mctx.beginPath();
      mctx.arc(mx, my, mr, 0, Math.PI * 2);
      mctx.fillStyle = node.color + "88";
      mctx.fill();
    }
    const wrap = wrapRef.current;
    if (wrap) {
      const { zoom, panX, panY } = stateRef.current;
      const W = wrap.clientWidth;
      const H = wrap.clientHeight;
      const worldLeft = -panX / zoom;
      const worldTop = -panY / zoom;
      const worldRight = (W - panX) / zoom;
      const worldBottom = (H - panY) / zoom;
      const rx = offX + worldLeft * sc;
      const ry = offY + worldTop * sc;
      const rw = (worldRight - worldLeft) * sc;
      const rh = (worldBottom - worldTop) * sc;
      mctx.strokeStyle = "#00ff88";
      mctx.lineWidth = 1.5 * dpr;
      mctx.strokeRect(rx, ry, rw, rh);
      mctx.fillStyle = "rgba(0,255,136,0.05)";
      mctx.fillRect(rx, ry, rw, rh);
    }
    mctx.strokeStyle = "#00ff88";
    mctx.lineWidth = 1 * dpr;
    mctx.strokeRect(0, 0, minimap.width, minimap.height);
  }, [layout]);

  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const { zoom, panX, panY } = stateRef.current;
    const dpr = window.devicePixelRatio || 1;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();
    ctx.fillStyle = "#0a0a0b";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#111111";
    ctx.lineWidth = 1;
    const gridSize = 40 * zoom * dpr;
    const startX = ((panX * dpr) % gridSize + gridSize) % gridSize;
    const startY = ((panY * dpr) % gridSize + gridSize) % gridSize;
    for (let gx = startX; gx < canvas.width; gx += gridSize) {
      ctx.beginPath();
      ctx.moveTo(gx, 0);
      ctx.lineTo(gx, canvas.height);
      ctx.stroke();
    }
    for (let gy = startY; gy < canvas.height; gy += gridSize) {
      ctx.beginPath();
      ctx.moveTo(0, gy);
      ctx.lineTo(canvas.width, gy);
      ctx.stroke();
    }
    ctx.restore();
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.translate(panX, panY);
    ctx.scale(zoom, zoom);
    const isFiltering = filterCategory !== "" || filterQuery.trim() !== "";
    const queryLower = filterQuery.trim().toLowerCase();
    for (const node of layout.nodes) {
      const isMatch =
        !isFiltering ||
        ((filterCategory === "" || node.category === filterCategory) &&
          (queryLower === "" ||
            (node.ch.title?.toLowerCase().includes(queryLower) ?? false) ||
            (node.ch.username?.toLowerCase().includes(queryLower) ?? false)));
      const isHovered = hoveredRef.current === node.ch.id;
      const isFocused = focusedChannelId === node.ch.id;
      const alpha = isFiltering && !isMatch ? 0.08 : 1;
      const scale = isHovered || isFocused ? 1.18 : 1;
      const r = node.r * scale;
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.translate(node.x, node.y);
      if (isHovered || isFocused || (isFiltering && isMatch)) {
        ctx.shadowColor = node.color;
        ctx.shadowBlur = isHovered || isFocused ? 22 : 10;
      }
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, Math.PI * 2);
      const grad = ctx.createRadialGradient(-r * 0.3, -r * 0.3, 0, 0, 0, r);
      grad.addColorStop(0, node.color + "cc");
      grad.addColorStop(1, node.color + "55");
      ctx.fillStyle = grad;
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.strokeStyle = node.color + (isHovered || isFocused ? "ff" : "88");
      ctx.lineWidth = isHovered || isFocused ? 2 / zoom : 1 / zoom;
      ctx.stroke();
      if (zoom > 0.6 && r * zoom > 12) {
        ctx.fillStyle = isHovered ? "#fff" : node.color + "ee";
        ctx.font = `bold ${Math.min(r * 0.5, 11) / zoom}px 'Geist Sans', sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        const label =
          node.ch.username
            ? `@${node.ch.username.slice(0, 8)}`
            : (node.ch.title ?? "").slice(0, 8);
        ctx.fillText(label, 0, 0);
      }
      ctx.restore();
    }
    if (zoom > 0.4) {
      for (const lbl of layout.labels) {
        ctx.save();
        ctx.globalAlpha = 0.7;
        ctx.fillStyle = lbl.color;
        ctx.font = `bold ${14 / zoom}px 'Geist Sans', sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        ctx.shadowColor = lbl.color;
        ctx.shadowBlur = 8;
        ctx.fillText(`${lbl.icon} ${lbl.category}`, lbl.cx, lbl.cy);
        ctx.restore();
      }
    }
    ctx.restore();
    drawMinimap();
  }, [layout, filterCategory, filterQuery, focusedChannelId, drawMinimap]);

  const animLoop = useCallback(() => {
    const tgt = animTargetRef.current;
    if (tgt) {
      const elapsed = performance.now() - tgt.startTime;
      const t = Math.min(elapsed / tgt.duration, 1);
      const e = easeOutCubic(t);
      stateRef.current.zoom =
        tgt.startZoom + (tgt.zoom - tgt.startZoom) * e;
      stateRef.current.panX =
        tgt.startPanX + (tgt.panX - tgt.startPanX) * e;
      stateRef.current.panY =
        tgt.startPanY + (tgt.panY - tgt.startPanY) * e;
      if (t >= 1) animTargetRef.current = null;
    }
    drawCanvas();
    animRef.current = requestAnimationFrame(animLoop);
  }, [drawCanvas]);

  useEffect(() => {
    animRef.current = requestAnimationFrame(animLoop);
    return () => cancelAnimationFrame(animRef.current);
  }, [animLoop]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const dpr = window.devicePixelRatio || 1;
    const W = wrap.clientWidth;
    const H = wrap.clientHeight;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;
    const scaleX = W / layout.width;
    const scaleY = H / layout.height;
    const fitZoom = Math.min(scaleX, scaleY) * 0.85;
    const panX = (W - layout.width * fitZoom) / 2;
    const panY = (H - layout.height * fitZoom) / 2;
    stateRef.current.zoom = fitZoom;
    stateRef.current.panX = panX;
    stateRef.current.panY = panY;
    initViewRef.current = { zoom: fitZoom, panX, panY };
    const minimap = minimapRef.current;
    if (minimap) {
      minimap.width = MINIMAP_W * dpr;
      minimap.height = MINIMAP_H * dpr;
      minimap.style.width = `${MINIMAP_W}px`;
      minimap.style.height = `${MINIMAP_H}px`;
    }
  }, [layout]);

  const hitTest = useCallback(
    (cx: number, cy: number): BubbleNode | null => {
      const { zoom, panX, panY } = stateRef.current;
      const wx = (cx - panX) / zoom;
      const wy = (cy - panY) / zoom;
      for (let i = nodesRef.current.length - 1; i >= 0; i--) {
        const n = nodesRef.current[i];
        const dx = wx - n.x,
          dy = wy - n.y;
        if (dx * dx + dy * dy <= n.r * n.r * 1.3) return n;
      }
      return null;
    },
    []
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return;
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      if (stateRef.current.dragging) {
        const dx = cx - stateRef.current.lastX;
        const dy = cy - stateRef.current.lastY;
        stateRef.current.panX += dx;
        stateRef.current.panY += dy;
        stateRef.current.lastX = cx;
        stateRef.current.lastY = cy;
        setTooltip(null);
        return;
      }
      const hit = hitTest(cx, cy);
      if (hit) {
        if (hoveredRef.current !== hit.ch.id) {
          hoveredRef.current = hit.ch.id;
          setHoveredId(hit.ch.id);
          const { zoom, panX, panY } = stateRef.current;
          setTooltip({
            x: hit.x * zoom + panX,
            y: hit.y * zoom + panY - hit.r * zoom - 12,
            ch: hit.ch,
          });
        }
        (canvasRef.current as HTMLCanvasElement).style.cursor = "pointer";
      } else {
        if (hoveredRef.current !== null) {
          hoveredRef.current = null;
          setHoveredId(null);
          setTooltip(null);
        }
        (canvasRef.current as HTMLCanvasElement).style.cursor = "grab";
      }
    },
    [hitTest]
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      stateRef.current.dragging = true;
      stateRef.current.lastX = e.clientX;
      stateRef.current.lastY = e.clientY;
      (canvasRef.current as HTMLCanvasElement).style.cursor = "grabbing";
    },
    []
  );

  const handleMouseUp = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (stateRef.current.dragging) {
        const dist = Math.hypot(
          e.clientX - stateRef.current.lastX,
          e.clientY - stateRef.current.lastY
        );
        stateRef.current.dragging = false;
        (canvasRef.current as HTMLCanvasElement).style.cursor = "grab";
        if (dist > 4) return;
      }
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return;
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      const hit = hitTest(cx, cy);
      if (hit) {
        setFocusedChannelId(hit.ch.id === focusedChannelId ? null : hit.ch.id);
        onSelect(hit.ch.id === focusedChannelId ? null : hit.ch);
      } else {
        setFocusedChannelId(null);
        onSelect(null);
      }
    },
    [hitTest, focusedChannelId, onSelect]
  );

  const handleWheel = useCallback((e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const delta = e.deltaY > 0 ? 0.85 : 1.18;
    const { zoom, panX, panY } = stateRef.current;
    const newZoom = Math.max(0.08, Math.min(6, zoom * delta));
    stateRef.current.panX = cx - (cx - panX) * (newZoom / zoom);
    stateRef.current.panY = cy - (cy - panY) * (newZoom / zoom);
    stateRef.current.zoom = newZoom;
  }, []);

  const handleReset = useCallback(() => {
    const { zoom, panX, panY } = initViewRef.current;
    animTargetRef.current = {
      zoom,
      panX,
      panY,
      startZoom: stateRef.current.zoom,
      startPanX: stateRef.current.panX,
      startPanY: stateRef.current.panY,
      startTime: performance.now(),
      duration: 600,
    };
  }, []);

  return (
    <div
      ref={wrapRef}
      style={{ position: "relative", width: "100%", height: 600, background: "#0a0a0b", borderRadius: 12, overflow: "hidden" }}
    >
      <canvas
        ref={canvasRef}
        style={{ display: "block", cursor: "grab" }}
        onMouseMove={handleMouseMove}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseLeave={() => {
          stateRef.current.dragging = false;
          hoveredRef.current = null;
          setHoveredId(null);
          setTooltip(null);
        }}
        onWheel={handleWheel}
      />
      {tooltip && (
        <div
          style={{
            position: "absolute",
            left: tooltip.x,
            top: tooltip.y,
            transform: "translate(-50%, -100%)",
            background: "rgba(10,10,11,0.95)",
            border: "1px solid var(--accent)",
            borderRadius: 8,
            padding: "8px 12px",
            pointerEvents: "none",
            zIndex: 10,
            minWidth: 160,
            boxShadow: "0 0 12px rgba(0,255,136,0.25)",
          }}
        >
          <div style={{ fontWeight: 700, fontSize: 13, color: "var(--accent)" }}>
            {tooltip.ch.title ?? `@${tooltip.ch.username}`}
          </div>
          {tooltip.ch.username && (
            <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "monospace" }}>
              @{tooltip.ch.username}
            </div>
          )}
          <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
            {formatNumber(tooltip.ch.member_count)} подписчиков
          </div>
          {tooltip.ch.category && (
            <div style={{ fontSize: 11, color: getCategoryColor(tooltip.ch.category), marginTop: 2 }}>
              {tooltip.ch.category}
            </div>
          )}
        </div>
      )}
      <canvas
        ref={minimapRef}
        style={{
          position: "absolute",
          bottom: 16,
          left: 16,
          borderRadius: 6,
          opacity: 0.85,
        }}
      />
      <button
        type="button"
        onClick={handleReset}
        style={{
          position: "absolute",
          top: 16,
          right: 16,
          all: "unset",
          cursor: "pointer",
          background: "rgba(10,10,11,0.8)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: "6px 10px",
          color: "var(--muted)",
          fontSize: 12,
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <RotateCcw size={12} /> Сброс вида
      </button>
      <div
        style={{
          position: "absolute",
          bottom: 16,
          right: 16,
          fontSize: 11,
          color: "var(--muted)",
          background: "rgba(10,10,11,0.6)",
          padding: "4px 8px",
          borderRadius: 6,
          pointerEvents: "none",
        }}
      >
        Колёсико — зум · Перетащить — панорама · Клик — детали
      </div>
    </div>
  );
}

// ── Globe 3D components ───────────────────────────────────────────────────────

// Continent outlines approximated as latitude/longitude line segments
// This is a simplified set of continent bounding arcs — no external GeoJSON needed
function buildContinentLines(): Float32Array[] {
  const lines: Array<Array<[number, number]>> = [
    // Europe rough outline
    [[36, -9],[44, -8],[47, 2],[51, 2],[54, 10],[56, 24],[64, 26],[70, 28],[71, 25],[65, 14],[60, 5],[58, -6],[52, -10],[44, -8]],
    // Africa rough outline
    [[37, 10],[37, 36],[11, 42],[-11, 40],[-34, 26],[-34, 18],[-17, 12],[-1, 8],[5, 2],[4, 9],[12, 15],[23, 12],[32, 22],[37, 10]],
    // Asia rough outline
    [[36, 27],[42, 40],[44, 50],[46, 60],[55, 60],[62, 68],[72, 68],[72, 142],[60, 142],[52, 140],[42, 132],[36, 130],[22, 114],[1, 104],[-8, 115],[-9, 122],[-8, 140],[1, 136],[10, 125],[18, 121],[22, 120],[22, 109],[18, 106],[10, 98],[8, 78],[20, 73],[20, 58],[12, 44],[12, 43],[20, 37],[28, 35],[36, 27]],
    // North America rough outline
    [[72, -73],[66, -82],[60, -95],[60, -138],[66, -166],[72, -160],[72, -141],[60, -133],[50, -126],[48, -122],[42, -124],[38, -122],[30, -116],[24, -110],[22, -100],[18, -92],[18, -85],[26, -80],[36, -75],[44, -66],[48, -53],[52, -56],[60, -64],[66, -64],[72, -73]],
    // South America rough outline
    [[12, -72],[10, -62],[4, -52],[0, -50],[-4, -36],[-10, -37],[-22, -42],[-34, -52],[-56, -64],[-54, -68],[-42, -72],[-28, -70],[-18, -72],[-6, -78],[0, -80],[6, -78],[10, -74],[12, -72]],
    // Australia rough outline
    [[-14, 128],[-12, 136],[-12, 142],[-18, 148],[-28, 154],[-36, 150],[-38, 146],[-38, 141],[-32, 136],[-28, 114],[-22, 112],[-18, 122],[-14, 128]],
  ];

  return lines.map((line) => {
    const pts: number[] = [];
    for (let i = 0; i < line.length; i++) {
      const [lat, lng] = line[i];
      const [x, y, z] = latLngToVec3(lat, lng, 1.002);
      pts.push(x, y, z);
      if (i > 0 && i < line.length - 1) {
        pts.push(x, y, z); // duplicate interior points for line segments
      }
    }
    return new Float32Array(pts);
  });
}

const CONTINENT_LINES = buildContinentLines();

// Continent line mesh
function ContinentLines() {
  const lineObjects = useMemo(() => {
    return CONTINENT_LINES.map((pts) => {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(pts, 3));
      const mat = new THREE.LineBasicMaterial({ color: "#1e3a2a", transparent: true, opacity: 0.6 });
      return new THREE.LineSegments(geo, mat);
    });
  }, []);

  // Dispose geometries and materials on unmount
  useEffect(() => {
    return () => {
      for (const obj of lineObjects) {
        obj.geometry.dispose();
        (obj.material as THREE.Material).dispose();
      }
    };
  }, [lineObjects]);

  return (
    <>
      {lineObjects.map((obj, i) => (
        <primitive key={i} object={obj} />
      ))}
    </>
  );
}

// Atmosphere glow sphere
function Atmosphere() {
  const meshRef = useRef<THREE.Mesh>(null);
  const matRef = useRef<THREE.ShaderMaterial>(null);

  const vertexShader = `
    varying vec3 vNormal;
    void main() {
      vNormal = normalize(normalMatrix * normal);
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `;

  const fragmentShader = `
    varying vec3 vNormal;
    void main() {
      float intensity = pow(0.65 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 3.0);
      gl_FragColor = vec4(0.0, 1.0, 0.533, 1.0) * intensity;
    }
  `;

  useFrame(() => {
    if (matRef.current) {
      matRef.current.needsUpdate = false;
    }
  });

  return (
    <mesh ref={meshRef} scale={[1.12, 1.12, 1.12]}>
      <sphereGeometry args={[1, 48, 48]} />
      <shaderMaterial
        ref={matRef}
        vertexShader={vertexShader}
        fragmentShader={fragmentShader}
        side={THREE.BackSide}
        blending={THREE.AdditiveBlending}
        transparent
      />
    </mesh>
  );
}

// Grid lines on globe
function GlobeGrid() {
  const linesData = useMemo(() => {
    const result: Float32Array[] = [];
    // Latitude lines every 30 degrees
    for (let lat = -60; lat <= 60; lat += 30) {
      const pts: number[] = [];
      for (let lng = -180; lng <= 180; lng += 4) {
        const [x, y, z] = latLngToVec3(lat, lng, 1.001);
        pts.push(x, y, z);
        if (lng > -180 && lng < 180) pts.push(x, y, z);
      }
      result.push(new Float32Array(pts));
    }
    // Longitude lines every 30 degrees
    for (let lng = -180; lng < 180; lng += 30) {
      const pts: number[] = [];
      for (let lat = -90; lat <= 90; lat += 4) {
        const [x, y, z] = latLngToVec3(lat, lng, 1.001);
        pts.push(x, y, z);
        if (lat > -90 && lat < 90) pts.push(x, y, z);
      }
      result.push(new Float32Array(pts));
    }
    return result;
  }, []);

  const lineObjects = useMemo(() => {
    return linesData.map((pts) => {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(pts, 3));
      const mat = new THREE.LineBasicMaterial({ color: "#0d1a12", transparent: true, opacity: 0.35 });
      return new THREE.LineSegments(geo, mat);
    });
  }, [linesData]);

  // Dispose geometries and materials on unmount
  useEffect(() => {
    return () => {
      for (const obj of lineObjects) {
        obj.geometry.dispose();
        (obj.material as THREE.Material).dispose();
      }
    };
  }, [lineObjects]);

  return (
    <>
      {lineObjects.map((obj, i) => (
        <primitive key={i} object={obj} />
      ))}
    </>
  );
}

// Instanced channel points
type GlobePointsProps = {
  channels: ChannelMapEntry[];
  filterCategory: string;
  filterQuery: string;
  hoveredId: number | null;
  onHover: (ch: ChannelMapEntry | null) => void;
  onSelect: (ch: ChannelMapEntry | null) => void;
};

function GlobePoints({
  channels,
  filterCategory,
  filterQuery,
  onHover,
  onSelect,
}: GlobePointsProps) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const { camera, gl } = useThree();

  // Compute per-instance data ONCE (or when channels/filters change).
  // Issue 1 fix: use real lat/lng from channel data when available; fall back to
  // a deterministic hash-based position only if both fields are missing/zero.
  const positioned = useMemo(() => {
    const queryLower = filterQuery.trim().toLowerCase();
    return channels.map((ch, idx) => {
      let lat: number;
      let lng: number;
      if (ch.lat != null && ch.lng != null && (ch.lat !== 0 || ch.lng !== 0)) {
        lat = ch.lat;
        lng = ch.lng;
      } else {
        // Fallback: deterministic pseudo-random spread from channel id
        const seed = (ch.id * 2654435761) >>> 0;
        lat = (seed % 160) - 80;
        lng = ((seed >> 8) % 360) - 180;
      }
      const color = new THREE.Color(getCategoryColor(ch.category));
      const members = ch.member_count ?? 0;
      const baseScale = 0.5 + Math.min(Math.log1p(members) / Math.log1p(10_000_000) * 3.5, 3.5);
      const isFiltering = filterCategory !== "" || queryLower !== "";
      const isMatch =
        !isFiltering ||
        ((filterCategory === "" || ch.category === filterCategory) &&
          (queryLower === "" ||
            (ch.title?.toLowerCase().includes(queryLower) ?? false) ||
            (ch.username?.toLowerCase().includes(queryLower) ?? false)));
      // Pre-compute the world-space position once
      const pos = latLngToVec3(lat, lng, 1.018);
      return { ch, pos, color, baseScale, isMatch, idx };
    });
  }, [channels, filterCategory, filterQuery]);

  // Issue 2 fix: set matrices ONCE when positioned data changes, not every frame.
  // Only matrix positions and base colours need a full rebuild.
  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    const dummy = new THREE.Object3D();
    positioned.forEach(({ pos, baseScale, isMatch, color }, i) => {
      dummy.position.set(pos[0], pos[1], pos[2]);
      dummy.lookAt(0, 0, 0);
      const s = baseScale * (isMatch ? 1 : 0.3);
      dummy.scale.set(s, s, s);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      const c = isMatch ? color : new THREE.Color("#333333");
      mesh.setColorAt(i, c);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [positioned]);

  // Issue 2 fix: in useFrame only apply a subtle pulse via scale — no lat/lng
  // recalculation, no new Object3D allocation per point per frame.
  // We re-use a single dummy Object3D and only write updated scale matrices.
  const frameDummy = useMemo(() => new THREE.Object3D(), []);
  useFrame((state) => {
    const mesh = meshRef.current;
    if (!mesh || positioned.length === 0) return;
    const t = state.clock.elapsedTime;
    for (let i = 0; i < positioned.length; i++) {
      const { pos, baseScale, isMatch, idx } = positioned[i];
      frameDummy.position.set(pos[0], pos[1], pos[2]);
      frameDummy.lookAt(0, 0, 0);
      const pulse = 1 + Math.sin(t * 2 + idx * 0.3) * 0.15;
      const s = baseScale * (isMatch ? 1 : 0.25) * pulse;
      frameDummy.scale.set(s, s, s);
      frameDummy.updateMatrix();
      mesh.setMatrixAt(i, frameDummy.matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
  });

  // Raycaster for hover/click
  const raycaster = useMemo(() => new THREE.Raycaster(), []);

  const handlePointerMove = useCallback(
    (e: { clientX: number; clientY: number }) => {
      const canvas = gl.domElement;
      const rect = canvas.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      const y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(new THREE.Vector2(x, y), camera);
      const mesh = meshRef.current;
      if (!mesh) return;
      const hits = raycaster.intersectObject(mesh);
      if (hits.length > 0 && hits[0].instanceId != null) {
        const idx = hits[0].instanceId;
        if (idx < positioned.length) {
          onHover(positioned[idx].ch);
        }
      } else {
        onHover(null);
      }
    },
    [camera, gl, raycaster, positioned, onHover]
  );

  const handlePointerDown = useCallback(
    (e: { clientX: number; clientY: number }) => {
      const canvas = gl.domElement;
      const rect = canvas.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      const y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(new THREE.Vector2(x, y), camera);
      const mesh = meshRef.current;
      if (!mesh) return;
      const hits = raycaster.intersectObject(mesh);
      if (hits.length > 0 && hits[0].instanceId != null) {
        const idx = hits[0].instanceId;
        if (idx < positioned.length) {
          onSelect(positioned[idx].ch);
        }
      }
    },
    [camera, gl, raycaster, positioned, onSelect]
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

  if (positioned.length === 0) return null;

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, positioned.length]} frustumCulled>
      <sphereGeometry args={[0.012, 6, 6]} />
      <meshBasicMaterial vertexColors toneMapped={false} />
    </instancedMesh>
  );
}

// Auto-rotating globe wrapper
function GlobeScene({
  channels,
  filterCategory,
  filterQuery,
  hoveredChannel,
  onHover,
  onSelect,
}: {
  channels: ChannelMapEntry[];
  filterCategory: string;
  filterQuery: string;
  hoveredChannel: ChannelMapEntry | null;
  onHover: (ch: ChannelMapEntry | null) => void;
  onSelect: (ch: ChannelMapEntry | null) => void;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const isInteracting = useRef(false);

  useFrame((_, delta) => {
    if (!isInteracting.current && groupRef.current) {
      groupRef.current.rotation.y += delta * 0.08;
    }
  });

  return (
    <group ref={groupRef}>
      {/* Globe base */}
      <mesh>
        <sphereGeometry args={[1, 64, 64]} />
        <meshPhongMaterial
          color="#060d0a"
          emissive="#021008"
          specular="#00ff88"
          shininess={8}
        />
      </mesh>

      {/* Grid lines */}
      <GlobeGrid />

      {/* Continent outlines */}
      <ContinentLines />

      {/* Atmosphere */}
      <Atmosphere />

      {/* Channel points */}
      <GlobePoints
        channels={channels}
        filterCategory={filterCategory}
        filterQuery={filterQuery}
        hoveredId={hoveredChannel?.id ?? null}
        onHover={onHover}
        onSelect={onSelect}
      />
    </group>
  );
}

// Tooltip overlay for hovered channel on globe
function GlobeTooltip({ ch }: { ch: ChannelMapEntry | null }) {
  if (!ch) return null;
  return (
    <div
      style={{
        position: "absolute",
        top: 80,
        left: "50%",
        transform: "translateX(-50%)",
        background: "rgba(10,10,11,0.92)",
        border: `1px solid ${getCategoryColor(ch.category)}`,
        borderRadius: 10,
        padding: "10px 16px",
        pointerEvents: "none",
        zIndex: 20,
        minWidth: 200,
        boxShadow: `0 0 20px ${getCategoryColor(ch.category)}44`,
        backdropFilter: "blur(8px)",
      }}
    >
      <div style={{ fontWeight: 700, fontSize: 14, color: getCategoryColor(ch.category) }}>
        {ch.title ?? `@${ch.username}`}
      </div>
      {ch.username && (
        <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "monospace", marginTop: 2 }}>
          @{ch.username}
        </div>
      )}
      <div style={{ display: "flex", gap: 12, marginTop: 6, fontSize: 12, color: "var(--text-secondary)" }}>
        <span><Users size={11} style={{ verticalAlign: "middle", marginRight: 3 }} />{formatNumber(ch.member_count)}</span>
        {ch.engagement_rate != null && (
          <span style={{ color: erColor(ch.engagement_rate) }}>
            <TrendingUp size={11} style={{ verticalAlign: "middle", marginRight: 3 }} />{erLabel(ch.engagement_rate)}
          </span>
        )}
      </div>
    </div>
  );
}

// Main Globe view wrapper
function GlobeView({
  channels,
  filterCategory,
  filterQuery,
  onSelect,
}: {
  channels: ChannelMapEntry[];
  filterCategory: string;
  filterQuery: string;
  onSelect: (ch: ChannelMapEntry | null) => void;
}) {
  const [hoveredChannel, setHoveredChannel] = useState<ChannelMapEntry | null>(null);

  return (
    <div style={{ position: "relative", width: "100%", height: 600, borderRadius: 12, overflow: "hidden", background: "#020705" }}>
      <Canvas
        camera={{ position: [0, 0, 2.8], fov: 45 }}
        gl={{ antialias: true, alpha: false }}
        style={{ background: "#020705" }}
      >
        <Suspense fallback={null}>
          <ambientLight intensity={0.3} />
          <directionalLight position={[5, 3, 5]} intensity={0.8} color="#ffffff" />
          <pointLight position={[-5, -3, -5]} intensity={0.2} color="#00ff88" />

          <GlobeScene
            channels={channels}
            filterCategory={filterCategory}
            filterQuery={filterQuery}
            hoveredChannel={hoveredChannel}
            onHover={setHoveredChannel}
            onSelect={onSelect}
          />

          <OrbitControls
            enablePan={false}
            enableZoom
            zoomSpeed={0.6}
            rotateSpeed={0.5}
            minDistance={1.5}
            maxDistance={5}
            autoRotate={false}
          />
        </Suspense>
      </Canvas>

      <GlobeTooltip ch={hoveredChannel} />

      {/* Info overlay */}
      <div
        style={{
          position: "absolute",
          bottom: 16,
          right: 16,
          fontSize: 11,
          color: "var(--muted)",
          background: "rgba(10,10,11,0.7)",
          padding: "4px 8px",
          borderRadius: 6,
          pointerEvents: "none",
        }}
      >
        Потяните — вращение · Скролл — зум · Клик — детали
      </div>

      <div
        style={{
          position: "absolute",
          top: 16,
          left: 16,
          fontSize: 11,
          color: "var(--accent)",
          background: "rgba(10,10,11,0.7)",
          padding: "4px 10px",
          borderRadius: 6,
          pointerEvents: "none",
          fontFamily: "monospace",
          border: "1px solid var(--accent-glow)",
        }}
      >
        {channels.length} каналов на глобусе
      </div>
    </div>
  );
}

// ── small UI components ───────────────────────────────────────────────────────

function MetricCard({
  label,
  value,
  sub,
  accent,
  icon,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="metric-card">
      <div className="metric-label">
        {icon}
        {label}
      </div>
      <div
        className="metric-value"
        style={accent ? { color: accent } : undefined}
      >
        {value}
      </div>
      {sub && <div className="metric-sub">{sub}</div>}
    </div>
  );
}

function CategoryCard({
  name,
  count,
  avgEr,
  totalReach,
  selected,
  onClick,
}: {
  name: string;
  count: number;
  avgEr: number | null;
  totalReach: number;
  selected: boolean;
  onClick: () => void;
}) {
  const meta = getCategoryMeta(name);
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        all: "unset",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "14px 16px",
        borderRadius: 12,
        background: selected ? "var(--accent-glow)" : "var(--surface-2)",
        border: selected
          ? `1px solid ${meta.color}`
          : "1px solid var(--border)",
        transition: "all 200ms cubic-bezier(0.16, 1, 0.3, 1)",
        boxShadow: selected ? `0 0 12px ${meta.color}33` : "none",
        textAlign: "left",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ fontSize: 16 }}>{meta.icon}</span>
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: selected ? meta.color : "var(--text)",
          }}
        >
          {name}
        </span>
      </div>
      <div
        style={{
          fontSize: 11,
          color: "var(--muted)",
          fontFamily: "'JetBrains Mono Variable', monospace",
        }}
      >
        {count} каналов
      </div>
      {totalReach > 0 && (
        <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
          {formatNumber(totalReach)} охват
        </div>
      )}
      {avgEr != null && (
        <div style={{ fontSize: 11, color: erColor(avgEr) }}>
          ER {erLabel(avgEr)}
        </div>
      )}
    </button>
  );
}

function ChannelCard({
  ch,
  highlightRe,
  index,
  selected,
  onToggleSelect,
}: {
  ch: ChannelMapEntry;
  highlightRe: RegExp | null;
  index: number;
  selected?: boolean;
  onToggleSelect?: () => void;
}) {
  const meta = getCategoryMeta(ch.category);
  const initials = getInitials(ch.title, ch.username);

  return (
    <motion.div
      className="card"
      variants={cardVariants}
      initial="hidden"
      animate="visible"
      custom={index}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        outline: selected ? "1px solid var(--accent)" : "none",
        background: selected ? "rgba(0,255,136,0.04)" : undefined,
        position: "relative",
      }}
    >
      {onToggleSelect && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onToggleSelect(); }}
          style={{
            all: "unset",
            cursor: "pointer",
            position: "absolute",
            top: 10,
            right: 10,
            color: selected ? "var(--accent)" : "var(--muted)",
            display: "flex",
            zIndex: 1,
          }}
        >
          {selected ? <CheckSquare size={16} /> : <Square size={16} />}
        </button>
      )}
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: 10,
            background: `linear-gradient(135deg, ${meta.color}33, ${meta.color}11)`,
            border: `1px solid ${meta.color}44`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 16,
            fontWeight: 700,
            color: meta.color,
            flexShrink: 0,
          }}
        >
          {initials}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontWeight: 600,
              fontSize: 14,
              marginBottom: 2,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
            dangerouslySetInnerHTML={{
              __html: highlight(ch.title ?? ch.username ?? String(ch.id), highlightRe),
            }}
          />
          {ch.username && (
            <a
              href={`https://t.me/${ch.username}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                fontSize: 12,
                color: "var(--accent)",
                fontFamily: "'JetBrains Mono Variable', monospace",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              @{ch.username}
              <ExternalLink size={10} />
            </a>
          )}
        </div>
      </div>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          fontSize: 11,
          color: "var(--text-secondary)",
        }}
      >
        {ch.category && (
          <span
            className="pill"
            style={{
              background: `${meta.color}18`,
              color: meta.color,
            }}
          >
            {meta.icon} {ch.category}
          </span>
        )}
        {ch.language && (
          <span className="pill">{langFlag(ch.language)} {ch.language.toUpperCase()}</span>
        )}
        {ch.has_comments && (
          <span
            className="pill"
            style={{
              background: "var(--accent-glow)",
              color: "var(--accent)",
            }}
          >
            <MessageCircle size={10} /> коммент.
          </span>
        )}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 8,
          paddingTop: 8,
          borderTop: "1px solid var(--border)",
        }}
      >
        <div>
          <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 2 }}>
            Подписчики
          </div>
          <div
            style={{
              fontSize: 14,
              fontWeight: 700,
              fontFamily: "'JetBrains Mono Variable', monospace",
            }}
          >
            {formatNumber(ch.member_count)}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 2 }}>
            ER
          </div>
          <div
            style={{
              fontSize: 14,
              fontWeight: 700,
              color: erColor(ch.engagement_rate),
              fontFamily: "'JetBrains Mono Variable', monospace",
            }}
          >
            {erLabel(ch.engagement_rate)}
          </div>
        </div>
      </div>
    </motion.div>
  );
}

// Channel detail panel (slides in from right)
function ChannelDetailPanel({
  ch,
  onClose,
  onAddToFarm,
  onAddToCampaign,
}: {
  ch: ChannelMapEntry;
  onClose: () => void;
  onAddToFarm?: () => void;
  onAddToCampaign?: () => void;
}) {
  const meta = getCategoryMeta(ch.category);
  const initials = getInitials(ch.title, ch.username);
  const color = getCategoryColor(ch.category);

  return (
    <motion.div
      initial={{ x: "100%", opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: "100%", opacity: 0 }}
      transition={{ type: "spring", damping: 28, stiffness: 280 }}
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: 360,
        background: "rgba(10,10,11,0.97)",
        borderLeft: `1px solid ${color}44`,
        zIndex: 200,
        display: "flex",
        flexDirection: "column",
        boxShadow: `-20px 0 60px ${color}22`,
        backdropFilter: "blur(12px)",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "20px 20px 16px",
          borderBottom: `1px solid ${color}22`,
          display: "flex",
          alignItems: "flex-start",
          gap: 12,
        }}
      >
        <div
          style={{
            width: 56,
            height: 56,
            borderRadius: 14,
            background: `linear-gradient(135deg, ${color}44, ${color}11)`,
            border: `1px solid ${color}66`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 20,
            fontWeight: 800,
            color,
            flexShrink: 0,
          }}
        >
          {initials}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 16, lineHeight: 1.3 }}>
            {ch.title ?? `@${ch.username}`}
          </div>
          {ch.username && (
            <a
              href={`https://t.me/${ch.username}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                fontSize: 12,
                color: "var(--accent)",
                fontFamily: "monospace",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                marginTop: 2,
              }}
            >
              @{ch.username}
              <ExternalLink size={10} />
            </a>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          style={{
            all: "unset",
            cursor: "pointer",
            color: "var(--muted)",
            padding: 4,
            borderRadius: 6,
            lineHeight: 1,
          }}
        >
          <X size={18} />
        </button>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflowY: "auto", padding: "16px 20px" }}>
        {/* Badges */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 16 }}>
          {ch.category && (
            <span
              className="pill"
              style={{ background: `${color}22`, color, fontSize: 12 }}
            >
              {meta.icon} {ch.category}
            </span>
          )}
          {ch.language && (
            <span className="pill" style={{ fontSize: 12 }}>
              {langFlag(ch.language)} {ch.language.toUpperCase()}
            </span>
          )}
          {ch.verified && (
            <span
              className="pill"
              style={{
                background: "rgba(68,136,255,0.15)",
                color: "#4488ff",
                fontSize: 12,
              }}
            >
              Верифицирован
            </span>
          )}
          {ch.has_comments && (
            <span
              className="pill"
              style={{
                background: "var(--accent-glow)",
                color: "var(--accent)",
                fontSize: 12,
              }}
            >
              <MessageCircle size={10} /> Комментарии
            </span>
          )}
        </div>

        {/* Stats grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 10,
            marginBottom: 16,
          }}
        >
          {[
            { label: "Подписчики", value: formatNumber(ch.member_count) },
            {
              label: "Вовлечённость",
              value: erLabel(ch.engagement_rate),
              color: erColor(ch.engagement_rate),
            },
            {
              label: "Охват поста",
              value: formatNumber(ch.avg_post_reach),
            },
            {
              label: "Коммент/пост",
              value: formatNumber(ch.avg_comments_per_post),
            },
            {
              label: "Постов в день",
              value: ch.post_frequency_daily != null
                ? ch.post_frequency_daily.toFixed(1)
                : "—",
            },
          ].map(({ label, value, color: c }) => (
            <div
              key={label}
              style={{
                background: "var(--surface-2)",
                borderRadius: 8,
                padding: "10px 12px",
                border: "1px solid var(--border)",
              }}
            >
              <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 4 }}>
                {label}
              </div>
              <div
                style={{
                  fontSize: 16,
                  fontWeight: 700,
                  fontFamily: "monospace",
                  color: c ?? "var(--text)",
                }}
              >
                {value}
              </div>
            </div>
          ))}
        </div>

        {/* Description */}
        {ch.description && (
          <div
            style={{
              background: "var(--surface-2)",
              borderRadius: 8,
              padding: "12px 14px",
              border: "1px solid var(--border)",
              fontSize: 13,
              color: "var(--text-secondary)",
              lineHeight: 1.6,
              marginBottom: 16,
            }}
          >
            {ch.description}
          </div>
        )}

        {/* Last indexed */}
        {ch.last_indexed_at && (
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 16 }}>
            Проиндексирован:{" "}
            {new Date(ch.last_indexed_at).toLocaleDateString("ru-RU")}
          </div>
        )}
      </div>

      {/* Footer */}
      <div
        style={{
          padding: "12px 20px",
          borderTop: `1px solid ${color}22`,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        {/* Action buttons */}
        <div style={{ display: "flex", gap: 8 }}>
          {onAddToFarm && (
            <button
              type="button"
              onClick={onAddToFarm}
              style={{
                all: "unset",
                cursor: "pointer",
                flex: 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
                padding: "9px 12px",
                borderRadius: 8,
                background: "rgba(0,255,136,0.1)",
                border: "1px solid rgba(0,255,136,0.35)",
                color: "var(--accent)",
                fontSize: 13,
                fontWeight: 600,
                transition: "all 150ms ease",
              }}
            >
              <Tractor size={13} />
              В ферму
            </button>
          )}
          {onAddToCampaign && (
            <button
              type="button"
              onClick={onAddToCampaign}
              style={{
                all: "unset",
                cursor: "pointer",
                flex: 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
                padding: "9px 12px",
                borderRadius: 8,
                background: "rgba(68,136,255,0.1)",
                border: "1px solid rgba(68,136,255,0.35)",
                color: "#4488ff",
                fontSize: 13,
                fontWeight: 600,
                transition: "all 150ms ease",
              }}
            >
              <Megaphone size={13} />
              В кампанию
            </button>
          )}
        </div>
        {/* Secondary row */}
        <div style={{ display: "flex", gap: 8 }}>
          {ch.username && (
            <a
              href={`https://t.me/${ch.username}`}
              target="_blank"
              rel="noopener noreferrer"
              className="primary-button"
              style={{ flex: 1, textAlign: "center", textDecoration: "none", fontSize: 13 }}
            >
              <ExternalLink size={13} style={{ marginRight: 6, verticalAlign: "middle" }} />
              Telegram
            </a>
          )}
          <button
            type="button"
            className="ghost-button"
            onClick={onClose}
            style={{ fontSize: 13 }}
          >
            Закрыть
          </button>
        </div>
      </div>
    </motion.div>
  );
}

// ── Selection modal (Farm / Campaign) ────────────────────────────────────────

type SelectionModalVariant = "farm" | "campaign";

function SelectionModal({
  variant,
  farms,
  channelDbs,
  campaigns,
  onConfirm,
  onClose,
  busy,
}: {
  variant: SelectionModalVariant;
  farms: FarmConfig[];
  channelDbs: ChannelDatabase[];
  campaigns: Campaign[];
  onConfirm: (id: number, dbId?: number) => void;
  onClose: () => void;
  busy: boolean;
}) {
  const [selectedId, setSelectedId] = useState<number | "">("");
  const [selectedDbId, setSelectedDbId] = useState<number | "">("");

  const isFarm = variant === "farm";
  const farmHasDbs = isFarm && channelDbs.length > 0;

  const canConfirm = isFarm
    ? selectedId !== "" && (!farmHasDbs || selectedDbId !== "")
    : selectedId !== "";

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 400,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0,0,0,0.7)",
        backdropFilter: "blur(4px)",
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.93 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.93 }}
        transition={{ duration: 0.18 }}
        style={{
          background: "#0a0a0b",
          border: "1px solid var(--accent)",
          borderRadius: 14,
          padding: "24px 28px",
          width: 360,
          boxShadow: "0 0 40px rgba(0,255,136,0.15)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
          <div style={{ fontWeight: 700, fontSize: 16, color: "var(--accent)", display: "flex", alignItems: "center", gap: 8 }}>
            {isFarm ? <Tractor size={16} /> : <Megaphone size={16} />}
            {isFarm ? "Добавить в ферму" : "Добавить в кампанию"}
          </div>
          <button type="button" onClick={onClose} style={{ all: "unset", cursor: "pointer", color: "var(--muted)", lineHeight: 1 }}>
            <X size={16} />
          </button>
        </div>

        {isFarm ? (
          <>
            <label className="field" style={{ marginBottom: 12 }}>
              <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>Ферма</span>
              <select
                value={selectedId}
                onChange={(e) => setSelectedId(e.target.value === "" ? "" : Number(e.target.value))}
                style={{ width: "100%" }}
              >
                <option value="">-- выберите ферму --</option>
                {farms.map((f) => (
                  <option key={f.id} value={f.id}>{f.name} ({f.status})</option>
                ))}
              </select>
            </label>
            {farmHasDbs && (
              <label className="field" style={{ marginBottom: 12 }}>
                <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>База каналов</span>
                <select
                  value={selectedDbId}
                  onChange={(e) => setSelectedDbId(e.target.value === "" ? "" : Number(e.target.value))}
                  style={{ width: "100%" }}
                >
                  <option value="">-- выберите базу --</option>
                  {channelDbs.map((db) => (
                    <option key={db.id} value={db.id}>{db.name} ({db.channel_count ?? 0} каналов)</option>
                  ))}
                </select>
              </label>
            )}
            {farms.length === 0 && (
              <p style={{ fontSize: 13, color: "var(--muted)", marginBottom: 12 }}>
                Фермы не найдены. Создайте ферму в разделе Farm.
              </p>
            )}
          </>
        ) : (
          <>
            <label className="field" style={{ marginBottom: 12 }}>
              <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>Кампания</span>
              <select
                value={selectedId}
                onChange={(e) => setSelectedId(e.target.value === "" ? "" : Number(e.target.value))}
                style={{ width: "100%" }}
              >
                <option value="">-- выберите кампанию --</option>
                {campaigns.map((c) => (
                  <option key={c.id} value={c.id}>{c.name} ({c.status})</option>
                ))}
              </select>
            </label>
            {campaigns.length === 0 && (
              <p style={{ fontSize: 13, color: "var(--muted)", marginBottom: 12 }}>
                Кампании не найдены. Создайте кампанию в разделе Campaigns.
              </p>
            )}
          </>
        )}

        <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
          <button
            type="button"
            className="primary-button"
            disabled={!canConfirm || busy}
            onClick={() => canConfirm && onConfirm(
              selectedId as number,
              isFarm && selectedDbId !== "" ? (selectedDbId as number) : undefined
            )}
            style={{ flex: 1, fontSize: 13 }}
          >
            {busy ? "Добавляем…" : "Добавить"}
          </button>
          <button type="button" className="ghost-button" onClick={onClose} style={{ fontSize: 13 }}>
            Отмена
          </button>
        </div>
      </motion.div>
    </div>
  );
}

function BarChart({
  data,
  maxVal,
  barClass,
}: {
  data: { label: string; value: number }[];
  maxVal: number;
  barClass?: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {data.map(({ label, value }) => {
        const pct = maxVal > 0 ? (value / maxVal) * 100 : 0;
        return (
          <div key={label} style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <div
              style={{
                minWidth: 60,
                fontSize: 11,
                color: "var(--text-secondary)",
                textAlign: "right",
              }}
            >
              {label}
            </div>
            <div
              style={{
                flex: 1,
                height: 6,
                background: "var(--surface-3)",
                borderRadius: 3,
                overflow: "hidden",
              }}
            >
              <div
                className={barClass}
                style={{
                  width: `${pct}%`,
                  height: "100%",
                  background: "var(--accent)",
                  borderRadius: 3,
                  transition: "width 600ms cubic-bezier(0.16,1,0.3,1)",
                }}
              />
            </div>
            <span
              style={{
                minWidth: 36,
                fontSize: 11,
                color: "var(--muted)",
                fontFamily: "'JetBrains Mono Variable', monospace",
                textAlign: "right",
              }}
            >
              {formatNumber(value)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── Category legend for globe overlay ────────────────────────────────────────

function GlobeCategoryLegend({
  categories,
  byCategory,
  selected,
  onSelect,
}: {
  categories: string[];
  byCategory: Record<string, number>;
  selected: string;
  onSelect: (cat: string) => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        maxHeight: 400,
        overflowY: "auto",
      }}
    >
      {categories.slice(0, 16).map((cat) => {
        const color = getCategoryColor(cat);
        const count = byCategory[cat] ?? 0;
        const isActive = selected === cat;
        return (
          <button
            key={cat}
            type="button"
            onClick={() => onSelect(isActive ? "" : cat)}
            style={{
              all: "unset",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "5px 10px",
              borderRadius: 6,
              background: isActive ? `${color}22` : "transparent",
              border: isActive ? `1px solid ${color}66` : "1px solid transparent",
              transition: "all 150ms ease",
              fontSize: 12,
              color: isActive ? color : "var(--text-secondary)",
            }}
          >
            <div
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: color,
                boxShadow: isActive ? `0 0 6px ${color}` : "none",
                flexShrink: 0,
              }}
            />
            <span style={{ flex: 1, whiteSpace: "nowrap" }}>{cat}</span>
            <span
              style={{
                fontSize: 10,
                color: "var(--muted)",
                fontFamily: "monospace",
              }}
            >
              {formatNumber(count)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ── main page ─────────────────────────────────────────────────────────────────

type ViewMode = "globe" | "cards" | "table" | "map";

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
  const [selectedRegion, setSelectedRegion] = useState("");

  // DEFAULT view is "globe"
  const [viewMode, setViewMode] = useState<ViewMode>("globe");
  const [selectedChannel, setSelectedChannel] = useState<ChannelMapEntry | null>(null);

  // Bulk selection
  const [bulkSelected, setBulkSelected] = useState<Set<number>>(new Set());

  // Farm / campaign / channel-db data for modals
  const [farms, setFarms] = useState<FarmConfig[]>([]);
  const [channelDbs, setChannelDbs] = useState<ChannelDatabase[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);

  // Modal state: null | "farm" | "campaign"
  const [modalVariant, setModalVariant] = useState<"farm" | "campaign" | null>(null);
  // Channels to add when modal confirms (null = use selectedChannel)
  const [pendingChannels, setPendingChannels] = useState<ChannelMapEntry[]>([]);
  const [modalBusy, setModalBusy] = useState(false);
  const [actionMsg, setActionMsg] = useState("");

  const highlightRe = useMemo(() => buildHighlightRe(query), [query]);

  const byCategory = (stats.by_category as Record<string, number> | undefined) ?? {};
  const byLanguage = (stats.by_language as Record<string, number> | undefined) ?? {};
  const totalIndexed = typeof stats.total === "number" ? stats.total : total;

  const totalReach = useMemo(
    () => items.reduce((sum, ch) => sum + (ch.member_count ?? 0), 0),
    [items]
  );

  const avgEr = useMemo(() => {
    const valid = items.filter((ch) => ch.engagement_rate != null);
    if (!valid.length) return null;
    return valid.reduce((sum, ch) => sum + (ch.engagement_rate ?? 0), 0) / valid.length;
  }, [items]);

  const commentsCount = useMemo(
    () => items.filter((ch) => ch.has_comments).length,
    [items]
  );

  const categoryStats = useMemo(() => {
    const result: Record<
      string,
      { count: number; totalEr: number; erCount: number; totalReach: number }
    > = {};
    for (const ch of items) {
      const cat = ch.category ?? "Другое";
      if (!result[cat])
        result[cat] = { count: 0, totalEr: 0, erCount: 0, totalReach: 0 };
      result[cat].count += 1;
      result[cat].totalReach += ch.member_count ?? 0;
      if (ch.engagement_rate != null) {
        result[cat].totalEr += ch.engagement_rate;
        result[cat].erCount += 1;
      }
    }
    return result;
  }, [items]);

  const memberRangeCounts = useMemo(() => {
    return MEMBER_RANGES.map((r) => ({
      label: r.label,
      value: items.filter(
        (ch) => (ch.member_count ?? 0) >= r.min && (ch.member_count ?? 0) < r.max
      ).length,
    }));
  }, [items]);

  const displayItems = useMemo(() => {
    let filtered = items;
    if (hasCommentsOnly) filtered = filtered.filter((ch) => ch.has_comments);
    return filtered;
  }, [items, hasCommentsOnly]);

  const selectAll = useCallback(() => {
    setBulkSelected(new Set(displayItems.map((ch) => ch.id)));
  }, [displayItems]);

  const bubbleLayout = useMemo(() => {
    if (viewMode !== "map" || displayItems.length === 0) {
      return { nodes: [], labels: [], width: 800, height: 600 };
    }
    return computeLayout(displayItems);
  }, [viewMode, displayItems]);

  const loadFarmData = useCallback(async () => {
    if (!accessToken) return;
    try {
      const [farmsRes, dbsRes] = await Promise.all([
        farmApi.list(accessToken),
        channelDbApi.list(accessToken),
      ]);
      setFarms(farmsRes.items ?? []);
      setChannelDbs(dbsRes.items ?? []);
    } catch {
      // optional
    }
  }, [accessToken]);

  const loadCampaignData = useCallback(async () => {
    if (!accessToken) return;
    try {
      const res = await campaignsApi.list(accessToken);
      setCampaigns(res.items ?? []);
    } catch {
      // optional
    }
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

  const handleModalConfirm = useCallback(async (id: number, dbId?: number) => {
    if (!accessToken || pendingChannels.length === 0) return;
    setModalBusy(true);
    try {
      if (modalVariant === "farm") {
        // Add channels to channel database
        const targetDbId = dbId ?? id; // if no separate db, use first available db
        const usernames = pendingChannels
          .map((ch) => ch.username)
          .filter((u): u is string => !!u);
        if (usernames.length === 0) {
          setActionMsg("Нет каналов с username для добавления.");
          setModalVariant(null);
          return;
        }
        const res = await channelDbApi.importChannels(accessToken, targetDbId, usernames);
        setActionMsg(`Добавлено ${res.imported} каналов в базу фермы (пропущено: ${res.skipped}).`);
      } else if (modalVariant === "campaign") {
        // Add channel database to campaign (update campaign's channel_database_id)
        // Since there's no direct "add channels to campaign" endpoint,
        // we use the channel-db import flow: import channels into campaign's existing db,
        // or just report success with what we know.
        const usernames = pendingChannels
          .map((ch) => ch.username)
          .filter((u): u is string => !!u);
        if (usernames.length === 0) {
          setActionMsg("Нет каналов с username для добавления.");
          setModalVariant(null);
          return;
        }
        // Find the campaign's channel_database_id
        const campaign = campaigns.find((c) => c.id === id);
        if (campaign?.channel_database_id) {
          const res = await channelDbApi.importChannels(accessToken, campaign.channel_database_id, usernames);
          setActionMsg(`Добавлено ${res.imported} каналов в базу кампании "${campaign.name}" (пропущено: ${res.skipped}).`);
        } else {
          // Campaign has no channel db yet — just show a message
          setActionMsg(`Кампания не имеет привязанной базы каналов. Привяжите базу в настройках кампании.`);
        }
      }
      setModalVariant(null);
      setBulkSelected(new Set());
    } catch (e) {
      setActionMsg(e instanceof Error ? e.message : "Ошибка добавления.");
      setModalVariant(null);
    } finally {
      setModalBusy(false);
    }
  }, [accessToken, modalVariant, pendingChannels, campaigns]);

  const handleBulkBlacklist = useCallback(async () => {
    if (!accessToken || bulkSelected.size === 0) return;
    const toBlacklist = items.filter((ch) => bulkSelected.has(ch.id));
    setActionMsg(`Помечаем ${toBlacklist.length} каналов как чёрный список...`);
    // Use channelsApi.blacklist per channel (map entry ids are channel_map ids, not channel_db ids)
    // We can only toggle blacklist on channel_map entries if the endpoint supports it.
    // For now, show a info message since channel_map entries don't have a direct blacklist endpoint.
    setActionMsg(`Чёрный список в Channel Map пока не поддерживается. Используйте раздел Channel DB.`);
    setBulkSelected(new Set());
  }, [accessToken, bulkSelected, items]);

  const toggleBulkSelect = useCallback((id: number) => {
    setBulkSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => {
    setBulkSelected(new Set());
  }, []);

  const loadCategories = useCallback(async () => {
    if (!accessToken) return;
    try {
      const payload = await channelMapApi.categories(accessToken);
      setCategories(payload.categories ?? []);
    } catch {
      // optional
    }
  }, [accessToken]);

  const loadStats = useCallback(async () => {
    if (!accessToken) return;
    try {
      const payload = await channelMapApi.stats(accessToken);
      setStats(payload);
    } catch {
      // optional
    }
  }, [accessToken]);

  const doSearch = useCallback(
    async (overrideQuery?: string) => {
      if (!accessToken) return;
      const q = overrideQuery ?? query;
      setBusy(true);
      setError("");
      try {
        const payload = await channelMapApi.search(accessToken, {
          query: q.trim() || undefined,
          category: selectedCategory || undefined,
          language: selectedLanguage || undefined,
          min_members: minMembers > 0 ? minMembers : undefined,
          limit: 500,
        });
        setItems(payload.items);
        setTotal(payload.total);
      } catch (e) {
        setError(e instanceof Error ? e.message : "search_failed");
      } finally {
        setBusy(false);
      }
    },
    [accessToken, query, selectedCategory, selectedLanguage, minMembers]
  );

  const loadAll = useCallback(async () => {
    if (!accessToken) return;
    setBusy(true);
    setError("");
    try {
      const payload = await channelMapApi.list(accessToken, {
        category: selectedCategory || undefined,
        language: selectedLanguage || undefined,
        min_members: minMembers > 0 ? minMembers : undefined,
        limit: 5000,
      });
      setItems(payload.items);
      setTotal(payload.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "load_failed");
    } finally {
      setBusy(false);
    }
  }, [accessToken, selectedCategory, selectedLanguage, minMembers]);

  useEffect(() => {
    void Promise.all([loadAll(), loadCategories(), loadStats(), loadFarmData(), loadCampaignData()]).catch(() => {});
  }, [accessToken]);

  useEffect(() => {
    if (!accessToken) return;
    if (query.trim()) {
      void doSearch();
    } else {
      void loadAll();
    }
  }, [selectedCategory, selectedLanguage, minMembers]);

  const handleRegionClick = (regionKey: string) => {
    setSelectedRegion(regionKey);
    const region = REGION_CONFIG.find((r) => r.key === regionKey);
    if (!region || region.languages.length === 0) {
      setSelectedLanguage("");
    } else if (region.languages.length === 1) {
      setSelectedLanguage(region.languages[0]);
    } else {
      setSelectedLanguage("");
    }
  };

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) {
      void doSearch();
    } else {
      void loadAll();
    }
  };

  const handleReset = () => {
    setQuery("");
    setSelectedCategory("");
    setSelectedLanguage("");
    setMinMembers(0);
    setHasCommentsOnly(false);
    setSelectedRegion("");
    void channelMapApi
      .list(accessToken!, { limit: 5000 })
      .then((p) => {
        setItems(p.items);
        setTotal(p.total);
      })
      .catch(() => {});
  };

  const allCategoryNames = useMemo(
    () => [
      ...new Set([
        ...categories,
        ...Object.keys(byCategory),
        ...Object.keys(CATEGORY_META),
      ]),
    ],
    [categories, byCategory]
  );

  const maxCatCount = Math.max(
    ...allCategoryNames.map((c) => categoryStats[c]?.count ?? byCategory[c] ?? 0),
    1
  );

  const langEntries = Object.entries(byLanguage)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 8)
    .map(([lang, count]) => ({
      label: `${langFlag(lang)} ${lang.toUpperCase()}`,
      value: count,
    }));

  const maxLangCount = Math.max(...langEntries.map((e) => e.value), 1);
  const maxRangeCount = Math.max(...memberRangeCounts.map((r) => r.value), 1);

  const VIEW_BUTTONS: Array<{ mode: ViewMode; icon: React.ReactNode; label: string }> = [
    { mode: "globe", icon: <Globe size={14} />, label: "Глобус" },
    { mode: "map", icon: <Layers size={14} />, label: "Карта" },
    { mode: "cards", icon: <Grid3X3 size={14} />, label: "Карточки" },
    { mode: "table", icon: <List size={14} />, label: "Таблица" },
  ];

  return (
    <div className="page-grid">
      <style>{`
        mark {
          background: var(--accent-glow);
          color: var(--accent);
          padding: 1px 3px;
          border-radius: 3px;
        }
        .chmap-bar-cat { background: linear-gradient(90deg, var(--info), #8b5cf6) !important; }
        .chmap-bar-lang { background: linear-gradient(90deg, var(--warning), var(--danger)) !important; }
        .chmap-bar-size { background: linear-gradient(90deg, var(--accent), var(--accent-dim)) !important; }
        .globe-overlay-panel {
          background: rgba(10,10,11,0.82);
          border: 1px solid rgba(0,255,136,0.15);
          border-radius: 10px;
          padding: 12px 14px;
          backdrop-filter: blur(10px);
        }
      `}</style>

      {/* Top metric cards */}
      <section className="dash-stats">
        <MetricCard
          label="Всего каналов"
          value={formatNumber(totalIndexed)}
          sub="в индексе"
          icon={<Map size={14} />}
        />
        <MetricCard
          label="Суммарный охват"
          value={formatNumber(totalReach)}
          sub={`по ${formatNumber(displayItems.length)} каналам`}
          icon={<Globe size={14} />}
        />
        <MetricCard
          label="Средний ER"
          value={avgEr != null ? erLabel(avgEr) : "—"}
          sub="вовлечённость"
          accent={erColor(avgEr)}
          icon={<TrendingUp size={14} />}
        />
        <MetricCard
          label="С комментариями"
          value={
            displayItems.length > 0
              ? `${Math.round((commentsCount / displayItems.length) * 100)}%`
              : "—"
          }
          sub={`${commentsCount} каналов`}
          accent="var(--accent)"
          icon={<MessageCircle size={14} />}
        />
      </section>

      {/* Region selector */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Регион / язык</div>
            <h2 style={{ fontSize: "1.2rem" }}>Быстрый фильтр по географии</h2>
          </div>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
          {REGION_CONFIG.map((r) => {
            const isActive = selectedRegion === r.key;
            const count =
              r.languages.length === 0
                ? totalIndexed
                : r.languages.reduce((s, l) => s + (byLanguage[l] ?? 0), 0);
            return (
              <button
                key={r.key}
                type="button"
                onClick={() => handleRegionClick(r.key)}
                style={{
                  all: "unset",
                  cursor: "pointer",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 4,
                  padding: "14px 22px",
                  borderRadius: 12,
                  background: isActive ? "var(--accent-glow)" : "var(--surface-2)",
                  border: isActive ? "1px solid var(--accent)" : "1px solid var(--border)",
                  color: isActive ? "var(--accent)" : "var(--text)",
                  fontWeight: isActive ? 700 : 500,
                  transition: "all 200ms cubic-bezier(0.16, 1, 0.3, 1)",
                  boxShadow: isActive ? "var(--glow)" : "none",
                  minWidth: 80,
                  textAlign: "center",
                }}
              >
                <span style={{ fontSize: 18 }}>{r.label}</span>
                {count > 0 && (
                  <span style={{ fontSize: 11, opacity: 0.7, fontFamily: "monospace" }}>
                    {formatNumber(count)}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </section>

      {/* Main visualization panel */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">
              <Globe size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />
              Визуализация
            </div>
            <h2 style={{ fontSize: "1.2rem" }}>
              Карта каналов
              {displayItems.length > 0 && (
                <span
                  style={{
                    color: "var(--muted)",
                    fontSize: "0.75em",
                    fontFamily: "monospace",
                    marginLeft: 8,
                  }}
                >
                  {displayItems.length} из {total}
                </span>
              )}
            </h2>
          </div>

          {/* View mode switcher */}
          <div
            style={{
              display: "flex",
              background: "var(--surface-2)",
              borderRadius: 10,
              border: "1px solid var(--border)",
              padding: 3,
              gap: 2,
            }}
          >
            {VIEW_BUTTONS.map(({ mode, icon, label }) => (
              <button
                key={mode}
                type="button"
                onClick={() => setViewMode(mode)}
                style={{
                  all: "unset",
                  cursor: "pointer",
                  padding: "6px 14px",
                  borderRadius: 8,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: 13,
                  fontWeight: viewMode === mode ? 600 : 400,
                  color: viewMode === mode ? "var(--accent)" : "var(--muted)",
                  background:
                    viewMode === mode ? "var(--accent-glow)" : "transparent",
                  transition: "all 200ms ease",
                }}
              >
                {icon} {label}
              </button>
            ))}
          </div>
        </div>

        {/* Globe view */}
        {viewMode === "globe" && (
          <div style={{ display: "flex", gap: 16 }}>
            {/* Globe canvas */}
            <div style={{ flex: 1 }}>
              <GlobeView
                channels={displayItems}
                filterCategory={selectedCategory}
                filterQuery={query}
                onSelect={setSelectedChannel}
              />
            </div>

            {/* Right overlay: legend + search */}
            <div
              style={{
                width: 200,
                display: "flex",
                flexDirection: "column",
                gap: 12,
              }}
            >
              {/* Quick search */}
              <div className="globe-overlay-panel">
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--muted)",
                    marginBottom: 8,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <Search size={11} /> Поиск
                </div>
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void doSearch();
                  }}
                  placeholder="Название, @username..."
                  style={{
                    width: "100%",
                    background: "var(--surface-3)",
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    padding: "6px 10px",
                    fontSize: 12,
                    color: "var(--text)",
                    outline: "none",
                    boxSizing: "border-box",
                  }}
                />
              </div>

              {/* Category legend */}
              <div className="globe-overlay-panel" style={{ flex: 1 }}>
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--muted)",
                    marginBottom: 8,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <Filter size={11} /> Категории
                  {selectedCategory && (
                    <button
                      type="button"
                      onClick={() => setSelectedCategory("")}
                      style={{
                        all: "unset",
                        cursor: "pointer",
                        color: "var(--accent)",
                        fontSize: 10,
                        marginLeft: "auto",
                      }}
                    >
                      сбросить
                    </button>
                  )}
                </div>
                <GlobeCategoryLegend
                  categories={allCategoryNames}
                  byCategory={
                    Object.fromEntries(
                      allCategoryNames.map((c) => [
                        c,
                        categoryStats[c]?.count ?? byCategory[c] ?? 0,
                      ])
                    )
                  }
                  selected={selectedCategory}
                  onSelect={setSelectedCategory}
                />
              </div>

              {/* Stats */}
              <div className="globe-overlay-panel">
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>
                  Статистика
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {[
                    { label: "Каналов загружено", value: formatNumber(displayItems.length) },
                    { label: "Всего в индексе", value: formatNumber(totalIndexed) },
                    { label: "Охват", value: formatNumber(totalReach) },
                  ].map(({ label, value }) => (
                    <div
                      key={label}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        fontSize: 11,
                      }}
                    >
                      <span style={{ color: "var(--text-secondary)" }}>{label}</span>
                      <span
                        style={{
                          color: "var(--accent)",
                          fontFamily: "monospace",
                          fontWeight: 600,
                        }}
                      >
                        {value}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Bubble map view */}
        {viewMode === "map" && (
          <BubbleMapCanvas
            layout={bubbleLayout}
            filterCategory={selectedCategory}
            filterQuery={query}
            onSelect={setSelectedChannel}
          />
        )}

        {/* Bulk action bar — shown in cards/table when items selected */}
        {bulkSelected.size > 0 && (viewMode === "cards" || viewMode === "table") && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "10px 14px",
              background: "rgba(0,255,136,0.07)",
              border: "1px solid rgba(0,255,136,0.25)",
              borderRadius: 10,
              marginBottom: 4,
            }}
          >
            <span style={{ fontSize: 13, color: "var(--accent)", fontWeight: 600 }}>
              Выбрано: {bulkSelected.size}
            </span>
            <button
              type="button"
              onClick={() => openFarmModal(displayItems.filter((ch) => bulkSelected.has(ch.id)))}
              style={{
                all: "unset",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 5,
                padding: "5px 12px",
                borderRadius: 7,
                background: "rgba(0,255,136,0.12)",
                border: "1px solid rgba(0,255,136,0.3)",
                color: "var(--accent)",
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              <Tractor size={13} /> В ферму
            </button>
            <button
              type="button"
              onClick={() => openCampaignModal(displayItems.filter((ch) => bulkSelected.has(ch.id)))}
              style={{
                all: "unset",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 5,
                padding: "5px 12px",
                borderRadius: 7,
                background: "rgba(68,136,255,0.12)",
                border: "1px solid rgba(68,136,255,0.3)",
                color: "#4488ff",
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              <Megaphone size={13} /> В кампанию
            </button>
            <button
              type="button"
              onClick={handleBulkBlacklist}
              style={{
                all: "unset",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 5,
                padding: "5px 12px",
                borderRadius: 7,
                background: "rgba(255,68,68,0.1)",
                border: "1px solid rgba(255,68,68,0.25)",
                color: "var(--danger)",
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              <Ban size={13} /> В черный список
            </button>
            <button
              type="button"
              onClick={clearSelection}
              style={{
                all: "unset",
                cursor: "pointer",
                marginLeft: "auto",
                color: "var(--muted)",
                fontSize: 12,
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <X size={12} /> Снять выбор
            </button>
          </motion.div>
        )}

        {busy && viewMode !== "globe" && <p className="muted">Загружаем…</p>}

        {/* Cards view */}
        <AnimatePresence mode="wait">
          {!busy && displayItems.length > 0 && viewMode === "cards" && (
            <motion.div
              key="cards-view"
              variants={viewSwitchVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
                gap: 16,
              }}
            >
              {displayItems.map((ch, i) => (
                <ChannelCard
                  key={ch.id}
                  ch={ch}
                  highlightRe={highlightRe}
                  index={i}
                  selected={bulkSelected.has(ch.id)}
                  onToggleSelect={() => toggleBulkSelect(ch.id)}
                />
              ))}
            </motion.div>
          )}

          {/* Table view */}
          {!busy && displayItems.length > 0 && viewMode === "table" && (
            <motion.div
              key="table-view"
              variants={viewSwitchVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="table-wrap"
            >
              <table className="data-table">
                <thead>
                  <tr>
                    <th style={{ width: 36 }}>
                      <button
                        type="button"
                        onClick={() => bulkSelected.size === displayItems.length ? clearSelection() : selectAll()}
                        style={{ all: "unset", cursor: "pointer", color: "var(--accent)", display: "flex" }}
                      >
                        {bulkSelected.size === displayItems.length && displayItems.length > 0
                          ? <CheckSquare size={15} />
                          : <Square size={15} />}
                      </button>
                    </th>
                    <th>Канал</th>
                    <th>Категория</th>
                    <th>Язык</th>
                    <th>Подписчики</th>
                    <th>Комментарии</th>
                    <th>Охват</th>
                    <th>ER%</th>
                    <th>Проиндексирован</th>
                  </tr>
                </thead>
                <tbody>
                  {displayItems.map((ch) => (
                    <tr
                      key={ch.id}
                      style={{
                        cursor: "pointer",
                        background: bulkSelected.has(ch.id) ? "rgba(0,255,136,0.05)" : undefined,
                      }}
                      onClick={() => setSelectedChannel(ch)}
                    >
                      <td
                        onClick={(e) => { e.stopPropagation(); toggleBulkSelect(ch.id); }}
                        style={{ width: 36 }}
                      >
                        <button
                          type="button"
                          style={{ all: "unset", cursor: "pointer", color: bulkSelected.has(ch.id) ? "var(--accent)" : "var(--muted)", display: "flex" }}
                        >
                          {bulkSelected.has(ch.id) ? <CheckSquare size={14} /> : <Square size={14} />}
                        </button>
                      </td>
                      <td>
                        <div>
                          <strong>
                            {ch.username ? (
                              <a
                                href={`https://t.me/${ch.username}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                style={{
                                  color: "var(--accent)",
                                  fontFamily: "monospace",
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: 4,
                                }}
                                onClick={(e) => e.stopPropagation()}
                              >
                                @{ch.username}
                                <ExternalLink size={11} style={{ opacity: 0.5 }} />
                              </a>
                            ) : (
                              <span
                                style={{
                                  fontFamily: "monospace",
                                  color: "var(--muted)",
                                }}
                              >
                                #{ch.id}
                              </span>
                            )}
                          </strong>
                          {ch.title && (
                            <div
                              style={{
                                fontSize: 11,
                                color: "var(--text-secondary)",
                              }}
                            >
                              {ch.title}
                            </div>
                          )}
                        </div>
                      </td>
                      <td>
                        {ch.category ? (
                          <span
                            className="pill"
                            style={{
                              background: `${getCategoryMeta(ch.category).color}18`,
                              color: getCategoryMeta(ch.category).color,
                            }}
                          >
                            {getCategoryMeta(ch.category).icon} {ch.category}
                          </span>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td>
                        {ch.language ? (
                          <span>
                            {langFlag(ch.language)} {ch.language.toUpperCase()}
                          </span>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td
                        style={{
                          fontFamily: "monospace",
                          fontWeight: 600,
                        }}
                      >
                        {formatNumber(ch.member_count)}
                      </td>
                      <td>
                        {ch.has_comments ? (
                          <span
                            className="pill"
                            style={{
                              background: "var(--accent-glow)",
                              color: "var(--accent)",
                            }}
                          >
                            Да
                          </span>
                        ) : (
                          <span className="muted">Нет</span>
                        )}
                      </td>
                      <td
                        style={{
                          fontFamily: "monospace",
                        }}
                      >
                        {formatNumber(ch.avg_post_reach)}
                      </td>
                      <td
                        style={{
                          fontFamily: "monospace",
                          color: erColor(ch.engagement_rate),
                          fontWeight: 600,
                        }}
                      >
                        {erLabel(ch.engagement_rate)}
                      </td>
                      <td style={{ fontSize: 11, color: "var(--muted)" }}>
                        {ch.last_indexed_at
                          ? new Date(ch.last_indexed_at).toLocaleDateString("ru-RU")
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </motion.div>
          )}
        </AnimatePresence>

        {!busy && displayItems.length === 0 && viewMode !== "globe" && (
          <p className="muted">
            Каналы не найдены. Попробуйте изменить фильтры или запустите индексирование.
          </p>
        )}
      </section>

      {/* Search form */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">
              <Search size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />
              Фильтры
            </div>
            <h2 style={{ fontSize: "1.2rem" }}>Найти каналы</h2>
          </div>
        </div>
        <form className="stack-form" onSubmit={handleSearch}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr 1fr",
              gap: 12,
            }}
          >
            <label className="field" style={{ gridColumn: "1 / 3" }}>
              <span
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  color: "var(--text-secondary)",
                }}
              >
                <Search size={13} /> Ключевое слово
              </span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Например: маркетинг, крипта, e-commerce..."
              />
            </label>
            <label className="field">
              <span
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  color: "var(--text-secondary)",
                }}
              >
                <Globe size={13} /> Язык
              </span>
              <select
                value={selectedLanguage}
                onChange={(e) => setSelectedLanguage(e.target.value)}
              >
                {LANGUAGE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="field">
            <span
              style={{
                color: "var(--text-secondary)",
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <Users size={13} />
              Минимум подписчиков:{" "}
              <span
                style={{
                  color: "var(--accent)",
                  fontFamily: "monospace",
                  fontWeight: 600,
                }}
              >
                {minMembers > 0 ? formatNumber(minMembers) : "не задано"}
              </span>
            </span>
            <input
              type="range"
              min={0}
              max={1_000_000}
              step={5_000}
              value={minMembers}
              onChange={(e) => setMinMembers(Number(e.target.value))}
              style={{ accentColor: "var(--accent)" }}
            />
          </label>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                cursor: "pointer",
                userSelect: "none",
                fontSize: 14,
                color: "var(--text-secondary)",
              }}
            >
              <input
                type="checkbox"
                checked={hasCommentsOnly}
                onChange={(e) => setHasCommentsOnly(e.target.checked)}
                style={{ width: 16, height: 16, accentColor: "var(--accent)" }}
              />
              <MessageCircle size={14} style={{ color: "var(--accent)" }} />
              Только с комментариями
            </label>
          </div>
          <div className="actions-row">
            <button className="primary-button" type="submit" disabled={busy}>
              <Search
                size={14}
                style={{ marginRight: 6, verticalAlign: "middle" }}
              />
              {busy ? "Ищем…" : "Найти"}
            </button>
            <button
              className="ghost-button"
              type="button"
              disabled={busy}
              onClick={handleReset}
            >
              Сбросить всё
            </button>
            <button
              className="ghost-button"
              type="button"
              disabled={busy}
              onClick={() => void loadAll()}
              style={{ marginLeft: "auto" }}
            >
              <RotateCcw size={13} style={{ marginRight: 6, verticalAlign: "middle" }} />
              Обновить
            </button>
          </div>
        </form>
      </section>

      {error ? <div className="status-banner">{error}</div> : null}

      {/* Category grid */}
      {allCategoryNames.length > 0 && (
        <section className="panel wide">
          <div className="panel-header">
            <div>
              <div className="eyebrow">
                <Filter size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />
                Категории
              </div>
              <h2 style={{ fontSize: "1.2rem" }}>Интерактивный фильтр по нише</h2>
            </div>
            {selectedCategory && (
              <button
                type="button"
                className="ghost-button"
                onClick={() => setSelectedCategory("")}
                style={{ fontSize: 13 }}
              >
                Сбросить фильтр
              </button>
            )}
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
              gap: 12,
            }}
          >
            {allCategoryNames.map((cat) => {
              const catStat = categoryStats[cat];
              const fallbackCount = byCategory[cat] ?? 0;
              const count = catStat?.count ?? fallbackCount;
              const avgErCat =
                catStat && catStat.erCount > 0
                  ? catStat.totalEr / catStat.erCount
                  : null;
              const reach = catStat?.totalReach ?? 0;
              return (
                <CategoryCard
                  key={cat}
                  name={cat}
                  count={count}
                  avgEr={avgErCat}
                  totalReach={reach}
                  selected={selectedCategory === cat}
                  onClick={() =>
                    setSelectedCategory(selectedCategory === cat ? "" : cat)
                  }
                />
              );
            })}
          </div>
        </section>
      )}

      {/* Analytics sidebar */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          gap: 16,
        }}
      >
        {langEntries.length > 0 && (
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="eyebrow">Языки</div>
                <h2 style={{ fontSize: "1rem" }}>Распределение по языкам</h2>
              </div>
            </div>
            <BarChart
              data={langEntries}
              maxVal={maxLangCount}
              barClass="chmap-bar-lang"
            />
          </section>
        )}

        <section className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Размер</div>
              <h2 style={{ fontSize: "1rem" }}>По числу подписчиков</h2>
            </div>
          </div>
          <BarChart
            data={memberRangeCounts}
            maxVal={maxRangeCount}
            barClass="chmap-bar-size"
          />
        </section>

        {allCategoryNames.length > 0 && (
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="eyebrow">Топ категорий</div>
                <h2 style={{ fontSize: "1rem" }}>По числу каналов</h2>
              </div>
            </div>
            <BarChart
              data={allCategoryNames
                .map((c) => ({
                  label: `${getCategoryMeta(c).icon} ${c}`,
                  value: categoryStats[c]?.count ?? byCategory[c] ?? 0,
                }))
                .sort((a, b) => b.value - a.value)
                .slice(0, 10)}
              maxVal={maxCatCount}
              barClass="chmap-bar-cat"
            />
          </section>
        )}
      </div>

      {/* Action result message */}
      <AnimatePresence>
        {actionMsg && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            style={{
              position: "fixed",
              bottom: 24,
              left: "50%",
              transform: "translateX(-50%)",
              background: "#0a0a0b",
              border: "1px solid var(--accent)",
              borderRadius: 10,
              padding: "12px 20px",
              zIndex: 500,
              fontSize: 13,
              color: "var(--accent)",
              boxShadow: "0 0 20px rgba(0,255,136,0.2)",
              display: "flex",
              alignItems: "center",
              gap: 10,
            }}
          >
            {actionMsg}
            <button
              type="button"
              onClick={() => setActionMsg("")}
              style={{ all: "unset", cursor: "pointer", color: "var(--muted)", display: "flex" }}
            >
              <X size={14} />
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Selection modal */}
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

      {/* Channel detail panel */}
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
    </div>
  );
}
