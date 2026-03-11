import { useEffect, useState, useMemo, useCallback } from "react";
import { channelMapApi, ChannelMapEntry } from "../api";
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
} from "lucide-react";

// ── helpers ─────────────────────────────────────────────────────────────────

function formatNumber(n: number | null | undefined): string {
  if (n == null) return "\u2014";
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
  if (rate == null) return "\u2014";
  return `${(rate * 100).toFixed(2)}%`;
}

function langFlag(lang: string | null): string {
  const map: Record<string, string> = {
    ru: "\ud83c\uddf7\ud83c\uddfa",
    en: "\ud83c\uddfa\ud83c\uddf8",
    uk: "\ud83c\uddfa\ud83c\udde6",
    kz: "\ud83c\uddf0\ud83c\uddff",
    de: "\ud83c\udde9\ud83c\uddea",
    fr: "\ud83c\uddeb\ud83c\uddf7",
    es: "\ud83c\uddea\ud83c\uddf8",
    zh: "\ud83c\udde8\ud83c\uddf3",
    ar: "\ud83c\uddf8\ud83c\udde6",
  };
  if (!lang) return "\ud83c\udf10";
  return map[lang.toLowerCase()] ?? "\ud83c\udf10";
}

function buildHighlightRe(query: string): RegExp | null {
  const q = query.trim();
  if (!q) return null;
  const escaped = q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(${escaped})`, "gi");
}

function highlight(text: string, re: RegExp | null): string {
  if (!re) return text;
  return text.replace(re, "<mark>$1</mark>");
}

// ── constants ────────────────────────────────────────────────────────────────

const REGION_CONFIG: Array<{ label: string; key: string; languages: string[] }> = [
  { label: "\ud83c\uddf7\ud83c\uddfa RU", key: "ru", languages: ["ru"] },
  { label: "\ud83c\udff3\ufe0f \u0421\u041d\u0413", key: "cis", languages: ["ru", "uk", "kz"] },
  { label: "\ud83c\uddfa\ud83c\udde6 UA", key: "uk", languages: ["uk"] },
  { label: "\ud83c\uddf0\ud83c\uddff KZ", key: "kz", languages: ["kz"] },
  { label: "\ud83c\uddfa\ud83c\uddf8 EN", key: "en", languages: ["en"] },
  { label: "\ud83c\udf10 \u0412\u0441\u0435", key: "", languages: [] },
];

const CATEGORY_META: Record<string, { icon: string; color: string }> = {
  Crypto:        { icon: "\u20bf",  color: "var(--warning)" },
  Marketing:     { icon: "\ud83d\udce2", color: "var(--info)" },
  "E-commerce":  { icon: "\ud83d\uded2", color: "var(--accent)" },
  EdTech:        { icon: "\ud83c\udf93", color: "#4488ff" },
  News:          { icon: "\ud83d\udcf0", color: "var(--text-secondary)" },
  Entertainment: { icon: "\ud83c\udfac", color: "#ec4899" },
  Tech:          { icon: "\ud83d\udcbb", color: "#8b5cf6" },
  Finance:       { icon: "\ud83d\udcc9", color: "var(--accent-dim)" },
  Lifestyle:     { icon: "\u2728", color: "#f97316" },
  Health:        { icon: "\ud83c\udfe5", color: "#14b8a6" },
  Gaming:        { icon: "\ud83c\udfae", color: "#7c3aed" },
  "18+":         { icon: "\ud83d\udd1e", color: "var(--danger)" },
  Politics:      { icon: "\ud83c\udfdb\ufe0f", color: "#0ea5e9" },
  Sports:        { icon: "\u26bd", color: "#84cc16" },
  Travel:        { icon: "\u2708\ufe0f", color: "#06b6d4" },
};

const DEFAULT_CATEGORY_META = { icon: "\ud83d\udccc", color: "var(--info)" };

function getCategoryMeta(cat: string | null | undefined): { icon: string; color: string } {
  if (!cat) return DEFAULT_CATEGORY_META;
  return CATEGORY_META[cat] ?? DEFAULT_CATEGORY_META;
}

const LANGUAGE_OPTIONS = [
  { value: "", label: "\u0412\u0441\u0435 \u044f\u0437\u044b\u043a\u0438" },
  { value: "ru", label: "\u0420\u0443\u0441\u0441\u043a\u0438\u0439" },
  { value: "en", label: "English" },
  { value: "uk", label: "\u0423\u043a\u0440\u0430\u0457\u043d\u0441\u044c\u043a\u0430" },
  { value: "kz", label: "\u049a\u0430\u0437\u0430\u049b\u0448\u0430" },
];

const MEMBER_RANGES = [
  { label: "0\u20131K",    min: 0,       max: 1_000 },
  { label: "1K\u201310K",  min: 1_000,   max: 10_000 },
  { label: "10K\u2013100K",min: 10_000,  max: 100_000 },
  { label: "100K\u20131M", min: 100_000, max: 1_000_000 },
  { label: "1M+",     min: 1_000_000, max: Infinity },
];

// ── framer-motion variants ──────────────────────────────────────────────────

const cardVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.03, duration: 0.3, ease: [0.16, 1, 0.3, 1] as const },
  }),
};

const viewSwitchVariants = {
  initial: { opacity: 0, scale: 0.97 },
  animate: { opacity: 1, scale: 1, transition: { duration: 0.25 } },
  exit: { opacity: 0, scale: 0.97, transition: { duration: 0.15 } },
};

// ── sub-components ───────────────────────────────────────────────────────────

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
    <div className="dash-stat">
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {icon && (
          <span style={{ color: "var(--accent)", opacity: 0.7 }}>{icon}</span>
        )}
        <span className="dash-stat-label">{label}</span>
      </div>
      <div
        className="dash-stat-value"
        style={{ color: accent ?? "var(--text)" }}
      >
        {value}
      </div>
      {sub && <div className="dash-stat-sub">{sub}</div>}
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
        background: selected ? "var(--surface-2)" : "var(--surface)",
        border: selected ? `2px solid ${meta.color}` : "1px solid var(--border)",
        borderLeft: `3px solid ${meta.color}`,
        borderRadius: 12,
        padding: "14px 16px",
        display: "grid",
        gap: 8,
        transition: "all 200ms cubic-bezier(0.16, 1, 0.3, 1)",
        boxShadow: selected ? `0 0 16px ${meta.color}22` : "none",
        minWidth: 0,
      }}
      onMouseEnter={(e) => {
        if (!selected) {
          e.currentTarget.style.borderColor = "var(--border-bright)";
          e.currentTarget.style.boxShadow = "var(--glow)";
        }
      }}
      onMouseLeave={(e) => {
        if (!selected) {
          e.currentTarget.style.borderColor = "var(--border)";
          e.currentTarget.style.boxShadow = "none";
        }
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 20 }}>{meta.icon}</span>
        <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text)" }}>{name}</span>
      </div>
      <div
        style={{
          fontSize: 22,
          fontWeight: 700,
          color: selected ? meta.color : "var(--text)",
          lineHeight: 1,
          fontFamily: "'JetBrains Mono Variable', 'JetBrains Mono', 'Fira Code', monospace",
        }}
      >
        {formatNumber(count)}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <div style={{ fontSize: 11, color: "var(--muted)" }}>
          \u041e\u0445\u0432\u0430\u0442 {formatNumber(totalReach)}
        </div>
        {avgEr != null && (
          <div style={{ fontSize: 11, color: erColor(avgEr), fontWeight: 600 }}>
            ER {erLabel(avgEr)}
          </div>
        )}
      </div>
    </button>
  );
}

function ChannelCard({
  ch,
  highlightRe,
  onAddToCampaign,
  index,
}: {
  ch: ChannelMapEntry;
  highlightRe: RegExp | null;
  onAddToCampaign?: (ch: ChannelMapEntry) => void;
  index: number;
}) {
  const firstLetter = (ch.title ?? ch.username ?? "?")[0].toUpperCase();
  const gradients = [
    "linear-gradient(135deg, var(--info), #8b5cf6)",
    "linear-gradient(135deg, var(--warning), var(--danger))",
    "linear-gradient(135deg, var(--accent), var(--accent-dim))",
    "linear-gradient(135deg, #3b82f6, #06b6d4)",
    "linear-gradient(135deg, #ec4899, #f97316)",
    "linear-gradient(135deg, #8b5cf6, var(--info))",
  ];
  const gradientIndex = (ch.id ?? 0) % gradients.length;
  const meta = ch.category ? getCategoryMeta(ch.category) : null;

  const titleHtml = ch.title ? highlight(ch.title, highlightRe) : null;
  const usernameHtml = ch.username ? highlight(`@${ch.username}`, highlightRe) : null;

  return (
    <motion.div
      custom={index}
      variants={cardVariants}
      initial="hidden"
      animate="visible"
      style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: 16,
        padding: "18px 18px 14px",
        display: "grid",
        gap: 12,
        transition: "border-color 200ms, box-shadow 200ms",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "var(--border-bright)";
        e.currentTarget.style.boxShadow = "var(--glow)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--border)";
        e.currentTarget.style.boxShadow = "none";
      }}
    >
      {/* Header row */}
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        {/* Avatar */}
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: "50%",
            background: gradients[gradientIndex],
            display: "grid",
            placeItems: "center",
            color: "white",
            fontWeight: 700,
            fontSize: 18,
            flexShrink: 0,
            boxShadow: "0 0 12px rgba(0,0,0,0.3)",
          }}
        >
          {firstLetter}
        </div>
        {/* Name + username */}
        <div style={{ minWidth: 0, flex: 1 }}>
          {ch.title && (
            <div
              style={{ fontWeight: 600, fontSize: 14, lineHeight: 1.3, marginBottom: 2, color: "var(--text)" }}
              dangerouslySetInnerHTML={{ __html: titleHtml ?? ch.title }}
            />
          )}
          {ch.username ? (
            <a
              href={`https://t.me/${ch.username}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                fontSize: 12,
                color: "var(--accent)",
                fontWeight: 500,
                fontFamily: "'JetBrains Mono Variable', 'JetBrains Mono', 'Fira Code', monospace",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
              dangerouslySetInnerHTML={{ __html: usernameHtml ?? `@${ch.username}` }}
            />
          ) : (
            <span style={{ fontSize: 12, color: "var(--muted)", fontFamily: "'JetBrains Mono Variable', monospace" }}>
              #{ch.id}
            </span>
          )}
        </div>
        {/* Lang flag */}
        <span style={{ fontSize: 18, flexShrink: 0 }} title={ch.language ?? ""}>
          {langFlag(ch.language)}
        </span>
      </div>

      {/* Metrics row */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 8,
        }}
      >
        <div
          style={{
            background: "var(--surface-2)",
            borderRadius: 10,
            padding: "8px 10px",
            border: "1px solid var(--border)",
          }}
        >
          <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            <Users size={10} style={{ marginRight: 4, verticalAlign: "middle" }} />
            \u041f\u043e\u0434\u043f\u0438\u0441\u0447\u0438\u043a\u0438
          </div>
          <div
            style={{
              fontWeight: 700,
              fontSize: 18,
              fontFamily: "'JetBrains Mono Variable', 'JetBrains Mono', 'Fira Code', monospace",
              color: "var(--text)",
            }}
          >
            {formatNumber(ch.member_count)}
          </div>
        </div>
        <div
          style={{
            background: "var(--surface-2)",
            borderRadius: 10,
            padding: "8px 10px",
            border: "1px solid var(--border)",
          }}
        >
          <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            <TrendingUp size={10} style={{ marginRight: 4, verticalAlign: "middle" }} />
            ER
          </div>
          <div
            style={{
              fontWeight: 700,
              fontSize: 18,
              color: erColor(ch.engagement_rate),
              fontFamily: "'JetBrains Mono Variable', 'JetBrains Mono', 'Fira Code', monospace",
            }}
          >
            {erLabel(ch.engagement_rate)}
          </div>
        </div>
      </div>

      {/* Tags row */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {ch.category && meta && (
          <span className="pill" style={{ background: `${meta.color}18`, color: meta.color, border: `1px solid ${meta.color}33` }}>
            {meta.icon} {ch.category}
          </span>
        )}
        <span
          className={`pill${ch.has_comments ? "" : " warning"}`}
          style={{
            background: ch.has_comments ? "var(--accent-glow)" : "rgba(90,90,94,0.15)",
            color: ch.has_comments ? "var(--accent)" : "var(--muted)",
            border: ch.has_comments ? "1px solid rgba(0,255,136,0.25)" : "1px solid var(--border)",
          }}
        >
          <MessageCircle size={11} />
          {ch.has_comments ? "\u041a\u043e\u043c\u043c\u0435\u043d\u0442\u044b" : "\u0411\u0435\u0437 \u043a\u043e\u043c\u043c\u0435\u043d\u0442."}
        </span>
      </div>

      {/* Footer */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 11, color: "var(--muted)", fontFamily: "'JetBrains Mono Variable', monospace" }}>
          {ch.last_indexed_at ? ch.last_indexed_at.slice(0, 10) : "\u2014"}
        </span>
        {onAddToCampaign && (
          <button
            type="button"
            onClick={() => onAddToCampaign(ch)}
            className="secondary-button"
            style={{
              padding: "5px 12px",
              fontSize: 12,
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            + \u0412 \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u044e
          </button>
        )}
      </div>
    </motion.div>
  );
}

