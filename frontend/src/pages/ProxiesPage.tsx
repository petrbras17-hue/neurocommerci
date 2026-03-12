import { useEffect, useState, useCallback, useRef } from "react";
import { motion } from "framer-motion";
import {
  Server,
  Wifi,
  WifiOff,
  Shield,
  Upload,
  Trash2,
  RefreshCw,
  Activity,
  AlertTriangle,
  CheckCircle,
  ChevronLeft,
  ChevronRight,
  Filter,
} from "lucide-react";
import { apiFetch } from "../api";
import { useAuth } from "../auth";

/* ── types ── */

type ProxyRow = {
  id: number;
  host: string;
  port: number;
  proxy_type: string;
  status: string;
  bound_account_phone: string | null;
  bound_account_id: number | null;
  last_checked_at: string | null;
  latency_ms: number | null;
  created_at: string | null;
};

type ProxyListResponse = {
  items: ProxyRow[];
  total: number;
  summary?: {
    alive: number;
    dead: number;
    failing: number;
    unknown: number;
    bound: number;
    free: number;
  };
};

type ProxyStats = {
  total: number;
  alive: number;
  dead: number;
  bound: number;
  unknown: number;
  failing: number;
};

type ImportResult = {
  imported: number;
  skipped: number;
  errors: string[];
};

type HealthCheckResult = {
  checked: number;
  alive: number;
  dead: number;
};

type CleanupResult = {
  deleted: number;
  kept_bound: number;
};

/* ── animation helpers ── */

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.07 } },
};

const item = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.16, 1, 0.3, 1] as const } },
};

/* ── status pill helper ── */

function proxyStatusColor(status: string): { bg: string; fg: string } {
  const s = status.toLowerCase();
  if (s === "alive" || s === "ok") return { bg: "rgba(0,255,136,0.15)", fg: "var(--accent)" };
  if (s === "dead" || s === "error") return { bg: "rgba(255,68,68,0.15)", fg: "var(--danger)" };
  if (s === "failing" || s === "slow") return { bg: "rgba(255,170,0,0.15)", fg: "var(--warning)" };
  return { bg: "rgba(68,136,255,0.15)", fg: "var(--info)" };
}

const STATUS_FILTERS = [
  { value: "", label: "Все" },
  { value: "alive", label: "Живые" },
  { value: "dead", label: "Мёртвые" },
  { value: "failing", label: "Сбоящие" },
  { value: "unknown", label: "Неизвестно" },
];

const PAGE_SIZE = 25;

const monoFont = "'JetBrains Mono Variable', monospace";

