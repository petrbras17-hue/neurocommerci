import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowLeft,
  Play,
  Pause,
  Square,
  RotateCcw,
  RefreshCw,
  MessageSquare,
  Hash,
  BarChart2,
  Clock,
} from "lucide-react";
import {
  campaignsApi,
  campaignDetailApi,
  type Campaign,
  type CampaignRun,
  type CampaignChannelRow,
  type CampaignCommentRow,
} from "../api";
import { useAuth } from "../auth";

type CampaignStatus = Campaign["status"];

const STATUS_LABELS: Record<CampaignStatus, string> = {
  draft: "Черновик",
  active: "Активна",
  paused: "Пауза",
  completed: "Завершена",
  archived: "Архив",
};

function statusPillStyle(status: CampaignStatus): React.CSSProperties {
  const map: Record<CampaignStatus, { bg: string; color: string }> = {
    active: { bg: "rgba(0,255,136,0.15)", color: "var(--accent)" },
    paused: { bg: "rgba(255,170,0,0.15)", color: "var(--warning)" },
    completed: { bg: "rgba(68,136,255,0.15)", color: "var(--info)" },
    draft: { bg: "var(--surface-2)", color: "var(--muted)" },
    archived: { bg: "var(--surface-2)", color: "var(--muted)" },
  };
  const s = map[status] ?? map.draft;
  return {
    background: s.bg,
    color: s.color,
    padding: "4px 10px",
    borderRadius: 999,
    fontSize: 11,
    fontWeight: 600,
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
  };
}

