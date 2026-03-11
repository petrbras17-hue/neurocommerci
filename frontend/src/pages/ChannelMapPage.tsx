import { useEffect, useState, useMemo, useCallback } from "react";
import { channelMapApi, ChannelMapEntry } from "../api";
import { useAuth } from "../auth";

// ─── helpers ─────────────────────────────────────────────────────────────────

function formatNumber(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function erColor(rate: number | null): string {
  if (rate == null) return "var(--muted)";
  if (rate >= 0.05) return "#22c55e";
  if (rate >= 0.02) return "#f59e0b";
  return "#ef4444";
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

function highlight(text: string, query: string): string {
  if (!query.trim()) return text;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return text.replace(new RegExp(`(${escaped})`, "gi"), "<mark>$1</mark>");
}

// ─── constants ────────────────────────────────────────────────────────────────

const REGION_CONFIG: Array<{ label: string; key: string; languages: string[] }> = [
  { label: "🇷🇺 RU", key: "ru", languages: ["ru"] },
  { label: "🏳️ СНГ", key: "cis", languages: ["ru", "uk", "kz"] },
  { label: "🇺🇦 UA", key: "uk", languages: ["uk"] },
  { label: "🇰🇿 KZ", key: "kz", languages: ["kz"] },
  { label: "🇺🇸 EN", key: "en", languages: ["en"] },
  { label: "🌐 Все", key: "", languages: [] },
];

const CATEGORY_META: Record<string, { icon: string; color: string }> = {
  Crypto:        { icon: "₿",  color: "#f59e0b" },
  Marketing:     { icon: "📢", color: "#6366f1" },
  "E-commerce":  { icon: "🛒", color: "#22c55e" },
  EdTech:        { icon: "🎓", color: "#3b82f6" },
  News:          { icon: "📰", color: "#64748b" },
  Entertainment: { icon: "🎬", color: "#ec4899" },
  Tech:          { icon: "💻", color: "#8b5cf6" },
  Finance:       { icon: "💹", color: "#10b981" },
  Lifestyle:     { icon: "✨", color: "#f97316" },
  Health:        { icon: "🏥", color: "#14b8a6" },
  Gaming:        { icon: "🎮", color: "#7c3aed" },
  "18+":         { icon: "🔞", color: "#dc2626" },
  Politics:      { icon: "🏛️", color: "#0ea5e9" },
  Sports:        { icon: "⚽", color: "#84cc16" },
  Travel:        { icon: "✈️", color: "#06b6d4" },
};

const LANGUAGE_OPTIONS = [
  { value: "", label: "Все языки" },
  { value: "ru", label: "Русский" },
  { value: "en", label: "English" },
  { value: "uk", label: "Українська" },
  { value: "kz", label: "Қазақша" },
];

const MEMBER_RANGES = [
  { label: "0–1K",    min: 0,       max: 1_000 },
  { label: "1K–10K",  min: 1_000,   max: 10_000 },
  { label: "10K–100K",min: 10_000,  max: 100_000 },
  { label: "100K–1M", min: 100_000, max: 1_000_000 },
  { label: "1M+",     min: 1_000_000, max: Infinity },
];

// ─── sub-components ───────────────────────────────────────────────────────────

function MetricCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div
      style={{
        background: "rgba(255,253,247,0.95)",
        border: "1px solid rgba(224,210,191,0.9)",
        borderRadius: 20,
        padding: "20px 22px",
        boxShadow: "0 4px 20px rgba(56,39,17,0.07)",
        display: "grid",
        gap: 6,
      }}
    >
      <div style={{ fontSize: 12, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--muted)" }}>
        {label}
      </div>
      <div
        style={{
          fontSize: "clamp(1.5rem, 3vw, 2rem)",
          fontWeight: 700,
          color: accent ?? "var(--text)",
          lineHeight: 1.1,
        }}
      >
        {value}
      </div>
      {sub && <div style={{ fontSize: 12, color: "var(--muted)" }}>{sub}</div>}
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
  const meta = CATEGORY_META[name] ?? { icon: "📌", color: "#6366f1" };
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        all: "unset",
        cursor: "pointer",
        background: selected
          ? `linear-gradient(135deg, ${meta.color}22, ${meta.color}11)`
          : "rgba(255,253,247,0.9)",
        border: selected ? `2px solid ${meta.color}` : "1px solid rgba(224,210,191,0.9)",
        borderRadius: 18,
        padding: "14px 16px",
        display: "grid",
        gap: 8,
        transition: "all 160ms ease",
        boxShadow: selected ? `0 0 0 3px ${meta.color}22` : "0 2px 8px rgba(56,39,17,0.05)",
        minWidth: 0,
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
        }}
      >
        {formatNumber(count)}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <div style={{ fontSize: 11, color: "var(--muted)" }}>
          Охват {formatNumber(totalReach)}
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
  query,
  onAddToCampaign,
}: {
  ch: ChannelMapEntry;
  query: string;
  onAddToCampaign?: (ch: ChannelMapEntry) => void;
}) {
  const firstLetter = (ch.title ?? ch.username ?? "?")[0].toUpperCase();
  const gradients = [
    "linear-gradient(135deg, #6366f1, #8b5cf6)",
    "linear-gradient(135deg, #f59e0b, #ef4444)",
    "linear-gradient(135deg, #22c55e, #10b981)",
    "linear-gradient(135deg, #3b82f6, #06b6d4)",
    "linear-gradient(135deg, #ec4899, #f97316)",
    "linear-gradient(135deg, #8b5cf6, #3b82f6)",
  ];
  const gradientIndex = (ch.id ?? 0) % gradients.length;
  const meta = ch.category ? (CATEGORY_META[ch.category] ?? { icon: "📌", color: "#6366f1" }) : null;

  const titleHtml = ch.title ? highlight(ch.title, query) : null;
  const usernameHtml = ch.username ? highlight(`@${ch.username}`, query) : null;

  return (
    <div
      style={{
        background: "rgba(255,253,247,0.97)",
        border: "1px solid rgba(224,210,191,0.9)",
        borderRadius: 20,
        padding: "18px 18px 14px",
        display: "grid",
        gap: 12,
        boxShadow: "0 2px 12px rgba(56,39,17,0.06)",
        transition: "box-shadow 160ms",
      }}
      onMouseEnter={(e) =>
        (e.currentTarget.style.boxShadow = "0 6px 24px rgba(56,39,17,0.12)")
      }
      onMouseLeave={(e) =>
        (e.currentTarget.style.boxShadow = "0 2px 12px rgba(56,39,17,0.06)")
      }
    >
      {/* Header row */}
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        {/* Avatar */}
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: 14,
            background: gradients[gradientIndex],
            display: "grid",
            placeItems: "center",
            color: "white",
            fontWeight: 700,
            fontSize: 18,
            flexShrink: 0,
          }}
        >
          {firstLetter}
        </div>
        {/* Name + username */}
        <div style={{ minWidth: 0, flex: 1 }}>
          {ch.title && (
            <div
              style={{ fontWeight: 600, fontSize: 14, lineHeight: 1.3, marginBottom: 2 }}
              dangerouslySetInnerHTML={{ __html: titleHtml ?? ch.title }}
            />
          )}
          {ch.username ? (
            <a
              href={`https://t.me/${ch.username}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{ fontSize: 12, color: "var(--accent)", fontWeight: 500 }}
              dangerouslySetInnerHTML={{ __html: usernameHtml ?? `@${ch.username}` }}
            />
          ) : (
            <span style={{ fontSize: 12, color: "var(--muted)" }}>#{ch.id}</span>
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
            background: "rgba(242,230,212,0.45)",
            borderRadius: 12,
            padding: "8px 10px",
          }}
        >
          <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Подписчики
          </div>
          <div style={{ fontWeight: 700, fontSize: 16 }}>{formatNumber(ch.member_count)}</div>
        </div>
        <div
          style={{
            background: "rgba(242,230,212,0.45)",
            borderRadius: 12,
            padding: "8px 10px",
          }}
        >
          <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            ER
          </div>
          <div style={{ fontWeight: 700, fontSize: 16, color: erColor(ch.engagement_rate) }}>
            {erLabel(ch.engagement_rate)}
          </div>
        </div>
      </div>

      {/* Tags row */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {ch.category && meta && (
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "3px 8px",
              borderRadius: 999,
              background: `${meta.color}18`,
              color: meta.color,
              border: `1px solid ${meta.color}33`,
              fontSize: 11,
              fontWeight: 600,
            }}
          >
            {meta.icon} {ch.category}
          </span>
        )}
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "3px 8px",
            borderRadius: 999,
            background: ch.has_comments ? "rgba(34,197,94,0.12)" : "rgba(107,93,78,0.10)",
            color: ch.has_comments ? "#16a34a" : "var(--muted)",
            border: ch.has_comments ? "1px solid rgba(34,197,94,0.3)" : "1px solid rgba(107,93,78,0.15)",
            fontSize: 11,
            fontWeight: 600,
          }}
        >
          {ch.has_comments ? "💬 Комменты" : "Без коммент."}
        </span>
      </div>

      {/* Footer */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 11, color: "var(--muted)" }}>
          {ch.last_indexed_at ? ch.last_indexed_at.slice(0, 10) : "—"}
        </span>
        {onAddToCampaign && (
          <button
            type="button"
            onClick={() => onAddToCampaign(ch)}
            style={{
              background: "linear-gradient(135deg, var(--accent) 0%, #ef7a2f 100%)",
              color: "white",
              border: "none",
              borderRadius: 10,
              padding: "6px 12px",
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            + В кампанию
          </button>
        )}
      </div>
    </div>
  );
}

function HBarChart({
  entries,
  max,
  color,
}: {
  entries: Array<{ label: string; value: number }>;
  max: number;
  color: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {entries.map(({ label, value }) => {
        const pct = max > 0 ? (value / max) * 100 : 0;
        return (
          <div key={label} style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ minWidth: 110, fontSize: 12, color: "var(--text)" }}>{label}</span>
            <div
              style={{
                flex: 1,
                height: 8,
                background: "rgba(224,210,191,0.5)",
                borderRadius: 4,
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${pct}%`,
                  height: "100%",
                  background: color,
                  borderRadius: 4,
                  transition: "width 400ms ease",
                }}
              />
            </div>
            <span style={{ minWidth: 40, textAlign: "right", fontSize: 12, color: "var(--muted)" }}>
              {formatNumber(value)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ─── main page ────────────────────────────────────────────────────────────────

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
      const cat = ch.category ?? "Другое";
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

  const allCategoryNames = [
    ...new Set([...categories, ...Object.keys(byCategory), ...Object.keys(CATEGORY_META)]),
  ];

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
      {/* ── Top metric cards ─────────────────────────────────────────────── */}
      <section style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 16 }}>
        <MetricCard
          label="Всего каналов"
          value={formatNumber(totalIndexed)}
          sub="в индексе"
        />
        <MetricCard
          label="Суммарный охват"
          value={formatNumber(totalReach)}
          sub={`по ${displayItems.length} каналам`}
        />
        <MetricCard
          label="Средний ER"
          value={avgEr != null ? erLabel(avgEr) : "—"}
          sub="вовлечённость"
          accent={erColor(avgEr)}
        />
        <MetricCard
          label="С комментариями"
          value={
            displayItems.length > 0
              ? `${Math.round((commentsCount / displayItems.length) * 100)}%`
              : "—"
          }
          sub={`${commentsCount} каналов`}
          accent="#22c55e"
        />
      </section>

      {/* ── Region selector ──────────────────────────────────────────────── */}
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
                  borderRadius: 16,
                  background: isActive
                    ? "linear-gradient(135deg, var(--accent) 0%, #ef7a2f 100%)"
                    : "rgba(255,253,247,0.9)",
                  border: isActive ? "2px solid var(--accent)" : "1px solid rgba(224,210,191,0.9)",
                  color: isActive ? "white" : "var(--text)",
                  fontWeight: isActive ? 700 : 500,
                  transition: "all 160ms ease",
                  boxShadow: isActive ? "0 4px 16px rgba(204,75,24,0.25)" : "none",
                  minWidth: 80,
                  textAlign: "center",
                }}
              >
                <span style={{ fontSize: 18 }}>{r.label}</span>
                {count > 0 && (
                  <span style={{ fontSize: 11, opacity: 0.8 }}>{formatNumber(count)}</span>
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
              <div className="eyebrow">Категории</div>
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

      {/* ── Search form ─────────────────────────────────────────────────── */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Умный поиск</div>
            <h2 style={{ fontSize: "1.2rem" }}>Найти каналы</h2>
          </div>
          {/* View toggle */}
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              onClick={() => setViewMode("cards")}
              style={{
                padding: "8px 16px",
                borderRadius: 12,
                border: viewMode === "cards" ? "2px solid var(--accent)" : "1px solid var(--border)",
                background: viewMode === "cards" ? "rgba(204,75,24,0.08)" : "transparent",
                cursor: "pointer",
                fontWeight: viewMode === "cards" ? 700 : 400,
                fontSize: 13,
                color: viewMode === "cards" ? "var(--accent)" : "var(--muted)",
              }}
            >
              ▦ Карточки
            </button>
            <button
              type="button"
              onClick={() => setViewMode("table")}
              style={{
                padding: "8px 16px",
                borderRadius: 12,
                border: viewMode === "table" ? "2px solid var(--accent)" : "1px solid var(--border)",
                background: viewMode === "table" ? "rgba(204,75,24,0.08)" : "transparent",
                cursor: "pointer",
                fontWeight: viewMode === "table" ? 700 : 400,
                fontSize: 13,
                color: viewMode === "table" ? "var(--accent)" : "var(--muted)",
              }}
            >
              ≡ Таблица
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
              <span>Ключевое слово</span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Например: маркетинг, крипта, e-commerce..."
              />
            </label>
            <label className="field">
              <span>Язык</span>
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
            <span>
              Минимум подписчиков:{" "}
              {minMembers > 0 ? formatNumber(minMembers) : "не задано"}
            </span>
            <input
              type="range"
              min={0}
              max={1_000_000}
              step={5_000}
              value={minMembers}
              onChange={(e) => setMinMembers(Number(e.target.value))}
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
              }}
            >
              <input
                type="checkbox"
                checked={hasCommentsOnly}
                onChange={(e) => setHasCommentsOnly(e.target.checked)}
                style={{ width: 16, height: 16 }}
              />
              Только с комментариями
            </label>
          </div>
          <div className="actions-row">
            <button className="primary-button" type="submit" disabled={busy}>
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
              Обновить
            </button>
          </div>
        </form>
      </section>

      {error ? <div className="status-banner">{error}</div> : null}

      {/* ── Results ────────────────────────────────────────────────────────── */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Результаты</div>
            <h2 style={{ fontSize: "1.2rem" }}>
              Каналы{" "}
              {displayItems.length > 0 ? (
                <span style={{ color: "var(--muted)", fontSize: "0.8em" }}>
                  ({displayItems.length} из {total})
                </span>
              ) : null}
            </h2>
          </div>
        </div>

        {busy && <p className="muted">Загружаем…</p>}

        {!busy && displayItems.length === 0 && (
          <p className="muted">
            Каналы не найдены. Попробуйте изменить фильтры или запустите
            индексирование через парсер.
          </p>
        )}

        {!busy && displayItems.length > 0 && viewMode === "cards" && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
              gap: 16,
            }}
          >
            {displayItems.map((ch) => (
              <ChannelCard key={ch.id} ch={ch} query={query} />
            ))}
          </div>
        )}

        {!busy && displayItems.length > 0 && viewMode === "table" && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
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
                  <tr key={ch.id}>
                    <td>
                      <div>
                        <strong>
                          {ch.username ? (
                            <a
                              href={`https://t.me/${ch.username}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              style={{ color: "var(--accent)" }}
                            >
                              @{ch.username}
                            </a>
                          ) : (
                            `#${ch.id}`
                          )}
                        </strong>
                        {ch.title ? (
                          <div className="muted" style={{ fontSize: 11 }}>
                            {ch.title}
                          </div>
                        ) : null}
                      </div>
                    </td>
                    <td>
                      {ch.category ? (
                        <span
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 4,
                            padding: "3px 8px",
                            borderRadius: 999,
                            background: `${(CATEGORY_META[ch.category] ?? { color: "#6366f1" }).color}18`,
                            color: (CATEGORY_META[ch.category] ?? { color: "#6366f1" }).color,
                            fontSize: 11,
                            fontWeight: 600,
                          }}
                        >
                          {(CATEGORY_META[ch.category] ?? { icon: "📌" }).icon} {ch.category}
                          {ch.subcategory ? ` / ${ch.subcategory}` : ""}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      {langFlag(ch.language)} {ch.language?.toUpperCase() ?? "—"}
                    </td>
                    <td style={{ fontWeight: 600 }}>{formatNumber(ch.member_count)}</td>
                    <td>
                      <span
                        style={{
                          padding: "3px 8px",
                          borderRadius: 999,
                          background: ch.has_comments
                            ? "rgba(34,197,94,0.12)"
                            : "rgba(107,93,78,0.10)",
                          color: ch.has_comments ? "#16a34a" : "var(--muted)",
                          border: ch.has_comments
                            ? "1px solid rgba(34,197,94,0.3)"
                            : "1px solid rgba(107,93,78,0.15)",
                          fontSize: 11,
                          fontWeight: 600,
                        }}
                      >
                        {ch.has_comments ? "Есть" : "Нет"}
                      </span>
                    </td>
                    <td>{formatNumber(ch.avg_post_reach)}</td>
                    <td>
                      <span style={{ color: erColor(ch.engagement_rate), fontWeight: 600 }}>
                        {erLabel(ch.engagement_rate)}
                      </span>
                    </td>
                    <td className="muted" style={{ fontSize: 11 }}>
                      {ch.last_indexed_at ? ch.last_indexed_at.slice(0, 10) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
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
                <div className="eyebrow">Распределение</div>
                <h2 style={{ fontSize: "1.1rem" }}>По категориям</h2>
              </div>
            </div>
            <HBarChart
              entries={allCategoryNames
                .map((cat) => ({
                  label: `${(CATEGORY_META[cat] ?? { icon: "📌" }).icon} ${cat}`,
                  value: categoryStats[cat]?.count ?? byCategory[cat] ?? 0,
                }))
                .sort((a, b) => b.value - a.value)
                .slice(0, 10)}
              max={maxCatCount}
              color="linear-gradient(90deg, #6366f1, #8b5cf6)"
            />
          </div>
        )}

        {/* By language */}
        {langEntries.length > 0 && (
          <div className="panel">
            <div className="panel-header">
              <div>
                <div className="eyebrow">Распределение</div>
                <h2 style={{ fontSize: "1.1rem" }}>По языкам</h2>
              </div>
            </div>
            <HBarChart entries={langEntries} max={maxLangCount} color="linear-gradient(90deg, #f59e0b, #ef4444)" />
          </div>
        )}

        {/* By member ranges */}
        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Распределение</div>
              <h2 style={{ fontSize: "1.1rem" }}>По размеру</h2>
            </div>
          </div>
          <HBarChart entries={memberRangeCounts} max={maxRangeCount} color="linear-gradient(90deg, #22c55e, #10b981)" />
        </div>
      </section>
    </div>
  );
}
