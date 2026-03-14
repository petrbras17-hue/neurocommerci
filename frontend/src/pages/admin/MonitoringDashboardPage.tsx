import { useState, useEffect, useCallback, useRef } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";

interface AccountStatus {
  id: number;
  workspace_id: number;
  account_id: number;
  account_phone: string | null;
  current_module: string | null;
  current_action: string | null;
  started_at: string | null;
  last_heartbeat_at: string | null;
}

interface ModuleBreakdown {
  module: string;
  actions: number;
  errors: number;
  avg_latency_ms: number | null;
  active_accounts: number;
}

interface DashboardData {
  total_accounts: number;
  active_accounts: number;
  module_counts: Record<string, number>;
  total_actions_1h: number;
  total_errors_1h: number;
  error_rate_percent: number;
  module_breakdown: ModuleBreakdown[];
  account_statuses: AccountStatus[];
}

const MODULE_COLORS: Record<string, string> = {
  warmup: "#3b82f6",
  farm: "#00ff88",
  parsing: "#f59e0b",
  reactions: "#a855f7",
  chatting: "#06b6d4",
  dialogs: "#ec4899",
  commenting: "#84cc16",
  free: "#444",
};

function getModuleColor(module: string | null): string {
  return MODULE_COLORS[module || "free"] || "#666";
}

function formatDuration(startedAt: string | null): string {
  if (!startedAt) return "--";
  const diff = Date.now() - new Date(startedAt).getTime();
  const mins = Math.floor(diff / 60000);
  const secs = Math.floor((diff % 60000) / 1000);
  if (mins > 60) return `${Math.floor(mins / 60)}h ${mins % 60}m`;
  if (mins > 0) return `${mins}m ${secs}s`;
  return `${secs}s`;
}