export function ProxiesPage() {
  const { accessToken } = useAuth();

  /* ── state ── */
  const [proxies, setProxies] = useState<ProxyListResponse>({ items: [], total: 0 });
  const [stats, setStats] = useState<ProxyStats>({ total: 0, alive: 0, dead: 0, bound: 0, unknown: 0, failing: 0 });
  const [page, setPage] = useState(0);
  const [statusFilter, setStatusFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  /* import */
  const [importText, setImportText] = useState("");
  const [importType, setImportType] = useState("http");
  const [importResult, setImportResult] = useState<ImportResult | null>(null);

  /* health check */
  const [healthCheckResult, setHealthCheckResult] = useState<HealthCheckResult | null>(null);

  /* auto refresh */
  const [autoRefresh, setAutoRefresh] = useState(false);
  const autoRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /* confirm dialog */
  const [showCleanupConfirm, setShowCleanupConfirm] = useState(false);

  /* ── data loading ── */

  const loadProxies = useCallback(async () => {
    if (!accessToken) return;
    const params = new URLSearchParams();
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(page * PAGE_SIZE));
    if (statusFilter) params.set("status", statusFilter);
    try {
      const data = await apiFetch<ProxyListResponse>(`/v1/proxies?${params.toString()}`, { accessToken });
      setProxies(data);
      // Use summary from backend response (aggregated across all pages)
      if (data.summary) {
        setStats({
          total: data.total,
          alive: data.summary.alive,
          dead: data.summary.dead,
          bound: data.summary.bound,
          unknown: data.summary.unknown,
          failing: data.summary.failing,
        });
      }
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : "load_proxies_failed");
    }
  }, [accessToken, page, statusFilter]);

  const reload = useCallback(async () => {
    await loadProxies();
  }, [loadProxies]);

  useEffect(() => {
    void reload();
  }, [reload]);

  /* auto refresh interval */
  useEffect(() => {
    if (autoRefresh) {
      autoRefreshRef.current = setInterval(() => {
        void reload();
      }, 10000);
    } else if (autoRefreshRef.current) {
      clearInterval(autoRefreshRef.current);
      autoRefreshRef.current = null;
    }
    return () => {
      if (autoRefreshRef.current) clearInterval(autoRefreshRef.current);
    };
  }, [autoRefresh, reload]);

  /* ── actions ── */

  const handleImport = async () => {
    if (!accessToken || !importText.trim()) {
      setStatusMessage("Вставьте строки прокси для импорта.");
      return;
    }
    setBusy(true);
    setStatusMessage("");
    setImportResult(null);
    try {
      const result = await apiFetch<ImportResult>("/v1/proxies/bulk-import", {
        method: "POST",
        accessToken,
        json: { lines: importText, proxy_type: importType },
      });
      setImportResult(result);
      setImportText("");
      setStatusMessage(`Импорт завершён: ${result.imported} добавлено, ${result.skipped} пропущено.`);
      await reload();
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : "import_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleHealthCheck = async () => {
    if (!accessToken) return;
    setBusy(true);
    setStatusMessage("Проверка прокси запущена...");
    setHealthCheckResult(null);
    try {
      const result = await apiFetch<HealthCheckResult>("/v1/proxies/health-check", {
        method: "POST",
        accessToken,
      });
      setHealthCheckResult(result);
      setStatusMessage(`Проверено: ${result.checked}. Живых: ${result.alive}, мёртвых: ${result.dead}.`);
      await reload();
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : "health_check_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleCleanup = async () => {
    if (!accessToken) return;
    setShowCleanupConfirm(false);
    setBusy(true);
    setStatusMessage("");
    try {
      const result = await apiFetch<CleanupResult>("/v1/proxies/cleanup", {
        method: "POST",
        accessToken,
      });
      setStatusMessage(`Удалено мёртвых прокси: ${result.deleted}. Сохранено привязанных: ${result.kept_bound}.`);
      await reload();
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : "cleanup_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleSingleCheck = async (proxyId: number) => {
    if (!accessToken) return;
    setBusy(true);
    try {
      await apiFetch(`/v1/proxies/${proxyId}/check`, { method: "POST", accessToken });
      setStatusMessage("Проверка завершена.");
      await reload();
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : "check_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (proxyId: number) => {
    if (!accessToken) return;
    setBusy(true);
    try {
      await apiFetch(`/v1/proxies/${proxyId}`, { method: "DELETE", accessToken });
      setStatusMessage("Прокси удалён.");
      await reload();
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : "delete_failed");
    } finally {
      setBusy(false);
    }
  };

  /* ── computed ── */

  const totalPages = Math.max(1, Math.ceil(proxies.total / PAGE_SIZE));
  const aliveRatio = stats.total > 0 ? stats.alive / stats.total : 1;
  const showLowAlert = stats.total > 0 && aliveRatio < 0.2;

  /* ── render ── */

  return (
    <motion.div
      className="page-grid"
      variants={container}
      initial="hidden"
      animate="show"
    >
      {/* ── Low proxy alert ── */}
      {showLowAlert ? (
        <motion.div
          variants={item}
          style={{
            padding: "14px 18px",
            borderRadius: 12,
            background: "rgba(255,68,68,0.1)",
            border: "1px solid rgba(255,68,68,0.25)",
            color: "var(--danger)",
            display: "flex",
            alignItems: "center",
            gap: 10,
            fontSize: 13,
            fontWeight: 500,
          }}
        >
          <AlertTriangle size={18} />
          <span>
            Внимание: только {Math.round(aliveRatio * 100)}% прокси живы ({stats.alive} из {stats.total}).
            Рекомендуется добавить новые прокси или проверить существующие.
          </span>
        </motion.div>
      ) : null}

      {/* ── Stats cards ── */}
      <motion.section
        variants={item}
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
          gap: 12,
        }}
      >
        {[
          { label: "Всего прокси", value: stats.total, icon: Server, color: "var(--text)" },
          { label: "Живые", value: stats.alive, icon: Wifi, color: "var(--accent)" },
          { label: "Мёртвые", value: stats.dead, icon: WifiOff, color: "var(--danger)" },
          { label: "Привязанные", value: stats.bound, icon: Shield, color: "var(--info)" },
        ].map((card) => (
          <article key={card.label} className="panel" style={{ borderTop: `2px solid ${card.color}` }}>
            <div className="panel-header" style={{ paddingBottom: 0 }}>
              <div>
                <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <card.icon size={12} /> {card.label}
                </div>
                <h2 style={{
                  color: card.color,
                  fontFamily: monoFont,
                  fontSize: 28,
                  margin: "8px 0 0",
                }}>
                  {card.value}
                </h2>
              </div>
            </div>
          </article>
        ))}
      </motion.section>

      {/* ── Bulk import ── */}
      <motion.section className="panel" variants={item}>
        <div className="panel-header">
          <div>
            <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Upload size={12} /> Импорт
            </div>
            <h2 style={{ color: "var(--text)" }}>Массовый импорт прокси</h2>
          </div>
        </div>
        <div className="stack-form">
          <label className="field">
            <span style={{ color: "var(--text-secondary)", fontSize: 12, fontWeight: 500 }}>
              Формат: host:port:user:pass (по одному на строку)
            </span>
            <textarea
              value={importText}
              onChange={(e) => setImportText(e.target.value)}
              placeholder={"185.123.45.67:8080:myuser:mypass\n192.168.1.1:1080:admin:secret"}
              rows={6}
              style={{
                width: "100%",
                padding: 14,
                borderRadius: 12,
                border: "1px solid var(--border)",
                background: "var(--surface-2)",
                color: "var(--text)",
                fontFamily: monoFont,
                fontSize: 13,
                lineHeight: 1.6,
                resize: "vertical",
              }}
            />
          </label>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <label className="field" style={{ flex: "0 0 auto" }}>
              <span style={{ color: "var(--text-secondary)", fontSize: 12, fontWeight: 500 }}>Тип</span>
              <select
                value={importType}
                onChange={(e) => setImportType(e.target.value)}
                style={{
                  padding: "10px 14px",
                  borderRadius: 8,
                  border: "1px solid var(--border)",
                  background: "var(--surface-2)",
                  color: "var(--text)",
                  fontSize: 13,
                }}
              >
                <option value="http">HTTP</option>
                <option value="socks5">SOCKS5</option>
              </select>
            </label>
            <button
              className="primary-button"
              type="button"
              disabled={busy || !importText.trim()}
              onClick={() => void handleImport()}
              style={{ display: "flex", alignItems: "center", gap: 8, alignSelf: "flex-end" }}
            >
              <Upload size={14} />
              {busy ? "Импортируем..." : "Импортировать"}
            </button>
          </div>
          {importResult ? (
            <div style={{
              padding: "10px 14px",
              borderRadius: 8,
              background: "rgba(0,255,136,0.08)",
              border: "1px solid rgba(0,255,136,0.2)",
              fontSize: 13,
              color: "var(--accent)",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}>
              <CheckCircle size={14} />
              Добавлено: {importResult.imported} | Пропущено: {importResult.skipped}
              {importResult.errors.length > 0 ? ` | Ошибки: ${importResult.errors.length}` : ""}
            </div>
          ) : null}
        </div>
      </motion.section>

      {/* ── Action bar ── */}
      <motion.section className="panel" variants={item}>
        <div className="panel-header">
          <div>
            <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Activity size={12} /> Действия
            </div>
            <h2 style={{ color: "var(--text)" }}>Управление прокси</h2>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <button
            className="secondary-button"
            type="button"
            disabled={busy}
            onClick={() => void handleHealthCheck()}
            style={{ display: "flex", alignItems: "center", gap: 8 }}
          >
            <RefreshCw size={14} />
            Проверить все
          </button>
          <button
            className="ghost-button"
            type="button"
            disabled={busy}
            onClick={() => setShowCleanupConfirm(true)}
            style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--danger)" }}
          >
            <Trash2 size={14} />
            Удалить мёртвые
          </button>
          <label style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginLeft: "auto",
            cursor: "pointer",
            fontSize: 13,
            color: "var(--text-secondary)",
          }}>
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Авто-обновление (10с)
          </label>
        </div>
        {healthCheckResult ? (
          <div style={{
            marginTop: 12,
            padding: "10px 14px",
            borderRadius: 8,
            background: "rgba(0,255,136,0.08)",
            border: "1px solid rgba(0,255,136,0.2)",
            fontSize: 13,
            color: "var(--accent)",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}>
            <CheckCircle size={14} />
            Проверено: {healthCheckResult.checked} | Живых: {healthCheckResult.alive} | Мёртвых: {healthCheckResult.dead}
          </div>
        ) : null}
      </motion.section>

      {/* ── Status message ── */}
      {statusMessage ? (
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          style={{
            padding: "10px 14px",
            borderRadius: 8,
            background: statusMessage.toLowerCase().includes("fail") || statusMessage.toLowerCase().includes("ошибк")
              ? "rgba(255,68,68,0.1)"
              : "rgba(0,255,136,0.1)",
            color: statusMessage.toLowerCase().includes("fail") || statusMessage.toLowerCase().includes("ошибк")
              ? "var(--danger)"
              : "var(--accent)",
            border: `1px solid ${
              statusMessage.toLowerCase().includes("fail") || statusMessage.toLowerCase().includes("ошибк")
                ? "rgba(255,68,68,0.2)"
                : "rgba(0,255,136,0.2)"
            }`,
            fontSize: 13,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          {statusMessage.toLowerCase().includes("fail") || statusMessage.toLowerCase().includes("ошибк")
            ? <AlertTriangle size={14} />
            : <CheckCircle size={14} />}
          {statusMessage}
        </motion.div>
      ) : null}

      {/* ── Proxy table ── */}
      <motion.section className="panel wide" variants={item}>
        <div className="panel-header">
          <div>
            <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Server size={12} /> Proxy pool
            </div>
            <h2 style={{ color: "var(--text)" }}>Список прокси</h2>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Filter size={14} style={{ color: "var(--muted)" }} />
              <select
                value={statusFilter}
                onChange={(e) => { setStatusFilter(e.target.value); setPage(0); }}
                style={{
                  padding: "6px 10px",
                  borderRadius: 8,
                  border: "1px solid var(--border)",
                  background: "var(--surface-2)",
                  color: "var(--text)",
                  fontSize: 12,
                }}
              >
                {STATUS_FILTERS.map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </select>
            </div>
            <span style={{
              fontFamily: monoFont,
              fontSize: 12,
              color: "var(--muted)",
            }}>
              {proxies.total} total
            </span>
          </div>
        </div>

        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Host:Port</th>
                <th>Тип</th>
                <th>Статус</th>
                <th>Привязан к аккаунту</th>
                <th>Последняя проверка</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {proxies.items.length ? (
                proxies.items.map((p, idx) => {
                  const sc = proxyStatusColor(p.status);
                  return (
                    <tr key={p.id}>
                      <td style={{ fontFamily: monoFont, fontSize: 12, color: "var(--muted)" }}>
                        {page * PAGE_SIZE + idx + 1}
                      </td>
                      <td style={{ fontFamily: monoFont, fontSize: 13, fontWeight: 500, color: "var(--text)" }}>
                        {p.host}:{p.port}
                      </td>
                      <td style={{ fontSize: 12, color: "var(--text-secondary)", textTransform: "uppercase" }}>
                        {p.proxy_type}
                      </td>
                      <td>
                        <span style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 6,
                          padding: "3px 10px",
                          borderRadius: 999,
                          background: sc.bg,
                          color: sc.fg,
                          fontSize: 11,
                          fontWeight: 600,
                        }}>
                          {p.status}
                        </span>
                      </td>
                      <td style={{
                        fontFamily: monoFont,
                        fontSize: 12,
                        color: p.bound_account_phone ? "var(--text-secondary)" : "var(--muted)",
                      }}>
                        {p.bound_account_phone || "---"}
                      </td>
                      <td style={{ fontFamily: monoFont, fontSize: 12, color: "var(--muted)" }}>
                        {p.last_checked_at || "---"}
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: 6 }}>
                          <button
                            className="ghost-button"
                            type="button"
                            disabled={busy}
                            onClick={() => void handleSingleCheck(p.id)}
                            title="Проверить"
                            style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, padding: "4px 8px" }}
                          >
                            <RefreshCw size={12} />
                          </button>
                          <button
                            className="ghost-button"
                            type="button"
                            disabled={busy}
                            onClick={() => void handleDelete(p.id)}
                            title="Удалить"
                            style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, padding: "4px 8px", color: "var(--danger)" }}
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={7} style={{ textAlign: "center", padding: 32, color: "var(--muted)" }}>
                    <Server size={24} style={{ marginBottom: 8, opacity: 0.4 }} />
                    <p style={{ margin: 0 }}>Нет прокси. Импортируйте прокси выше.</p>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {proxies.total > PAGE_SIZE ? (
          <div style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 12,
            marginTop: 16,
            paddingTop: 12,
            borderTop: "1px solid var(--border)",
          }}>
            <button
              className="ghost-button"
              type="button"
              disabled={page === 0}
              onClick={() => setPage(page - 1)}
              style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}
            >
              <ChevronLeft size={14} /> Назад
            </button>
            <span style={{ fontFamily: monoFont, fontSize: 12, color: "var(--text-secondary)" }}>
              {page + 1} / {totalPages}
            </span>
            <button
              className="ghost-button"
              type="button"
              disabled={page >= totalPages - 1}
              onClick={() => setPage(page + 1)}
              style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}
            >
              Далее <ChevronRight size={14} />
            </button>
          </div>
        ) : null}
      </motion.section>

      {/* ── Cleanup confirm modal ── */}
      {showCleanupConfirm ? (
        <div
          className="modal-overlay"
          onClick={() => setShowCleanupConfirm(false)}
        >
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div>
                <div className="eyebrow" style={{ color: "var(--danger)" }}>Подтверждение</div>
                <h2 style={{ color: "var(--text)" }}>Удалить все мёртвые прокси?</h2>
              </div>
            </div>
            <p style={{ color: "var(--text-secondary)", fontSize: 13, lineHeight: 1.5, margin: "0 0 16px" }}>
              Все мёртвые прокси без привязки к аккаунтам будут безвозвратно удалены.
              Привязанные к аккаунтам прокси останутся нетронутыми.
            </p>
            <div className="actions-row">
              <button
                className="primary-button"
                type="button"
                disabled={busy}
                onClick={() => void handleCleanup()}
                style={{ background: "var(--danger)", display: "flex", alignItems: "center", gap: 8 }}
              >
                <Trash2 size={14} />
                {busy ? "Удаляем..." : "Удалить мёртвые"}
              </button>
              <button
                className="ghost-button"
                type="button"
                onClick={() => setShowCleanupConfirm(false)}
              >
                Отмена
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </motion.div>
  );
}
