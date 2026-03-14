import { useEffect, useState, useCallback } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";
import { useNavigate } from "react-router-dom";

type Stats = {
  accounts: { total: number; by_status: Record<string, number> };
  proxies: { total: number; alive: number; dead: number; bound: number; free: number };
};

type OpLogEntry = {
  id: number;
  account_id: number | null;
  module: string;
  action: string;
  status: string;
  detail: string | null;
  created_at: string | null;
};

export function AdminDashboardPage() {
  const { accessToken } = useAuth();
  const navigate = useNavigate();
  const [stats, setStats] = useState<Stats | null>(null);
  const [recentOps, setRecentOps] = useState<OpLogEntry[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [s, logs] = await Promise.all([
        apiFetch<Stats>("/v1/admin/onboarding/stats", { accessToken }),
        apiFetch<{ items: OpLogEntry[] }>("/v1/admin/operations-log?limit=20", { accessToken }),
      ]);
      setStats(s);
      setRecentOps(logs.items);
    } catch (e) {
      console.error("Failed to load admin stats:", e);
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => { void load(); }, [load]);

  if (loading) return <div className="loading-screen">Загрузка...</div>;

  const statusColors: Record<string, string> = {
    uploaded: "#888",
    verified: "#4488ff",
    hardened: "#ff8844",
    warmup: "#ffcc00",
    ready: "#00ff88",
    frozen: "#ff4444",
    appeal: "#ff8800",
    banned: "#ff0000",
    dead: "#666",
  };

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: 24, color: "#ff4444" }}>ADMIN — Command Center</h1>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn-accent" onClick={() => navigate("/admin-onboarding")}>
            + Загрузить аккаунт
          </button>
          <button className="btn-secondary" onClick={() => navigate("/admin-proxies")}>
            Импорт прокси
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16, marginBottom: 32 }}>
        {stats && (
          <>
            <StatCard label="Аккаунтов всего" value={stats.accounts.total} color="#4488ff" />
            {Object.entries(stats.accounts.by_status).map(([s, count]) => (
              <StatCard key={s} label={s.toUpperCase()} value={count} color={statusColors[s] || "#888"} />
            ))}
            <StatCard label="Прокси живых" value={stats.proxies.alive} color="#00ff88" />
            <StatCard label="Прокси мёртвых" value={stats.proxies.dead} color="#ff4444" />
            <StatCard label="Прокси свободных" value={stats.proxies.free} color="#ffcc00" />
            <StatCard label="Прокси привязанных" value={stats.proxies.bound} color="#4488ff" />
          </>
        )}
      </div>

      {/* Recent Operations */}
      <h2 style={{ fontSize: 18, marginBottom: 12, color: "var(--fg)" }}>Последние операции</h2>
      <div style={{ overflowX: "auto" }}>
        <table className="data-table" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th>Время</th>
              <th>Модуль</th>
              <th>Действие</th>
              <th>Статус</th>
              <th>Детали</th>
            </tr>
          </thead>
          <tbody>
            {recentOps.map((op) => (
              <tr key={op.id}>
                <td style={{ whiteSpace: "nowrap", fontSize: 12, color: "#888" }}>
                  {op.created_at ? new Date(op.created_at).toLocaleString("ru-RU") : "—"}
                </td>
                <td><span className="badge">{op.module}</span></td>
                <td>{op.action}</td>
                <td>
                  <span style={{
                    color: op.status === "success" ? "#00ff88" : op.status === "error" ? "#ff4444" : "#ffcc00",
                    fontWeight: 600,
                  }}>
                    {op.status}
                  </span>
                </td>
                <td style={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 12 }}>
                  {op.detail || "—"}
                </td>
              </tr>
            ))}
            {recentOps.length === 0 && (
              <tr><td colSpan={5} style={{ textAlign: "center", color: "#666" }}>Нет операций</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{
      background: "rgba(255,255,255,0.03)",
      border: "1px solid rgba(255,255,255,0.08)",
      borderRadius: 8,
      padding: 16,
      textAlign: "center",
    }}>
      <div style={{ fontSize: 28, fontWeight: 700, color, fontFamily: "'JetBrains Mono', monospace" }}>
        {value}
      </div>
      <div style={{ fontSize: 12, color: "#888", marginTop: 4 }}>{label}</div>
    </div>
  );
}
