import { useEffect, useState, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Shield, Server, Users, Zap, Activity, RefreshCw, CheckCircle,
  AlertTriangle, Search, X, ChevronRight, Database, Wifi, WifiOff,
} from "lucide-react";
import {
  apiFetch,
  adminApi,
  AdminPlatformStats,
  AdminAiSpend,
  TenantSummary,
  TenantDetail,
  RecentSignup,
  HealthOverview,
} from "../api";
import { useAuth } from "../auth";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "short", year: "numeric" });
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function statusColor(s: string | null | undefined): string {
  if (s === "active" || s === "trial") return "#00ff88";
  if (s === "suspended" || s === "cancelled" || s === "expired") return "#ef4444";
  if (s === "past_due") return "#f59e0b";
  return "#888";
}

function statusLabel(s: string | null | undefined): string {
  if (!s) return "—";
  const map: Record<string, string> = {
    active: "Активен", suspended: "Приостановлен", trial: "Триал",
    cancelled: "Отменён", expired: "Истёк", past_due: "Просрочен",
    owner: "Владелец", admin: "Админ", member: "Участник",
  };
  return map[s] ?? s;
}

const SURFACE = "#111113";
const ELEVATED = "#1a1a1f";
const BORDER = "rgba(255,255,255,0.07)";
const ACCENT = "#00ff88";
const DANGER = "#ef4444";
const MUTED = "#888";
const TEXT = "#e8e8e8";
const TEXT2 = "#aaa";

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatCard({
  label, value, sub, icon, accent,
}: {
  label: string; value: string | number; sub?: string;
  icon: React.ReactNode; accent?: string;
}) {
  return (
    <div style={{
      background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 12,
      padding: "18px 20px", display: "flex", flexDirection: "column", gap: 6,
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 12, color: TEXT2, letterSpacing: "0.04em", textTransform: "uppercase" }}>{label}</span>
        <span style={{ color: accent ?? ACCENT, opacity: 0.7 }}>{icon}</span>
      </div>
      <span style={{
        fontFamily: "'JetBrains Mono', monospace", fontSize: "1.6rem", fontWeight: 700,
        color: accent ?? ACCENT, lineHeight: 1.1,
      }}>{typeof value === "number" ? value.toLocaleString("ru-RU") : value}</span>
      {sub && <span style={{ fontSize: 12, color: MUTED }}>{sub}</span>}
    </div>
  );
}

function Pill({ label, color }: { label: string; color: string }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center",
      padding: "2px 8px", borderRadius: 20,
      fontSize: 11, fontWeight: 600,
      background: `${color}18`, color, border: `1px solid ${color}44`,
    }}>{label}</span>
  );
}

// ---------------------------------------------------------------------------
// Tenant Detail Panel (slide-in)
// ---------------------------------------------------------------------------

