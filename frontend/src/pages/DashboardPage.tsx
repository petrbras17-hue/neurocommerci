import { useEffect, useMemo, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { apiFetch } from "../api";
import { useAuth } from "../auth";

/* ─── Types ────────────────────────────────────────────────────────────────── */

type AccountsResponse = {
  items: Array<{ status: string; health_status: string; recommended_next_action: string }>;
  total: number;
};

type ProxiesResponse = {
  items: Array<{ health_status: string }>;
  total: number;
  summary?: Record<string, unknown>;
};

type ContextResponse = {
  brief: {
    completeness_score: number;
    assistant_ready: boolean;
    missing_fields: string[];
    assets_count: number;
    draft_count: number;
    status: string;
  };
};

type CreativeResponse = {
  total: number;
  items: Array<{ status: string }>;
};

type QualitySummary = {
  overview: {
    total_requests: number;
    avg_quality_score: number;
    fallback_rate: number;
    repair_rate: number;
  };
  latest_by_task: Record<
    string,
    {
      provider: string | null;
      model: string | null;
      quality_score: number;
      fallback_used: boolean;
      repair_applied: boolean;
      latency_ms: number | null;
    }
  >;
};

type LoadState = "loading" | "loaded" | "error";

/* ─── Helpers ──────────────────────────────────────────────────────────────── */

const MONO: React.CSSProperties = {
  fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)",
};

function formatDate(): string {
  return new Date().toLocaleDateString("ru-RU", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function avgLatency(latest: QualitySummary["latest_by_task"]): number {
  const entries = Object.values(latest).filter((e) => e.latency_ms != null);
  if (!entries.length) return 0;
  return Math.round(entries.reduce((s, e) => s + (e.latency_ms ?? 0), 0) / entries.length);
}

function avgCostLabel(total: number): string {
  if (!total) return "$0.00";
  // Rough estimate: avg $0.002 per request for manager-tier
  return `~$${(total * 0.002).toFixed(2)}`;
}

/* ─── Animation variants ───────────────────────────────────────────────────── */

const containerVariants = {
  hidden: {},
  visible: {
    transition: { staggerChildren: 0.06 },
  },
};

const itemVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.35, ease: "easeOut" as const } },
};

/* ─── Component ────────────────────────────────────────────────────────────── */

