import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";

interface LaunchPlan {
  id: number;
  farm_id: number;
  name: string | null;
  scaling_curve: string;
  current_day: number;
  is_active: boolean;
  day_1_limit: number;
  day_3_limit: number;
  day_7_limit: number;
  day_14_limit: number;
  day_30_limit: number;
  health_gate_threshold: number;
  auto_reduce_factor: number;
  started_at: string | null;
  created_at: string | null;
}

interface ScalingEvent {
  id: number;
  farm_id: number;
  account_id: number | null;
  day_number: number;
  max_allowed: number;
  actual_performed: number;
  was_health_gated: boolean;
  was_antifraud_gated: boolean;
  recorded_at: string | null;
}

interface CurrentLimit {
  plan_id: number;
  current_day: number;
  current_limit: number;
}

const CURVES = ["gradual", "linear", "exponential", "custom"] as const;

function ScalingCurveChart({ plan }: { plan: LaunchPlan }) {
  const W = 320;
  const H = 120;
  const PAD = 24;

  const getLimit = (day: number): number => {
    if (plan.scaling_curve === "linear") {
      const d30 = plan.day_30_limit === -1 ? 50 : plan.day_30_limit;
      if (day >= 30) return d30;
      return Math.max(1, Math.round(plan.day_1_limit + (d30 - plan.day_1_limit) * day / 30));
    }
    if (plan.scaling_curve === "exponential") {
      const d30 = plan.day_30_limit === -1 ? 50 : plan.day_30_limit;
      if (day >= 30) return d30;
      return Math.max(1, Math.min(Math.round(plan.day_1_limit * Math.pow(2, day / 7)), d30));
    }
    // gradual (step function)
    if (day >= 30) return plan.day_30_limit === -1 ? 50 : plan.day_30_limit;
    if (day >= 14) return plan.day_14_limit;
    if (day >= 7) return plan.day_7_limit;
    if (day >= 3) return plan.day_3_limit;
    return plan.day_1_limit;
  };

  const points: Array<[number, number]> = [];
  const maxVal = Math.max(...Array.from({ length: 31 }, (_, i) => getLimit(i)), 1);

  for (let d = 0; d <= 30; d++) {
    const x = PAD + (d / 30) * (W - 2 * PAD);
    const y = H - PAD - (getLimit(d) / maxVal) * (H - 2 * PAD);
    points.push([x, y]);
  }

  const pathD = points.map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");

  const currentX = PAD + ((plan.current_day || 0) / 30) * (W - 2 * PAD);
  const currentY = H - PAD - (getLimit(plan.current_day || 0) / maxVal) * (H - 2 * PAD);

  return (
    <svg width={W} height={H} style={{ background: "#111", borderRadius: 6 }}>
      {/* Grid lines */}
      {[0, 7, 14, 21, 30].map(d => {
        const x = PAD + (d / 30) * (W - 2 * PAD);
        return (
          <g key={d}>
            <line x1={x} y1={PAD} x2={x} y2={H - PAD} stroke="#333" strokeWidth={0.5} />
            <text x={x} y={H - 6} fill="#666" fontSize={9} textAnchor="middle">{d}</text>
          </g>
        );
      })}
      {/* Curve */}
      <path d={pathD} fill="none" stroke="#00ff88" strokeWidth={2} />
      {/* Current position */}
      <circle cx={currentX} cy={currentY} r={4} fill="#00ff88" />
      <text x={currentX + 6} y={currentY - 6} fill="#00ff88" fontSize={10}>
        Day {plan.current_day}
      </text>
    </svg>
  );
}

