import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";

interface RiskSummary {
  total_scores_today: number;
  avg_risk_today: number;
  decisions_today: Record<string, number>;
  unresolved_alerts: number;
  pattern_types: Record<string, number>;
}

interface AntifraudDecision {
  id: number;
  account_id: number;
  action_type: string;
  risk_score: number;
  risk_factors: Record<string, number>;
  decision: string;
  decided_at: string | null;
}

interface PatternAlert {
  id: number;
  pattern_type: string;
  accounts_involved: number[];
  severity: string;
  detail: string | null;
  is_resolved: boolean;
  detected_at: string | null;
}

const SEVERITY_COLORS: Record<string, string> = {
  low: "#3b82f6",
  medium: "#f59e0b",
  high: "#ff4444",
  critical: "#ff0000",
};

const DECISION_COLORS: Record<string, string> = {
  proceed: "#00ff88",
  delay: "#f59e0b",
  skip: "#ff4444",
  alert: "#ff0000",
};

const PATTERN_ICONS: Record<string, string> = {
  identical_timing: "Clock",
  same_content: "FileText",
  burst_activity: "Zap",
  geo_mismatch: "MapPin",
};

function RiskBar({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = score < 0.3 ? "#00ff88" : score < 0.6 ? "#f59e0b" : "#ff4444";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ width: 60, height: 6, background: "#222", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: 11, color }}>{pct}%</span>
    </div>
  );
}

