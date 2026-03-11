import { useEffect, useState } from "react";
import { analyticsApi, DashboardData } from "../api";
import { useAuth } from "../auth";

const DAYS_OPTIONS = [7, 14, 30] as const;
type DaysOption = typeof DAYS_OPTIONS[number];

function StatCard({ label, value, sub }: { label: string; value: number | string; sub?: string }) {
  return (
    <div className="info-block">
      <strong>{label}</strong>
      <span style={{ fontSize: 24, fontWeight: 700 }}>{value}</span>
      {sub ? <span className="muted" style={{ fontSize: 11 }}>{sub}</span> : null}
    </div>
  );
}

function ProgressBar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ flex: 1, height: 6, background: "#2d2d2d", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: 11, color: "#aaa", minWidth: 36 }}>{pct}%</span>
    </div>
  );
}

export function AnalyticsPage() {
  const { accessToken } = useAuth();

  const [days, setDays] = useState<DaysOption>(7);
  const [data, setData] = useState<DashboardData | null>(null);
  const [roi, setRoi] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = async (d: DaysOption) => {
    if (!accessToken) return;
    setBusy(true);
    setError("");
    try {
      const [dashPayload, roiPayload] = await Promise.all([
        analyticsApi.dashboard(accessToken, d),
        analyticsApi.roi(accessToken).catch(() => null),
      ]);
      setData(dashPayload);
      setRoi(roiPayload);
    } catch (e) {
      setError(e instanceof Error ? e.message : "load_failed");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load(days).catch(() => {});
  }, [accessToken, days]);

  const maxDayComments = data
    ? Math.max(1, ...data.daily_breakdown.map((d) => d.comments + d.reactions + d.errors))
    : 1;

  const maxChannelActions = data
    ? Math.max(1, ...data.top_channels.map((c) => c.actions))
    : 1;

  const maxAccountActions = data
    ? Math.max(1, ...data.account_activity.map((a) => a.actions))
    : 1;

  const actionsPerDay = data && days > 0
    ? Math.round((data.total_comments + data.total_reactions) / days)
    : 0;

  return (
    <div className="page-grid">
      {/* Header */}
      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Analytics Dashboard</div>
              <h2>Аналитика</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Сводные метрики по всем активным кампаниям и аккаунтам.</li>
            <li>Дневная активность, топ каналов и здоровье аккаунтов в одном месте.</li>
            <li>Данные обновляются при переключении периода.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Период</div>
              <h2>Выберите диапазон</h2>
            </div>
          </div>
          <div className="badge-row">
            {DAYS_OPTIONS.map((d) => (
              <button
                key={d}
                type="button"
                className={days === d ? "pill badge-green" : "pill badge-gray"}
                style={{ cursor: "pointer" }}
                onClick={() => setDays(d)}
                disabled={busy}
              >
                {d}д
              </button>
            ))}
            <button className="ghost-button" type="button" disabled={busy} onClick={() => void load(days)}>
              Обновить
            </button>
          </div>
        </article>
      </section>

      {error ? <div className="status-banner">{error}</div> : null}

      {/* Summary cards */}
      {data ? (
        <section className="panel wide">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Сводка за {days} дней</div>
              <h2>Ключевые метрики</h2>
            </div>
          </div>
          <div className="status-grid">
            <StatCard label="Комментариев" value={data.total_comments} sub="всего отправлено" />
            <StatCard label="Реакций" value={data.total_reactions} sub="всего поставлено" />
            <StatCard label="Flood Wait" value={data.total_flood_waits} sub="ограничений Telegram" />
            <StatCard label="Спам-блоков" value={data.total_spam_blocks} sub="блокировок аккаунтов" />
          </div>
        </section>
      ) : busy ? (
        <section className="panel wide">
          <p className="muted">Загружаем аналитику…</p>
        </section>
      ) : null}

      {/* Daily activity chart */}
      {data && data.daily_breakdown.length > 0 ? (
        <section className="panel wide">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Активность по дням</div>
              <h2>Дневная статистика</h2>
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {data.daily_breakdown.map((day) => {
              const total = day.comments + day.reactions + day.errors;
              const commPct = total > 0 ? (day.comments / maxDayComments) * 100 : 0;
              const reactPct = total > 0 ? (day.reactions / maxDayComments) * 100 : 0;
              const errPct = total > 0 ? (day.errors / maxDayComments) * 100 : 0;
              return (
                <div key={day.date} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ minWidth: 80, fontSize: 12, color: "#aaa" }}>{day.date.slice(5)}</span>
                  <div style={{ flex: 1, display: "flex", height: 12, borderRadius: 6, overflow: "hidden", background: "#1a1a1a" }}>
                    <div style={{ width: `${commPct}%`, background: "#22c55e" }} title={`Комменты: ${day.comments}`} />
                    <div style={{ width: `${reactPct}%`, background: "#6366f1" }} title={`Реакции: ${day.reactions}`} />
                    <div style={{ width: `${errPct}%`, background: "#ef4444" }} title={`Ошибки: ${day.errors}`} />
                  </div>
                  <span style={{ fontSize: 11, color: "#aaa", minWidth: 60, textAlign: "right" }}>
                    {day.comments + day.reactions} действий
                  </span>
                </div>
              );
            })}
          </div>
          <div className="badge-row" style={{ marginTop: 12, gap: 16 }}>
            <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <span style={{ width: 12, height: 12, background: "#22c55e", borderRadius: 2, display: "inline-block" }} />
              Комментарии
            </span>
            <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <span style={{ width: 12, height: 12, background: "#6366f1", borderRadius: 2, display: "inline-block" }} />
              Реакции
            </span>
            <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <span style={{ width: 12, height: 12, background: "#ef4444", borderRadius: 2, display: "inline-block" }} />
              Ошибки
            </span>
          </div>
        </section>
      ) : null}

      {/* Top channels */}
      {data && data.top_channels.length > 0 ? (
        <section className="panel wide">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Топ площадок</div>
              <h2>Активность по каналам</h2>
            </div>
          </div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Канал</th>
                  <th>Действий</th>
                  <th>Успешность</th>
                </tr>
              </thead>
              <tbody>
                {data.top_channels.map((ch, i) => (
                  <tr key={i}>
                    <td>
                      <a
                        href={`https://t.me/${ch.channel.replace("@", "")}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ color: "inherit" }}
                      >
                        {ch.channel}
                      </a>
                    </td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <div style={{ width: 80, height: 6, background: "#2d2d2d", borderRadius: 3, overflow: "hidden" }}>
                          <div
                            style={{
                              width: `${Math.round((ch.actions / maxChannelActions) * 100)}%`,
                              height: "100%",
                              background: "#6366f1",
                              borderRadius: 3,
                            }}
                          />
                        </div>
                        <span style={{ fontSize: 12 }}>{ch.actions}</span>
                      </div>
                    </td>
                    <td>
                      <ProgressBar
                        value={Math.round(ch.success_rate * 100)}
                        max={100}
                        color={ch.success_rate >= 0.8 ? "#22c55e" : ch.success_rate >= 0.5 ? "#eab308" : "#ef4444"}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {/* Account activity */}
      {data && data.account_activity.length > 0 ? (
        <section className="panel wide">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Аккаунты</div>
              <h2>Активность аккаунтов</h2>
            </div>
          </div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Аккаунт</th>
                  <th>Действий</th>
                  <th>Здоровье</th>
                </tr>
              </thead>
              <tbody>
                {data.account_activity.map((acc) => (
                  <tr key={acc.account_id}>
                    <td>{acc.phone ?? `#${acc.account_id}`}</td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <div style={{ width: 80, height: 6, background: "#2d2d2d", borderRadius: 3, overflow: "hidden" }}>
                          <div
                            style={{
                              width: `${Math.round((acc.actions / maxAccountActions) * 100)}%`,
                              height: "100%",
                              background: "#22c55e",
                              borderRadius: 3,
                            }}
                          />
                        </div>
                        <span style={{ fontSize: 12 }}>{acc.actions}</span>
                      </div>
                    </td>
                    <td>
                      <ProgressBar
                        value={acc.health_score}
                        max={100}
                        color={acc.health_score >= 70 ? "#22c55e" : acc.health_score >= 40 ? "#eab308" : "#ef4444"}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {/* ROI summary */}
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="eyebrow">ROI и оценка</div>
            <h2>Сводка эффективности</h2>
          </div>
        </div>
        <div className="status-grid">
          <div className="info-block">
            <strong>Действий/день</strong>
            <span>{actionsPerDay}</span>
          </div>
          {roi && typeof roi.estimated_cost_usd === "number" ? (
            <div className="info-block">
              <strong>Est. стоимость</strong>
              <span>${roi.estimated_cost_usd.toFixed(2)}</span>
            </div>
          ) : null}
          {roi && typeof roi.cost_per_action === "number" ? (
            <div className="info-block">
              <strong>Стоимость/действие</strong>
              <span>${roi.cost_per_action.toFixed(4)}</span>
            </div>
          ) : null}
        </div>
        {!roi ? (
          <p className="muted" style={{ marginTop: 12 }}>
            ROI-данные появятся после накопления статистики по кампаниям.
          </p>
        ) : null}
      </section>
    </div>
  );
}
