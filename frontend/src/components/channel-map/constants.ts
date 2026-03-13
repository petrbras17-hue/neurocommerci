// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map: constants, design tokens, helpers
// ═══════════════════════════════════════════════════════════════════════════════

// ── Category registry ─────────────────────────────────────────────────────────

export type CategoryMeta = {
  icon: string;
  color: string;
  label: string;
};

export const CATEGORIES: Record<string, CategoryMeta> = {
  Crypto:        { icon: "₿",  color: "#f59e0b", label: "Крипто" },
  Marketing:     { icon: "📢", color: "#3b82f6", label: "Маркетинг" },
  "E-commerce":  { icon: "🛒", color: "#10b981", label: "E-commerce" },
  EdTech:        { icon: "🎓", color: "#6366f1", label: "EdTech" },
  News:          { icon: "📰", color: "#8b5cf6", label: "Новости" },
  Entertainment: { icon: "🎬", color: "#ec4899", label: "Развлечения" },
  Tech:          { icon: "💻", color: "#14b8a6", label: "Технологии" },
  Finance:       { icon: "📉", color: "#0ea5e9", label: "Финансы" },
  Lifestyle:     { icon: "✨", color: "#f97316", label: "Лайфстайл" },
  Health:        { icon: "🏥", color: "#22c55e", label: "Здоровье" },
  Gaming:        { icon: "🎮", color: "#e11d48", label: "Игры" },
  "18+":         { icon: "🔞", color: "#ef4444", label: "18+" },
  Politics:      { icon: "🏛️", color: "#dc2626", label: "Политика" },
  Sports:        { icon: "⚽", color: "#eab308", label: "Спорт" },
  Travel:        { icon: "✈️", color: "#06b6d4", label: "Путешествия" },
  Business:      { icon: "💼", color: "#84cc16", label: "Бизнес" },
  Science:       { icon: "🔬", color: "#a855f7", label: "Наука" },
  Music:         { icon: "🎵", color: "#f472b6", label: "Музыка" },
  Food:          { icon: "🍴", color: "#34d399", label: "Еда" },
  "AI/ML":       { icon: "🤖", color: "#d946ef", label: "AI/ML" },
  Cybersecurity: { icon: "🔒", color: "#22d3ee", label: "Кибербез" },
} as const;

export const DEFAULT_CATEGORY: CategoryMeta = {
  icon: "📌",
  color: "#64748b",
  label: "Другое",
} as const;

// ── Region coordinates [lat, lon] ─────────────────────────────────────────────

export const REGION_COORDS: Record<string, [number, number]> = {
  ru: [55.75,  37.62],
  en: [40.71,  -74.01],
  uk: [50.45,  30.52],
  kz: [51.17,  71.45],
  de: [52.52,  13.40],
  fr: [48.86,   2.35],
  es: [40.42,  -3.70],
  zh: [39.90, 116.40],
  ar: [24.71,  46.68],
  ja: [35.68, 139.69],
  ko: [37.57, 126.98],
  pt: [-23.55, -46.63],
  it: [41.90,  12.50],
  tr: [41.01,  28.98],
  pl: [52.23,  21.01],
  nl: [52.37,   4.90],
  hi: [28.61,  77.21],
  th: [13.76, 100.50],
  vi: [21.03, 105.85],
  id: [-6.21, 106.85],
} as const;

// ── Design tokens ─────────────────────────────────────────────────────────────

export const DESIGN_TOKENS = {
  BG:               '#0a0a0b',
  SURFACE:          'rgba(10, 10, 11, 0.75)',
  BORDER:           'rgba(0, 255, 136, 0.15)',
  ACCENT:           '#00ff88',
  ACCENT_SECONDARY: '#00d4ff',
  TEXT_PRIMARY:     '#ffffff',
  TEXT_SECONDARY:   '#70777b',
  GLOBE_COLOR:      '#1a1a2e',
  GLOBE_EMISSIVE:   '#0a0a0b',
  ATMOSPHERE:       '#00ff88',
} as const;

// ── Globe configuration ───────────────────────────────────────────────────────

export const GLOBE_CONFIG = {
  /** Default radius used by react-globe.gl */
  RADIUS:              100,
  ATMOSPHERE_ALTITUDE: 0.18,
  POINT_ALTITUDE:      0.01,
  ARC_ALTITUDE:        0.3,
  HEX_BIN_RESOLUTION:  3,
  ZOOM_LEVELS: {
    FAR:    2,
    MEDIUM: 4,
    CLOSE:  6,
  },
} as const;

// ── Language flags ─────────────────────────────────────────────────────────────

export const LANG_FLAGS: Record<string, string> = {
  ru: "🇷🇺",
  en: "🇺🇸",
  uk: "🇺🇦",
  kz: "🇰🇿",
  de: "🇩🇪",
  fr: "🇫🇷",
  es: "🇪🇸",
  zh: "🇨🇳",
  ar: "🇸🇦",
  ja: "🇯🇵",
  ko: "🇰🇷",
  pt: "🇧🇷",
  it: "🇮🇹",
  tr: "🇹🇷",
} as const;

// ── Helper functions ───────────────────────────────────────────────────────────

/**
 * Returns the CategoryMeta for a given category key, or DEFAULT_CATEGORY when
 * the key is absent or unrecognised.
 */
export function getCategoryMeta(cat: string | null | undefined): CategoryMeta {
  if (!cat) return DEFAULT_CATEGORY;
  return CATEGORIES[cat] ?? DEFAULT_CATEGORY;
}

/**
 * Convenience shorthand — returns the hex color string for a category.
 */
export function getCategoryColor(cat: string | null | undefined): string {
  return getCategoryMeta(cat).color;
}

/**
 * Returns the emoji flag for a language code, or "🌐" when unrecognised.
 */
export function getLangFlag(lang: string | null): string {
  if (!lang) return "🌐";
  return LANG_FLAGS[lang.toLowerCase()] ?? "🌐";
}

/**
 * Human-readable subscriber / view count.
 * null / undefined → "—"
 * ≥ 1 000 000      → "1.2M"
 * ≥ 1 000          → "45.3K"
 * otherwise        → raw integer string
 */
export function formatNumber(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/**
 * Formats an engagement-rate fraction (0–1) as a percentage string.
 * null / undefined → "—"
 * otherwise        → "3.21%"
 */
export function formatER(rate: number | null | undefined): string {
  if (rate == null) return "—";
  return `${(rate * 100).toFixed(2)}%`;
}
