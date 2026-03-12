import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  BarChart3, TrendingUp, Activity, Users, Target, RefreshCw,
  MessageSquare, Zap, Clock, FileText, Plus, Send,
} from "lucide-react";
import {
  analyticsApi, weeklyReportApi,
  DashboardData, DailyStatsRow, ChannelComparisonRow, HeatmapCell,
  TopCommentRow, WeeklyReportItem,
} from "../api";
import { useAuth } from "../auth";

const DAYS_OPTIONS = [7, 14, 30] as const;
type DaysOption = (typeof DAYS_OPTIONS)[number];

type Tab = "overview" | "channels" | "heatmap" | "top-comments" | "weekly";

const cardVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.06, duration: 0.35, ease: [0.16, 1, 0.3, 1] as const },
  }),
};

function StatCard({
  label, value, sub, icon, index,
}: {
  label: string; value: number | string; sub?: string; icon: React.ReactNode; index: number;
}) {
  return (
    <motion.div className="dash-stat" custom={index} initial="hidden" animate="visible" variants={cardVariants}>
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
      <div style={{
        flex: 1, height: 6, background: "var(--surface-2)", borderRadius: 3,
        overflow: "hidden", border: "1px solid var(--border)",
      }}>
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] as const }}
          style={{ height: "100%", background: color, borderRadius: 3 }}
        />
      </div>
      <span className="mono" style={{ fontSize: 11, color: "var(--text-secondary)", minWidth: 36 }}>{pct}%</span>
    </div>
  );
}

const BAR_COLORS = {
  comments: "var(--accent)",
  reactions: "var(--info)",
  errors: "var(--danger)",
};

const WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

// ---------------------------------------------------------------------------
// Heatmap cell component
// ---------------------------------------------------------------------------

