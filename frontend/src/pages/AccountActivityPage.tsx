import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { apiFetch } from "../api";
import { useAuth } from "../auth";

interface ActivityItem {
  id: number;
  action_type: string;
  success: boolean;
  duration_ms: number | null;
  error_message: string | null;
  details: Record<string, unknown> | null;
  created_at: string | null;
}

interface ActivitySummary {
  account_id: number;
  hours: number;
  total_actions: number;
  total_success: number;
  total_fail: number;
  success_rate: number;
  actions_by_type: Record<string, { total: number; success: number; fail: number; avg_duration_ms: number | null }>;
  top_errors: { action_type: string; error: string; count: number }[];
}

const ACTION_LABELS: Record<string, string> = {
  warmup_read: "Чтение каналов",
  warmup_reaction: "Реакция",
  comment: "Комментарий",
  flood_wait: "FloodWait",
  spam_block: "Спам-блок",
  quarantine: "Карантин",
  health_check: "Проверка здоровья",
  login: "Вход в аккаунт",
  typing_sim: "Симуляция печати",
  channel_browse: "Просмотр каналов",
  dialog_read: "Чтение диалогов",
  error: "Ошибка",
};

function actionLabel(type: string): string {
  return ACTION_LABELS[type] || type;
}

export function AccountActivityPage() {
  const { id } = useParams<{ id: string }>();
  const auth = useAuth();
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [summary, setSummary] = useState<ActivitySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [hours, setHours] = useState(48);
  const [filter, setFilter] = useState<string>("");

  const accountId = id ? parseInt(id, 10) : 0;

  const fetchData = useCallback(async () => {
    if (!accountId || !auth.accessToken) return;
    setLoading(true);
    try {
      const qs = filter ? `&action_type=${encodeURIComponent(filter)}` : "";
      const [actResp, sumResp] = await Promise.all([
        apiFetch<{ items: ActivityItem[] }>(
          `/v1/accounts/${accountId}/activity?hours=${hours}&limit=200${qs}`,
          { accessToken: auth.accessToken }
        ),
        apiFetch<ActivitySummary>(
          `/v1/accounts/${accountId}/activity/summary?hours=${hours}`,
          { accessToken: auth.accessToken }
        ),
      ]);
      setItems(actResp.items);
      setSummary(sumResp);
    } catch {
      // silently fail
    } finally {
      setLoading(false);
    }
  }, [accountId, auth.accessToken, hours, filter]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  if (!accountId) return <div className="page-card">Аккаунт не найден</div>;

  return (
    <div style={{ padding: "20px", maxWidth: 1000, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
        <Link to="/accounts" style={{ color: "#0f0", textDecoration: "none" }}>&larr; Аккаунты</Link>
        <h1 style={{ margin: 0, fontSize: 22 }}>Активность аккаунта #{accountId}</h1>
      </div>

      {/* Controls */}
      <div style={{ display: "flex", gap: 12, marginBottom: 20, flexWrap: "wrap" }}>
        <select
          value={hours}
          onChange={(e) => setHours(Number(e.target.value))}
          style={{ padding: "8px 12px", background: "#1a1a2e", color: "#e0e0e0", border: "1px solid #333", borderRadius: 6 }}
        >
          <option value={12}>12 часов</option>
          <option value={24}>24 часа</option>
          <option value={48}>48 часов</option>
          <option value={168}>7 дней</option>
        </select>
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ padding: "8px 12px", background: "#1a1a2e", color: "#e0e0e0", border: "1px solid #333", borderRadius: 6 }}
        >
          <option value="">Все действия</option>
          {Object.keys(ACTION_LABELS).map((k) => (
            <option key={k} value={k}>{ACTION_LABELS[k]}</option>
          ))}
        </select>
        <button onClick={() => void fetchData()} style={{ padding: "8px 16px", background: "#0f0", color: "#000", border: "none", borderRadius: 6, cursor: "pointer", fontWeight: 600 }}>
          Обновить
        </button>
      </div>

      {loading && <div>Загрузка...</div>}

      {/* Summary Cards */}
      {summary && !loading && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12, marginBottom: 24 }}>
          <div className="page-card" style={{ padding: 16, textAlign: "center" }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: "#0f0" }}>{summary.total_actions}</div>
            <div style={{ fontSize: 12, color: "#999" }}>Всего действий</div>
          </div>
          <div className="page-card" style={{ padding: 16, textAlign: "center" }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: "#4caf50" }}>{summary.total_success}</div>
            <div style={{ fontSize: 12, color: "#999" }}>Успешных</div>
          </div>
          <div className="page-card" style={{ padding: 16, textAlign: "center" }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: "#f44336" }}>{summary.total_fail}</div>
            <div style={{ fontSize: 12, color: "#999" }}>Ошибок</div>
          </div>
          <div className="page-card" style={{ padding: 16, textAlign: "center" }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: summary.success_rate >= 90 ? "#0f0" : summary.success_rate >= 70 ? "#ff0" : "#f44336" }}>
              {summary.success_rate}%
            </div>
            <div style={{ fontSize: 12, color: "#999" }}>Success Rate</div>
          </div>
        </div>
      )}

      {/* Actions by type breakdown */}
      {summary && Object.keys(summary.actions_by_type).length > 0 && (
        <div className="page-card" style={{ padding: 16, marginBottom: 24 }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>По типам действий</h3>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid #333", textAlign: "left" }}>
                <th style={{ padding: 8 }}>Действие</th>
                <th style={{ padding: 8 }}>Всего</th>
                <th style={{ padding: 8 }}>OK</th>
                <th style={{ padding: 8 }}>Fail</th>
                <th style={{ padding: 8 }}>Avg ms</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(summary.actions_by_type).map(([type, data]) => (
                <tr key={type} style={{ borderBottom: "1px solid #222" }}>
                  <td style={{ padding: 8 }}>{actionLabel(type)}</td>
                  <td style={{ padding: 8 }}>{data.total}</td>
                  <td style={{ padding: 8, color: "#4caf50" }}>{data.success}</td>
                  <td style={{ padding: 8, color: data.fail > 0 ? "#f44336" : "#666" }}>{data.fail}</td>
                  <td style={{ padding: 8, color: "#999" }}>{data.avg_duration_ms ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Top Errors */}
      {summary && summary.top_errors.length > 0 && (
        <div className="page-card" style={{ padding: 16, marginBottom: 24 }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 16, color: "#f44336" }}>Топ ошибок</h3>
          {summary.top_errors.map((e, i) => (
            <div key={i} style={{ padding: "6px 0", borderBottom: "1px solid #222", display: "flex", gap: 12 }}>
              <span style={{ color: "#999", minWidth: 30 }}>x{e.count}</span>
              <span style={{ color: "#ff9800" }}>{actionLabel(e.action_type)}</span>
              <span style={{ color: "#f44336", fontSize: 13 }}>{e.error}</span>
            </div>
          ))}
        </div>
      )}

      {/* Activity Timeline */}
      {!loading && items.length > 0 && (
        <div className="page-card" style={{ padding: 16 }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>Лог активности ({items.length})</h3>
          <div style={{ maxHeight: 500, overflowY: "auto" }}>
            {items.map((item) => (
              <div
                key={item.id}
                style={{
                  padding: "8px 0",
                  borderBottom: "1px solid #222",
                  display: "grid",
                  gridTemplateColumns: "140px 160px 60px 80px 1fr",
                  gap: 8,
                  fontSize: 13,
                  alignItems: "center",
                }}
              >
                <span style={{ color: "#666" }}>
                  {item.created_at ? new Date(item.created_at).toLocaleString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit", day: "2-digit", month: "2-digit" }) : "—"}
                </span>
                <span>{actionLabel(item.action_type)}</span>
                <span style={{ color: item.success ? "#4caf50" : "#f44336" }}>
                  {item.success ? "OK" : "FAIL"}
                </span>
                <span style={{ color: "#999" }}>
                  {item.duration_ms != null ? `${item.duration_ms}ms` : "—"}
                </span>
                <span style={{ color: "#f44336", fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {item.error_message || ""}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {!loading && items.length === 0 && (
        <div className="page-card" style={{ padding: 32, textAlign: "center", color: "#666" }}>
          Нет данных за выбранный период. Активность начнёт записываться после запуска warmup или farm.
        </div>
      )}
    </div>
  );
}
