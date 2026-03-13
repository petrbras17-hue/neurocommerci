// ═══════════════════════════════════════════════════════════════════════════════
// NEURO COMMENTING — Channel Map: Fallback states (WebGL, empty, network error)
// ═══════════════════════════════════════════════════════════════════════════════
import React, { useMemo } from "react";
import { AlertTriangle, WifiOff, RefreshCw, Rocket } from "lucide-react";
import { DESIGN_TOKENS as T, CATEGORIES } from "./constants";

// ── Shared styles ────────────────────────────────────────────────────────────

const wrap: React.CSSProperties = {
  width: "100%", height: "100%", minHeight: "60vh",
  background: T.BG, display: "flex", flexDirection: "column",
  alignItems: "center", justifyContent: "center", padding: 32, textAlign: "center",
};
const heading: React.CSSProperties = {
  color: T.TEXT_PRIMARY, fontSize: 22, fontWeight: 700, margin: "14px 0 8px",
};
const sub: React.CSSProperties = {
  color: T.TEXT_SECONDARY, fontSize: 14, maxWidth: 420, lineHeight: 1.5,
};

// ── useWebGLCheck ────────────────────────────────────────────────────────────

export function useWebGLCheck(): boolean {
  return useMemo(() => {
    try {
      const c = document.createElement("canvas");
      return !!(c.getContext("webgl2") || c.getContext("webgl"));
    } catch {
      return false;
    }
  }, []);
}

// ── WebGLFallback ────────────────────────────────────────────────────────────

export function WebGLFallback({ channelCount }: { channelCount: number }) {
  const cats = Object.values(CATEGORIES).slice(0, 12);
  return (
    <div style={wrap}>
      <div style={{ background: `${T.ACCENT}18`, color: T.ACCENT, fontSize: 13, fontWeight: 600,
        padding: "6px 18px", borderRadius: 100, marginBottom: 24,
        border: `1px solid ${T.BORDER}` }}>
        Для интерактивной 3D карты используйте Chrome / Firefox / Edge
      </div>
      <AlertTriangle size={40} color="#f59e0b" />
      <div style={heading}>WebGL недоступен ({channelCount} каналов)</div>
      <div style={sub}>
        Ваш браузер не поддерживает WebGL, необходимый для 3D-глобуса.
        Ниже — упрощённый обзор категорий.
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center",
        marginTop: 24, maxWidth: 460 }}>
        {cats.map((c) => (
          <span key={c.label} style={{ background: `${c.color}22`, color: c.color,
            fontSize: 12, fontWeight: 600, padding: "5px 14px", borderRadius: 100,
            border: `1px solid ${c.color}44` }}>
            {c.icon} {c.label}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── EmptyGlobe ───────────────────────────────────────────────────────────────

const EMPTY_KEYFRAMES = `
@keyframes fg-spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
@keyframes fg-pulse{0%,100%{box-shadow:0 0 0 0 rgba(0,255,136,0.25)}50%{box-shadow:0 0 0 18px rgba(0,255,136,0)}}
`;

export function EmptyGlobe() {
  return (
    <div style={wrap}>
      <style>{EMPTY_KEYFRAMES}</style>
      <div style={{ position: "relative", width: 80, height: 80,
        display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ position: "absolute", inset: 0, borderRadius: "50%",
          animation: "fg-pulse 2.4s ease-in-out infinite" }} />
        <span style={{ fontSize: 48, animation: "fg-spin 3s linear infinite",
          display: "inline-block" }}>
          🌍
        </span>
      </div>
      <div style={heading}>Каналов пока нет</div>
      <div style={sub}>
        Запустите парсер, чтобы собрать каналы и увидеть их на интерактивной карте.
      </div>
      <a href="/app/parser" style={{ display: "inline-flex", alignItems: "center", gap: 8,
        marginTop: 20, padding: "10px 28px", borderRadius: 10, fontSize: 14, fontWeight: 700,
        color: T.BG, background: T.ACCENT, textDecoration: "none", border: "none",
        cursor: "pointer" }}>
        <Rocket size={16} /> Запустить парсер
      </a>
    </div>
  );
}

// ── NetworkError ─────────────────────────────────────────────────────────────

export function NetworkError({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div style={wrap}>
      <div style={{ background: "rgba(239,68,68,0.08)", backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)", border: "1px solid rgba(239,68,68,0.25)",
        borderRadius: 16, padding: "32px 28px", maxWidth: 420,
        display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
        <WifiOff size={36} color="#ef4444" />
        <div style={{ ...heading, margin: 0 }}>Ошибка загрузки</div>
        <div style={{ color: T.TEXT_SECONDARY, fontSize: 13, lineHeight: 1.5,
          background: "rgba(239,68,68,0.06)", borderRadius: 8, padding: "8px 14px",
          width: "100%", wordBreak: "break-word", fontFamily: "'JetBrains Mono', monospace" }}>
          {error}
        </div>
        <button onClick={onRetry} style={{ display: "inline-flex", alignItems: "center", gap: 8,
          marginTop: 4, padding: "9px 24px", borderRadius: 10, fontSize: 14, fontWeight: 700,
          color: T.BG, background: T.ACCENT, border: "none", cursor: "pointer" }}>
          <RefreshCw size={15} /> Повторить
        </button>
      </div>
    </div>
  );
}