export function CampaignDetailPage() {
  const { id } = useParams<{ id: string }>();
  const campaignId = id ? Number(id) : null;
  const navigate = useNavigate();
  const { accessToken } = useAuth();

  const [campaign, setCampaign] = useState<(Campaign & { runs: CampaignRun[] }) | null>(null);
  const [channels, setChannels] = useState<CampaignChannelRow[]>([]);
  const [comments, setComments] = useState<CampaignCommentRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [loading, setLoading] = useState(true);

  const loadAll = async () => {
    if (!accessToken || !campaignId) return;
    try {
      const [detail, chRes, cmRes] = await Promise.all([
        campaignsApi.get(accessToken, campaignId),
        campaignDetailApi.channels(accessToken, campaignId, 50),
        campaignDetailApi.comments(accessToken, campaignId, 20),
      ]);
      setCampaign(detail);
      setChannels(chRes.items);
      setComments(cmRes.items);
    } catch {
      // handled by empty state
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAll();
  }, [accessToken, campaignId]);

  const handleAction = async (action: "start" | "pause" | "resume" | "stop") => {
    if (!accessToken || !campaignId) return;
    setBusy(true);
    setStatusMessage("");
    try {
      if (action === "start") await campaignsApi.start(accessToken, campaignId);
      else if (action === "pause") await campaignsApi.pause(accessToken, campaignId);
      else if (action === "resume") await campaignsApi.resume(accessToken, campaignId);
      else if (action === "stop") await campaignsApi.stop(accessToken, campaignId);
      setStatusMessage(`Действие выполнено.`);
      await loadAll();
    } catch (e) {
      setStatusMessage(e instanceof Error ? e.message : "action_failed");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="dash">
        <div className="dash-panel" style={{ textAlign: "center", padding: 48 }}>
          <RefreshCw size={24} className="spin" style={{ color: "var(--accent)" }} />
          <p style={{ color: "var(--muted)", marginTop: 12 }}>Загружаем кампанию...</p>
        </div>
      </div>
    );
  }

  if (!campaign) {
    return (
      <div className="dash">
        <div className="dash-panel">
          <p className="dash-empty">Кампания не найдена.</p>
          <button
            className="ghost-button"
            type="button"
            onClick={() => navigate("/campaigns")}
            style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 12 }}
          >
            <ArrowLeft size={14} />
            Назад к кампаниям
          </button>
        </div>
      </div>
    );
  }

  const totalErrors = campaign.runs.reduce((s, r) => s + r.errors, 0);

  return (
    <div className="dash">
      {/* Header */}
      <motion.div
        className="dash-panel"
        initial={{ opacity: 0, y: -16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
        style={{ marginBottom: 0 }}
      >
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <button
              className="ghost-button"
              type="button"
              onClick={() => navigate("/campaigns")}
              style={{ display: "flex", alignItems: "center", gap: 6 }}
            >
              <ArrowLeft size={14} />
              Назад
            </button>
            <div>
              <p className="dash-panel-title">Кампания</p>
              <h2 style={{ fontSize: "1.3rem", marginTop: 4, display: "flex", alignItems: "center", gap: 10 }}>
                {campaign.name}
                <span style={statusPillStyle(campaign.status)}>{STATUS_LABELS[campaign.status]}</span>
              </h2>
            </div>
          </div>
          <button
            className="ghost-button"
            type="button"
            disabled={busy}
            onClick={() => void loadAll()}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <RefreshCw size={14} />
            Обновить
          </button>
        </div>

        {statusMessage ? (
          <motion.div
            className="status-banner"
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            style={{ marginTop: 16 }}
          >
            {statusMessage}
          </motion.div>
        ) : null}

        {/* Controls */}
        <div className="actions-row" style={{ marginTop: 16 }}>
          <button
            className="primary-button"
            type="button"
            disabled={busy || campaign.status === "active"}
            onClick={() => void handleAction("start")}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <Play size={13} />
            Запустить
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={busy || campaign.status !== "active"}
            onClick={() => void handleAction("pause")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              borderColor: campaign.status === "active" ? "var(--warning)" : undefined,
              color: campaign.status === "active" ? "var(--warning)" : undefined,
            }}
          >
            <Pause size={13} />
            Пауза
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={busy || campaign.status !== "paused"}
            onClick={() => void handleAction("resume")}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <RotateCcw size={13} />
            Продолжить
          </button>
          <button
            className="ghost-button"
            type="button"
            disabled={busy || (campaign.status !== "active" && campaign.status !== "paused")}
            onClick={() => void handleAction("stop")}
            style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--danger)" }}
          >
            <Square size={13} />
            Стоп
          </button>
        </div>
      </motion.div>

      {/* Stats */}
      <motion.div
        className="dash-panel"
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, delay: 0.05 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <BarChart2 size={16} style={{ color: "var(--accent)" }} />
          <p className="dash-panel-title" style={{ margin: 0 }}>Статистика</p>
        </div>
        <div className="dash-stats" style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))" }}>
          <div className="dash-stat">
            <span className="dash-stat-label">Комментариев</span>
            <span className="dash-stat-value">{campaign.total_comments_sent}</span>
          </div>
          <div className="dash-stat">
            <span className="dash-stat-label">Реакций</span>
            <span className="dash-stat-value">{campaign.total_reactions_sent}</span>
          </div>
          <div className="dash-stat">
            <span className="dash-stat-label">Всего действий</span>
            <span className="dash-stat-value">{campaign.total_actions_performed}</span>
          </div>
          <div className="dash-stat">
            <span className="dash-stat-label">Ошибок</span>
            <span
              className="dash-stat-value"
              style={{ color: totalErrors > 0 ? "var(--danger)" : "var(--text)" }}
            >
              {totalErrors}
            </span>
          </div>
        </div>
      </motion.div>

      {/* Channels */}
      <motion.div
        className="dash-panel"
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, delay: 0.1 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <Hash size={16} style={{ color: "var(--accent)" }} />
          <p className="dash-panel-title" style={{ margin: 0 }}>Каналы ({channels.length})</p>
        </div>
        {channels.length > 0 ? (
          <div className="terminal-window">
            <table className="data-table" style={{ marginBottom: 0 }}>
              <thead>
                <tr>
                  <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Канал</th>
                  <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Статус</th>
                  <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Комменты</th>
                  <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Последний</th>
                </tr>
              </thead>
              <tbody>
                <AnimatePresence>
                  {channels.map((ch) => (
                    <motion.tr
                      key={ch.id}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                    >
                      <td className="mono" style={{ fontSize: 12 }}>
                        {ch.channel_username ? `@${ch.channel_username}` : `#${ch.channel_id ?? ch.id}`}
                      </td>
                      <td>
                        <span
                          style={{
                            padding: "3px 8px",
                            borderRadius: 999,
                            fontSize: 11,
                            fontWeight: 600,
                            background:
                              ch.status === "active"
                                ? "rgba(0,255,136,0.15)"
                                : "var(--surface-2)",
                            color:
                              ch.status === "active" ? "var(--accent)" : "var(--muted)",
                          }}
                        >
                          {ch.status}
                        </span>
                      </td>
                      <td className="mono" style={{ fontSize: 12 }}>
                        {ch.comments_count}
                      </td>
                      <td className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
                        {ch.last_comment_at?.slice(0, 16) ?? "---"}
                      </td>
                    </motion.tr>
                  ))}
                </AnimatePresence>
              </tbody>
            </table>
          </div>
        ) : (
          <p className="dash-empty">Каналы не назначены. Создайте кампанию через онбординг для авто-подбора.</p>
        )}
      </motion.div>

      {/* Recent comments */}
      <motion.div
        className="dash-panel"
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, delay: 0.15 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <MessageSquare size={16} style={{ color: "var(--accent)" }} />
          <p className="dash-panel-title" style={{ margin: 0 }}>Последние комментарии</p>
        </div>
        {comments.length > 0 ? (
          <div className="terminal-window">
            <table className="data-table" style={{ marginBottom: 0 }}>
              <thead>
                <tr>
                  <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>
                    <Clock size={12} style={{ display: "inline", marginRight: 4 }} />
                    Время
                  </th>
                  <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Канал</th>
                  <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Данные</th>
                </tr>
              </thead>
              <tbody>
                {comments.map((c) => (
                  <tr key={c.id}>
                    <td className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
                      {c.created_at?.slice(0, 16) ?? "---"}
                    </td>
                    <td className="mono" style={{ fontSize: 12 }}>
                      {c.channel_username ? `@${c.channel_username}` : "---"}
                    </td>
                    <td style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                      {JSON.stringify(c.event_data).slice(0, 80)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="dash-empty">Комментариев ещё не было.</p>
        )}
      </motion.div>

      {/* Run history */}
      <motion.div
        className="dash-panel"
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, delay: 0.2 }}
      >
        <p className="dash-panel-title">История запусков ({campaign.runs.length})</p>
        {campaign.runs.length > 0 ? (
          <div className="terminal-window" style={{ maxHeight: 300 }}>
            <table className="data-table" style={{ marginBottom: 0 }}>
              <thead>
                <tr>
                  <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>#</th>
                  <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Статус</th>
                  <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Действия</th>
                  <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Комменты</th>
                  <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Ошибки</th>
                  <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Начало</th>
                </tr>
              </thead>
              <tbody>
                {campaign.runs.map((r) => (
                  <tr key={r.id}>
                    <td className="mono" style={{ fontSize: 12 }}>{r.id}</td>
                    <td>
                      <span
                        style={{
                          padding: "3px 8px",
                          borderRadius: 999,
                          fontSize: 11,
                          fontWeight: 600,
                          background:
                            r.status === "completed"
                              ? "rgba(0,255,136,0.15)"
                              : r.status === "failed"
                                ? "rgba(255,68,68,0.15)"
                                : "var(--surface-2)",
                          color:
                            r.status === "completed"
                              ? "var(--accent)"
                              : r.status === "failed"
                                ? "var(--danger)"
                                : "var(--muted)",
                        }}
                      >
                        {r.status}
                      </span>
                    </td>
                    <td className="mono" style={{ fontSize: 12 }}>{r.actions_performed}</td>
                    <td className="mono" style={{ fontSize: 12 }}>{r.comments_sent}</td>
                    <td
                      className="mono"
                      style={{ fontSize: 12, color: r.errors > 0 ? "var(--danger)" : undefined }}
                    >
                      {r.errors}
                    </td>
                    <td className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
                      {r.started_at?.slice(0, 16) ?? "---"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="dash-empty">Запусков ещё не было.</p>
        )}
      </motion.div>
    </div>
  );
}