export function AntifraudPage() {
  const { accessToken } = useAuth();
  const [summary, setSummary] = useState<RiskSummary | null>(null);
  const [alerts, setAlerts] = useState<PatternAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [testAccountId, setTestAccountId] = useState(1);
  const [testActionType, setTestActionType] = useState("comment");
  const [lastScore, setLastScore] = useState<AntifraudDecision | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [sumData, alertData] = await Promise.all([
        apiFetch<RiskSummary>("/v1/admin/antifraud/summary", { accessToken }),
        apiFetch<{ items: PatternAlert[] }>("/v1/admin/antifraud/alerts", { accessToken }),
      ]);
      setSummary(sumData);
      setAlerts(alertData.items);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const scoreAction = async () => {
    try {
      const data = await apiFetch<AntifraudDecision>("/v1/admin/antifraud/score", {
        method: "POST",
        accessToken,
        json: { account_id: testAccountId, action_type: testActionType },
      });
      setLastScore(data);
      fetchData();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const detectPatterns = async () => {
    try {
      await apiFetch("/v1/admin/antifraud/detect-patterns", { method: "POST", accessToken });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const resolveAlert = async (patternId: number) => {
    await apiFetch(`/v1/admin/antifraud/alerts/${patternId}/resolve`, { method: "POST", accessToken });
    fetchData();
  };

  if (loading) return <div style={{ padding: 24, color: "#888" }}>Loading...</div>;

  return (
    <div style={{ padding: 24, color: "#e0e0e0", maxWidth: 1200 }}>
      <h1 style={{ color: "#00ff88", marginBottom: 24 }}>Anti-Fraud Intelligence</h1>

      {error && <div style={{ color: "#ff4444", marginBottom: 16 }}>{error}</div>}

      {/* Summary cards */}
      {summary && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 24 }}>
          <div style={{ background: "#111", border: "1px solid #222", borderRadius: 8, padding: 16 }}>
            <div style={{ fontSize: 12, color: "#888" }}>Scores Today</div>
            <div style={{ fontSize: 28, fontWeight: 700, color: "#00ff88" }}>{summary.total_scores_today}</div>
          </div>
          <div style={{ background: "#111", border: "1px solid #222", borderRadius: 8, padding: 16 }}>
            <div style={{ fontSize: 12, color: "#888" }}>Avg Risk</div>
            <div style={{ fontSize: 28, fontWeight: 700, color: summary.avg_risk_today < 0.3 ? "#00ff88" : summary.avg_risk_today < 0.6 ? "#f59e0b" : "#ff4444" }}>
              {(summary.avg_risk_today * 100).toFixed(1)}%
            </div>
          </div>
          <div style={{ background: "#111", border: "1px solid #222", borderRadius: 8, padding: 16 }}>
            <div style={{ fontSize: 12, color: "#888" }}>Unresolved Alerts</div>
            <div style={{ fontSize: 28, fontWeight: 700, color: summary.unresolved_alerts > 0 ? "#ff4444" : "#00ff88" }}>
              {summary.unresolved_alerts}
            </div>
          </div>
          <div style={{ background: "#111", border: "1px solid #222", borderRadius: 8, padding: 16 }}>
            <div style={{ fontSize: 12, color: "#888" }}>Decisions</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
              {Object.entries(summary.decisions_today).map(([dec, count]) => (
                <span key={dec} style={{
                  fontSize: 11, padding: "2px 6px", borderRadius: 4,
                  background: "#1a1a1a", color: DECISION_COLORS[dec] || "#888",
                }}>
                  {dec}: {count}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Test scoring */}
      <div style={{ background: "#111", border: "1px solid #222", borderRadius: 8, padding: 20, marginBottom: 24 }}>
        <h3 style={{ color: "#00ff88", marginTop: 0 }}>Test Risk Scoring</h3>
        <div style={{ display: "flex", gap: 12, alignItems: "flex-end" }}>
          <div>
            <label style={{ fontSize: 12, color: "#888" }}>Account ID</label>
            <input type="number" value={testAccountId} onChange={e => setTestAccountId(+e.target.value)}
              style={{ display: "block", background: "#0a0a0b", border: "1px solid #333", color: "#e0e0e0", padding: "6px 8px", borderRadius: 4, width: 100 }} />
          </div>
          <div>
            <label style={{ fontSize: 12, color: "#888" }}>Action Type</label>
            <select value={testActionType} onChange={e => setTestActionType(e.target.value)}
              style={{ display: "block", background: "#0a0a0b", border: "1px solid #333", color: "#e0e0e0", padding: "6px 8px", borderRadius: 4 }}>
              <option value="comment">comment</option>
              <option value="reaction">reaction</option>
              <option value="dm">dm</option>
              <option value="join">join</option>
            </select>
          </div>
          <button onClick={scoreAction}
            style={{ padding: "8px 16px", background: "#00ff88", color: "#000", border: "none", borderRadius: 4, fontWeight: 600, cursor: "pointer" }}>
            Score
          </button>
          <button onClick={detectPatterns}
            style={{ padding: "8px 16px", background: "#222", color: "#f59e0b", border: "1px solid #333", borderRadius: 4, cursor: "pointer" }}>
            Detect Patterns
          </button>
        </div>

        {lastScore && (
          <div style={{ marginTop: 16, padding: 12, background: "#0a0a0b", borderRadius: 6 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
              <span style={{ fontSize: 12, color: "#888" }}>Account #{lastScore.account_id}</span>
              <span style={{ fontSize: 12, color: "#888" }}>{lastScore.action_type}</span>
              <RiskBar score={lastScore.risk_score} />
              <span style={{
                fontSize: 11, padding: "2px 8px", borderRadius: 4, fontWeight: 600,
                background: lastScore.decision === "proceed" ? "#002200" : lastScore.decision === "alert" ? "#330000" : "#222",
                color: DECISION_COLORS[lastScore.decision] || "#888",
              }}>
                {lastScore.decision.toUpperCase()}
              </span>
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
              {Object.entries(lastScore.risk_factors).map(([factor, score]) => (
                <span key={factor} style={{
                  fontSize: 10, padding: "2px 6px", borderRadius: 3,
                  background: "#1a1a1a",
                  color: score < 0.3 ? "#00ff88" : score < 0.6 ? "#f59e0b" : "#ff4444",
                }}>
                  {factor}: {(score * 100).toFixed(0)}%
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Pattern alerts */}
      <div style={{ background: "#111", border: "1px solid #222", borderRadius: 8, padding: 20 }}>
        <h3 style={{ color: "#00ff88", marginTop: 0 }}>Pattern Alerts ({alerts.length})</h3>
        {alerts.length === 0 ? (
          <div style={{ color: "#666", fontSize: 13 }}>No unresolved patterns detected.</div>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {alerts.map(a => (
              <div key={a.id} style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                background: "#0a0a0b", border: "1px solid #222", borderRadius: 6, padding: 12,
              }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: 13, fontWeight: 600, color: "#e0e0e0" }}>
                      {PATTERN_ICONS[a.pattern_type] || "?"} {a.pattern_type.replace(/_/g, " ")}
                    </span>
                    <span style={{
                      fontSize: 10, padding: "1px 6px", borderRadius: 3,
                      background: "#1a1a1a", color: SEVERITY_COLORS[a.severity] || "#888",
                    }}>
                      {a.severity}
                    </span>
                  </div>
                  <div style={{ fontSize: 11, color: "#888", marginTop: 4 }}>{a.detail}</div>
                  <div style={{ fontSize: 10, color: "#666", marginTop: 2 }}>
                    Accounts: {(a.accounts_involved || []).join(", ")}
                    {a.detected_at && <span style={{ marginLeft: 8 }}>{new Date(a.detected_at).toLocaleString()}</span>}
                  </div>
                </div>
                {!a.is_resolved && (
                  <button onClick={() => resolveAlert(a.id)}
                    style={{ padding: "4px 12px", background: "#222", color: "#00ff88", border: "1px solid #333", borderRadius: 4, cursor: "pointer", fontSize: 11 }}>
                    Resolve
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default AntifraudPage;