function HeatmapGrid({ cells }: { cells: HeatmapCell[] }) {
  if (!cells.length) {
    return <p style={{ color: "var(--muted)", fontSize: 13 }}>Нет данных для тепловой карты.</p>;
  }

  const maxCount = Math.max(1, ...cells.map((c) => c.count));

  const cellsByKey: Record<string, number> = {};
  for (const c of cells) {
    cellsByKey[`${c.weekday}-${c.hour}`] = c.count;
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <div style={{ minWidth: 600 }}>
        <div style={{ display: "flex", gap: 2, marginBottom: 4 }}>
          <div style={{ width: 28, flexShrink: 0 }} />
          {Array.from({ length: 24 }, (_, h) => (
            <div
              key={h}
              className="mono"
              style={{ flex: 1, textAlign: "center", fontSize: 9, color: "var(--muted)", minWidth: 18 }}
            >
              {h}
            </div>
          ))}
        </div>
        {WEEKDAY_LABELS.map((label, wd) => (
          <div key={wd} style={{ display: "flex", gap: 2, marginBottom: 2, alignItems: "center" }}>
            <div
              className="mono"
              style={{ width: 28, fontSize: 10, color: "var(--muted)", flexShrink: 0, textAlign: "right", paddingRight: 4 }}
            >
              {label}
            </div>
            {Array.from({ length: 24 }, (_, h) => {
              const count = cellsByKey[`${wd}-${h}`] || 0;
              const opacity = count === 0 ? 0.05 : 0.15 + (count / maxCount) * 0.85;
              return (
                <div
                  key={h}
                  title={`${label} ${h}:00 — ${count} коммент.`}
                  style={{
                    flex: 1, minWidth: 18, height: 18, borderRadius: 2,
                    background: `rgba(0, 255, 136, ${opacity})`,
                    cursor: "default",
                  }}
                />
              );
            })}
          </div>
        ))}
        <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--muted)" }}>
          <span>Меньше</span>
          {[0.05, 0.25, 0.5, 0.75, 1.0].map((op, i) => (
            <div key={i} style={{ width: 12, height: 12, borderRadius: 2, background: `rgba(0, 255, 136, ${op})` }} />
          ))}
          <span>Больше</span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function AnalyticsPage() {
  const { accessToken } = useAuth();

  const [days, setDays] = useState<DaysOption>(7);
  const [activeTab, setActiveTab] = useState<Tab>("overview");

  // Overview data
  const [dashData, setDashData] = useState<DashboardData | null>(null);
  const [roi, setRoi] = useState<Record<string, unknown> | null>(null);

  // Sprint 10 data
  const [dailyRows, setDailyRows] = useState<DailyStatsRow[]>([]);
  const [channelRows, setChannelRows] = useState<ChannelComparisonRow[]>([]);
  const [heatmapCells, setHeatmapCells] = useState<HeatmapCell[]>([]);
  const [topComments, setTopComments] = useState<TopCommentRow[]>([]);
  const [weeklyReports, setWeeklyReports] = useState<WeeklyReportItem[]>([]);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [generating, setGenerating] = useState(false);

  const loadOverview = async (d: DaysOption) => {
    if (!accessToken) return;
    const [dashPayload, roiPayload, dailyPayload] = await Promise.all([
      analyticsApi.dashboard(accessToken, d),
      analyticsApi.roi(accessToken).catch(() => null),
      analyticsApi.daily(accessToken, d).catch(() => null),
    ]);
    setDashData(dashPayload);
    setRoi(roiPayload);
    if (dailyPayload) setDailyRows(dailyPayload.rows);
  };

  const loadChannels = async (d: DaysOption) => {
    if (!accessToken) return;
    const payload = await analyticsApi.channels(accessToken, d);
    setChannelRows(payload.channels);
  };

  const loadHeatmap = async (d: DaysOption) => {
    if (!accessToken) return;
    const payload = await analyticsApi.heatmap(accessToken, d);
    setHeatmapCells(payload.heatmap);
  };

  const loadTopComments = async (d: DaysOption) => {
    if (!accessToken) return;
    const payload = await analyticsApi.topComments(accessToken, d);
    setTopComments(payload.comments);
  };

  const loadWeeklyReports = async () => {
    if (!accessToken) return;
    const payload = await weeklyReportApi.list(accessToken);
    setWeeklyReports(payload.items);
  };

  const loadTab = async (tab: Tab, d: DaysOption) => {
    if (!accessToken) return;
    setBusy(true);
    setError("");
    try {
      if (tab === "overview") await loadOverview(d);
      else if (tab === "channels") await loadChannels(d);
      else if (tab === "heatmap") await loadHeatmap(d);
      else if (tab === "top-comments") await loadTopComments(d);
      else if (tab === "weekly") await loadWeeklyReports();
    } catch (e) {
      setError(e instanceof Error ? e.message : "load_failed");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void loadTab(activeTab, days);
  }, [accessToken, activeTab, days]);

  const handleGenerateReport = async () => {
    if (!accessToken || generating) return;
    setGenerating(true);
    try {
      await weeklyReportApi.generate(accessToken, { send_telegram: true });
      await loadWeeklyReports();
    } catch (e) {
      setError(e instanceof Error ? e.message : "generate_failed");
    } finally {
      setGenerating(false);
    }
  };

  const maxDayActions = Math.max(1, ...dailyRows.map((d) => d.comments + d.reactions + d.errors));
  const maxChannelActions = Math.max(1, ...channelRows.map((c) => c.total_actions));
  const actionsPerDay = dashData && days > 0
    ? Math.round((dashData.total_comments + dashData.total_reactions) / days)
    : 0;

  const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "overview", label: "Обзор", icon: <BarChart3 size={14} /> },
    { id: "channels", label: "Каналы", icon: <Target size={14} /> },
    { id: "heatmap", label: "Тепловая карта", icon: <Clock size={14} /> },
    { id: "top-comments", label: "Топ комментов", icon: <MessageSquare size={14} /> },
    { id: "weekly", label: "Недельные отчёты", icon: <FileText size={14} /> },
  ];

  return (
    <div className="dash">
      {/* Header */}
      <div className="dash-columns">
        <motion.div
          className="dash-panel"
          initial={{ opacity: 0, x: -16 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.35 }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div className="dash-action-icon"><BarChart3 size={18} /></div>
            <div>
              <p className="dash-panel-title">Analytics Dashboard</p>
              <h2 style={{ fontSize: "1.3rem", marginTop: 4 }}>Аналитика</h2>
            </div>
          </div>
          <ul className="bullet-list" style={{ fontSize: 13 }}>
            <li>Сводные метрики, тепловые карты, топ каналов и каментов.</li>
            <li>AI-генерация еженедельных отчётов с доставкой в Telegram.</li>
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
                className="pill"
                style={{
                  cursor: "pointer",
                  background: days === d ? "var(--accent)" : "var(--surface-2)",
                  color: days === d ? "#000" : "var(--text-secondary)",
                  border: `1px solid ${days === d ? "var(--accent)" : "var(--border)"}`,
                  fontWeight: 600, padding: "6px 16px", fontSize: 13,
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
              onClick={() => void loadTab(activeTab, days)}
              style={{ display: "flex", alignItems: "center", gap: 6 }}
            >
              <RefreshCw size={14} className={busy ? "spin" : ""} />
              Обновить
            </button>
          </div>
        </motion.div>
      </div>

      {error ? <div className="status-banner">{error}</div> : null}

      {/* Tabs */}
      <div className="badge-row" style={{ gap: 4, flexWrap: "wrap" }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className="pill"
            style={{
              display: "flex", alignItems: "center", gap: 6, cursor: "pointer",
              background: activeTab === t.id ? "var(--accent)" : "var(--surface-2)",
              color: activeTab === t.id ? "#000" : "var(--text-secondary)",
              border: `1px solid ${activeTab === t.id ? "var(--accent)" : "var(--border)"}`,
              fontWeight: activeTab === t.id ? 600 : 400,
              padding: "5px 14px", fontSize: 13,
            }}
            onClick={() => setActiveTab(t.id)}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* ---- TAB: Overview ---- */}
      {activeTab === "overview" && (
        <>
          {dashData ? (
            <div className="dash-stats">
              <StatCard label="Комментариев" value={dashData.total_comments} sub="всего отправлено" icon={<BarChart3 size={18} />} index={0} />
              <StatCard label="Реакций" value={dashData.total_reactions} sub="всего поставлено" icon={<TrendingUp size={18} />} index={1} />
              <StatCard label="Flood Wait" value={dashData.total_flood_waits} sub="ограничений Telegram" icon={<Activity size={18} />} index={2} />
              <StatCard label="Спам-блоков" value={dashData.total_spam_blocks} sub="блокировок аккаунтов" icon={<Target size={18} />} index={3} />
            </div>
          ) : busy ? (
            <div className="dash-stats">
              {[0, 1, 2, 3].map((i) => <div key={i} className="dash-skeleton dash-skeleton--stat" />)}
            </div>
          ) : null}

          {/* Daily chart from Sprint 10 endpoint */}
          {dailyRows.length > 0 ? (
            <motion.div className="dash-panel" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4, delay: 0.2 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div>
                  <p className="dash-panel-title">Активность по дням</p>
                  <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>Дневная статистика</h2>
                </div>
                <div className="badge-row" style={{ gap: 16 }}>
                  {(["comments", "reactions", "errors"] as const).map((key) => (
                    <span key={key} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)" }}>
                      <span style={{ width: 10, height: 10, background: BAR_COLORS[key], borderRadius: 2, display: "inline-block" }} />
                      {key === "comments" ? "Комментарии" : key === "reactions" ? "Реакции" : "Ошибки"}
                    </span>
                  ))}
                </div>
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 16 }}>
                {dailyRows.map((day, i) => {
                  const commPct = (day.comments / maxDayActions) * 100;
                  const reactPct = (day.reactions / maxDayActions) * 100;
                  const errPct = (day.errors / maxDayActions) * 100;
                  return (
                    <motion.div
                      key={day.date}
                      initial={{ opacity: 0, x: -12 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: 0.25 + i * 0.03, duration: 0.3 }}
                      style={{ display: "flex", alignItems: "center", gap: 12 }}
                    >
                      <span className="mono" style={{ minWidth: 56, fontSize: 12, color: "var(--muted)" }}>{day.date.slice(5)}</span>
                      <div style={{ flex: 1, display: "flex", height: 14, borderRadius: 7, overflow: "hidden", background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                        <div style={{ width: `${commPct}%`, background: BAR_COLORS.comments }} title={`Комменты: ${day.comments}`} />
                        <div style={{ width: `${reactPct}%`, background: BAR_COLORS.reactions }} title={`Реакции: ${day.reactions}`} />
                        <div style={{ width: `${errPct}%`, background: BAR_COLORS.errors }} title={`Ошибки: ${day.errors}`} />
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

          {/* ROI summary */}
          <motion.div className="dash-panel" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4, delay: 0.3 }}>
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
                <span className="dash-stat-value" style={{ fontSize: "1.6rem" }}>{actionsPerDay}</span>
              </div>
              {roi && typeof roi.estimated_cost_usd === "number" ? (
                <div className="dash-stat">
                  <span className="dash-stat-label">Est. стоимость</span>
                  <span className="dash-stat-value" style={{ fontSize: "1.6rem" }}>${(roi.estimated_cost_usd as number).toFixed(2)}</span>
                </div>
              ) : null}
              {roi && typeof roi.cost_per_action === "number" ? (
                <div className="dash-stat">
                  <span className="dash-stat-label">Стоимость/действие</span>
                  <span className="dash-stat-value" style={{ fontSize: "1.6rem" }}>${(roi.cost_per_action as number).toFixed(4)}</span>
                </div>
              ) : null}
            </div>
            {!roi ? (
              <p style={{ marginTop: 12, color: "var(--muted)", fontSize: 13 }}>
                ROI-данные появятся после накопления статистики по кампаниям.
              </p>
            ) : null}
          </motion.div>
        </>
      )}

      {/* ---- TAB: Channel comparison ---- */}
      {activeTab === "channels" && (
        <motion.div className="dash-panel" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Target size={16} style={{ color: "var(--accent)" }} />
            <div>
              <p className="dash-panel-title">Сравнение каналов</p>
              <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>Рейтинг площадок</h2>
            </div>
          </div>
          {channelRows.length === 0 ? (
            <p style={{ marginTop: 16, color: "var(--muted)", fontSize: 13 }}>
              Нет данных. Запустите ферму — статистика появится после первых комментариев.
            </p>
          ) : (
            <div className="table-wrap" style={{ marginTop: 12 }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Канал</th>
                    <th>Комменты</th>
                    <th>Реакции</th>
                    <th>CTR</th>
                    <th>Всего</th>
                  </tr>
                </thead>
                <tbody>
                  {channelRows.map((ch, i) => (
                    <motion.tr key={ch.channel} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 + i * 0.04 }}>
                      <td><span className="mono" style={{ color: "var(--accent)", fontSize: 12 }}>{ch.rank}</span></td>
                      <td>
                        <a href={`https://t.me/${ch.channel.replace("@", "")}`} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
                          {ch.channel}
                        </a>
                      </td>
                      <td><span className="mono" style={{ fontSize: 13 }}>{ch.comments}</span></td>
                      <td><span className="mono" style={{ fontSize: 13 }}>{ch.reactions}</span></td>
                      <td>
                        <ProgressBar
                          value={Math.round(ch.ctr * 100)}
                          max={100}
                          color={ch.ctr >= 0.5 ? "var(--accent)" : ch.ctr >= 0.2 ? "var(--warning)" : "var(--danger)"}
                        />
                      </td>
                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <div style={{ width: 60, height: 6, background: "var(--surface-2)", borderRadius: 3, overflow: "hidden", border: "1px solid var(--border)" }}>
                            <div style={{ width: `${Math.round((ch.total_actions / maxChannelActions) * 100)}%`, height: "100%", background: "var(--info)", borderRadius: 3 }} />
                          </div>
                          <span className="mono" style={{ fontSize: 12 }}>{ch.total_actions}</span>
                        </div>
                      </td>
                    </motion.tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </motion.div>
      )}

      {/* ---- TAB: Heatmap ---- */}
      {activeTab === "heatmap" && (
        <motion.div className="dash-panel" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Clock size={16} style={{ color: "var(--accent)" }} />
            <div>
              <p className="dash-panel-title">Лучшее время для комментирования</p>
              <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>Тепловая карта активности</h2>
            </div>
          </div>
          <p style={{ fontSize: 13, color: "var(--muted)", marginBottom: 16 }}>
            Данные за последние {days} дней. Яркость = количество комментариев.
          </p>
          <HeatmapGrid cells={heatmapCells} />
        </motion.div>
      )}

      {/* ---- TAB: Top comments ---- */}
      {activeTab === "top-comments" && (
        <motion.div className="dash-panel" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <MessageSquare size={16} style={{ color: "var(--accent)" }} />
            <div>
              <p className="dash-panel-title">Лучшие комментарии</p>
              <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>Топ по реакциям</h2>
            </div>
          </div>
          {topComments.length === 0 ? (
            <p style={{ marginTop: 16, color: "var(--muted)", fontSize: 13 }}>Данные появятся после первых комментариев с реакциями.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 16 }}>
              {topComments.map((c, i) => (
                <motion.div
                  key={c.id}
                  initial={{ opacity: 0, x: -12 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.1 + i * 0.05, duration: 0.3 }}
                  style={{
                    background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 8,
                    padding: "10px 14px",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                    <a href={`https://t.me/${c.channel.replace("@", "")}`} target="_blank" rel="noopener noreferrer"
                      style={{ color: "var(--accent)", fontSize: 13, fontWeight: 600 }}>
                      {c.channel || "—"}
                    </a>
                    <div style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 13, color: "var(--accent)" }}>
                      <Zap size={12} />
                      <span className="mono">{c.reactions}</span>
                    </div>
                  </div>
                  <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: 0, lineHeight: 1.5 }}>
                    {c.text || <em style={{ color: "var(--muted)" }}>текст недоступен</em>}
                  </p>
                  <div style={{ marginTop: 6, fontSize: 11, color: "var(--muted)" }}>
                    {c.created_at ? new Date(c.created_at).toLocaleString("ru-RU") : "—"}
                  </div>
                </motion.div>
              ))}
            </div>
          )}
        </motion.div>
      )}

      {/* ---- TAB: Weekly reports ---- */}
      {activeTab === "weekly" && (
        <motion.div className="dash-panel" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <FileText size={16} style={{ color: "var(--accent)" }} />
              <div>
                <p className="dash-panel-title">Еженедельные отчёты</p>
                <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>AI-аналитика за неделю</h2>
              </div>
            </div>
            <button
              className="primary-button"
              type="button"
              disabled={generating}
              onClick={() => void handleGenerateReport()}
              style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 14px", fontSize: 13 }}
            >
              {generating ? <RefreshCw size={14} className="spin" /> : <Plus size={14} />}
              {generating ? "Генерация..." : "Создать отчёт"}
            </button>
          </div>

          {weeklyReports.length === 0 ? (
            <p style={{ marginTop: 16, color: "var(--muted)", fontSize: 13 }}>
              Отчётов ещё нет. Нажмите «Создать отчёт» — AI сформирует еженедельную аналитику
              и пришлёт её в Telegram (если настроен digest bot).
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 16 }}>
              {weeklyReports.map((r) => (
                <div
                  key={r.id}
                  style={{
                    background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 8,
                    padding: "12px 16px",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                    <div>
                      <span className="mono" style={{ fontSize: 13, fontWeight: 600, color: "var(--accent)" }}>
                        {r.week_start} — {r.week_end}
                      </span>
                      {r.sent_at ? (
                        <span style={{ marginLeft: 10, fontSize: 11, color: "var(--muted)" }}>
                          <Send size={10} style={{ verticalAlign: "middle", marginRight: 3 }} />
                          отправлен в Telegram
                        </span>
                      ) : null}
                    </div>
                    <span className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
                      {r.generated_at ? new Date(r.generated_at).toLocaleDateString("ru-RU") : "—"}
                    </span>
                  </div>
                  {r.report_text ? (
                    <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: 0, lineHeight: 1.65, whiteSpace: "pre-wrap" }}>
                      {r.report_text.length > 600 ? r.report_text.slice(0, 600) + "…" : r.report_text}
                    </p>
                  ) : (
                    <p style={{ fontSize: 13, color: "var(--muted)", margin: 0 }}>Отчёт не содержит текста.</p>
                  )}
                  {r.metrics_snapshot ? (
                    <div className="badge-row" style={{ marginTop: 10, gap: 12, flexWrap: "wrap" }}>
                      {typeof (r.metrics_snapshot as Record<string, unknown>).total_comments === "number" && (
                        <span style={{ fontSize: 12, color: "var(--muted)" }}>
                          Комментов: <span className="mono" style={{ color: "var(--accent)" }}>
                            {(r.metrics_snapshot as Record<string, unknown>).total_comments as number}
                          </span>
                        </span>
                      )}
                      {typeof (r.metrics_snapshot as Record<string, unknown>).total_reactions === "number" && (
                        <span style={{ fontSize: 12, color: "var(--muted)" }}>
                          Реакций: <span className="mono" style={{ color: "var(--info)" }}>
                            {(r.metrics_snapshot as Record<string, unknown>).total_reactions as number}
                          </span>
                        </span>
                      )}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </motion.div>
      )}
    </div>
  );
}
