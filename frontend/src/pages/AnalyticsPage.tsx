import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { BarChart3, TrendingUp, Activity, Users, Target, RefreshCw } from "lucide-react";
import { analyticsApi, DashboardData } from "../api";
import { useAuth } from "../auth";

const DAYS_OPTIONS = [7, 14, 30] as const;
type DaysOption = (typeof DAYS_OPTIONS)[number];

const cardVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.06, duration: 0.35, ease: [0.16, 1, 0.3, 1] as const },
  }),
};

function StatCard({
  label,
  value,
  sub,
  icon,
  index,
}: {
  label: string;
  value: number | string;
  sub?: string;
  icon: React.ReactNode;
  index: number;
}) {
  return (
    <motion.div
      className="dash-stat"
      custom={index}
      initial="hidden"
      animate="visible"
      variants={cardVariants}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span className="dash-stat-label">{label}</span>
        <span style={{ color: "var(--accent)", opacity: 0.6 }}>{icon}</span>
      </div>
      <span className="dash-stat-value">{value}</span>
      {sub ? <span className="dash-stat-sub">{sub}</span> : null}
    </motion.div>
  );
}

function ProgressBar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div
        style={{
          flex: 1,
          height: 6,
          background: "var(--surface-2)",
          borderRadius: 3,
          overflow: "hidden",
          border: "1px solid var(--border)",
        }}
      >
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] as const }}
          style={{ height: "100%", background: color, borderRadius: 3 }}
        />
      </div>
      <span className="mono" style={{ fontSize: 11, color: "var(--text-secondary)", minWidth: 36 }}>
        {pct}%
      </span>
    </div>
  );
}

