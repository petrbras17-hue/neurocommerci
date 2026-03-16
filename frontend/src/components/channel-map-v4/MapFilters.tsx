// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map v4: MapFilters
// Horizontal filter bar: Category | Language | Region | Subscriber tier pills | Search input
// All styling inline, dark terminal theme.
// ═══════════════════════════════════════════════════════════════════════════════
import React, { useState, useEffect, useRef, useCallback } from "react";
import { useAuth } from "../../auth";
import { DESIGN_TOKENS as T, CATEGORIES, LANG_FLAGS } from "../channel-map/constants";
import { useChannelSearch } from "./hooks/useChannelSearch";
import type { ChannelMapEntry } from "../../api";

// ─── Subscriber tiers ─────────────────────────────────────────────────────────

const SUBSCRIBER_TIERS: { label: string; min: number }[] = [
  { label: "Все", min: 0 },
  { label: "1K+", min: 1_000 },
  { label: "5K+", min: 5_000 },
  { label: "10K+", min: 10_000 },
  { label: "50K+", min: 50_000 },
  { label: "100K+", min: 100_000 },
  { label: "1M+", min: 1_000_000 },
];

// ─── Language options ─────────────────────────────────────────────────────────

const LANGUAGE_OPTIONS: { code: string; label: string }[] = [
  { code: "", label: "Все языки" },
  { code: "ru", label: "🇷🇺 Русский" },
  { code: "en", label: "🇺🇸 English" },
  { code: "uk", label: "🇺🇦 Украинский" },
  { code: "kz", label: "🇰🇿 Казахский" },
  { code: "de", label: "🇩🇪 Deutsch" },
  { code: "fr", label: "🇫🇷 Français" },
  { code: "es", label: "🇪🇸 Español" },
  { code: "zh", label: "🇨🇳 中文" },
  { code: "ar", label: "🇸🇦 العربية" },
  { code: "tr", label: "🇹🇷 Türkçe" },
];

// ─── Region options ───────────────────────────────────────────────────────────

const REGION_OPTIONS: { code: string; label: string }[] = [
  { code: "", label: "Все регионы" },
  { code: "ru", label: "Россия" },
  { code: "ua", label: "Украина" },
  { code: "kz", label: "Казахстан" },
  { code: "by", label: "Беларусь" },
  { code: "us", label: "США" },
  { code: "eu", label: "Европа" },
  { code: "asia", label: "Азия" },
];

// ─── Props ────────────────────────────────────────────────────────────────────

export type FiltersState = {
  category: string | null;
  language: string | null;
  region: string | null;
  minSubscribers: number;
  searchQuery: string;
};

export type MapFiltersProps = {
  onChange: (filters: FiltersState) => void;
  onSearchResultClick?: (channel: ChannelMapEntry) => void;
  /** Initial values; changes to this prop reset the dropdowns */
  initialFilters?: Partial<FiltersState>;
};

// ─── Shared input/select style ────────────────────────────────────────────────

const selectStyle: React.CSSProperties = {
  background: "#111113",
  border: "1px solid rgba(0,255,136,0.15)",
  borderRadius: 8,
  color: T.TEXT_PRIMARY,
  fontSize: 12,
  padding: "0 10px",
  height: 34,
  cursor: "pointer",
  outline: "none",
  fontFamily: "'Geist Sans', system-ui, sans-serif",
  appearance: "none" as const,
  WebkitAppearance: "none" as const,
  minWidth: 120,
};

// ─── Pill button ──────────────────────────────────────────────────────────────

interface PillProps {
  label: string;
  active: boolean;
  onClick: () => void;
}

function Pill({ label, active, onClick }: PillProps) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        background: active
          ? T.ACCENT
          : hover
          ? "rgba(0,255,136,0.08)"
          : "rgba(255,255,255,0.04)",
        border: active
          ? `1px solid ${T.ACCENT}`
          : "1px solid rgba(0,255,136,0.2)",
        borderRadius: 100,
        color: active ? "#000" : T.TEXT_PRIMARY,
        fontSize: 11,
        fontWeight: active ? 700 : 500,
        padding: "4px 10px",
        cursor: "pointer",
        whiteSpace: "nowrap" as const,
        fontFamily: "'Geist Sans', system-ui, sans-serif",
        transition: "background 0.12s, border-color 0.12s, color 0.12s",
        lineHeight: 1,
        height: 26,
      }}
    >
      {label}
    </button>
  );
}

// ─── Search dropdown ──────────────────────────────────────────────────────────

interface SearchDropdownProps {
  results: ChannelMapEntry[];
  visible: boolean;
  onSelect: (ch: ChannelMapEntry) => void;
}