export function FarmLaunchPage() {
  const { accessToken } = useAuth();
  const [plans, setPlans] = useState<LaunchPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPlan, setSelectedPlan] = useState<number | null>(null);
  const [history, setHistory] = useState<ScalingEvent[]>([]);
  const [currentLimit, setCurrentLimit] = useState<CurrentLimit | null>(null);

  // Form state
  const [formFarmId, setFormFarmId] = useState(1);
  const [formName, setFormName] = useState("");
  const [formCurve, setFormCurve] = useState<string>("gradual");
  const [formDay1, setFormDay1] = useState(2);
  const [formDay3, setFormDay3] = useState(5);
  const [formDay7, setFormDay7] = useState(10);
  const [formDay14, setFormDay14] = useState(20);
  const [formDay30, setFormDay30] = useState(-1);
  const [formHealthThreshold, setFormHealthThreshold] = useState(40);
  const [formReduceFactor, setFormReduceFactor] = useState(0.5);

  const fetchPlans = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: LaunchPlan[] }>("/v1/admin/farm/launch-plans", { accessToken });
      setPlans(data.items);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => { fetchPlans(); }, [fetchPlans]);

  const createPlan = async () => {
    try {
      await apiFetch("/v1/admin/farm/launch-plans", {
        method: "POST",
        accessToken,
        json: {
          farm_id: formFarmId,
          name: formName || null,
          scaling_curve: formCurve,
          day_1_limit: formDay1,
          day_3_limit: formDay3,
          day_7_limit: formDay7,
          day_14_limit: formDay14,
          day_30_limit: formDay30,
          health_gate_threshold: formHealthThreshold,
          auto_reduce_factor: formReduceFactor,
        },
      });
      setFormName("");
      fetchPlans();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const advanceDay = async (planId: number) => {
    await apiFetch(`/v1/admin/farm/launch-plans/${planId}/advance-day`, { method: "POST", accessToken });
    fetchPlans();
    if (selectedPlan === planId) loadPlanDetails(planId);
  };

  const deletePlan = async (planId: number) => {
    await apiFetch(`/v1/admin/farm/launch-plans/${planId}`, { method: "DELETE", accessToken });
    if (selectedPlan === planId) setSelectedPlan(null);
    fetchPlans();
  };

  const loadPlanDetails = async (planId: number) => {
    setSelectedPlan(planId);
    const [histData, limitData] = await Promise.all([
      apiFetch<{ items: ScalingEvent[] }>(`/v1/admin/farm/launch-plans/${planId}/scaling-history`, { accessToken }),
      apiFetch<CurrentLimit>(`/v1/admin/farm/launch-plans/${planId}/current-limit`, { accessToken }),
    ]);
    setHistory(histData.items);
    setCurrentLimit(limitData);
  };

  return (
    <div style={{ padding: 24, color: "#e0e0e0", maxWidth: 1200 }}>
      <h1 style={{ color: "#00ff88", marginBottom: 24 }}>Farm Launch Plans</h1>

      {error && <div style={{ color: "#ff4444", marginBottom: 16 }}>{error}</div>}

      {/* Create form */}
      <div style={{ background: "#111", border: "1px solid #222", borderRadius: 8, padding: 20, marginBottom: 24 }}>
        <h3 style={{ color: "#00ff88", marginTop: 0 }}>New Launch Plan</h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 12, marginBottom: 12 }}>
          <div>
            <label style={{ fontSize: 12, color: "#888" }}>Farm ID</label>
            <input type="number" value={formFarmId} onChange={e => setFormFarmId(+e.target.value)}
              style={{ width: "100%", background: "#0a0a0b", border: "1px solid #333", color: "#e0e0e0", padding: "6px 8px", borderRadius: 4 }} />
          </div>
          <div>
            <label style={{ fontSize: 12, color: "#888" }}>Name</label>
            <input value={formName} onChange={e => setFormName(e.target.value)} placeholder="Plan name"
              style={{ width: "100%", background: "#0a0a0b", border: "1px solid #333", color: "#e0e0e0", padding: "6px 8px", borderRadius: 4 }} />
          </div>
          <div>
            <label style={{ fontSize: 12, color: "#888" }}>Scaling Curve</label>
            <select value={formCurve} onChange={e => setFormCurve(e.target.value)}
              style={{ width: "100%", background: "#0a0a0b", border: "1px solid #333", color: "#e0e0e0", padding: "6px 8px", borderRadius: 4 }}>
              {CURVES.map(c => <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 12, color: "#888" }}>Health Gate</label>
            <input type="range" min={0} max={100} value={formHealthThreshold}
              onChange={e => setFormHealthThreshold(+e.target.value)} style={{ width: "100%" }} />
            <span style={{ fontSize: 11, color: "#888" }}>{formHealthThreshold}</span>
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12, marginBottom: 12 }}>
          {[
            { label: "Day 1", val: formDay1, set: setFormDay1 },
            { label: "Day 3", val: formDay3, set: setFormDay3 },
            { label: "Day 7", val: formDay7, set: setFormDay7 },
            { label: "Day 14", val: formDay14, set: setFormDay14 },
            { label: "Day 30", val: formDay30, set: setFormDay30 },
          ].map(f => (
            <div key={f.label}>
              <label style={{ fontSize: 12, color: "#888" }}>{f.label}</label>
              <input type="number" value={f.val} onChange={e => f.set(+e.target.value)}
                style={{ width: "100%", background: "#0a0a0b", border: "1px solid #333", color: "#e0e0e0", padding: "6px 8px", borderRadius: 4 }} />
            </div>
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <label style={{ fontSize: 12, color: "#888" }}>Auto-reduce factor: {formReduceFactor}</label>
          <input type="range" min={0.1} max={1.0} step={0.05} value={formReduceFactor}
            onChange={e => setFormReduceFactor(+e.target.value)} style={{ flex: 1 }} />
          <button onClick={createPlan}
            style={{ padding: "8px 20px", background: "#00ff88", color: "#000", border: "none", borderRadius: 4, fontWeight: 600, cursor: "pointer" }}>
            Create Plan
          </button>
        </div>
      </div>

      {/* Plans list */}
      {loading ? (
        <div style={{ color: "#888" }}>Loading...</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          {/* Left: plans */}
          <div>
            <h3 style={{ color: "#888", marginTop: 0 }}>Active Plans ({plans.length})</h3>
            {plans.map(p => (
              <div key={p.id} onClick={() => loadPlanDetails(p.id)}
                style={{
                  background: selectedPlan === p.id ? "#1a1a2e" : "#111",
                  border: `1px solid ${selectedPlan === p.id ? "#00ff88" : "#222"}`,
                  borderRadius: 8, padding: 16, marginBottom: 8, cursor: "pointer",
                }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <strong style={{ color: "#00ff88" }}>{p.name || `Plan #${p.id}`}</strong>
                    <span style={{ color: "#666", marginLeft: 8, fontSize: 12 }}>Farm #{p.farm_id}</span>
                  </div>
                  <span style={{
                    fontSize: 11, padding: "2px 8px", borderRadius: 4,
                    background: p.is_active ? "#002200" : "#220000",
                    color: p.is_active ? "#00ff88" : "#ff4444",
                  }}>
                    {p.is_active ? "Active" : "Inactive"}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: "#888", marginTop: 8 }}>
                  Curve: {p.scaling_curve} | Day {p.current_day}/30 | Limits: {p.day_1_limit}/{p.day_3_limit}/{p.day_7_limit}/{p.day_14_limit}/{p.day_30_limit}
                </div>
                <ScalingCurveChart plan={p} />
                <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                  <button onClick={(e) => { e.stopPropagation(); advanceDay(p.id); }}
                    style={{ padding: "4px 12px", background: "#222", color: "#00ff88", border: "1px solid #333", borderRadius: 4, cursor: "pointer", fontSize: 11 }}>
                    Advance Day
                  </button>
                  <button onClick={(e) => { e.stopPropagation(); deletePlan(p.id); }}
                    style={{ padding: "4px 12px", background: "#222", color: "#ff4444", border: "1px solid #333", borderRadius: 4, cursor: "pointer", fontSize: 11 }}>
                    Delete
                  </button>
                </div>
              </div>
            ))}
            {plans.length === 0 && <div style={{ color: "#666" }}>No launch plans yet.</div>}
          </div>

          {/* Right: details */}
          <div>
            {selectedPlan && currentLimit && (
              <>
                <h3 style={{ color: "#888", marginTop: 0 }}>
                  Plan #{selectedPlan} — Current Limit: <span style={{ color: "#00ff88" }}>{currentLimit.current_limit}</span>
                </h3>
                <div style={{ background: "#111", border: "1px solid #222", borderRadius: 8, padding: 16 }}>
                  <h4 style={{ color: "#888", marginTop: 0 }}>Scaling History</h4>
                  {history.length === 0 ? (
                    <div style={{ color: "#666", fontSize: 13 }}>No scaling events yet.</div>
                  ) : (
                    <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                      <thead>
                        <tr style={{ color: "#888", borderBottom: "1px solid #333" }}>
                          <th style={{ padding: 4, textAlign: "left" }}>Day</th>
                          <th style={{ padding: 4, textAlign: "left" }}>Account</th>
                          <th style={{ padding: 4, textAlign: "left" }}>Max</th>
                          <th style={{ padding: 4, textAlign: "left" }}>Actual</th>
                          <th style={{ padding: 4, textAlign: "left" }}>Gates</th>
                        </tr>
                      </thead>
                      <tbody>
                        {history.map(h => (
                          <tr key={h.id} style={{ borderBottom: "1px solid #1a1a1a" }}>
                            <td style={{ padding: 4 }}>{h.day_number}</td>
                            <td style={{ padding: 4, color: "#888" }}>{h.account_id || "-"}</td>
                            <td style={{ padding: 4 }}>{h.max_allowed}</td>
                            <td style={{ padding: 4, color: h.actual_performed >= h.max_allowed ? "#ff4444" : "#00ff88" }}>
                              {h.actual_performed}
                            </td>
                            <td style={{ padding: 4 }}>
                              {h.was_health_gated && <span style={{ color: "#f59e0b", marginRight: 4 }}>HP</span>}
                              {h.was_antifraud_gated && <span style={{ color: "#ff4444" }}>AF</span>}
                              {!h.was_health_gated && !h.was_antifraud_gated && <span style={{ color: "#666" }}>-</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default FarmLaunchPage;