const BAR_COLORS = {
  comments: "var(--accent)",
  reactions: "var(--info)",
  errors: "var(--danger)",
};

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

  const maxChannelActions = data ? Math.max(1, ...data.top_channels.map((c) => c.actions)) : 1;

  const maxAccountActions = data ? Math.max(1, ...data.account_activity.map((a) => a.actions)) : 1;

  const actionsPerDay =
    data && days > 0 ? Math.round((data.total_comments + data.total_reactions) / days) : 0;

  return (
    <div className="dash">
      {/* Header row */}
      <div className="dash-columns">
        <motion.div
          className="dash-panel"
          initial={{ opacity: 0, x: -16 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.35 }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div className="dash-action-icon">
              <BarChart3 size={18} />
            </div>
            <div>
              <p className="dash-panel-title">Analytics Dashboard</p>
              <h2 style={{ fontSize: "1.3rem", marginTop: 4 }}>Аналитика</h2>
            </div>
          </div>
          <ul className="bullet-list" style={{ fontSize: 13 }}>
            <li>Сводные метрики по всем активным кампаниям и аккаунтам.</li>
            <li>Дневная активность, топ каналов и здоровье аккаунтов.</li>
            <li>Данные обновляются при переключении периода.</li>
          </ul>
        </motion.div>

        <motion.div
          className="dash-panel"
          initial={{ opacity: 0, x: 16 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.35, delay: 0.05 }}
        >
          <p className="dash-panel-title">Период</p>
          <h2 style={{ fontSize: "1.3rem", marginTop: 4 }}>Выберите диапазон</h2>
          <div className="badge-row" style={{ marginTop: 8 }}>
            {DAYS_OPTIONS.map((d) => (
              <button
                key={d}
                type="button"
                className={days === d ? "pill" : "pill"}
                style={{
                  cursor: "pointer",
                  background:
                    days === d ? "var(--accent)" : "var(--surface-2)",
                  color: days === d ? "#000" : "var(--text-secondary)",
                  border: `1px solid ${days === d ? "var(--accent)" : "var(--border)"}`,
                  fontWeight: 600,
                  padding: "6px 16px",
                  fontSize: 13,
                }}
                onClick={() => setDays(d)}
                disabled={busy}
              >
                {d}д
              </button>
            ))}
            <button
              className="ghost-button"
              type="button"
              disabled={busy}
              onClick={() => void load(days)}
              style={{ display: "flex", alignItems: "center", gap: 6 }}
            >
              <RefreshCw size={14} className={busy ? "spin" : ""} />
              Обновить
            </button>
          </div>
        </motion.div>
      </div>

      {error ? (
        <div className="status-banner">{error}</div>
      ) : null}

      {/* Summary stat cards */}
      {data ? (
        <div className="dash-stats">
          <StatCard
            label="Комментариев"
            value={data.total_comments}
            sub="всего отправлено"
            icon={<BarChart3 size={18} />}
            index={0}
          />
          <StatCard
            label="Реакций"
            value={data.total_reactions}
            sub="всего поставлено"
            icon={<TrendingUp size={18} />}
            index={1}
          />
          <StatCard
            label="Flood Wait"
            value={data.total_flood_waits}
            sub="ограничений Telegram"
            icon={<Activity size={18} />}
            index={2}
          />
          <StatCard
            label="Спам-блоков"
            value={data.total_spam_blocks}
            sub="блокировок аккаунтов"
            icon={<Target size={18} />}
            index={3}
          />
        </div>
      ) : busy ? (
        <div className="dash-stats">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="dash-skeleton dash-skeleton--stat" />
          ))}
        </div>
      ) : null}

      {/* Daily activity chart */}
      {data && data.daily_breakdown.length > 0 ? (
        <motion.div
          className="dash-panel"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.2 }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div>
              <p className="dash-panel-title">Активность по дням</p>
              <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>Дневная статистика</h2>
            </div>
            <div className="badge-row" style={{ gap: 16 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)" }}>
                <span style={{ width: 10, height: 10, background: BAR_COLORS.comments, borderRadius: 2, display: "inline-block" }} />
                Комментарии
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)" }}>
                <span style={{ width: 10, height: 10, background: BAR_COLORS.reactions, borderRadius: 2, display: "inline-block" }} />
                Реакции
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)" }}>
                <span style={{ width: 10, height: 10, background: BAR_COLORS.errors, borderRadius: 2, display: "inline-block" }} />
                Ошибки
              </span>
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 16 }}>
            {data.daily_breakdown.map((day, i) => {
              const commPct = (day.comments / maxDayComments) * 100;
              const reactPct = (day.reactions / maxDayComments) * 100;
              const errPct = (day.errors / maxDayComments) * 100;
              return (
                <motion.div
                  key={day.date}
                  initial={{ opacity: 0, x: -12 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.25 + i * 0.03, duration: 0.3 }}
                  style={{ display: "flex", alignItems: "center", gap: 12 }}
                >
                  <span className="mono" style={{ minWidth: 56, fontSize: 12, color: "var(--muted)" }}>
                    {day.date.slice(5)}
                  </span>
                  <div
                    style={{
                      flex: 1,
                      display: "flex",
                      height: 14,
                      borderRadius: 7,
                      overflow: "hidden",
                      background: "var(--surface-2)",
                      border: "1px solid var(--border)",
                    }}
                  >
                    <div
                      style={{ width: `${commPct}%`, background: BAR_COLORS.comments }}
                      title={`Комменты: ${day.comments}`}
                    />
                    <div
                      style={{ width: `${reactPct}%`, background: BAR_COLORS.reactions }}
                      title={`Реакции: ${day.reactions}`}
                    />
                    <div
                      style={{ width: `${errPct}%`, background: BAR_COLORS.errors }}
                      title={`Ошибки: ${day.errors}`}
                    />
                  </div>
                  <span className="mono" style={{ fontSize: 11, color: "var(--text-secondary)", minWidth: 72, textAlign: "right" }}>
                    {day.comments + day.reactions} действ.
                  </span>
                </motion.div>
              );
            })}
          </div>
        </motion.div>
      ) : null}

      {/* Top channels */}
      {data && data.top_channels.length > 0 ? (
        <motion.div
          className="dash-panel"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.3 }}
        >
          <p className="dash-panel-title">Топ площадок</p>
          <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>Активность по каналам</h2>

          <div className="table-wrap" style={{ marginTop: 12 }}>
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
                  <motion.tr
                    key={i}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: 0.35 + i * 0.04 }}
                  >
                    <td>
                      <a
                        href={`https://t.me/${ch.channel.replace("@", "")}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ color: "var(--accent)" }}
                      >
                        {ch.channel}
                      </a>
                    </td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <div
                          style={{
                            width: 80,
                            height: 6,
                            background: "var(--surface-2)",
                            borderRadius: 3,
                            overflow: "hidden",
                            border: "1px solid var(--border)",
                          }}
                        >
                          <div
                            style={{
                              width: `${Math.round((ch.actions / maxChannelActions) * 100)}%`,
                              height: "100%",
                              background: "var(--info)",
                              borderRadius: 3,
                            }}
                          />
                        </div>
                        <span className="mono" style={{ fontSize: 12 }}>{ch.actions}</span>
                      </div>
                    </td>
                    <td>
                      <ProgressBar
                        value={Math.round(ch.success_rate * 100)}
                        max={100}
                        color={
                          ch.success_rate >= 0.8
                            ? "var(--accent)"
                            : ch.success_rate >= 0.5
                              ? "var(--warning)"
                              : "var(--danger)"
                        }
                      />
                    </td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </div>
        </motion.div>
      ) : null}

      {/* Account activity */}
      {data && data.account_activity.length > 0 ? (
        <motion.div
          className="dash-panel"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.35 }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Users size={16} style={{ color: "var(--accent)" }} />
            <div>
              <p className="dash-panel-title">Аккаунты</p>
              <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>Активность аккаунтов</h2>
            </div>
          </div>

          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Аккаунт</th>
                  <th>Действий</th>
                  <th>Здоровье</th>
                </tr>
              </thead>
              <tbody>
                {data.account_activity.map((acc) => {
                  const healthColor =
                    acc.health_score >= 70
                      ? "var(--accent)"
                      : acc.health_score >= 40
                        ? "var(--warning)"
                        : "var(--danger)";
                  return (
                    <tr key={acc.account_id}>
                      <td>
                        <span className="mono" style={{ fontSize: 13 }}>
                          {acc.phone ?? `#${acc.account_id}`}
                        </span>
                      </td>
                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                          <div
                            style={{
                              width: 80,
                              height: 6,
                              background: "var(--surface-2)",
                              borderRadius: 3,
                              overflow: "hidden",
                              border: "1px solid var(--border)",
                            }}
                          >
                            <div
                              style={{
                                width: `${Math.round((acc.actions / maxAccountActions) * 100)}%`,
                                height: "100%",
                                background: "var(--accent)",
                                borderRadius: 3,
                              }}
                            />
                          </div>
                          <span className="mono" style={{ fontSize: 12 }}>{acc.actions}</span>
                        </div>
                      </td>
                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <div
                            style={{
                              width: 8,
                              height: 8,
                              borderRadius: "50%",
                              background: healthColor,
                              boxShadow: `0 0 8px ${healthColor}`,
                              flexShrink: 0,
                            }}
                          />
                          <ProgressBar value={acc.health_score} max={100} color={healthColor} />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </motion.div>
      ) : null}

      {/* ROI summary */}
      <motion.div
        className="dash-panel"
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.4 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <TrendingUp size={16} style={{ color: "var(--accent)" }} />
          <div>
            <p className="dash-panel-title">ROI и оценка</p>
            <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>Сводка эффективности</h2>
          </div>
        </div>

        <div className="dash-stats" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))", marginTop: 12 }}>
          <div className="dash-stat">
            <span className="dash-stat-label">Действий/день</span>
            <span className="dash-stat-value" style={{ fontSize: "1.6rem" }}>
              {actionsPerDay}
            </span>
          </div>
          {roi && typeof roi.estimated_cost_usd === "number" ? (
            <div className="dash-stat">
              <span className="dash-stat-label">Est. стоимость</span>
              <span className="dash-stat-value" style={{ fontSize: "1.6rem" }}>
                ${(roi.estimated_cost_usd as number).toFixed(2)}
              </span>
            </div>
          ) : null}
          {roi && typeof roi.cost_per_action === "number" ? (
            <div className="dash-stat">
              <span className="dash-stat-label">Стоимость/действие</span>
              <span className="dash-stat-value" style={{ fontSize: "1.6rem" }}>
                ${(roi.cost_per_action as number).toFixed(4)}
              </span>
            </div>
          ) : null}
        </div>
        {!roi ? (
          <p style={{ marginTop: 12, color: "var(--muted)", fontSize: 13 }}>
            ROI-данные появятся после накопления статистики по кампаниям.
          </p>
        ) : null}
      </motion.div>
    </div>
  );
}
