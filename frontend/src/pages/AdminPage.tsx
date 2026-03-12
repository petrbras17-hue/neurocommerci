import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Shield, Server, Users, Zap, Activity, RefreshCw, CheckCircle, AlertTriangle } from "lucide-react";
import { apiFetch, AdminPlatformStats, AdminAiSpend, AdminTenantHealthItem } from "../api";
import { useAuth } from "../auth";

const cardVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.06, duration: 0.35, ease: [0.16, 1, 0.3, 1] as const },
  }),
};

function StatCard({
  label, value, sub, icon, index, color,
}: {
  label: string; value: number | string; sub?: string; icon: React.ReactNode; index: number; color?: string;
}) {
  return (
    <motion.div className="dash-stat" custom={index} initial="hidden" animate="visible" variants={cardVariants}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span className="dash-stat-label">{label}</span>
        <span style={{ color: color ?? "var(--accent)", opacity: 0.7 }}>{icon}</span>
      </div>
      <span className="dash-stat-value" style={color ? { color } : undefined}>{value}</span>
      {sub ? <span className="dash-stat-sub">{sub}</span> : null}
    </motion.div>
  );
}

function PieDonut({
  alive, warmup, frozen, banned,
}: {
  alive: number; warmup: number; frozen: number; banned: number;
}) {
  const total = alive + warmup + frozen + banned || 1;
  const segments = [
    { label: "Активны", value: alive, color: "#00ff88" },
    { label: "Прогрев", value: warmup, color: "#3b9eff" },
    { label: "Заморожены", value: frozen, color: "#f59e0b" },
    { label: "Забанены", value: banned, color: "#ef4444" },
  ].filter((s) => s.value > 0);

  // Simple horizontal bar instead of SVG donut for reliability
  return (
    <div>
      <div style={{ display: "flex", height: 20, borderRadius: 10, overflow: "hidden", border: "1px solid var(--border)", marginBottom: 10 }}>
        {segments.map((s) => (
          <div
            key={s.label}
            title={`${s.label}: ${s.value} (${Math.round((s.value / total) * 100)}%)`}
            style={{ width: `${(s.value / total) * 100}%`, background: s.color, minWidth: s.value > 0 ? 2 : 0 }}
          />
        ))}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px" }}>
        {segments.map((s) => (
          <div key={s.label} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)" }}>
            <div style={{ width: 10, height: 10, borderRadius: 2, background: s.color, flexShrink: 0 }} />
            <span>{s.label}</span>
            <span className="mono" style={{ color: s.color }}>{s.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function AdminPage() {
  const { accessToken, profile } = useAuth();

  const [platformStats, setPlatformStats] = useState<AdminPlatformStats | null>(null);
  const [aiSpend, setAiSpend] = useState<AdminAiSpend | null>(null);
  const [tenantHealth, setTenantHealth] = useState<AdminTenantHealthItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Check if user is admin role
  const isAdmin = profile?.team?.some((m: Record<string, unknown>) => m.role === "admin" || m.role === "owner") ?? false;

  const load = async () => {
    if (!accessToken) return;
    setBusy(true);
    setError("");
    try {
      // Admin endpoints use OPS_API_TOKEN — these are internal-only.
      // In this UI we show a notice that these require ops token access.
      // For demo purposes, try to fetch with JWT and show what's available.
      const [stats, spend, health] = await Promise.all([
        apiFetch<AdminPlatformStats>("/v1/admin/platform-stats", { accessToken }).catch(() => null),
        apiFetch<AdminAiSpend>("/v1/admin/ai-spend", { accessToken }).catch(() => null),
        apiFetch<{items: AdminTenantHealthItem[]}>("/v1/admin/tenant-health", { accessToken }).catch(() => null),
      ]);
      if (stats) setPlatformStats(stats);
      if (spend) setAiSpend(spend);
      if (health) setTenantHealth(health.items ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "load_failed");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load();
  }, [accessToken]);

  return (
    <div className="dash">
      {/* Header */}
      <div className="dash-columns">
        <motion.div
          className="dash-panel"
          initial={{ opacity: 0, x: -16 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.35 }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div className="dash-action-icon"><Shield size={18} /></div>
            <div>
              <p className="dash-panel-title">Admin Dashboard</p>
              <h2 style={{ fontSize: "1.3rem", marginTop: 4 }}>Платформа</h2>
            </div>
          </div>
          <ul className="bullet-list" style={{ fontSize: 13 }}>
            <li>Сводная статистика по всем тенантам и аккаунтам платформы.</li>
            <li>AI-расходы, здоровье аккаунтов, активные подписки.</li>
          </ul>
        </motion.div>

        <motion.div
          className="dash-panel"
          initial={{ opacity: 0, x: 16 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.35, delay: 0.05 }}
        >
          <p className="dash-panel-title">Статус</p>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
            {busy ? (
              <><RefreshCw size={14} className="spin" style={{ color: "var(--accent)" }} /><span style={{ fontSize: 13, color: "var(--muted)" }}>Загружаем...</span></>
            ) : error ? (
              <><AlertTriangle size={14} style={{ color: "var(--danger)" }} /><span style={{ fontSize: 13, color: "var(--danger)" }}>{error}</span></>
            ) : (
              <><CheckCircle size={14} style={{ color: "var(--accent)" }} /><span style={{ fontSize: 13, color: "var(--muted)" }}>Данные загружены</span></>
            )}
          </div>
          <button
            className="ghost-button"
            type="button"
            disabled={busy}
            onClick={() => void load()}
            style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}
          >
            <RefreshCw size={14} className={busy ? "spin" : ""} />
            Обновить
          </button>
        </motion.div>
      </div>

      {/* Note for non-admin users */}
      {!isAdmin && (
        <div className="status-banner" style={{ borderColor: "var(--warning)", color: "var(--warning)" }}>
          Эта страница предназначена для администраторов платформы. Данные требуют OPS_API_TOKEN.
        </div>
      )}

      {/* Platform stats */}
      {platformStats ? (
        <>
          <div className="dash-stats">
            <StatCard
              label="Тенантов"
              value={platformStats.tenants.total}
              sub="всего в системе"
              icon={<Server size={18} />}
              index={0}
            />
            <StatCard
              label="Аккаунтов"
              value={platformStats.accounts.total}
              sub={`${platformStats.accounts.alive} живых`}
              icon={<Users size={18} />}
              index={1}
            />
            <StatCard
              label="Прокси"
              value={platformStats.proxies.total}
              sub={`${platformStats.proxies.alive} рабочих`}
              icon={<Server size={18} />}
              index={2}
            />
            <StatCard
              label="Подписки"
              value={platformStats.subscriptions.active}
              sub="активных/триал"
              icon={<Activity size={18} />}
              index={3}
            />
          </div>

          {/* Account health pie */}
          <motion.div
            className="dash-panel"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: 0.2 }}
          >
            <p className="dash-panel-title">Здоровье аккаунтов</p>
            <h2 style={{ fontSize: "1.2rem", marginTop: 4, marginBottom: 16 }}>Распределение по статусам</h2>
            <PieDonut
              alive={platformStats.accounts.alive}
              warmup={platformStats.accounts.other}
              frozen={platformStats.accounts.frozen}
              banned={platformStats.accounts.banned}
            />
          </motion.div>
        </>
      ) : busy ? (
        <div className="dash-stats">
          {[0, 1, 2, 3].map((i) => <div key={i} className="dash-skeleton dash-skeleton--stat" />)}
        </div>
      ) : null}

      {/* AI spend */}
      {aiSpend ? (
        <motion.div
          className="dash-panel"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.25 }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Zap size={16} style={{ color: "var(--accent)" }} />
            <div>
              <p className="dash-panel-title">AI расходы</p>
              <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>Токены и стоимость</h2>
            </div>
          </div>
          <div className="dash-stats" style={{ gridTemplateColumns: "repeat(2, minmax(0, 1fr))", marginTop: 12 }}>
            <div className="dash-stat">
              <span className="dash-stat-label">Токенов сегодня</span>
              <span className="dash-stat-value" style={{ fontSize: "1.5rem" }}>
                {aiSpend.today.tokens.toLocaleString("ru-RU")}
              </span>
              <span className="dash-stat-sub">${aiSpend.today.estimated_cost_usd.toFixed(4)}</span>
            </div>
            <div className="dash-stat">
              <span className="dash-stat-label">Токенов в месяце</span>
              <span className="dash-stat-value" style={{ fontSize: "1.5rem" }}>
                {aiSpend.month.tokens.toLocaleString("ru-RU")}
              </span>
              <span className="dash-stat-sub">${aiSpend.month.estimated_cost_usd.toFixed(4)}</span>
            </div>
          </div>
        </motion.div>
      ) : null}

      {/* Tenant health table */}
      {tenantHealth.length > 0 ? (
        <motion.div
          className="dash-panel"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.3 }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Users size={16} style={{ color: "var(--accent)" }} />
            <div>
              <p className="dash-panel-title">Тенанты</p>
              <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>Здоровье по тенантам</h2>
            </div>
          </div>
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Название</th>
                  <th>Статус</th>
                  <th>Аккаунты</th>
                  <th>Живых</th>
                  <th>Регистрация</th>
                </tr>
              </thead>
              <tbody>
                {tenantHealth.map((t, i) => (
                  <motion.tr
                    key={t.tenant_id}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: 0.35 + i * 0.04 }}
                  >
                    <td><span className="mono" style={{ fontSize: 12 }}>{t.tenant_id}</span></td>
                    <td>{t.name}</td>
                    <td>
                      <span
                        className="pill"
                        style={{
                          background: t.status === "active" ? "rgba(0,255,136,0.1)" : "rgba(239,68,68,0.1)",
                          color: t.status === "active" ? "var(--accent)" : "var(--danger)",
                          border: `1px solid ${t.status === "active" ? "var(--accent)" : "var(--danger)"}`,
                          fontSize: 11,
                        }}
                      >
                        {t.status}
                      </span>
                    </td>
                    <td><span className="mono" style={{ fontSize: 13 }}>{t.accounts_total}</span></td>
                    <td>
                      <span className="mono" style={{ fontSize: 13, color: t.accounts_alive > 0 ? "var(--accent)" : "var(--muted)" }}>
                        {t.accounts_alive}
                      </span>
                    </td>
                    <td>
                      <span className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
                        {t.created_at ? new Date(t.created_at).toLocaleDateString("ru-RU") : "—"}
                      </span>
                    </td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </div>
        </motion.div>
      ) : null}

      {!platformStats && !busy && !error && (
        <div className="dash-panel" style={{ textAlign: "center" }}>
          <Shield size={32} style={{ color: "var(--muted)", margin: "0 auto 12px" }} />
          <p style={{ color: "var(--muted)", fontSize: 14 }}>
            Admin-данные требуют OPS_API_TOKEN авторизации.<br />
            Убедитесь, что сервер запущен с правильным токеном.
          </p>
        </div>
      )}
    </div>
  );
}