function SearchDropdown({ results, visible, onSelect }: SearchDropdownProps) {
  if (!visible || results.length === 0) return null;

  return (
    <div
      style={{
        position: "absolute",
        top: "calc(100% + 4px)",
        left: 0,
        right: 0,
        background: "#111113",
        border: "1px solid rgba(0,255,136,0.2)",
        borderRadius: 10,
        boxShadow: "0 8px 32px rgba(0,0,0,0.7)",
        zIndex: 9999,
        overflow: "hidden",
        maxHeight: 320,
        overflowY: "auto",
      }}
    >
      {results.map((ch) => {
        const title = ch.title || ch.username || `#${ch.id}`;
        const subs =
          ch.member_count >= 1_000_000
            ? `${(ch.member_count / 1_000_000).toFixed(1)}M`
            : ch.member_count >= 1_000
            ? `${(ch.member_count / 1_000).toFixed(1)}K`
            : String(ch.member_count);

        return (
          <DropdownItem
            key={ch.id}
            title={title}
            username={ch.username}
            subs={subs}
            avatarUrl={ch.avatar_url}
            onClick={() => onSelect(ch)}
          />
        );
      })}
    </div>
  );
}

interface DropdownItemProps {
  title: string;
  username: string | null;
  subs: string;
  avatarUrl: string | null;
  onClick: () => void;
}

