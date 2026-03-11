import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Megaphone,
  Play,
  Pause,
  Square,
  Plus,
  Trash2,
  BarChart2,
  RefreshCw,
  RotateCcw,
  X,
} from "lucide-react";
import {
  campaignsApi,
  channelDbApi,
  Campaign,
  CampaignRun,
  CampaignStatus,
  ChannelDatabase,
  apiFetch,
} from "../api";
import { useAuth } from "../auth";

type AccountRow = { id: number; phone: string; status: string; health_status: string };
type AccountsResponse = { items: AccountRow[]; total: number };

const STATUS_LABELS: Record<CampaignStatus, string> = {
  draft: "Черновик",
  active: "Активна",
  paused: "Пауза",
  completed: "Завершена",
  archived: "Архив",
};

const TYPE_LABELS: Record<string, string> = {
  commenting: "Комментирование",
  reactions: "Реакции",
  chatting: "Чаттинг",
  mixed: "Смешанный",
};

const SCHEDULE_LABELS: Record<string, string> = {
  continuous: "Непрерывный",
  scheduled: "По расписанию",
  burst: "Пакетный",
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

function statusDot(status: CampaignStatus): string {
  if (status === "active") return "var(--accent)";
  if (status === "paused") return "var(--warning)";
  if (status === "completed") return "var(--info)";
  return "var(--muted)";
}

function BudgetBar({ used, total }: { used: number; total: number | null }) {
  if (!total) return <span style={{ color: "var(--muted)", fontSize: 12 }}>без лимита</span>;
  const pct = Math.min(100, Math.round((used / total) * 100));
  const color =
    pct >= 90 ? "var(--danger)" : pct >= 70 ? "var(--warning)" : "var(--accent)";
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
          transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] as const }}
          style={{ height: "100%", background: color, borderRadius: 3 }}
        />
      </div>
      <span className="mono" style={{ fontSize: 11, color: "var(--text-secondary)", minWidth: 42 }}>
        {used}/{total}
      </span>
    </div>
  );
}

const cardVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.05, duration: 0.3, ease: [0.16, 1, 0.3, 1] as const },
  }),
  exit: { opacity: 0, y: -8, transition: { duration: 0.2 } },
};

