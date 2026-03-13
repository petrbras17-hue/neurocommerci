// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map: Cmd+K search overlay
// ═══════════════════════════════════════════════════════════════════════════════

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Search, X } from "lucide-react";
import type { ChannelMapEntry } from "../../api";
import {
  DESIGN_TOKENS as T,
  getCategoryMeta,
  REGION_COORDS,
  LANG_FLAGS,
  formatNumber,
} from "./constants";

// ── Region labels ────────────────────────────────────────────────────────────

const REGION_LABELS: Record<string, string> = {
  ru: "Россия", en: "США", uk: "Украина", kz: "Казахстан", de: "Германия",
  fr: "Франция", es: "Испания", zh: "Китай", ar: "Сауд. Аравия", ja: "Япония",
  ko: "Корея", pt: "Бразилия", it: "Италия", tr: "Турция", pl: "Польша",
  nl: "Нидерланды", hi: "Индия", th: "Таиланд", vi: "Вьетнам", id: "Индонезия",
};

// ── Types ────────────────────────────────────────────────────────────────────

export type SearchOverlayProps = {
  isOpen: boolean;
  onClose: () => void;
  onSelectChannel: (channel: ChannelMapEntry) => void;
  onSelectCategory: (categoryKey: string) => void;
  onSelectRegion: (region: { label: string; lat: number; lng: number; altitude: number }) => void;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  searchResults: ChannelMapEntry[];
  categories: string[];
};

type ResultItem =
  | { kind: "channel"; data: ChannelMapEntry }
  | { kind: "category"; key: string }
  | { kind: "region"; key: string };

// ── Hook: Cmd+K shortcut ────────────────────────────────────────────────────

export function useSearchShortcut(onOpen: () => void) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        onOpen();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onOpen]);
}

// ── Component ───────────────────────────────────────────────────────────────

