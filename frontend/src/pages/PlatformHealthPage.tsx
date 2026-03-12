import { useState, useEffect, useCallback } from "react";
import { apiFetch } from "../api";
import { useAuth } from "../auth";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type OverallStatus = "green" | "yellow" | "red";

interface PlatformHealth {
  overall: OverallStatus;
  accounts: { alive: number; total: number; alive_percent: number };
  proxies: { alive: number; total: number; alive_percent: number };
  threads_active: number;
  unresolved_critical_alerts: number;
  unresolved_warning_alerts: number;
}

interface ResourcePrediction {
  accounts_alive: number;
  accounts_total: number;
  accounts_burn_rate_per_day: number;
  days_until_accounts_depleted: number | null;
  proxies_alive: number;
  proxies_total: number;
  proxies_burn_rate_per_day: number;
  days_until_proxies_depleted: number | null;
}

interface HealingLogItem {
  id: number;
  action_type: string;
  target_type: string;
  target_id: number | null;
  details: Record<string, unknown>;
  outcome: string;
  created_at: string | null;
}

interface PlatformAlertItem {
  id: number;
  alert_type: string;
  severity: "info" | "warning" | "critical";
  message: string;
  is_resolved: boolean;
  created_at: string | null;
  resolved_at: string | null;
}

interface PurchaseRequestItem {
  id: number;
  resource_type: string;
  quantity: number;
  provider_name: string;
  status: string;
  estimated_cost_usd: number | null;
  details: Record<string, unknown>;
  created_at: string | null;
  approved_at: string | null;
  completed_at: string | null;
}