function DropdownItem({
  title,
  username,
  subs,
  avatarUrl,
  onClick,
}: DropdownItemProps) {
  const [hover, setHover] = useState(false);
  const [imgFailed, setImgFailed] = useState(false);

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        width: "100%",
        background: hover ? "rgba(0,255,136,0.06)" : "transparent",
        border: "none",
        borderBottom: "1px solid rgba(255,255,255,0.04)",
        padding: "8px 12px",
        cursor: "pointer",
        textAlign: "left",
        transition: "background 0.1s",
      }}
    >
      {avatarUrl && !imgFailed ? (
        <img
          src={avatarUrl}
          alt={title}
          style={{
            width: 32,
            height: 32,
            borderRadius: "50%",
            objectFit: "cover",
            flexShrink: 0,
          }}
          onError={() => setImgFailed(true)}
        />
      ) : (
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: "50%",
            background: "rgba(0,255,136,0.12)",
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 13,
            fontWeight: 700,
            color: T.ACCENT,
          }}
        >
          {title.charAt(0).toUpperCase()}
        </div>
      )}
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          style={{
            color: T.TEXT_PRIMARY,
            fontSize: 12,
            fontWeight: 600,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {title}
        </div>
        {username && (
          <div style={{ color: T.TEXT_SECONDARY, fontSize: 10 }}>
            @{username}
          </div>
        )}
      </div>
      <span
        style={{
          color: T.ACCENT,
          fontSize: 11,
          fontWeight: 600,
          fontFamily: "'JetBrains Mono', monospace",
          flexShrink: 0,
        }}
      >
        {subs}
      </span>
    </button>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function MapFilters({
  onChange,
  onSearchResultClick,
  initialFilters,
}: MapFiltersProps) {
  const { accessToken: token } = useAuth();

  // Local filter state
  const [category, setCategory] = useState<string>(
    initialFilters?.category ?? ""
  );
  const [language, setLanguage] = useState<string>(
    initialFilters?.language ?? ""
  );
  const [region, setRegion] = useState<string>(
    initialFilters?.region ?? ""
  );
  const [minSubs, setMinSubs] = useState<number>(
    initialFilters?.minSubscribers ?? 0
  );

  // Categories loaded from API
  const [apiCategories, setApiCategories] = useState<string[]>([]);

  // Search
  const { query, setQuery, results, loading: searchLoading, clear: clearSearch } =
    useChannelSearch();
  const [searchFocused, setSearchFocused] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  // Load categories from API on mount
  useEffect(() => {
    if (!token) return;
    const ctrl = new AbortController();

    fetch("/v1/channel-map/categories", {
      headers: { Authorization: `Bearer ${token}` },
      signal: ctrl.signal,
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.categories) {
          setApiCategories(data.categories as string[]);
        }
      })
      .catch(() => {
        // Silently ignore; fall back to hardcoded list
      });

    return () => ctrl.abort();
  }, [token]);

  // Notify parent on any filter change
  const notifyParent = useCallback(
    (
      cat: string,
      lang: string,
      reg: string,
      subs: number,
      sq: string
    ) => {
      onChange({
        category: cat || null,
        language: lang || null,
        region: reg || null,
        minSubscribers: subs,
        searchQuery: sq,
      });
    },
    [onChange]
  );

  // Sync on each individual change
  const handleCategory = (v: string) => {
    setCategory(v);
    notifyParent(v, language, region, minSubs, query);
  };
  const handleLanguage = (v: string) => {
    setLanguage(v);
    notifyParent(category, v, region, minSubs, query);
  };
  const handleRegion = (v: string) => {
    setRegion(v);
    notifyParent(category, language, v, minSubs, query);
  };
  const handleTier = (min: number) => {
    setMinSubs(min);
    notifyParent(category, language, region, min, query);
  };

  // Search input changes
  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    setQuery(v);
    setShowDropdown(v.length > 0);
    notifyParent(category, language, region, minSubs, v);
  };

  const handleSearchResultClick = (ch: ChannelMapEntry) => {
    onSearchResultClick?.(ch);
    clearSearch();
    setShowDropdown(false);
  };

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        searchRef.current &&
        !searchRef.current.contains(e.target as Node)
      ) {
        setShowDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Show dropdown when results arrive
  useEffect(() => {
    if (results.length > 0 && searchFocused) {
      setShowDropdown(true);
    }
  }, [results, searchFocused]);

  // Build category options from API list + fallback to hardcoded keys
  const categoryOptions = apiCategories.length > 0 ? apiCategories : Object.keys(CATEGORIES);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "8px 16px",
        background: "#0a0a0b",
        borderBottom: "1px solid rgba(0,255,136,0.1)",
        overflowX: "auto",
        flexWrap: "nowrap",
        minHeight: 52,
        fontFamily: "'Geist Sans', system-ui, sans-serif",
      }}
    >
      {/* Category dropdown */}
      <div style={{ position: "relative", flexShrink: 0 }}>
        <select
          value={category}
          onChange={(e) => handleCategory(e.target.value)}
          style={selectStyle}
          title="Категория"
        >
          <option value="">Все категории</option>
          {categoryOptions.map((cat) => {
            const meta = CATEGORIES[cat as keyof typeof CATEGORIES];
            const label = meta ? `${meta.icon} ${meta.label}` : cat;
            return (
              <option key={cat} value={cat}>
                {label}
              </option>
            );
          })}
        </select>
        {/* Custom chevron */}
        <span
          style={{
            position: "absolute",
            right: 8,
            top: "50%",
            transform: "translateY(-50%)",
            pointerEvents: "none",
            color: T.TEXT_SECONDARY,
            fontSize: 10,
          }}
        >
          ▾
        </span>
      </div>

      {/* Language dropdown */}
      <div style={{ position: "relative", flexShrink: 0 }}>
        <select
          value={language}
          onChange={(e) => handleLanguage(e.target.value)}
          style={{ ...selectStyle, minWidth: 110 }}
          title="Язык"
        >
          {LANGUAGE_OPTIONS.map((opt) => (
            <option key={opt.code} value={opt.code}>
              {opt.label}
            </option>
          ))}
        </select>
        <span
          style={{
            position: "absolute",
            right: 8,
            top: "50%",
            transform: "translateY(-50%)",
            pointerEvents: "none",
            color: T.TEXT_SECONDARY,
            fontSize: 10,
          }}
        >
          ▾
        </span>
      </div>

      {/* Region dropdown */}
      <div style={{ position: "relative", flexShrink: 0 }}>
        <select
          value={region}
          onChange={(e) => handleRegion(e.target.value)}
          style={{ ...selectStyle, minWidth: 110 }}
          title="Регион"
        >
          {REGION_OPTIONS.map((opt) => (
            <option key={opt.code} value={opt.code}>
              {opt.label}
            </option>
          ))}
        </select>
        <span
          style={{
            position: "absolute",
            right: 8,
            top: "50%",
            transform: "translateY(-50%)",
            pointerEvents: "none",
            color: T.TEXT_SECONDARY,
            fontSize: 10,
          }}
        >
          ▾
        </span>
      </div>

      {/* Divider */}
      <div
        style={{
          width: 1,
          height: 24,
          background: "rgba(255,255,255,0.1)",
          flexShrink: 0,
          margin: "0 4px",
        }}
      />

      {/* Subscriber tier pills */}
      <div
        style={{
          display: "flex",
          gap: 4,
          alignItems: "center",
          flexShrink: 0,
        }}
      >
        {SUBSCRIBER_TIERS.map((tier) => (
          <Pill
            key={tier.min}
            label={tier.label}
            active={minSubs === tier.min}
            onClick={() => handleTier(tier.min)}
          />
        ))}
      </div>

      {/* Spacer pushes search to the right */}
      <div style={{ flex: 1, minWidth: 8 }} />

      {/* Search input */}
      <div
        ref={searchRef}
        style={{
          position: "relative",
          flexShrink: 0,
          width: 220,
        }}
      >
        <input
          type="text"
          placeholder="Поиск каналов..."
          value={query}
          onChange={handleSearchChange}
          onFocus={() => {
            setSearchFocused(true);
            if (results.length > 0) setShowDropdown(true);
          }}
          onBlur={() => setSearchFocused(false)}
          style={{
            background: "#111113",
            border: `1px solid ${searchFocused ? "rgba(0,255,136,0.4)" : "rgba(0,255,136,0.15)"}`,
            borderRadius: 8,
            color: T.TEXT_PRIMARY,
            fontSize: 12,
            padding: "0 32px 0 10px",
            height: 34,
            width: "100%",
            outline: "none",
            fontFamily: "'Geist Sans', system-ui, sans-serif",
            boxSizing: "border-box",
            transition: "border-color 0.15s",
          }}
        />
        {/* Search / loading icon */}
        <span
          style={{
            position: "absolute",
            right: 10,
            top: "50%",
            transform: "translateY(-50%)",
            fontSize: 12,
            pointerEvents: "none",
            color: searchLoading ? T.ACCENT : T.TEXT_MUTED,
          }}
        >
          {searchLoading ? "⟳" : "⌕"}
        </span>

        {/* Results dropdown */}
        <SearchDropdown
          results={results}
          visible={showDropdown}
          onSelect={handleSearchResultClick}
        />
      </div>
    </div>
  );
}