function HBarChart({
  entries,
  max,
  barClass,
}: {
  entries: Array<{ label: string; value: number }>;
  max: number;
  barClass: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {entries.map(({ label, value }) => {
        const pct = max > 0 ? (value / max) * 100 : 0;
        return (
          <div key={label} style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ minWidth: 110, fontSize: 12, color: "var(--muted)" }}>{label}</span>
            <div
              style={{
                flex: 1,
                height: 8,
                background: "var(--surface-3)",
                borderRadius: 4,
                overflow: "hidden",
              }}
            >
              <div
                className={barClass}
                style={{
                  width: `${pct}%`,
                  height: "100%",
                  borderRadius: 4,
                  transition: "width 400ms ease",
                }}
              />
            </div>
            <span
              style={{
                minWidth: 40,
                textAlign: "right",
                fontSize: 12,
                color: "var(--text-secondary)",
                fontFamily: "'JetBrains Mono Variable', 'JetBrains Mono', 'Fira Code', monospace",
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

// ── main page ────────────────────────────────────────────────────────────────

type ViewMode = "cards" | "table";

export function ChannelMapPage() {
  const { accessToken } = useAuth();

  const [items, setItems] = useState<ChannelMapEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [categories, setCategories] = useState<string[]>([]);
  const [stats, setStats] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Filter state
  const [query, setQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("");
  const [selectedLanguage, setSelectedLanguage] = useState("");
  const [minMembers, setMinMembers] = useState(0);
  const [hasCommentsOnly, setHasCommentsOnly] = useState(false);
  const [selectedRegion, setSelectedRegion] = useState("");

  // UI state
  const [viewMode, setViewMode] = useState<ViewMode>("cards");

  // Memoize highlight regex (compiled once per query change, not per card)
  const highlightRe = useMemo(() => buildHighlightRe(query), [query]);

  // ── derived stats ────────────────────────────────────────────────────────

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

  const commentsCount = useMemo(() => items.filter((ch) => ch.has_comments).length, [items]);

  // Category enrichment: count / avgEr / reach from loaded items
  const categoryStats = useMemo(() => {
    const result: Record<string, { count: number; totalEr: number; erCount: number; totalReach: number }> =
      {};
    for (const ch of items) {
      const cat = ch.category ?? "\u0414\u0440\u0443\u0433\u043e\u0435";
      if (!result[cat]) result[cat] = { count: 0, totalEr: 0, erCount: 0, totalReach: 0 };
      result[cat].count += 1;
      result[cat].totalReach += ch.member_count ?? 0;
      if (ch.engagement_rate != null) {
        result[cat].totalEr += ch.engagement_rate;
        result[cat].erCount += 1;
      }
    }
    return result;
  }, [items]);

  // Member range distribution
  const memberRangeCounts = useMemo(() => {
    return MEMBER_RANGES.map((r) => ({
      label: r.label,
      value: items.filter(
        (ch) => (ch.member_count ?? 0) >= r.min && (ch.member_count ?? 0) < r.max
      ).length,
    }));
  }, [items]);

  // ── filtered display items ──────────────────────────────────────────────

  const displayItems = useMemo(() => {
    let filtered = items;
    if (hasCommentsOnly) filtered = filtered.filter((ch) => ch.has_comments);
    return filtered;
  }, [items, hasCommentsOnly]);

  // ── data loading ─────────────────────────────────────────────────────────

  const loadCategories = useCallback(async () => {
    if (!accessToken) return;
    try {
      const payload = await channelMapApi.categories(accessToken);
      setCategories(payload.categories ?? []);
    } catch {
      // categories optional
    }
  }, [accessToken]);

  const loadStats = useCallback(async () => {
    if (!accessToken) return;
    try {
      const payload = await channelMapApi.stats(accessToken);
      setStats(payload);
    } catch {
      // stats optional
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
          limit: 300,
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
    void Promise.all([loadAll(), loadCategories(), loadStats()]).catch(() => {});
  }, [accessToken]);

  useEffect(() => {
    if (!accessToken) return;
    if (query.trim()) {
      void doSearch();
    } else {
      void loadAll();
    }
  }, [selectedCategory, selectedLanguage, minMembers]);

  // Apply region filter as language shortcut
  const handleRegionClick = (regionKey: string) => {
    setSelectedRegion(regionKey);
    const region = REGION_CONFIG.find((r) => r.key === regionKey);
    if (!region || region.languages.length === 0) {
      setSelectedLanguage("");
    } else if (region.languages.length === 1) {
      setSelectedLanguage(region.languages[0]);
    } else {
      // For multi-language regions (e.g. CIS), clear language filter - handled by query
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
    void Promise.all([
      channelMapApi.list(accessToken!, {}).then((p) => {
        setItems(p.items);
        setTotal(p.total);
      }),
    ]).catch(() => {});
  };

  // ── render ────────────────────────────────────────────────────────────────

  const allCategoryNames = useMemo(
    () => [...new Set([...categories, ...Object.keys(byCategory), ...Object.keys(CATEGORY_META)])],
    [categories, byCategory],
  );

  const maxCatCount = Math.max(
    ...allCategoryNames.map(
      (c) => categoryStats[c]?.count ?? byCategory[c] ?? 0
    ),
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

  return (
    <div className="page-grid">
      {/* Mark element styling for search highlights */}
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
      `}</style>

      {/* ── Top metric cards ─────────────────────────────────────────────── */}
      <section className="dash-stats">
        <MetricCard
          label="\u0412\u0441\u0435\u0433\u043e \u043a\u0430\u043d\u0430\u043b\u043e\u0432"
          value={formatNumber(totalIndexed)}
          sub="\u0432 \u0438\u043d\u0434\u0435\u043a\u0441\u0435"
          icon={<Map size={14} />}
        />
        <MetricCard
          label="\u0421\u0443\u043c\u043c\u0430\u0440\u043d\u044b\u0439 \u043e\u0445\u0432\u0430\u0442"
          value={formatNumber(totalReach)}
          sub={`\u043f\u043e ${displayItems.length} \u043a\u0430\u043d\u0430\u043b\u0430\u043c`}
          icon={<Globe size={14} />}
        />
        <MetricCard
          label="\u0421\u0440\u0435\u0434\u043d\u0438\u0439 ER"
          value={avgEr != null ? erLabel(avgEr) : "\u2014"}
          sub="\u0432\u043e\u0432\u043b\u0435\u0447\u0451\u043d\u043d\u043e\u0441\u0442\u044c"
          accent={erColor(avgEr)}
          icon={<TrendingUp size={14} />}
        />
        <MetricCard
          label="\u0421 \u043a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u044f\u043c\u0438"
          value={
            displayItems.length > 0
              ? `${Math.round((commentsCount / displayItems.length) * 100)}%`
              : "\u2014"
          }
          sub={`${commentsCount} \u043a\u0430\u043d\u0430\u043b\u043e\u0432`}
          accent="var(--accent)"
          icon={<MessageCircle size={14} />}
        />
      </section>

      {/* ── Region selector ──────────────────────────────────────────────── */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">\u0420\u0435\u0433\u0438\u043e\u043d / \u044f\u0437\u044b\u043a</div>
            <h2 style={{ fontSize: "1.2rem" }}>\u0411\u044b\u0441\u0442\u0440\u044b\u0439 \u0444\u0438\u043b\u044c\u0442\u0440 \u043f\u043e \u0433\u0435\u043e\u0433\u0440\u0430\u0444\u0438\u0438</h2>
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
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.borderColor = "var(--border-bright)";
                    e.currentTarget.style.boxShadow = "var(--glow)";
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.borderColor = "var(--border)";
                    e.currentTarget.style.boxShadow = "none";
                  }
                }}
              >
                <span style={{ fontSize: 18 }}>{r.label}</span>
                {count > 0 && (
                  <span
                    style={{
                      fontSize: 11,
                      opacity: 0.7,
                      fontFamily: "'JetBrains Mono Variable', monospace",
                    }}
                  >
                    {formatNumber(count)}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </section>

      {/* ── Category grid ────────────────────────────────────────────────── */}
      {allCategoryNames.length > 0 && (
        <section className="panel wide">
          <div className="panel-header">
            <div>
              <div className="eyebrow">
                <Filter size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />
                \u041a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u0438
              </div>
              <h2 style={{ fontSize: "1.2rem" }}>\u0418\u043d\u0442\u0435\u0440\u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0439 \u0444\u0438\u043b\u044c\u0442\u0440 \u043f\u043e \u043d\u0438\u0448\u0435</h2>
            </div>
            {selectedCategory && (
              <button
                type="button"
                className="ghost-button"
                onClick={() => setSelectedCategory("")}
                style={{ fontSize: 13 }}
              >
                \u0421\u0431\u0440\u043e\u0441\u0438\u0442\u044c \u0444\u0438\u043b\u044c\u0442\u0440
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

      {/* ── Search form ─────────────────────────────────────────────────── */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">
              <Search size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />
              \u0423\u043c\u043d\u044b\u0439 \u043f\u043e\u0438\u0441\u043a
            </div>
            <h2 style={{ fontSize: "1.2rem" }}>\u041d\u0430\u0439\u0442\u0438 \u043a\u0430\u043d\u0430\u043b\u044b</h2>
          </div>
          {/* View toggle */}
          <div
            style={{
              display: "flex",
              background: "var(--surface-2)",
              borderRadius: 10,
              border: "1px solid var(--border)",
              padding: 3,
            }}
          >
            <button
              type="button"
              onClick={() => setViewMode("cards")}
              style={{
                all: "unset",
                cursor: "pointer",
                padding: "6px 14px",
                borderRadius: 8,
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 13,
                fontWeight: viewMode === "cards" ? 600 : 400,
                color: viewMode === "cards" ? "var(--accent)" : "var(--muted)",
                background: viewMode === "cards" ? "var(--accent-glow)" : "transparent",
                transition: "all 200ms ease",
              }}
            >
              <Grid3X3 size={14} /> \u041a\u0430\u0440\u0442\u043e\u0447\u043a\u0438
            </button>
            <button
              type="button"
              onClick={() => setViewMode("table")}
              style={{
                all: "unset",
                cursor: "pointer",
                padding: "6px 14px",
                borderRadius: 8,
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 13,
                fontWeight: viewMode === "table" ? 600 : 400,
                color: viewMode === "table" ? "var(--accent)" : "var(--muted)",
                background: viewMode === "table" ? "var(--accent-glow)" : "transparent",
                transition: "all 200ms ease",
              }}
            >
              <List size={14} /> \u0422\u0430\u0431\u043b\u0438\u0446\u0430
            </button>
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
              <span style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--text-secondary)" }}>
                <Search size={13} /> \u041a\u043b\u044e\u0447\u0435\u0432\u043e\u0435 \u0441\u043b\u043e\u0432\u043e
              </span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="\u041d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: \u043c\u0430\u0440\u043a\u0435\u0442\u0438\u043d\u0433, \u043a\u0440\u0438\u043f\u0442\u0430, e-commerce..."
              />
            </label>
            <label className="field">
              <span style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--text-secondary)" }}>
                <Globe size={13} /> \u042f\u0437\u044b\u043a
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
            <span style={{ color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 6 }}>
              <Users size={13} />
              \u041c\u0438\u043d\u0438\u043c\u0443\u043c \u043f\u043e\u0434\u043f\u0438\u0441\u0447\u0438\u043a\u043e\u0432:{" "}
              <span
                style={{
                  color: "var(--accent)",
                  fontFamily: "'JetBrains Mono Variable', monospace",
                  fontWeight: 600,
                }}
              >
                {minMembers > 0 ? formatNumber(minMembers) : "\u043d\u0435 \u0437\u0430\u0434\u0430\u043d\u043e"}
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
              \u0422\u043e\u043b\u044c\u043a\u043e \u0441 \u043a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u044f\u043c\u0438
            </label>
          </div>
          <div className="actions-row">
            <button className="primary-button" type="submit" disabled={busy}>
              <Search size={14} style={{ marginRight: 6, verticalAlign: "middle" }} />
              {busy ? "\u0418\u0449\u0435\u043c\u2026" : "\u041d\u0430\u0439\u0442\u0438"}
            </button>
            <button
              className="ghost-button"
              type="button"
              disabled={busy}
              onClick={handleReset}
            >
              \u0421\u0431\u0440\u043e\u0441\u0438\u0442\u044c \u0432\u0441\u0451
            </button>
            <button
              className="ghost-button"
              type="button"
              disabled={busy}
              onClick={() => void loadAll()}
              style={{ marginLeft: "auto" }}
            >
              \u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c
            </button>
          </div>
        </form>
      </section>

      {error ? <div className="status-banner">{error}</div> : null}

      {/* ── Results ────────────────────────────────────────────────────────── */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b</div>
            <h2 style={{ fontSize: "1.2rem" }}>
              \u041a\u0430\u043d\u0430\u043b\u044b{" "}
              {displayItems.length > 0 ? (
                <span style={{ color: "var(--muted)", fontSize: "0.8em", fontFamily: "'JetBrains Mono Variable', monospace" }}>
                  ({displayItems.length} \u0438\u0437 {total})
                </span>
              ) : null}
            </h2>
          </div>
        </div>

        {busy && <p className="muted">\u0417\u0430\u0433\u0440\u0443\u0436\u0430\u0435\u043c\u2026</p>}

        {!busy && displayItems.length === 0 && (
          <p className="muted">
            \u041a\u0430\u043d\u0430\u043b\u044b \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u044b. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0444\u0438\u043b\u044c\u0442\u0440\u044b \u0438\u043b\u0438 \u0437\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u0435
            \u0438\u043d\u0434\u0435\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0447\u0435\u0440\u0435\u0437 \u043f\u0430\u0440\u0441\u0435\u0440.
          </p>
        )}

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
                <ChannelCard key={ch.id} ch={ch} highlightRe={highlightRe} index={i} />
              ))}
            </motion.div>
          )}

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
                    <th>\u041a\u0430\u043d\u0430\u043b</th>
                    <th>\u041a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f</th>
                    <th>\u042f\u0437\u044b\u043a</th>
                    <th>\u041f\u043e\u0434\u043f\u0438\u0441\u0447\u0438\u043a\u0438</th>
                    <th>\u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0438</th>
                    <th>\u041e\u0445\u0432\u0430\u0442</th>
                    <th>ER%</th>
                    <th>\u041f\u0440\u043e\u0438\u043d\u0434\u0435\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u043d</th>
                  </tr>
                </thead>
                <tbody>
                  {displayItems.map((ch) => (
                    <tr key={ch.id}>
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
                                  fontFamily: "'JetBrains Mono Variable', monospace",
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: 4,
                                }}
                              >
                                @{ch.username}
                                <ExternalLink size={11} style={{ opacity: 0.5 }} />
                              </a>
                            ) : (
                              <span style={{ fontFamily: "'JetBrains Mono Variable', monospace", color: "var(--muted)" }}>
                                #{ch.id}
                              </span>
                            )}
                          </strong>
                          {ch.title ? (
                            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                              {ch.title}
                            </div>
                          ) : null}
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
                            {ch.subcategory ? ` / ${ch.subcategory}` : ""}
                          </span>
                        ) : (
                          <span style={{ color: "var(--muted)" }}>\u2014</span>
                        )}
                      </td>
                      <td>
                        {langFlag(ch.language)} {ch.language?.toUpperCase() ?? "\u2014"}
                      </td>
                      <td
                        style={{
                          fontWeight: 600,
                          fontFamily: "'JetBrains Mono Variable', monospace",
                          color: "var(--text)",
                        }}
                      >
                        {formatNumber(ch.member_count)}
                      </td>
                      <td>
                        <span
                          className="pill"
                          style={{
                            background: ch.has_comments ? "var(--accent-glow)" : "rgba(90,90,94,0.15)",
                            color: ch.has_comments ? "var(--accent)" : "var(--muted)",
                            border: ch.has_comments ? "1px solid rgba(0,255,136,0.25)" : "1px solid var(--border)",
                          }}
                        >
                          {ch.has_comments ? "\u0415\u0441\u0442\u044c" : "\u041d\u0435\u0442"}
                        </span>
                      </td>
                      <td style={{ fontFamily: "'JetBrains Mono Variable', monospace", color: "var(--text-secondary)" }}>
                        {formatNumber(ch.avg_post_reach)}
                      </td>
                      <td>
                        <span
                          style={{
                            color: erColor(ch.engagement_rate),
                            fontWeight: 600,
                            fontFamily: "'JetBrains Mono Variable', monospace",
                          }}
                        >
                          {erLabel(ch.engagement_rate)}
                        </span>
                      </td>
                      <td
                        style={{
                          fontSize: 11,
                          color: "var(--muted)",
                          fontFamily: "'JetBrains Mono Variable', monospace",
                        }}
                      >
                        {ch.last_indexed_at ? ch.last_indexed_at.slice(0, 10) : "\u2014"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </motion.div>
          )}
        </AnimatePresence>
      </section>

      {/* ── Distribution charts ─────────────────────────────────────────── */}
      <section
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, minmax(0,1fr))",
          gap: 20,
        }}
      >
        {/* By category */}
        {allCategoryNames.length > 0 && (
          <div className="panel">
            <div className="panel-header">
              <div>
                <div className="eyebrow">\u0420\u0430\u0441\u043f\u0440\u0435\u0434\u0435\u043b\u0435\u043d\u0438\u0435</div>
                <h2 style={{ fontSize: "1.1rem" }}>\u041f\u043e \u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f\u043c</h2>
              </div>
            </div>
            <HBarChart
              entries={allCategoryNames
                .map((cat) => ({
                  label: `${getCategoryMeta(cat).icon} ${cat}`,
                  value: categoryStats[cat]?.count ?? byCategory[cat] ?? 0,
                }))
                .sort((a, b) => b.value - a.value)
                .slice(0, 10)}
              max={maxCatCount}
              barClass="chmap-bar-cat"
            />
          </div>
        )}

        {/* By language */}
        {langEntries.length > 0 && (
          <div className="panel">
            <div className="panel-header">
              <div>
                <div className="eyebrow">\u0420\u0430\u0441\u043f\u0440\u0435\u0434\u0435\u043b\u0435\u043d\u0438\u0435</div>
                <h2 style={{ fontSize: "1.1rem" }}>\u041f\u043e \u044f\u0437\u044b\u043a\u0430\u043c</h2>
              </div>
            </div>
            <HBarChart entries={langEntries} max={maxLangCount} barClass="chmap-bar-lang" />
          </div>
        )}

        {/* By member ranges */}
        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">\u0420\u0430\u0441\u043f\u0440\u0435\u0434\u0435\u043b\u0435\u043d\u0438\u0435</div>
              <h2 style={{ fontSize: "1.1rem" }}>\u041f\u043e \u0440\u0430\u0437\u043c\u0435\u0440\u0443</h2>
            </div>
          </div>
          <HBarChart entries={memberRangeCounts} max={maxRangeCount} barClass="chmap-bar-size" />
        </div>
      </section>
    </div>
  );
}