export function DashboardPage() {
  const { accessToken, profile } = useAuth();
  const navigate = useNavigate();

  const [accounts, setAccounts] = useState<AccountsResponse | null>(null);
  const [proxies, setProxies] = useState<ProxiesResponse | null>(null);
  const [context, setContext] = useState<ContextResponse | null>(null);
  const [creative, setCreative] = useState<CreativeResponse | null>(null);
  const [aiQuality, setAiQuality] = useState<QualitySummary | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");

  const fetchAll = useCallback(() => {
    if (!accessToken) return;
    setLoadState("loading");
    void Promise.all([
      apiFetch<AccountsResponse>("/v1/web/accounts", { accessToken }),
      apiFetch<ProxiesResponse>("/v1/web/proxies/available", { accessToken }),
      apiFetch<ContextResponse>("/v1/context", { accessToken }),
      apiFetch<CreativeResponse>("/v1/creative/drafts", { accessToken }),
      apiFetch<QualitySummary>("/v1/ai/quality-summary", { accessToken }),
    ])
      .then(([a, p, ctx, cr, ai]) => {
        setAccounts(a);
        setProxies(p);
        setContext(ctx);
        setCreative(cr);
        setAiQuality(ai);
        setLoadState("loaded");
      })
      .catch(() => {
        setAccounts(null);
        setProxies(null);
        setContext(null);
        setCreative(null);
        setAiQuality(null);
        setLoadState("error");
      });
  }, [accessToken]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const m = useMemo(() => {
    const accts = accounts?.items ?? [];
    const prx = proxies?.items ?? [];
    const activeAccounts = accts.filter((i) => i.status === "active").length;
    const frozenAccounts = accts.filter((i) => i.health_status === "frozen").length;
    const newAccounts = accts.filter((i) => i.status === "new" || i.status === "uploaded").length;
    const healthyProxies = prx.filter((i) => i.health_status === "alive").length;
    const proxyHealthPct = prx.length ? Math.round((healthyProxies / prx.length) * 100) : 0;
    const briefScore = Math.round(Number(context?.brief?.completeness_score ?? 0) * 100);
    const totalBriefFields = (context?.brief?.missing_fields?.length ?? 0) + (briefScore > 0 ? Math.round(briefScore / (100 / Math.max(1, (context?.brief?.missing_fields?.length ?? 0) + 1))) : 0);
    const filledFields = totalBriefFields - (context?.brief?.missing_fields?.length ?? 0);
    const drafts = creative?.total ?? 0;
    const approved = (creative?.items ?? []).filter((i) => i.status === "approved").length;
    const qualityScore = Number(aiQuality?.overview?.avg_quality_score ?? 0);
    const fallbackPct = Math.round(Number(aiQuality?.overview?.fallback_rate ?? 0) * 100);
    const repairPct = Math.round(Number(aiQuality?.overview?.repair_rate ?? 0) * 100);
    const totalReqs = Number(aiQuality?.overview?.total_requests ?? 0);
    const latency = avgLatency(aiQuality?.latest_by_task ?? {});

    return {
      accountsTotal: accts.length,
      activeAccounts,
      frozenAccounts,
      newAccounts,
      proxiesAvailable: prx.length,
      proxyHealthPct,
      briefScore,
      filledFields,
      totalBriefFields,
      drafts,
      approved,
      qualityScore,
      fallbackPct,
      repairPct,
      totalReqs,
      latency,
    };
  }, [accounts, proxies, context, creative, aiQuality]);

  const userName = String(
    (profile?.user as Record<string, unknown> | undefined)?.first_name ??
    (profile?.user as Record<string, unknown> | undefined)?.username ??
    "Оператор"
  );

  /* ─── Loading skeleton ─────────────────────────────────────────────────── */

  if (loadState === "loading") {
    return (
      <div className="dash">
        <div className="dash-hero">
          <div className="dash-skeleton" style={{ width: 300, height: 32 }} />
          <div className="dash-skeleton" style={{ width: 200, height: 16, marginTop: 4 }} />
        </div>
        <div className="dash-stats">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="dash-skeleton dash-skeleton--stat" />
          ))}
        </div>
        <div className="dash-skeleton dash-skeleton--bar" />
        <div className="dash-columns">
          <div className="dash-skeleton dash-skeleton--panel" />
          <div className="dash-skeleton dash-skeleton--panel" />
        </div>
        <div className="dash-actions">
          {[0, 1, 2].map((i) => (
            <div key={i} className="dash-skeleton dash-skeleton--action" />
          ))}
        </div>
      </div>
    );
  }

  /* ─── Error state ──────────────────────────────────────────────────────── */

  if (loadState === "error") {
    return (
      <div className="dash">
        <div className="dash-error">
          <div className="dash-error-icon">!</div>
          <p className="dash-error-text">
            Не удалось загрузить данные дашборда. Проверьте подключение и попробуйте снова.
          </p>
          <button className="dash-error-retry" onClick={fetchAll}>
            Повторить
          </button>
        </div>
      </div>
    );
  }

  /* ─── Main render ──────────────────────────────────────────────────────── */

  const latestTasks = Object.entries(aiQuality?.latest_by_task ?? {});

  return (
    <motion.div
      className="dash"
      variants={containerVariants}
      initial="hidden"
      animate="visible"
    >
      {/* ── 1. Hero Welcome ──────────────────────────────────────────────── */}
      <motion.div className="dash-hero" variants={itemVariants}>
        <h1 className="dash-hero-greeting">
          {`Добро пожаловать, ${userName}`}
        </h1>
        <span className="dash-hero-date">{formatDate()}</span>
      </motion.div>

      {/* ── Stat cards ───────────────────────────────────────────────────── */}
      <motion.div className="dash-stats" variants={itemVariants}>
        <div className="dash-stat">
          <span className="dash-stat-label">Аккаунты</span>
          <span className="dash-stat-value" style={MONO}>{m.accountsTotal}</span>
          <span className="dash-stat-sub">
            {m.activeAccounts} active / {m.frozenAccounts} frozen / {m.newAccounts} new
          </span>
        </div>

        <div className="dash-stat">
          <span className="dash-stat-label">Прокси</span>
          <span className="dash-stat-value" style={MONO}>{m.proxiesAvailable}</span>
          <span className="dash-stat-sub">
            {m.proxyHealthPct}% healthy
          </span>
        </div>

        <div className="dash-stat">
          <span className="dash-stat-label">Контекст</span>
          <span className="dash-stat-value" style={MONO}>{m.briefScore}%</span>
          <span className="dash-stat-sub">
            readiness
          </span>
        </div>

        <div className="dash-stat">
          <span className="dash-stat-label">Креатив</span>
          <span className="dash-stat-value" style={MONO}>{m.drafts}</span>
          <span className="dash-stat-sub">
            {m.approved} approved
          </span>
        </div>
      </motion.div>

      {/* ── 2. System Status Bar ─────────────────────────────────────────── */}
      <motion.div className="dash-status-bar" variants={itemVariants}>
        <div className="dash-status-item">
          <span className={`dash-status-dot ${m.totalReqs > 0 ? "dash-status-dot--green" : "dash-status-dot--amber"}`} />
          <span>AI Router {m.totalReqs > 0 ? "Online" : "Idle"}</span>
        </div>

        <div className="dash-status-sep" />

        <div className="dash-status-item">
          <span style={{ color: "var(--text-secondary)" }}>Requests:</span>
          <span style={{ color: "var(--text)" }}>{m.totalReqs}</span>
        </div>

        <div className="dash-status-sep" />

        <div className="dash-status-item">
          <span style={{ color: "var(--text-secondary)" }}>Est. cost:</span>
          <span style={{ color: "var(--text)" }}>{avgCostLabel(m.totalReqs)}</span>
        </div>

        <div className="dash-status-sep" />

        <div className="dash-status-item">
          <span style={{ color: "var(--text-secondary)" }}>Avg latency:</span>
          <span style={{ color: m.latency > 3000 ? "var(--warning)" : "var(--text)" }}>
            {m.latency ? `${m.latency}ms` : "--"}
          </span>
        </div>

        {latestTasks.length > 0 && (
          <>
            <div className="dash-status-sep" />
            <div className="dash-status-item">
              <span style={{ color: "var(--text-secondary)" }}>Last model:</span>
              <span style={{ color: "var(--accent)" }}>
                {latestTasks[latestTasks.length - 1]?.[1]?.model?.split("/").pop() ?? "--"}
              </span>
            </div>
          </>
        )}
      </motion.div>

      {/* ── 3. Two-Column Grid ───────────────────────────────────────────── */}
      <motion.div className="dash-columns" variants={itemVariants}>
        {/* Left: Recent activity */}
        <div className="dash-panel">
          <h3 className="dash-panel-title">Последняя активность</h3>
          {latestTasks.length > 0 ? (
            latestTasks.slice(0, 6).map(([task, info]) => (
              <div className="dash-event" key={task}>
                <div
                  className={`dash-event-icon ${
                    info.fallback_used
                      ? "dash-event-icon--amber"
                      : info.repair_applied
                        ? "dash-event-icon--blue"
                        : "dash-event-icon--green"
                  }`}
                >
                  {info.fallback_used ? "F" : info.repair_applied ? "R" : "✓"}
                </div>
                <div className="dash-event-body">
                  <p className="dash-event-text">
                    {task.replace(/_/g, " ")}
                    {" — "}
                    {info.provider ?? "unknown"} / {info.model?.split("/").pop() ?? "unknown"}
                  </p>
                  <span className="dash-event-time">
                    score {info.quality_score.toFixed(2)}
                    {info.latency_ms ? ` · ${info.latency_ms}ms` : ""}
                  </span>
                </div>
              </div>
            ))
          ) : (
            <div className="dash-empty">
              Пока нет активности. Начните с ассистента.
            </div>
          )}
        </div>

        {/* Right: AI Quality */}
        <div className="dash-panel">
          <h3 className="dash-panel-title">AI Качество</h3>

          <div className="dash-quality-row">
            <span className="dash-quality-label">Avg latency</span>
            <span
              className={`dash-quality-value ${
                m.latency > 5000
                  ? "dash-quality-value--bad"
                  : m.latency > 2000
                    ? "dash-quality-value--warn"
                    : "dash-quality-value--good"
              }`}
            >
              {m.latency ? `${m.latency}ms` : "--"}
            </span>
          </div>

          <div className="dash-quality-row">
            <span className="dash-quality-label">Quality score</span>
            <span
              className={`dash-quality-value ${
                m.qualityScore >= 0.8
                  ? "dash-quality-value--good"
                  : m.qualityScore >= 0.5
                    ? "dash-quality-value--warn"
                    : "dash-quality-value--bad"
              }`}
            >
              {m.qualityScore.toFixed(2)}
            </span>
          </div>

          <div className="dash-quality-row">
            <span className="dash-quality-label">Fallback rate</span>
            <span
              className={`dash-quality-value ${
                m.fallbackPct > 30
                  ? "dash-quality-value--bad"
                  : m.fallbackPct > 10
                    ? "dash-quality-value--warn"
                    : "dash-quality-value--good"
              }`}
            >
              {m.fallbackPct}%
            </span>
          </div>

          <div className="dash-quality-row">
            <span className="dash-quality-label">Repair rate</span>
            <span
              className={`dash-quality-value ${
                m.repairPct > 20
                  ? "dash-quality-value--bad"
                  : m.repairPct > 5
                    ? "dash-quality-value--warn"
                    : "dash-quality-value--good"
              }`}
            >
              {m.repairPct}%
            </span>
          </div>

          <div className="dash-quality-row">
            <span className="dash-quality-label">Total requests</span>
            <span className="dash-quality-value dash-quality-value--good">
              {m.totalReqs}
            </span>
          </div>
        </div>
      </motion.div>

      {/* ── 4. Quick Actions ─────────────────────────────────────────────── */}
      <motion.div className="dash-actions" variants={itemVariants}>
        <button className="dash-action" onClick={() => navigate("/accounts")}>
          <div className="dash-action-icon">{"↑"}</div>
          <span className="dash-action-title">Загрузить аккаунты</span>
          <span className="dash-action-desc">
            Загрузите .session + .json пары и привяжите прокси для безопасного старта.
          </span>
          <span className="dash-action-arrow">{"→"}</span>
        </button>

        <button className="dash-action" onClick={() => navigate("/assistant")}>
          <div className="dash-action-icon">{"✦"}</div>
          <span className="dash-action-title">Запустить ассистент</span>
          <span className="dash-action-desc">
            AI-ассистент поможет собрать бриф и подготовить бизнес-контекст.
          </span>
          <span className="dash-action-arrow">{"→"}</span>
        </button>

        <button className="dash-action" onClick={() => navigate("/creative")}>
          <div className="dash-action-icon">{"☆"}</div>
          <span className="dash-action-title">Генерировать креатив</span>
          <span className="dash-action-desc">
            Создайте AI-черновики контента на основе подтверждённого контекста.
          </span>
          <span className="dash-action-arrow">{"→"}</span>
        </button>
      </motion.div>
    </motion.div>
  );
}