function TenantDetailPanel({
  tenantId, token, onClose, onStatusToggle,
}: {
  tenantId: number; token: string;
  onClose: () => void;
  onStatusToggle: (id: number, newStatus: "active" | "suspended") => void;
}) {
  const [detail, setDetail] = useState<TenantDetail | null>(null);
  const [busy, setBusy] = useState(true);
  const [toggling, setToggling] = useState(false);

  useEffect(() => {
    setBusy(true);
    adminApi.getTenant(token, tenantId)
      .then(setDetail)
      .catch(() => null)
      .finally(() => setBusy(false));
  }, [tenantId, token]);

  const handleToggle = async () => {
    if (!detail) return;
    const next = detail.status === "active" ? "suspended" : "active";
    setToggling(true);
    try {
      await adminApi.updateTenantStatus(token, tenantId, next);
      setDetail({ ...detail, status: next });
      onStatusToggle(tenantId, next);
    } catch {
      // ignore
    } finally {
      setToggling(false);
    }
  };

  return (
    <motion.div
      initial={{ x: "100%" }}
      animate={{ x: 0 }}
      exit={{ x: "100%" }}
      transition={{ type: "spring", stiffness: 320, damping: 32 }}
      style={{
        position: "fixed", top: 0, right: 0, bottom: 0, width: "min(460px, 100vw)",
        background: "#0e0e10", borderLeft: `1px solid ${BORDER}`,
        zIndex: 200, overflowY: "auto", padding: "28px 24px",
        display: "flex", flexDirection: "column", gap: 20,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 16, fontWeight: 700, color: TEXT }}>Детали тенанта #{tenantId}</span>
        <button type="button" onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: MUTED, padding: 4 }}>
          <X size={18} />
        </button>
      </div>

      {busy && (
        <div style={{ color: MUTED, fontSize: 13, display: "flex", alignItems: "center", gap: 8 }}>
          <RefreshCw size={14} className="spin" style={{ color: ACCENT }} />
          Загрузка...
        </div>
      )}

      {detail && (
        <>
          {/* Header info */}
          <div style={{ background: SURFACE, borderRadius: 10, padding: "14px 16px", border: `1px solid ${BORDER}` }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
              <span style={{ fontWeight: 700, fontSize: 15, color: TEXT }}>{detail.name}</span>
              <Pill label={statusLabel(detail.status)} color={statusColor(detail.status)} />
            </div>
            <div style={{ fontSize: 12, color: MUTED, fontFamily: "'JetBrains Mono', monospace" }}>
              slug: {detail.slug}
            </div>
            <div style={{ fontSize: 12, color: MUTED, marginTop: 4 }}>
              Создан: {fmtDate(detail.created_at)}
            </div>
          </div>

          {/* Accounts */}
          <div style={{ background: SURFACE, borderRadius: 10, padding: "14px 16px", border: `1px solid ${BORDER}` }}>
            <div style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 10 }}>Аккаунты</div>
            <div style={{ display: "flex", gap: 24 }}>
              <div>
                <div style={{ fontSize: 22, fontWeight: 700, color: ACCENT, fontFamily: "'JetBrains Mono', monospace" }}>{detail.accounts.total}</div>
                <div style={{ fontSize: 11, color: MUTED }}>всего</div>
              </div>
              <div>
                <div style={{ fontSize: 22, fontWeight: 700, color: "#3b9eff", fontFamily: "'JetBrains Mono', monospace" }}>{detail.accounts.alive}</div>
                <div style={{ fontSize: 11, color: MUTED }}>живых</div>
              </div>
            </div>
          </div>

          {/* Subscription */}
          <div style={{ background: SURFACE, borderRadius: 10, padding: "14px 16px", border: `1px solid ${BORDER}` }}>
            <div style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 10 }}>Подписка</div>
            {detail.subscription ? (
              <>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                  <span style={{ fontWeight: 600, color: TEXT }}>{detail.subscription.plan_name}</span>
                  <Pill label={statusLabel(detail.subscription.status)} color={statusColor(detail.subscription.status)} />
                </div>
                {detail.subscription.trial_ends_at && (
                  <div style={{ fontSize: 12, color: MUTED }}>Триал до: {fmtDate(detail.subscription.trial_ends_at)}</div>
                )}
                {detail.subscription.current_period_end && (
                  <div style={{ fontSize: 12, color: MUTED }}>Период до: {fmtDate(detail.subscription.current_period_end)}</div>
                )}
              </>
            ) : (
              <span style={{ fontSize: 13, color: MUTED }}>Нет активной подписки</span>
            )}
          </div>

          {/* Members */}
          {detail.members.length > 0 && (
            <div style={{ background: SURFACE, borderRadius: 10, padding: "14px 16px", border: `1px solid ${BORDER}` }}>
              <div style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 10 }}>Участники ({detail.members.length})</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {detail.members.map((m, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <div>
                      <div style={{ fontSize: 13, color: TEXT }}>{m.email ?? m.name ?? `user #${m.user_id}`}</div>
                      {m.email && m.name && <div style={{ fontSize: 11, color: MUTED }}>{m.name}</div>}
                    </div>
                    <Pill label={statusLabel(m.role)} color={m.role === "owner" ? ACCENT : "#3b9eff"} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Workspaces */}
          {detail.workspaces.length > 0 && (
            <div style={{ background: SURFACE, borderRadius: 10, padding: "14px 16px", border: `1px solid ${BORDER}` }}>
              <div style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 10 }}>Воркспейсы ({detail.workspaces.length})</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {detail.workspaces.map((w) => (
                  <div key={w.id} style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
                    <span style={{ color: TEXT }}>{w.name}</span>
                    <span style={{ color: MUTED, fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>#{w.id}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Status toggle */}
          <button
            type="button"
            onClick={handleToggle}
            disabled={toggling}
            style={{
              marginTop: 4, padding: "10px 18px", borderRadius: 8, border: "none", cursor: "pointer",
              background: detail.status === "active" ? `${DANGER}22` : `${ACCENT}22`,
              color: detail.status === "active" ? DANGER : ACCENT,
              fontWeight: 600, fontSize: 13,
            }}
          >
            {toggling ? "..." : detail.status === "active" ? "Приостановить тенанта" : "Активировать тенанта"}
          </button>
        </>
      )}
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// Health Overview Card
// ---------------------------------------------------------------------------

function HealthOverviewPanel({ data }: { data: HealthOverview }) {
  return (
    <div style={{ background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "18px 20px" }}>
      <div style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 14 }}>Состояние системы</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {data.db.ok
            ? <CheckCircle size={15} style={{ color: ACCENT, flexShrink: 0 }} />
            : <AlertTriangle size={15} style={{ color: DANGER, flexShrink: 0 }} />}
          <div>
            <div style={{ fontSize: 13, color: TEXT }}>PostgreSQL</div>
            <div style={{ fontSize: 11, color: MUTED, fontFamily: "'JetBrains Mono', monospace" }}>
              {data.db.latency_ms != null ? `${data.db.latency_ms} ms` : "—"}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {data.redis.ok
            ? <Wifi size={15} style={{ color: ACCENT, flexShrink: 0 }} />
            : <WifiOff size={15} style={{ color: DANGER, flexShrink: 0 }} />}
          <div>
            <div style={{ fontSize: 13, color: TEXT }}>Redis</div>
            <div style={{ fontSize: 11, color: MUTED, fontFamily: "'JetBrains Mono', monospace" }}>
              {data.redis.memory_mb != null ? `${data.redis.memory_mb} MB` : "—"}
              {data.redis.total_keys != null ? ` · ${data.redis.total_keys} keys` : ""}
            </div>
          </div>
        </div>
      </div>

      {Object.keys(data.queues).length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: MUTED, marginBottom: 6 }}>Очереди с задачами:</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {Object.entries(data.queues).map(([q, depth]) => (
              <span key={q} style={{
                padding: "2px 8px", borderRadius: 6, fontSize: 11,
                fontFamily: "'JetBrains Mono', monospace",
                background: `${ACCENT}15`, color: ACCENT, border: `1px solid ${ACCENT}33`,
              }}>
                {q}: {depth}
              </span>
            ))}
          </div>
        </div>
      )}

      {data.ai_error_rate_5min != null && (
        <div style={{ fontSize: 12, color: data.ai_error_rate_5min > 10 ? DANGER : MUTED }}>
          AI ошибки (5 мин): {data.ai_error_rate_5min}%
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main AdminPage
// ---------------------------------------------------------------------------

export function AdminPage() {
  const { accessToken, profile } = useAuth();

  const [platformStats, setPlatformStats] = useState<AdminPlatformStats | null>(null);
  const [aiSpend, setAiSpend] = useState<AdminAiSpend | null>(null);
  const [tenants, setTenants] = useState<TenantSummary[]>([]);
  const [tenantsTotal, setTenantsTotal] = useState(0);
  const [signups, setSignups] = useState<RecentSignup[]>([]);
  const [healthData, setHealthData] = useState<HealthOverview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [selectedTenantId, setSelectedTenantId] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<"tenants" | "signups" | "health">("tenants");

  const searchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    if (!accessToken) return;
    setBusy(true);
    setError("");
    try {
      const [stats, spend, tenantsRes, signupsRes, healthRes] = await Promise.all([
        adminApi.platformStats(accessToken).catch(() => null),
        adminApi.aiSpend(accessToken).catch(() => null),
        adminApi.listTenants(accessToken, 100, 0, search || undefined).catch(() => null),
        adminApi.recentSignups(accessToken, 20).catch(() => null),
        adminApi.healthOverview(accessToken).catch(() => null),
      ]);
      if (stats) setPlatformStats(stats);
      if (spend) setAiSpend(spend);
      if (tenantsRes) { setTenants(tenantsRes.items); setTenantsTotal(tenantsRes.total); }
      if (signupsRes) setSignups(signupsRes.items);
      if (healthRes) setHealthData(healthRes);
    } catch (e) {
      setError(e instanceof Error ? e.message : "load_failed");
    } finally {
      setBusy(false);
    }
  }, [accessToken, search]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSearchChange = (val: string) => {
    setSearchInput(val);
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    searchTimeout.current = setTimeout(() => setSearch(val), 400);
  };

  const handleStatusToggle = (id: number, newStatus: "active" | "suspended") => {
    setTenants((prev) => prev.map((t) => t.id === id ? { ...t, status: newStatus } : t));
  };

  const tabStyle = (tab: string): React.CSSProperties => ({
    padding: "7px 16px", borderRadius: 8, border: "none", cursor: "pointer", fontSize: 13, fontWeight: 600,
    background: activeTab === tab ? `${ACCENT}20` : "transparent",
    color: activeTab === tab ? ACCENT : TEXT2,
    transition: "all 0.15s",
  });

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "24px 16px", display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{
            width: 40, height: 40, borderRadius: 10, background: `${ACCENT}18`,
            display: "flex", alignItems: "center", justifyContent: "center", color: ACCENT, flexShrink: 0,
          }}>
            <Shield size={20} />
          </div>
          <div>
            <h1 style={{ fontSize: "1.4rem", fontWeight: 800, color: TEXT, margin: 0 }}>Admin Panel</h1>
            <p style={{ fontSize: 13, color: MUTED, margin: 0 }}>Управление платформой · все тенанты</p>
          </div>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => void load()}
          style={{
            display: "flex", alignItems: "center", gap: 6, padding: "8px 14px",
            borderRadius: 8, border: `1px solid ${BORDER}`, background: "transparent",
            color: TEXT2, cursor: "pointer", fontSize: 13,
          }}
        >
          <RefreshCw size={14} className={busy ? "spin" : ""} />
          Обновить
        </button>
      </div>

      {error && (
        <div style={{
          padding: "10px 14px", borderRadius: 8, background: `${DANGER}15`,
          border: `1px solid ${DANGER}44`, color: DANGER, fontSize: 13,
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <AlertTriangle size={14} />
          {error}
        </div>
      )}

      {/* Top stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
        <StatCard
          label="Тенантов"
          value={platformStats?.tenants.total ?? "—"}
          sub="всего на платформе"
          icon={<Server size={17} />}
        />
        <StatCard
          label="Аккаунтов"
          value={platformStats?.accounts.total ?? "—"}
          sub={platformStats ? `${platformStats.accounts.alive} живых` : undefined}
          icon={<Users size={17} />}
        />
        <StatCard
          label="Подписок"
          value={platformStats?.subscriptions.active ?? "—"}
          sub="активных/триал"
          icon={<Activity size={17} />}
        />
        <StatCard
          label="AI токены / месяц"
          value={aiSpend ? aiSpend.month.tokens.toLocaleString("ru-RU") : "—"}
          sub={aiSpend ? `$${aiSpend.month.estimated_cost_usd.toFixed(3)}` : undefined}
          icon={<Zap size={17} />}
          accent="#a78bfa"
        />
      </div>

      {/* Health overview (compact) */}
      {healthData && <HealthOverviewPanel data={healthData} />}

      {/* Tab bar */}
      <div style={{ display: "flex", gap: 4, background: SURFACE, borderRadius: 10, padding: 4, border: `1px solid ${BORDER}`, width: "fit-content" }}>
        <button type="button" style={tabStyle("tenants")} onClick={() => setActiveTab("tenants")}>
          Тенанты {tenantsTotal > 0 && <span style={{ marginLeft: 4, opacity: 0.7, fontFamily: "'JetBrains Mono', monospace" }}>({tenantsTotal})</span>}
        </button>
        <button type="button" style={tabStyle("signups")} onClick={() => setActiveTab("signups")}>
          Регистрации
        </button>
        <button type="button" style={tabStyle("health")} onClick={() => setActiveTab("health")}>
          AI расходы
        </button>
      </div>

      {/* ── Tenants tab ── */}
      {activeTab === "tenants" && (
        <div style={{ background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 12, overflow: "hidden" }}>
          {/* Search bar */}
          <div style={{ padding: "14px 16px", borderBottom: `1px solid ${BORDER}`, display: "flex", alignItems: "center", gap: 10 }}>
            <Search size={15} style={{ color: MUTED, flexShrink: 0 }} />
            <input
              type="text"
              placeholder="Поиск по имени или slug..."
              value={searchInput}
              onChange={(e) => handleSearchChange(e.target.value)}
              style={{
                flex: 1, background: "transparent", border: "none", outline: "none",
                fontSize: 13, color: TEXT, fontFamily: "inherit",
              }}
            />
            {searchInput && (
              <button type="button" onClick={() => { setSearchInput(""); setSearch(""); }} style={{ background: "none", border: "none", cursor: "pointer", color: MUTED, padding: 2 }}>
                <X size={14} />
              </button>
            )}
          </div>

          {/* Table */}
          {tenants.length === 0 && !busy ? (
            <div style={{ padding: "40px 20px", textAlign: "center", color: MUTED, fontSize: 14 }}>
              Тенанты не найдены
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                    {["ID", "Название", "Slug", "Статус", "Подписка", "Аккаунты", "Владелец", "Дата", ""].map((h) => (
                      <th key={h} style={{ padding: "10px 14px", textAlign: "left", fontSize: 11, color: MUTED, fontWeight: 600, whiteSpace: "nowrap", textTransform: "uppercase", letterSpacing: "0.04em" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {tenants.map((t) => (
                    <tr
                      key={t.id}
                      onClick={() => setSelectedTenantId(t.id)}
                      style={{ borderBottom: `1px solid ${BORDER}`, cursor: "pointer", transition: "background 0.12s" }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = ELEVATED)}
                      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                    >
                      <td style={{ padding: "10px 14px" }}>
                        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, color: MUTED }}>{t.id}</span>
                      </td>
                      <td style={{ padding: "10px 14px", fontWeight: 600, color: TEXT, fontSize: 13, whiteSpace: "nowrap" }}>{t.name}</td>
                      <td style={{ padding: "10px 14px" }}>
                        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: MUTED }}>{t.slug}</span>
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <Pill label={statusLabel(t.status)} color={statusColor(t.status)} />
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        {t.subscription_plan ? (
                          <div>
                            <div style={{ fontSize: 12, color: TEXT }}>{t.subscription_plan}</div>
                            {t.subscription_status && (
                              <div style={{ fontSize: 11, color: statusColor(t.subscription_status) }}>{statusLabel(t.subscription_status)}</div>
                            )}
                          </div>
                        ) : <span style={{ color: MUTED, fontSize: 12 }}>—</span>}
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 13, color: ACCENT }}>{t.accounts_count}</span>
                      </td>
                      <td style={{ padding: "10px 14px", fontSize: 12, color: TEXT2, maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {t.owner_email ?? t.owner_name ?? "—"}
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: MUTED, whiteSpace: "nowrap" }}>{fmtDate(t.created_at)}</span>
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <ChevronRight size={14} style={{ color: MUTED }} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Signups tab ── */}
      {activeTab === "signups" && (
        <div style={{ background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 12, overflow: "hidden" }}>
          <div style={{ padding: "14px 16px", borderBottom: `1px solid ${BORDER}` }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: TEXT }}>Последние регистрации</span>
          </div>
          {signups.length === 0 ? (
            <div style={{ padding: "40px 20px", textAlign: "center", color: MUTED, fontSize: 14 }}>
              Нет данных
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                    {["#", "Email", "Имя", "Компания", "Telegram", "Дата"].map((h) => (
                      <th key={h} style={{ padding: "10px 14px", textAlign: "left", fontSize: 11, color: MUTED, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {signups.map((u) => (
                    <tr key={u.id} style={{ borderBottom: `1px solid ${BORDER}` }}>
                      <td style={{ padding: "10px 14px" }}>
                        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: MUTED }}>{u.id}</span>
                      </td>
                      <td style={{ padding: "10px 14px", fontSize: 13, color: TEXT }}>{u.email ?? "—"}</td>
                      <td style={{ padding: "10px 14px", fontSize: 13, color: TEXT2 }}>{u.name ?? "—"}</td>
                      <td style={{ padding: "10px 14px", fontSize: 13, color: TEXT2 }}>{u.company ?? "—"}</td>
                      <td style={{ padding: "10px 14px", fontSize: 12, color: MUTED }}>
                        {u.telegram_username ? `@${u.telegram_username}` : "—"}
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: MUTED, whiteSpace: "nowrap" }}>{fmtDateTime(u.created_at)}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── AI spend tab ── */}
      {activeTab === "health" && aiSpend && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div style={{ background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "20px" }}>
            <div style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 12 }}>Сегодня</div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "1.8rem", fontWeight: 700, color: "#a78bfa" }}>
              {aiSpend.today.tokens.toLocaleString("ru-RU")}
            </div>
            <div style={{ fontSize: 12, color: MUTED, marginTop: 4 }}>токенов</div>
            <div style={{ fontSize: 14, color: TEXT2, marginTop: 10 }}>${aiSpend.today.estimated_cost_usd.toFixed(4)}</div>
          </div>
          <div style={{ background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "20px" }}>
            <div style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 12 }}>В этом месяце</div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "1.8rem", fontWeight: 700, color: "#a78bfa" }}>
              {aiSpend.month.tokens.toLocaleString("ru-RU")}
            </div>
            <div style={{ fontSize: 12, color: MUTED, marginTop: 4 }}>токенов</div>
            <div style={{ fontSize: 14, color: TEXT2, marginTop: 10 }}>${aiSpend.month.estimated_cost_usd.toFixed(4)}</div>
          </div>
        </div>
      )}

      {/* Tenant detail panel */}
      <AnimatePresence>
        {selectedTenantId !== null && accessToken && (
          <>
            <motion.div
              key="overlay"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setSelectedTenantId(null)}
              style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 199 }}
            />
            <TenantDetailPanel
              key={selectedTenantId}
              tenantId={selectedTenantId}
              token={accessToken}
              onClose={() => setSelectedTenantId(null)}
              onStatusToggle={handleStatusToggle}
            />
          </>
        )}
      </AnimatePresence>
    </div>
  );
}