export function CampaignsPage() {
  const { accessToken } = useAuth();

  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedCampaign, setSelectedCampaign] = useState<
    (Campaign & { runs: CampaignRun[] }) | null
  >(null);
  const [channelDbs, setChannelDbs] = useState<ChannelDatabase[]>([]);
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [analyticsData, setAnalyticsData] = useState<Record<string, unknown> | null>(null);

  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  // Create modal
  const [showCreate, setShowCreate] = useState(false);
  const [formName, setFormName] = useState("");
  const [formType, setFormType] = useState("commenting");
  const [formAccountIds, setFormAccountIds] = useState<number[]>([]);
  const [formChannelDbId, setFormChannelDbId] = useState<number | null>(null);
  const [formPrompt, setFormPrompt] = useState("");
  const [formTone, setFormTone] = useState("neutral");
  const [formLanguage, setFormLanguage] = useState("ru");
  const [formSchedule, setFormSchedule] = useState("continuous");
  const [formDailyBudget, setFormDailyBudget] = useState(50);
  const [formTotalBudget, setFormTotalBudget] = useState("");

  const loadCampaigns = async () => {
    if (!accessToken) return;
    const payload = await campaignsApi.list(accessToken);
    setCampaigns(payload.items);
  };

  const loadDetail = async (id: number) => {
    if (!accessToken) return;
    const [detail, analytics] = await Promise.all([
      campaignsApi.get(accessToken, id),
      campaignsApi.analytics(accessToken, id).catch(() => null),
    ]);
    setSelectedCampaign(detail);
    setAnalyticsData(analytics);
  };

  useEffect(() => {
    if (!accessToken) return;
    void Promise.all([
      loadCampaigns(),
      channelDbApi
        .list(accessToken)
        .then((p) => setChannelDbs(p.items))
        .catch(() => {}),
      apiFetch<AccountsResponse>("/v1/web/accounts", { accessToken })
        .then((p) => setAccounts(p.items))
        .catch(() => {}),
    ]).catch(() => {});
  }, [accessToken]);

  useEffect(() => {
    if (selectedId != null) {
      void loadDetail(selectedId).catch(() => {});
    } else {
      setSelectedCampaign(null);
      setAnalyticsData(null);
    }
  }, [selectedId]);

  const handleCreate = async () => {
    if (!accessToken || !formName.trim()) {
      setStatusMessage("Введите название кампании.");
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      await campaignsApi.create(accessToken, {
        name: formName.trim(),
        campaign_type: formType,
        account_ids: formAccountIds,
        channel_database_id: formChannelDbId,
        comment_prompt: formPrompt || null,
        comment_tone: formTone,
        comment_language: formLanguage,
        schedule_type: formSchedule,
        budget_daily_actions: formDailyBudget,
        budget_total_actions: formTotalBudget ? Number(formTotalBudget) : null,
      });
      setShowCreate(false);
      setFormName("");
      setFormPrompt("");
      setFormAccountIds([]);
      setStatusMessage("Кампания создана.");
      await loadCampaigns();
    } catch (e) {
      setStatusMessage(e instanceof Error ? e.message : "create_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleAction = async (action: "start" | "pause" | "resume" | "stop" | "delete") => {
    if (!accessToken || selectedId == null) return;
    setBusy(true);
    setStatusMessage("");
    try {
      if (action === "start") await campaignsApi.start(accessToken, selectedId);
      else if (action === "pause") await campaignsApi.pause(accessToken, selectedId);
      else if (action === "resume") await campaignsApi.resume(accessToken, selectedId);
      else if (action === "stop") await campaignsApi.stop(accessToken, selectedId);
      else if (action === "delete") {
        await campaignsApi.delete(accessToken, selectedId);
        setSelectedId(null);
      }
      setStatusMessage(`Действие "${action}" выполнено.`);
      await loadCampaigns();
      if (action !== "delete" && selectedId != null) {
        await loadDetail(selectedId);
      }
    } catch (e) {
      setStatusMessage(e instanceof Error ? e.message : "action_failed");
    } finally {
      setBusy(false);
    }
  };

  const toggleAccount = (id: number) => {
    setFormAccountIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  const sc = selectedCampaign;

  const activeCnt = campaigns.filter((c) => c.status === "active").length;
  const pausedCnt = campaigns.filter((c) => c.status === "paused").length;
  const draftCnt = campaigns.filter((c) => c.status === "draft").length;

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
              <Megaphone size={18} />
            </div>
            <div>
              <p className="dash-panel-title">Campaign Manager</p>
              <h2 style={{ fontSize: "1.3rem", marginTop: 4 }}>Кампании</h2>
            </div>
          </div>
          <ul className="bullet-list" style={{ fontSize: 13 }}>
            <li>Кампания объединяет аккаунты, базу каналов и AI-промпт.</li>
            <li>Типы: комментирование, реакции, чаттинг или смешанный.</li>
            <li>Управляйте бюджетом и расписанием из интерфейса.</li>
          </ul>
        </motion.div>

        <motion.div
          className="dash-panel"
          initial={{ opacity: 0, x: 16 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.35, delay: 0.05 }}
        >
          <p className="dash-panel-title">Статус</p>
          <h2 style={{ fontSize: "1.3rem", marginTop: 4 }}>Сводка кампаний</h2>
          <div className="dash-stats" style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))", marginTop: 8 }}>
            <div className="dash-stat" style={{ padding: 14 }}>
              <span className="dash-stat-label">Всего</span>
              <span className="dash-stat-value" style={{ fontSize: "1.4rem" }}>
                {campaigns.length}
              </span>
            </div>
            <div className="dash-stat" style={{ padding: 14 }}>
              <span className="dash-stat-label">Активных</span>
              <span className="dash-stat-value" style={{ fontSize: "1.4rem", color: "var(--accent)" }}>
                {activeCnt}
              </span>
            </div>
            <div className="dash-stat" style={{ padding: 14 }}>
              <span className="dash-stat-label">На паузе</span>
              <span className="dash-stat-value" style={{ fontSize: "1.4rem", color: "var(--warning)" }}>
                {pausedCnt}
              </span>
            </div>
            <div className="dash-stat" style={{ padding: 14 }}>
              <span className="dash-stat-label">Черновиков</span>
              <span className="dash-stat-value" style={{ fontSize: "1.4rem", color: "var(--muted)" }}>
                {draftCnt}
              </span>
            </div>
          </div>
        </motion.div>
      </div>

      {statusMessage ? (
        <motion.div
          className="status-banner"
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
        >
          {statusMessage}
        </motion.div>
      ) : null}

      {/* Campaign list */}
      <motion.div
        className="dash-panel"
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, delay: 0.1 }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <p className="dash-panel-title">Список</p>
            <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>Ваши кампании</h2>
          </div>
          <button
            className="primary-button"
            type="button"
            onClick={() => setShowCreate(true)}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <Plus size={14} />
            Создать
          </button>
        </div>

        {campaigns.length ? (
          <div className="creative-list" style={{ marginTop: 16 }}>
            <AnimatePresence>
              {campaigns.map((c, i) => (
                <motion.div
                  key={c.id}
                  custom={i}
                  initial="hidden"
                  animate="visible"
                  exit="exit"
                  variants={cardVariants}
                  className="creative-item"
                  style={{
                    cursor: "pointer",
                    borderColor: selectedId === c.id ? "var(--accent)" : undefined,
                    boxShadow: selectedId === c.id ? "0 0 20px rgba(0,255,136,0.1)" : undefined,
                  }}
                  onClick={() => setSelectedId(selectedId === c.id ? null : c.id)}
                >
                  <div className="thread-meta" style={{ alignItems: "center" }}>
                    <div
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        background: statusDot(c.status),
                        boxShadow: `0 0 8px ${statusDot(c.status)}`,
                        flexShrink: 0,
                      }}
                    />
                    <strong style={{ color: "var(--text)" }}>{c.name}</strong>
                    <span style={statusPillStyle(c.status)}>{STATUS_LABELS[c.status]}</span>
                    <span
                      className="pill"
                      style={{
                        background: "var(--surface-2)",
                        color: "var(--text-secondary)",
                        border: "1px solid var(--border)",
                      }}
                    >
                      {TYPE_LABELS[c.campaign_type] ?? c.campaign_type}
                    </span>
                  </div>
                  <div className="mono" style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
                    Действий: {c.total_actions_performed} / Комментов: {c.total_comments_sent} / Реакций:{" "}
                    {c.total_reactions_sent}
                  </div>
                  <div style={{ marginTop: 8, maxWidth: 320 }}>
                    <BudgetBar used={c.total_actions_performed} total={c.budget_total_actions} />
                  </div>

                  {selectedId === c.id ? (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      className="actions-row"
                      style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--border)" }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        className="primary-button"
                        type="button"
                        disabled={busy || c.status === "active"}
                        onClick={() => void handleAction("start")}
                        style={{ display: "flex", alignItems: "center", gap: 6 }}
                      >
                        <Play size={13} />
                        Запустить
                      </button>
                      <button
                        className="secondary-button"
                        type="button"
                        disabled={busy || c.status !== "active"}
                        onClick={() => void handleAction("pause")}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          borderColor: c.status === "active" ? "var(--warning)" : undefined,
                          color: c.status === "active" ? "var(--warning)" : undefined,
                        }}
                      >
                        <Pause size={13} />
                        Пауза
                      </button>
                      <button
                        className="secondary-button"
                        type="button"
                        disabled={busy || c.status !== "paused"}
                        onClick={() => void handleAction("resume")}
                        style={{ display: "flex", alignItems: "center", gap: 6 }}
                      >
                        <RotateCcw size={13} />
                        Продолжить
                      </button>
                      <button
                        className="ghost-button"
                        type="button"
                        disabled={busy || (c.status !== "active" && c.status !== "paused")}
                        onClick={() => void handleAction("stop")}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          color: "var(--danger)",
                        }}
                      >
                        <Square size={13} />
                        Стоп
                      </button>
                      {c.status === "draft" || c.status === "archived" ? (
                        <button
                          className="danger-button"
                          type="button"
                          disabled={busy}
                          onClick={() => void handleAction("delete")}
                          style={{ display: "flex", alignItems: "center", gap: 6 }}
                        >
                          <Trash2 size={13} />
                          Удалить
                        </button>
                      ) : null}
                    </motion.div>
                  ) : null}
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        ) : (
          <p className="dash-empty">Кампаний нет. Создайте первую кампанию для начала работы.</p>
        )}
      </motion.div>

      {/* Campaign detail */}
      {sc ? (
        <>
          {/* Analytics cards */}
          <motion.div
            className="dash-panel"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, delay: 0.15 }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <BarChart2 size={16} style={{ color: "var(--accent)" }} />
                <div>
                  <p className="dash-panel-title">Аналитика кампании</p>
                  <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>{sc.name}</h2>
                </div>
              </div>
              <button
                className="ghost-button"
                type="button"
                onClick={() => void loadDetail(sc.id)}
                disabled={busy}
                style={{ display: "flex", alignItems: "center", gap: 6 }}
              >
                <RefreshCw size={14} />
                Обновить
              </button>
            </div>

            <div className="dash-stats" style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))", marginTop: 12 }}>
              <div className="dash-stat" style={{ padding: 16 }}>
                <span className="dash-stat-label">Всего действий</span>
                <span className="dash-stat-value" style={{ fontSize: "1.5rem" }}>
                  {sc.total_actions_performed}
                </span>
              </div>
              <div className="dash-stat" style={{ padding: 16 }}>
                <span className="dash-stat-label">Комментариев</span>
                <span className="dash-stat-value" style={{ fontSize: "1.5rem" }}>
                  {sc.total_comments_sent}
                </span>
              </div>
              <div className="dash-stat" style={{ padding: 16 }}>
                <span className="dash-stat-label">Реакций</span>
                <span className="dash-stat-value" style={{ fontSize: "1.5rem" }}>
                  {sc.total_reactions_sent}
                </span>
              </div>
              <div className="dash-stat" style={{ padding: 16 }}>
                <span className="dash-stat-label">Ошибок</span>
                <span
                  className="dash-stat-value"
                  style={{
                    fontSize: "1.5rem",
                    color:
                      sc.runs.reduce((s, r) => s + r.errors, 0) > 0
                        ? "var(--danger)"
                        : "var(--text)",
                  }}
                >
                  {sc.runs.reduce((s, r) => s + r.errors, 0)}
                </span>
              </div>
            </div>

            {analyticsData && Object.keys(analyticsData).length > 0 ? (
              <div className="terminal-window" style={{ marginTop: 16 }}>
                <div className="terminal-line">
                  <span className="timestamp">analytics</span>
                  <span className="message white" style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
                    {JSON.stringify(analyticsData, null, 2)}
                  </span>
                </div>
              </div>
            ) : null}
          </motion.div>

          {/* Run history */}
          <motion.div
            className="dash-panel"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, delay: 0.2 }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div>
                <p className="dash-panel-title">История запусков</p>
                <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>Запуски кампании</h2>
              </div>
              <span className="pill">{sc.runs.length} запусков</span>
            </div>

            {sc.runs.length ? (
              <div className="terminal-window" style={{ maxHeight: 400, marginTop: 12 }}>
                <table className="data-table" style={{ marginBottom: 0 }}>
                  <thead>
                    <tr>
                      <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>#</th>
                      <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Статус</th>
                      <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Действия</th>
                      <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Комменты</th>
                      <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Реакции</th>
                      <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Ошибки</th>
                      <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Начало</th>
                      <th style={{ color: "var(--accent)", borderColor: "var(--border)" }}>Конец</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sc.runs.map((r) => {
                      const runStatusColor =
                        r.status === "completed"
                          ? "var(--accent)"
                          : r.status === "failed"
                            ? "var(--danger)"
                            : "var(--muted)";
                      return (
                        <tr key={r.id}>
                          <td className="mono" style={{ fontSize: 12 }}>
                            {r.id}
                          </td>
                          <td>
                            <span
                              style={{
                                display: "inline-flex",
                                alignItems: "center",
                                gap: 6,
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
                                color: runStatusColor,
                              }}
                            >
                              <span
                                style={{
                                  width: 6,
                                  height: 6,
                                  borderRadius: "50%",
                                  background: runStatusColor,
                                }}
                              />
                              {r.status}
                            </span>
                          </td>
                          <td className="mono" style={{ fontSize: 12 }}>
                            {r.actions_performed}
                          </td>
                          <td className="mono" style={{ fontSize: 12 }}>
                            {r.comments_sent}
                          </td>
                          <td className="mono" style={{ fontSize: 12 }}>
                            {r.reactions_sent}
                          </td>
                          <td
                            className="mono"
                            style={{
                              fontSize: 12,
                              color: r.errors > 0 ? "var(--danger)" : "var(--text-secondary)",
                            }}
                          >
                            {r.errors}
                          </td>
                          <td className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
                            {r.started_at?.slice(0, 16) ?? "---"}
                          </td>
                          <td className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
                            {r.completed_at?.slice(0, 16) ?? "---"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="dash-empty">Запусков ещё не было.</p>
            )}
          </motion.div>
        </>
      ) : null}

      {/* Create modal */}
      <AnimatePresence>
        {showCreate ? (
          <motion.div
            className="modal-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setShowCreate(false)}
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.7)",
              backdropFilter: "blur(4px)",
              display: "grid",
              placeItems: "center",
              zIndex: 200,
              padding: 24,
            }}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 20 }}
              transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] as const }}
              className="dash-panel"
              onClick={(e) => e.stopPropagation()}
              style={{
                width: "min(600px, 100%)",
                maxHeight: "85vh",
                overflowY: "auto",
                boxShadow: "0 16px 64px rgba(0,0,0,0.6)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div>
                  <p className="dash-panel-title">Новая кампания</p>
                  <h2 style={{ fontSize: "1.2rem", marginTop: 4 }}>Создать кампанию</h2>
                </div>
                <button
                  className="ghost-button"
                  type="button"
                  onClick={() => setShowCreate(false)}
                  style={{ padding: 8 }}
                >
                  <X size={18} />
                </button>
              </div>

              <div className="stack-form" style={{ marginTop: 16 }}>
                <label className="field">
                  <span style={{ color: "var(--accent)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 500 }}>
                    Название кампании
                  </span>
                  <input
                    value={formName}
                    onChange={(e) => setFormName(e.target.value)}
                    placeholder="Например: Продвижение бренда - март 2026"
                  />
                </label>

                <label className="field">
                  <span style={{ color: "var(--accent)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 500 }}>
                    Тип кампании
                  </span>
                  <select value={formType} onChange={(e) => setFormType(e.target.value)}>
                    {Object.entries(TYPE_LABELS).map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </select>
                </label>

                <div className="field">
                  <span style={{ color: "var(--accent)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 500 }}>
                    Аккаунты ({formAccountIds.length} выбрано)
                  </span>
                  <div
                    style={{
                      maxHeight: 160,
                      overflowY: "auto",
                      borderRadius: 8,
                      border: "1px solid var(--border)",
                      background: "var(--surface-2)",
                      padding: 8,
                    }}
                  >
                    {accounts.map((acc) => (
                      <label
                        key={acc.id}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          padding: "6px 8px",
                          cursor: "pointer",
                          borderRadius: 6,
                          transition: "background 150ms ease",
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={formAccountIds.includes(acc.id)}
                          onChange={() => toggleAccount(acc.id)}
                          style={{ accentColor: "var(--accent)" }}
                        />
                        <span className="mono" style={{ fontSize: 13 }}>
                          {acc.phone}
                        </span>
                        <span
                          style={{
                            ...statusPillStyle(
                              acc.health_status === "alive" ? "active" : "draft",
                            ),
                            fontSize: 10,
                            padding: "2px 8px",
                          }}
                        >
                          {acc.health_status}
                        </span>
                      </label>
                    ))}
                    {accounts.length === 0 && (
                      <p className="dash-empty" style={{ padding: 8 }}>
                        Нет доступных аккаунтов.
                      </p>
                    )}
                  </div>
                </div>

                <label className="field">
                  <span style={{ color: "var(--accent)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 500 }}>
                    База каналов
                  </span>
                  <select
                    value={formChannelDbId ?? ""}
                    onChange={(e) =>
                      setFormChannelDbId(e.target.value ? Number(e.target.value) : null)
                    }
                  >
                    <option value="">--- выберите базу ---</option>
                    {channelDbs.map((db) => (
                      <option key={db.id} value={db.id}>
                        {db.name}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="field">
                  <span style={{ color: "var(--accent)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 500 }}>
                    AI-промпт
                  </span>
                  <textarea
                    className="assistant-textarea"
                    value={formPrompt}
                    onChange={(e) => setFormPrompt(e.target.value)}
                    placeholder="Опишите стиль и содержание комментариев..."
                  />
                </label>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <label className="field">
                    <span style={{ color: "var(--accent)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 500 }}>
                      Тональность
                    </span>
                    <select value={formTone} onChange={(e) => setFormTone(e.target.value)}>
                      <option value="neutral">Нейтральный</option>
                      <option value="hater">Хейтер</option>
                      <option value="flirt">Флирт</option>
                      <option value="native">Нативный</option>
                      <option value="custom">Свой</option>
                    </select>
                  </label>
                  <label className="field">
                    <span style={{ color: "var(--accent)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 500 }}>
                      Язык
                    </span>
                    <select value={formLanguage} onChange={(e) => setFormLanguage(e.target.value)}>
                      <option value="ru">Русский</option>
                      <option value="en">English</option>
                      <option value="uk">Українська</option>
                      <option value="kz">Қазақша</option>
                      <option value="auto">Авто</option>
                    </select>
                  </label>
                </div>

                <label className="field">
                  <span style={{ color: "var(--accent)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 500 }}>
                    Тип расписания
                  </span>
                  <select value={formSchedule} onChange={(e) => setFormSchedule(e.target.value)}>
                    {Object.entries(SCHEDULE_LABELS).map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </select>
                </label>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <label className="field">
                    <span style={{ color: "var(--accent)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 500 }}>
                      Дневной лимит действий
                    </span>
                    <input
                      type="number"
                      min={1}
                      value={formDailyBudget}
                      onChange={(e) => setFormDailyBudget(Number(e.target.value))}
                    />
                  </label>
                  <label className="field">
                    <span style={{ color: "var(--accent)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 500 }}>
                      Общий лимит (необязательно)
                    </span>
                    <input
                      type="number"
                      min={1}
                      value={formTotalBudget}
                      onChange={(e) => setFormTotalBudget(e.target.value)}
                      placeholder="без лимита"
                    />
                  </label>
                </div>

                <div className="actions-row" style={{ marginTop: 8 }}>
                  <button
                    className="primary-button"
                    type="button"
                    disabled={busy}
                    onClick={() => void handleCreate()}
                    style={{ display: "flex", alignItems: "center", gap: 6 }}
                  >
                    <Plus size={14} />
                    {busy ? "Создаём..." : "Создать кампанию"}
                  </button>
                  <button
                    className="ghost-button"
                    type="button"
                    onClick={() => setShowCreate(false)}
                  >
                    Отмена
                  </button>
                </div>
              </div>
            </motion.div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