export function SearchOverlay(props: SearchOverlayProps) {
  const {
    isOpen, onClose, onSelectChannel, onSelectCategory, onSelectRegion,
    searchQuery, onSearchChange, searchResults, categories,
  } = props;

  const inputRef = useRef<HTMLInputElement>(null);
  const [activeIdx, setActiveIdx] = useState(0);
  const q = searchQuery.trim().toLowerCase();

  const flat = useMemo<ResultItem[]>(() => {
    const items: ResultItem[] = [];
    const matchedCats = q
      ? categories.filter((k) => {
          const meta = getCategoryMeta(k);
          return k.toLowerCase().includes(q) || meta.label.toLowerCase().includes(q);
        })
      : [];
    const matchedRegions = q
      ? Object.keys(REGION_COORDS).filter((k) => {
          const label = REGION_LABELS[k] ?? k;
          return label.toLowerCase().includes(q) || k.toLowerCase().includes(q);
        })
      : [];
    matchedCats.forEach((k) => items.push({ kind: "category", key: k }));
    matchedRegions.forEach((k) => items.push({ kind: "region", key: k }));
    searchResults.slice(0, 20).forEach((ch) => items.push({ kind: "channel", data: ch }));
    return items;
  }, [q, categories, searchResults]);

  useEffect(() => { setActiveIdx(0); }, [flat.length]);
  useEffect(() => { if (isOpen) setTimeout(() => inputRef.current?.focus(), 50); }, [isOpen]);

  const select = useCallback((item: ResultItem) => {
    if (item.kind === "channel") onSelectChannel(item.data);
    else if (item.kind === "category") onSelectCategory(item.key);
    else {
      const coords = REGION_COORDS[item.key];
      onSelectRegion({ label: REGION_LABELS[item.key] ?? item.key, lat: coords[0], lng: coords[1], altitude: 1.8 });
    }
    onClose();
  }, [onSelectChannel, onSelectCategory, onSelectRegion, onClose]);

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Escape") { onClose(); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); setActiveIdx((i) => Math.min(i + 1, flat.length - 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setActiveIdx((i) => Math.max(i - 1, 0)); }
    if (e.key === "Enter" && flat[activeIdx]) { e.preventDefault(); select(flat[activeIdx]); }
  }, [flat, activeIdx, select, onClose]);

  const sectionHeader = (label: string) => (
    <div style={{ padding: "6px 16px", fontSize: 11, fontWeight: 600, color: T.TEXT_SECONDARY, textTransform: "uppercase" as const, letterSpacing: 1 }}>
      {label}
    </div>
  );

  const renderItem = (item: ResultItem, idx: number) => {
    const active = idx === activeIdx;
    let icon = "", title = "", sub = "";
    if (item.kind === "category") {
      const m = getCategoryMeta(item.key);
      icon = m.icon; title = m.label; sub = item.key;
    } else if (item.kind === "region") {
      icon = LANG_FLAGS[item.key] ?? "\u{1F310}"; title = REGION_LABELS[item.key] ?? item.key; sub = item.key;
    } else {
      icon = getCategoryMeta(item.data.category ?? "").icon; title = item.data.title || item.data.username || "—";
      sub = `${item.data.username ? "@" + item.data.username : ""} \u00B7 ${formatNumber(item.data.member_count)}`;
    }
    const itemKey = item.kind === "channel" ? `ch-${item.data.id}` : `${item.kind}-${item.key}`;
    return (
      <div key={itemKey} onMouseEnter={() => setActiveIdx(idx)} onClick={() => select(item)}
        style={{
          display: "flex", alignItems: "center", gap: 10, padding: "8px 16px", cursor: "pointer",
          background: active ? "rgba(0,255,136,0.1)" : "transparent",
          borderLeft: active ? `2px solid ${T.ACCENT}` : "2px solid transparent",
          transition: "background 0.15s",
        }}>
        <span style={{ fontSize: 18, width: 24, textAlign: "center" as const }}>{icon}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ color: T.TEXT_PRIMARY, fontSize: 14, fontWeight: 500, whiteSpace: "nowrap" as const, overflow: "hidden", textOverflow: "ellipsis" }}>{title}</div>
          <div style={{ color: T.TEXT_SECONDARY, fontSize: 12, whiteSpace: "nowrap" as const, overflow: "hidden", textOverflow: "ellipsis" }}>{sub}</div>
        </div>
      </div>
    );
  };

  let sectionIdx = 0;
  const catItems = flat.filter((i) => i.kind === "category");
  const regItems = flat.filter((i) => i.kind === "region");
  const chItems = flat.filter((i) => i.kind === "channel");

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}
          onClick={onClose}
          style={{ position: "fixed", inset: 0, zIndex: 9999, background: "rgba(0,0,0,0.6)", backdropFilter: "blur(6px)", display: "flex", alignItems: "flex-start", justifyContent: "center", paddingTop: "12vh" }}>
          <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.95 }} transition={{ duration: 0.15 }}
            onClick={(e) => e.stopPropagation()} onKeyDown={onKeyDown}
            style={{ width: "100%", maxWidth: 560, background: T.SURFACE, border: `1px solid ${T.BORDER}`, borderRadius: 16, backdropFilter: "blur(24px)", overflow: "hidden", boxShadow: `0 24px 64px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.04)` }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "14px 16px", borderBottom: `1px solid ${T.BORDER}` }}>
              <Search size={18} color={T.TEXT_SECONDARY} />
              <input ref={inputRef} value={searchQuery} onChange={(e) => onSearchChange(e.target.value)} placeholder="Поиск каналов..."
                style={{ flex: 1, background: "transparent", border: "none", outline: "none", color: T.TEXT_PRIMARY, fontSize: 15, fontFamily: "inherit" }} />
              {searchQuery && <X size={16} color={T.TEXT_SECONDARY} style={{ cursor: "pointer" }} onClick={() => onSearchChange("")} />}
              <kbd style={{ fontSize: 11, color: T.TEXT_SECONDARY, background: "rgba(255,255,255,0.06)", padding: "2px 6px", borderRadius: 4, border: `1px solid ${T.BORDER}` }}>ESC</kbd>
            </div>
            <div style={{ maxHeight: 360, overflowY: "auto" }}>
              {!q && <div style={{ padding: 24, textAlign: "center", color: T.TEXT_SECONDARY, fontSize: 13 }}>Начните вводить для поиска</div>}
              {q && flat.length === 0 && <div style={{ padding: 24, textAlign: "center", color: T.TEXT_SECONDARY, fontSize: 13 }}>Нет результатов</div>}
              {q && catItems.length > 0 && <>{sectionHeader("Категории")}{catItems.map((it) => renderItem(it, sectionIdx++))}</>}
              {q && regItems.length > 0 && <>{sectionHeader("Регионы")}{regItems.map((it) => renderItem(it, sectionIdx++))}</>}
              {q && chItems.length > 0 && <>{sectionHeader("Каналы")}{chItems.map((it) => renderItem(it, sectionIdx++))}</>}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
