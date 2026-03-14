import { useEffect, useState, useCallback } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";

type OpLogEntry = {
  id: number;
  account_id: number | null;
  proxy_id: number | null;
  module: string;
  action: string;
  status: string;
  detail: string | null;
  created_at: string | null;
};

export function AdminOperationsLogPage() {
  const { accessToken } = useAuth();
  const [logs, setLogs] = useState<OpLogEntry[]>([]);
  const [moduleFilter, setModuleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (moduleFilter) params.set("module", moduleFilter);
      if (statusFilter) params.set("action_status", statusFilter);
      params.set("limit", "100");
      const result = await apiFetch<{ items: OpLogEntry[] }>(`/v1/admin/operations-log?${params}`, { accessToken });
      setLogs(result.items);
    } catch (e) {
      console.error("Failed to load ops log:", e);
    } finally {
      setLoading(false);
    }
  }, [accessToken, moduleFilter, statusFilter]);

  useEffect(() => { void load(); }, [load]);

  // Auto-refresh every 10 seconds
  useEffect(() => {
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, [load]);

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <h1 style={{ fontSize: 24, color: "#ff4444", margin: 0 }}>Лог операций</h1>
        <div style={{ display: "flex", gap: 8 }}>
          <select value={moduleFilter} onChange={(e) => setModuleFilter(e.target.value)} style={{ padding: "4px 8px" }}>
            <option value="">Все модули</option>
            <option value="onboarding">Onboarding</option>
            <option value="proxy">Proxy</option>
            <option value="security">Security</option>
            <option value="warmup">Warmup</option>
            <option value="appeal">Appeal</option>
          </select>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} style={{ padding: "4px 8px" }}>
            <option value="">Все статусы</option>
            <option value="success">Success</option>
            <option value="error">Error</option>
            <option value="started">Started</option>
          </select>
          <button className="btn-secondary" onClick={load} disabled={loading}>
            Обновить
          </button>
        </div>
      </div>

      <div style={{ fontSize: 12, color: "#888", marginBottom: 8 }}>
        Автообновление каждые 10 секунд • Показано записей: {logs.length}
      </div>

      <div style={{ overflowX: "auto" }}>
        <table className="data-table" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th>Время</th>
              <th>Модуль</th>
              <th>Действие</th>
              <th>Статус</th>
              <th>Аккаунт</th>
              <th>Прокси</th>
              <th>Детали</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((entry) => (
              <tr key={entry.id}>
                <td style={{ whiteSpace: "nowrap", fontSize: 11, color: "#888", fontFamily: "'JetBrains Mono', monospace" }}>
                  {entry.created_at ? new Date(entry.created_at).toLocaleString("ru-RU") : "—"}
                </td>
                <td>
                  <span className="badge" style={{
                    background: entry.module === "security" ? "rgba(255,68,68,0.15)" :
                      entry.module === "proxy" ? "rgba(68,136,255,0.15)" : "rgba(255,255,255,0.08)",
                    color: entry.module === "security" ? "#ff4444" :
                      entry.module === "proxy" ? "#4488ff" : "#ccc",
                  }}>
                    {entry.module}
                  </span>
                </td>
                <td style={{ fontSize: 13 }}>{entry.action}</td>
                <td>
                  <span style={{
                    color: entry.status === "success" ? "#00ff88" : entry.status === "error" ? "#ff4444" : "#ffcc00",
                    fontWeight: 600,
                    fontSize: 12,
                  }}>
                    ● {entry.status}
                  </span>
                </td>
                <td style={{ fontSize: 12 }}>{entry.account_id || "—"}</td>
                <td style={{ fontSize: 12 }}>{entry.proxy_id || "—"}</td>
                <td style={{
                  maxWidth: 400,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  fontSize: 11,
                  color: "#888",
                }}>
                  {entry.detail || "—"}
                </td>
              </tr>
            ))}
            {logs.length === 0 && (
              <tr>
                <td colSpan={7} style={{ textAlign: "center", color: "#666", padding: 32 }}>
                  {loading ? "Загрузка..." : "Нет записей"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