interface AlertConfigState {
  resource_type: string;
  threshold_percent: number;
  auto_purchase_enabled: boolean;
  notify_telegram: boolean;
  notify_email: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function statusColor(s: OverallStatus): string {
  if (s === "green") return "#00ff88";
  if (s === "yellow") return "#ffcc00";
  return "#ff4466";
}

function statusLabel(s: OverallStatus): string {
  if (s === "green") return "ВСЁ ХОРОШО";
  if (s === "yellow") return "ПРЕДУПРЕЖДЕНИЯ";
  return "КРИТИЧНО";
}

function severityColor(s: string): string {
  if (s === "critical") return "#ff4466";
  if (s === "warning") return "#ffcc00";
  return "#00d4ff";
}

function gaugeColor(pct: number): string {
  if (pct >= 50) return "#00ff88";
  if (pct >= 20) return "#ffcc00";
  return "#ff4466";
}

function daysLabel(days: number | null): string {
  if (days === null) return "неизвестно (нет данных о расходе)";
  if (days > 365) return "> 1 года";
  return `~${days} дн.`;
}

function fmtDate(d: string | null): string {
  if (!d) return "—";
  return new Date(d).toLocaleString("ru-RU");
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function Gauge({ label, alive, total, pct }: { label: string; alive: number; total: number; pct: number }) {
  const color = gaugeColor(pct);
  return (
    <div style={{ flex: 1, minWidth: 180 }}>
      <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 4 }}>{label}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div
          style={{
            flex: 1,
            height: 8,
            background: "rgba(255,255,255,0.08)",
            borderRadius: 4,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: `${Math.min(pct, 100)}%`,
              height: "100%",
              background: color,
              borderRadius: 4,
              transition: "width 0.4s",
            }}
          />
        </div>
        <span style={{ color, fontWeight: 600, fontSize: 14, minWidth: 42 }}>{pct}%</span>
      </div>
      <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 2 }}>
        {alive} / {total} живых
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function PlatformHealthPage() {
  const { accessToken } = useAuth();

  const [health, setHealth] = useState<PlatformHealth | null>(null);
  const [predictions, setPredictions] = useState<ResourcePrediction | null>(null);
  const [healingLog, setHealingLog] = useState<HealingLogItem[]>([]);
  const [alerts, setAlerts] = useState<PlatformAlertItem[]>([]);
  const [purchases, setPurchases] = useState<PurchaseRequestItem[]>([]);

  const [alertConfigs, setAlertConfigs] = useState<Record<string, AlertConfigState>>({
    account: {
      resource_type: "account",
      threshold_percent: 10,
      auto_purchase_enabled: false,
      notify_telegram: true,
      notify_email: false,
    },
    proxy: {
      resource_type: "proxy",
      threshold_percent: 20,
      auto_purchase_enabled: false,
      notify_telegram: true,
      notify_email: false,
    },
  });

  const [sweepLoading, setSweepLoading] = useState(false);
  const [sweepMsg, setSweepMsg] = useState<string | null>(null);
  const [configSaving, setConfigSaving] = useState(false);

  const opts = { accessToken: accessToken };

  const load = useCallback(async () => {
    try {
      const [h, p, log, al, pr] = await Promise.all([
        apiFetch<PlatformHealth>("/v1/platform/health", opts),
        apiFetch<ResourcePrediction>("/v1/platform/resources", opts),
        apiFetch<{ items: HealingLogItem[] }>("/v1/healing/log?limit=20", opts),
        apiFetch<{ items: PlatformAlertItem[] }>("/v1/platform/alerts?limit=20", opts),
        apiFetch<{ items: PurchaseRequestItem[] }>("/v1/purchases/requests?limit=20", opts),
      ]);
      setHealth(h);
      setPredictions(p);
      setHealingLog(log.items);
      setAlerts(al.items);
      setPurchases(pr.items);
    } catch (e) {
      console.error("PlatformHealthPage load error", e);
    }
  }, [accessToken]);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    void load();
    const interval = setInterval(() => void load(), 30000);
    return () => clearInterval(interval);
  }, [load]);

  async function triggerSweep() {
    setSweepLoading(true);
    setSweepMsg(null);
    try {
      const res = await apiFetch<{ job_id: number; status: string }>("/v1/healing/sweep", {
        ...opts,
        method: "POST",
        json: {},
      });
      setSweepMsg(`Задача #${res.job_id} поставлена в очередь`);
      setTimeout(() => void load(), 2000);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setSweepMsg(`Ошибка: ${msg}`);
    } finally {
      setSweepLoading(false);
    }
  }

  async function handleApprove(id: number) {
    try {
      await apiFetch(`/v1/purchases/requests/${id}/approve`, {
        ...opts,
        method: "POST",
        json: {},
      });
      void load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleReject(id: number) {
    try {
      await apiFetch(`/v1/purchases/requests/${id}/reject`, {
        ...opts,
        method: "POST",
        json: {},
      });
      void load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : String(e));
    }
  }

  async function saveAlertConfig(resourceType: "account" | "proxy") {
    setConfigSaving(true);
    const cfg = alertConfigs[resourceType];
    try {
      await apiFetch("/v1/platform/alerts/configure", {
        ...opts,
        method: "POST",
        json: cfg,
      });
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setConfigSaving(false);
    }
  }

  function updateConfig(resourceType: "account" | "proxy", patch: Partial<AlertConfigState>) {
    setAlertConfigs((prev) => ({
      ...prev,
      [resourceType]: { ...prev[resourceType], ...patch },
    }));
  }

  return (
    <div style={{ padding: "0 0 48px" }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Platform Health</h1>
        <p style={{ color: "var(--muted)", fontSize: 13 }}>
          Мониторинг ресурсов, самовосстановление и автозакупка
        </p>
      </div>

      {/* Overall Status */}
      {health && (
        <div
          style={{
            background: "var(--card)",
            border: `1px solid ${statusColor(health.overall)}40`,
            borderRadius: 12,
            padding: "20px 24px",
            marginBottom: 20,
            display: "flex",
            alignItems: "center",
            gap: 16,
          }}
        >
          <div
            style={{
              width: 48,
              height: 48,
              borderRadius: "50%",
              background: statusColor(health.overall),
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 22,
            }}
          >
            {health.overall === "green" ? "✓" : health.overall === "yellow" ? "!" : "✕"}
          </div>
          <div>
            <div
              style={{
                fontWeight: 700,
                fontSize: 18,
                color: statusColor(health.overall),
              }}
            >
              {statusLabel(health.overall)}
            </div>
            <div style={{ color: "var(--muted)", fontSize: 12 }}>
              Потоков активно: {health.threads_active} &nbsp;|&nbsp; Критических алертов:{" "}
              {health.unresolved_critical_alerts} &nbsp;|&nbsp; Предупреждений:{" "}
              {health.unresolved_warning_alerts}
            </div>
          </div>
          <div style={{ marginLeft: "auto" }}>
            <button
              className="primary-button"
              style={{ fontSize: 12, padding: "8px 18px" }}
              onClick={() => void triggerSweep()}
              disabled={sweepLoading}
            >
              {sweepLoading ? "Запуск..." : "Запустить проверку"}
            </button>
            {sweepMsg && (
              <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 4, textAlign: "right" }}>
                {sweepMsg}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Resource Gauges */}
      {health && (
        <div
          style={{
            background: "var(--card)",
            borderRadius: 12,
            padding: "20px 24px",
            marginBottom: 20,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 16 }}>Ресурсы</div>
          <div style={{ display: "flex", gap: 32, flexWrap: "wrap" }}>
            <Gauge
              label="Аккаунты"
              alive={health.accounts.alive}
              total={health.accounts.total}
              pct={health.accounts.alive_percent}
            />
            <Gauge
              label="Прокси"
              alive={health.proxies.alive}
              total={health.proxies.total}
              pct={health.proxies.alive_percent}
            />
          </div>
        </div>
      )}

      {/* Depletion Predictions */}
      {predictions && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
            gap: 16,
            marginBottom: 20,
          }}
        >
          {[
            {
              label: "Аккаунтов хватит на",
              days: predictions.days_until_accounts_depleted,
              burnRate: predictions.accounts_burn_rate_per_day,
              unit: "бан/день",
            },
            {
              label: "Прокси хватит на",
              days: predictions.days_until_proxies_depleted,
              burnRate: predictions.proxies_burn_rate_per_day,
              unit: "смерть/день",
            },
          ].map((card) => (
            <div
              key={card.label}
              style={{
                background: "var(--card)",
                borderRadius: 12,
                padding: "16px 20px",
              }}
            >
              <div style={{ color: "var(--muted)", fontSize: 12, marginBottom: 6 }}>{card.label}</div>
              <div
                style={{
                  fontSize: 24,
                  fontWeight: 700,
                  color: card.days === null ? "var(--muted)" : card.days < 14 ? "#ff4466" : "#00ff88",
                }}
              >
                {daysLabel(card.days)}
              </div>
              <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 4 }}>
                Расход: {card.burnRate} {card.unit}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Alerts */}
      <div
        style={{
          background: "var(--card)",
          borderRadius: 12,
          padding: "20px 24px",
          marginBottom: 20,
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: 12 }}>
          Активные алерты ({alerts.filter((a) => !a.is_resolved).length})
        </div>
        {alerts.length === 0 ? (
          <div style={{ color: "var(--muted)", fontSize: 13 }}>Алертов нет</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {alerts.map((a) => (
              <div
                key={a.id}
                style={{
                  padding: "10px 14px",
                  borderRadius: 8,
                  background: `${severityColor(a.severity)}10`,
                  borderLeft: `3px solid ${severityColor(a.severity)}`,
                  fontSize: 13,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span
                    style={{
                      color: severityColor(a.severity),
                      fontWeight: 600,
                      textTransform: "uppercase",
                      fontSize: 10,
                    }}
                  >
                    {a.severity}
                  </span>
                  <span style={{ color: "var(--muted)", fontSize: 11 }}>{fmtDate(a.created_at)}</span>
                </div>
                <div style={{ marginTop: 4 }}>{a.message}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Purchase Requests */}
      <div
        style={{
          background: "var(--card)",
          borderRadius: 12,
          padding: "20px 24px",
          marginBottom: 20,
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: 12 }}>Запросы на закупку</div>
        {purchases.length === 0 ? (
          <div style={{ color: "var(--muted)", fontSize: 13 }}>Запросов нет</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ color: "var(--muted)", textAlign: "left" }}>
                  <th style={{ padding: "6px 12px" }}>#</th>
                  <th style={{ padding: "6px 12px" }}>Ресурс</th>
                  <th style={{ padding: "6px 12px" }}>Кол-во</th>
                  <th style={{ padding: "6px 12px" }}>Провайдер</th>
                  <th style={{ padding: "6px 12px" }}>Статус</th>
                  <th style={{ padding: "6px 12px" }}>Стоимость</th>
                  <th style={{ padding: "6px 12px" }}>Создан</th>
                  <th style={{ padding: "6px 12px" }}>Действия</th>
                </tr>
              </thead>
              <tbody>
                {purchases.map((p) => (
                  <tr
                    key={p.id}
                    style={{ borderTop: "1px solid rgba(255,255,255,0.04)" }}
                  >
                    <td style={{ padding: "8px 12px", color: "var(--muted)" }}>{p.id}</td>
                    <td style={{ padding: "8px 12px" }}>{p.resource_type}</td>
                    <td style={{ padding: "8px 12px" }}>{p.quantity}</td>
                    <td style={{ padding: "8px 12px" }}>{p.provider_name}</td>
                    <td style={{ padding: "8px 12px" }}>
                      <span
                        style={{
                          padding: "2px 8px",
                          borderRadius: 4,
                          fontSize: 11,
                          background:
                            p.status === "approved"
                              ? "#00ff8820"
                              : p.status === "rejected"
                              ? "#ff446620"
                              : p.status === "completed"
                              ? "#00d4ff20"
                              : "rgba(255,255,255,0.06)",
                          color:
                            p.status === "approved"
                              ? "#00ff88"
                              : p.status === "rejected"
                              ? "#ff4466"
                              : p.status === "completed"
                              ? "#00d4ff"
                              : "var(--muted)",
                        }}
                      >
                        {p.status}
                      </span>
                    </td>
                    <td style={{ padding: "8px 12px", color: "var(--muted)" }}>
                      {p.estimated_cost_usd != null ? `$${p.estimated_cost_usd}` : "—"}
                    </td>
                    <td style={{ padding: "8px 12px", color: "var(--muted)", fontSize: 11 }}>
                      {fmtDate(p.created_at)}
                    </td>
                    <td style={{ padding: "8px 12px" }}>
                      {p.status === "pending" && (
                        <div style={{ display: "flex", gap: 6 }}>
                          <button
                            className="primary-button"
                            style={{ fontSize: 11, padding: "4px 10px" }}
                            onClick={() => void handleApprove(p.id)}
                          >
                            Одобрить
                          </button>
                          <button
                            className="ghost-button"
                            style={{ fontSize: 11, padding: "4px 10px" }}
                            onClick={() => void handleReject(p.id)}
                          >
                            Отклонить
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Healing Log */}
      <div
        style={{
          background: "var(--card)",
          borderRadius: 12,
          padding: "20px 24px",
          marginBottom: 20,
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: 12 }}>Лог самовосстановления (последние 20)</div>
        {healingLog.length === 0 ? (
          <div style={{ color: "var(--muted)", fontSize: 13 }}>Действий нет</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {healingLog.map((item) => (
              <div
                key={item.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "8px 0",
                  borderBottom: "1px solid rgba(255,255,255,0.04)",
                  fontSize: 13,
                }}
              >
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background:
                      item.outcome === "success"
                        ? "#00ff88"
                        : item.outcome === "failed"
                        ? "#ff4466"
                        : "#ffcc00",
                    flexShrink: 0,
                  }}
                />
                <span style={{ color: "var(--muted)", fontSize: 11, minWidth: 140 }}>
                  {fmtDate(item.created_at)}
                </span>
                <span style={{ fontWeight: 500 }}>{item.action_type}</span>
                <span style={{ color: "var(--muted)" }}>
                  {item.target_type}
                  {item.target_id != null ? ` #${item.target_id}` : ""}
                </span>
                <span
                  style={{
                    marginLeft: "auto",
                    fontSize: 11,
                    color:
                      item.outcome === "success"
                        ? "#00ff88"
                        : item.outcome === "failed"
                        ? "#ff4466"
                        : "#ffcc00",
                  }}
                >
                  {item.outcome}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Alert Configuration */}
      <div
        style={{
          background: "var(--card)",
          borderRadius: 12,
          padding: "20px 24px",
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: 16 }}>Настройки порогов и уведомлений</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 24 }}>
          {(["account", "proxy"] as const).map((rt) => {
            const cfg = alertConfigs[rt];
            return (
              <div
                key={rt}
                style={{
                  padding: "16px",
                  background: "rgba(255,255,255,0.03)",
                  borderRadius: 8,
                  border: "1px solid rgba(255,255,255,0.06)",
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: 12, textTransform: "capitalize" }}>
                  {rt === "account" ? "Аккаунты" : "Прокси"}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  <label style={{ fontSize: 13 }}>
                    <span style={{ color: "var(--muted)", display: "block", marginBottom: 4 }}>
                      Порог (%)
                    </span>
                    <input
                      type="number"
                      min={1}
                      max={99}
                      value={cfg.threshold_percent}
                      onChange={(e) =>
                        updateConfig(rt, { threshold_percent: Number(e.target.value) })
                      }
                      style={{
                        width: "100%",
                        background: "var(--input-bg, rgba(255,255,255,0.06))",
                        border: "1px solid rgba(255,255,255,0.1)",
                        borderRadius: 6,
                        color: "var(--fg)",
                        padding: "6px 10px",
                        fontSize: 13,
                      }}
                    />
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                    <input
                      type="checkbox"
                      checked={cfg.auto_purchase_enabled}
                      onChange={(e) =>
                        updateConfig(rt, { auto_purchase_enabled: e.target.checked })
                      }
                    />
                    Автозакупка при достижении порога
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                    <input
                      type="checkbox"
                      checked={cfg.notify_telegram}
                      onChange={(e) =>
                        updateConfig(rt, { notify_telegram: e.target.checked })
                      }
                    />
                    Уведомлять в Telegram
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                    <input
                      type="checkbox"
                      checked={cfg.notify_email}
                      onChange={(e) =>
                        updateConfig(rt, { notify_email: e.target.checked })
                      }
                    />
                    Уведомлять по Email
                  </label>
                  <button
                    className="primary-button"
                    style={{ fontSize: 12, padding: "7px 14px", marginTop: 4 }}
                    onClick={() => void saveAlertConfig(rt)}
                    disabled={configSaving}
                  >
                    {configSaving ? "Сохранение..." : "Сохранить"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