export function MonitoringDashboardPage() {
  const { accessToken, workspace } = useAuth();
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const wsRef = useRef<WebSocket | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [wsMessages, setWsMessages] = useState<string[]>([]);

  const fetchDashboard = useCallback(async () => {
    try {
      const data = await apiFetch<DashboardData>("/v1/admin/monitoring/dashboard", { accessToken });
      setDashboard(data);
    } catch (e) {
      console.error("fetch dashboard:", e);
    }
    setLoading(false);
  }, [accessToken]);

  // REST polling every 5 seconds
  useEffect(() => {
    fetchDashboard();
    const interval = setInterval(fetchDashboard, 5000);
    return () => clearInterval(interval);
  }, [fetchDashboard]);

  // WebSocket for real-time status updates
  useEffect(() => {
    if (!workspace?.id) return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/v1/ws/status?workspace_id=${workspace.id}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => setWsConnected(true);
    ws.onclose = () => setWsConnected(false);
    ws.onerror = () => setWsConnected(false);
    ws.onmessage = (event) => {
      setWsMessages(prev => [event.data, ...prev.slice(0, 49)]);
      // Trigger a re-fetch on status change
      fetchDashboard();
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [workspace?.id, fetchDashboard]);

  const cardStyle: React.CSSProperties = {
    background: "#111", border: "1px solid #222", borderRadius: 8, padding: 16,
  };
  const metricStyle: React.CSSProperties = {
    ...cardStyle, textAlign: "center",
  };

  if (loading && !dashboard) {
    return (
      <div style={{ padding: 24, color: "#888" }}>Loading monitoring dashboard...</div>
    );
  }

  const d = dashboard;

  return (
    <div style={{ padding: 24, maxWidth: 1200, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <h1 style={{ color: "#00ff88", fontFamily: "JetBrains Mono, monospace" }}>
          Monitoring Dashboard
        </h1>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{
            display: "inline-block", width: 8, height: 8, borderRadius: "50%",
            background: wsConnected ? "#00ff88" : "#ff4444",
            boxShadow: wsConnected ? "0 0 6px #00ff88" : "0 0 6px #ff4444",
          }} />
          <span style={{ color: wsConnected ? "#00ff88" : "#ff4444", fontSize: 12 }}>
            {wsConnected ? "Live" : "Offline"}
          </span>
        </div>
      </div>

      {/* Top metrics row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 24 }}>
        <div style={metricStyle}>
          <div style={{ color: "#666", fontSize: 11, marginBottom: 4 }}>Total Accounts</div>
          <div style={{ color: "#e0e0e0", fontSize: 28, fontWeight: 700, fontFamily: "JetBrains Mono, monospace" }}>
            {d?.total_accounts || 0}
          </div>
        </div>
        <div style={metricStyle}>
          <div style={{ color: "#666", fontSize: 11, marginBottom: 4 }}>Active Now</div>
          <div style={{ color: "#00ff88", fontSize: 28, fontWeight: 700, fontFamily: "JetBrains Mono, monospace" }}>
            {d?.active_accounts || 0}
          </div>
        </div>
        <div style={metricStyle}>
          <div style={{ color: "#666", fontSize: 11, marginBottom: 4 }}>Actions (1h)</div>
          <div style={{ color: "#3b82f6", fontSize: 28, fontWeight: 700, fontFamily: "JetBrains Mono, monospace" }}>
            {d?.total_actions_1h || 0}
          </div>
        </div>
        <div style={metricStyle}>
          <div style={{ color: "#666", fontSize: 11, marginBottom: 4 }}>Error Rate</div>
          <div style={{
            color: (d?.error_rate_percent || 0) > 5 ? "#ff4444" : "#00ff88",
            fontSize: 28, fontWeight: 700, fontFamily: "JetBrains Mono, monospace",
          }}>
            {d?.error_rate_percent?.toFixed(1) || "0.0"}%
          </div>
        </div>
      </div>

      {/* Module breakdown */}
      <div style={{ ...cardStyle, marginBottom: 24 }}>
        <h3 style={{ color: "#888", fontSize: 14, marginBottom: 16 }}>Module Activity</h3>
        {d?.module_breakdown && d.module_breakdown.length > 0 ? (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 12 }}>
            {d.module_breakdown.map(mod => {
              const maxActions = Math.max(...d.module_breakdown.map(m => m.actions), 1);
              const barWidth = (mod.actions / maxActions) * 100;
              return (
                <div key={mod.module} style={{
                  background: "#0a0a0b", borderRadius: 6, padding: 12,
                  borderLeft: `3px solid ${getModuleColor(mod.module)}`,
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                    <span style={{ color: getModuleColor(mod.module), fontWeight: 600, fontSize: 13 }}>
                      {mod.module}
                    </span>
                    <span style={{ color: "#666", fontSize: 11 }}>
                      {mod.active_accounts} acc
                    </span>
                  </div>
                  <div style={{ background: "#1a1a1a", borderRadius: 3, height: 6, marginBottom: 6 }}>
                    <div style={{
                      background: getModuleColor(mod.module), borderRadius: 3,
                      height: "100%", width: `${barWidth}%`, transition: "width 0.3s",
                    }} />
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#888" }}>
                    <span>{mod.actions} actions</span>
                    <span style={{ color: mod.errors > 0 ? "#ff4444" : "#666" }}>
                      {mod.errors} err
                    </span>
                  </div>
                  {mod.avg_latency_ms != null && (
                    <div style={{ fontSize: 10, color: "#555", marginTop: 4 }}>
                      avg {mod.avg_latency_ms}ms
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <p style={{ color: "#555", fontSize: 13 }}>No throughput data yet.</p>
        )}
      </div>

      {/* Account status grid */}
      <div style={{ ...cardStyle, marginBottom: 24 }}>
        <h3 style={{ color: "#888", fontSize: 14, marginBottom: 16 }}>Account Status Grid</h3>
        {d?.account_statuses && d.account_statuses.length > 0 ? (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 10 }}>
            {d.account_statuses.map(acc => {
              const mod = acc.current_module || "free";
              const color = getModuleColor(mod);
              const isFree = mod === "free";
              return (
                <div key={acc.id} style={{
                  background: "#0a0a0b", borderRadius: 6, padding: 10,
                  border: `1px solid ${isFree ? "#222" : color}40`,
                  opacity: isFree ? 0.6 : 1,
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ color: "#e0e0e0", fontSize: 13, fontFamily: "JetBrains Mono, monospace" }}>
                      {acc.account_phone || `#${acc.account_id}`}
                    </span>
                    <span style={{
                      display: "inline-block", width: 6, height: 6, borderRadius: "50%",
                      background: color,
                      boxShadow: isFree ? "none" : `0 0 4px ${color}`,
                      animation: isFree ? "none" : "pulse 2s infinite",
                    }} />
                  </div>
                  <div style={{
                    color, fontSize: 11, fontWeight: 600, marginTop: 4,
                    textTransform: "uppercase", letterSpacing: "0.5px",
                  }}>
                    {mod}
                  </div>
                  {acc.current_action && (
                    <div style={{ color: "#888", fontSize: 11, marginTop: 2 }}>
                      {acc.current_action}
                    </div>
                  )}
                  <div style={{ color: "#555", fontSize: 10, marginTop: 4 }}>
                    {formatDuration(acc.started_at)}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p style={{ color: "#555", fontSize: 13 }}>No account statuses yet.</p>
        )}
      </div>

      {/* WebSocket live feed */}
      <div style={{ ...cardStyle }}>
        <h3 style={{ color: "#888", fontSize: 14, marginBottom: 12 }}>Live Feed</h3>
        <div style={{
          maxHeight: 200, overflowY: "auto", fontFamily: "JetBrains Mono, monospace", fontSize: 11,
        }}>
          {wsMessages.length > 0 ? wsMessages.map((msg, i) => {
            let parsed: Record<string, unknown> = {};
            try { parsed = JSON.parse(msg); } catch { /* ignore */ }
            return (
              <div key={i} style={{
                color: "#888", padding: "2px 0", borderBottom: "1px solid #1a1a1a",
              }}>
                <span style={{ color: getModuleColor(String(parsed.module || "")) }}>
                  [{String(parsed.module || "?")}]
                </span>
                {" "}acc#{String(parsed.account_id || "?")} {String(parsed.action || "")}
                <span style={{ color: "#444", marginLeft: 8 }}>
                  {String(parsed.ts || "").slice(11, 19)}
                </span>
              </div>
            );
          }) : (
            <p style={{ color: "#555" }}>Waiting for status updates...</p>
          )}
        </div>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
      `}</style>
    </div>
  );
}
