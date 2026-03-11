import { useEffect, useState } from "react";
import { campaignsApi, channelDbApi, Campaign, CampaignRun, CampaignStatus, ChannelDatabase } from "../api";
import { useAuth } from "../auth";

type AccountRow = { id: number; phone: string; status: string; health_status: string };
type AccountsResponse = { items: AccountRow[]; total: number };

import { apiFetch } from "../api";

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

function statusBadgeClass(status: CampaignStatus): string {
  if (status === "active") return "badge-green";
  if (status === "paused") return "badge-yellow";
  if (status === "completed") return "badge-blue";
  return "badge-gray";
}

function BudgetBar({ used, total }: { used: number; total: number | null }) {
  if (!total) return <span className="muted">без лимита</span>;
  const pct = Math.min(100, Math.round((used / total) * 100));
  const color = pct >= 90 ? "#ef4444" : pct >= 70 ? "#eab308" : "#22c55e";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ flex: 1, height: 6, background: "#2d2d2d", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: 11, color: "#aaa", minWidth: 42 }}>{used}/{total}</span>
    </div>
  );
}

export function CampaignsPage() {
  const { accessToken } = useAuth();

  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedCampaign, setSelectedCampaign] = useState<(Campaign & { runs: CampaignRun[] }) | null>(null);
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
      channelDbApi.list(accessToken).then((p) => setChannelDbs(p.items)).catch(() => {}),
      apiFetch<AccountsResponse>("/v1/web/accounts", { accessToken }).then((p) => setAccounts(p.items)).catch(() => {}),
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
      setStatusMessage(`Действие «${action}» выполнено.`);
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
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const sc = selectedCampaign;

  return (
    <div className="page-grid">
      {/* Header */}
      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Campaign Manager</div>
              <h2>Кампании</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Кампания объединяет аккаунты, базу каналов и AI-промпт в единый рабочий процесс.</li>
            <li>Типы: комментирование, реакции, чаттинг или смешанный режим.</li>
            <li>Управляйте бюджетом действий и расписанием прямо из интерфейса.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Статус</div>
              <h2>Сводка кампаний</h2>
            </div>
          </div>
          <div className="status-grid">
            <div className="info-block">
              <strong>Всего</strong>
              <span>{campaigns.length}</span>
            </div>
            <div className="info-block">
              <strong>Активных</strong>
              <span>{campaigns.filter((c) => c.status === "active").length}</span>
            </div>
            <div className="info-block">
              <strong>На паузе</strong>
              <span>{campaigns.filter((c) => c.status === "paused").length}</span>
            </div>
            <div className="info-block">
              <strong>Черновиков</strong>
              <span>{campaigns.filter((c) => c.status === "draft").length}</span>
            </div>
          </div>
        </article>
      </section>

      {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

      {/* Campaign list */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Список</div>
            <h2>Ваши кампании</h2>
          </div>
          <div className="badge-row">
            <button className="primary-button" type="button" onClick={() => setShowCreate(true)}>
              + Создать
            </button>
          </div>
        </div>
        {campaigns.length ? (
          <div className="creative-list">
            {campaigns.map((c) => (
              <div
                key={c.id}
                className={`creative-item ${selectedId === c.id ? "selected" : ""}`}
                style={{ cursor: "pointer" }}
                onClick={() => setSelectedId(selectedId === c.id ? null : c.id)}
              >
                <div className="thread-meta">
                  <strong>{c.name}</strong>
                  <span className={`pill ${statusBadgeClass(c.status)}`}>{STATUS_LABELS[c.status]}</span>
                  <span className="pill badge-gray">{TYPE_LABELS[c.campaign_type] ?? c.campaign_type}</span>
                  <span className="muted">
                    Действий: {c.total_actions_performed} · Комментов: {c.total_comments_sent} · Реакций: {c.total_reactions_sent}
                  </span>
                </div>
                <div style={{ marginTop: 8, maxWidth: 320 }}>
                  <BudgetBar used={c.total_actions_performed} total={c.budget_total_actions} />
                </div>

                {selectedId === c.id ? (
                  <div className="actions-row" style={{ marginTop: 10 }} onClick={(e) => e.stopPropagation()}>
                    <button
                      className="primary-button"
                      type="button"
                      disabled={busy || c.status === "active"}
                      onClick={() => void handleAction("start")}
                    >
                      Запустить
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={busy || c.status !== "active"}
                      onClick={() => void handleAction("pause")}
                    >
                      Пауза
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={busy || c.status !== "paused"}
                      onClick={() => void handleAction("resume")}
                    >
                      Продолжить
                    </button>
                    <button
                      className="ghost-button"
                      type="button"
                      disabled={busy || (c.status !== "active" && c.status !== "paused")}
                      onClick={() => void handleAction("stop")}
                    >
                      Стоп
                    </button>
                    {(c.status === "draft" || c.status === "archived") ? (
                      <button
                        className="ghost-button"
                        type="button"
                        disabled={busy}
                        onClick={() => void handleAction("delete")}
                        style={{ color: "#ef4444" }}
                      >
                        Удалить
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">Кампаний нет. Создайте первую кампанию для начала работы.</p>
        )}
      </section>

      {/* Campaign detail */}
      {sc ? (
        <>
          {/* Analytics cards */}
          <section className="panel wide">
            <div className="panel-header">
              <div>
                <div className="eyebrow">Аналитика кампании</div>
                <h2>{sc.name}</h2>
              </div>
              <div className="badge-row">
                <button className="ghost-button" type="button" onClick={() => void loadDetail(sc.id)} disabled={busy}>
                  Обновить
                </button>
              </div>
            </div>
            <div className="status-grid">
              <div className="info-block">
                <strong>Всего действий</strong>
                <span>{sc.total_actions_performed}</span>
              </div>
              <div className="info-block">
                <strong>Комментариев</strong>
                <span>{sc.total_comments_sent}</span>
              </div>
              <div className="info-block">
                <strong>Реакций</strong>
                <span>{sc.total_reactions_sent}</span>
              </div>
              <div className="info-block">
                <strong>Ошибок</strong>
                <span>
                  {sc.runs.reduce((s, r) => s + r.errors, 0)}
                </span>
              </div>
            </div>
            {analyticsData && Object.keys(analyticsData).length > 0 ? (
              <div className="inline-note" style={{ marginTop: 12 }}>
                {JSON.stringify(analyticsData, null, 2)}
              </div>
            ) : null}
          </section>

          {/* Run history */}
          <section className="panel wide">
            <div className="panel-header">
              <div>
                <div className="eyebrow">История запусков</div>
                <h2>Запуски кампании</h2>
              </div>
              <div className="badge-row">
                <span className="pill">{sc.runs.length} запусков</span>
              </div>
            </div>
            {sc.runs.length ? (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Статус</th>
                      <th>Действия</th>
                      <th>Комменты</th>
                      <th>Реакции</th>
                      <th>Ошибки</th>
                      <th>Начало</th>
                      <th>Конец</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sc.runs.map((r) => (
                      <tr key={r.id}>
                        <td>{r.id}</td>
                        <td>
                          <span className={`pill ${r.status === "completed" ? "badge-green" : r.status === "failed" ? "badge-red" : "badge-gray"}`}>
                            {r.status}
                          </span>
                        </td>
                        <td>{r.actions_performed}</td>
                        <td>{r.comments_sent}</td>
                        <td>{r.reactions_sent}</td>
                        <td>{r.errors}</td>
                        <td className="muted" style={{ fontSize: 11 }}>{r.started_at?.slice(0, 16) ?? "—"}</td>
                        <td className="muted" style={{ fontSize: 11 }}>{r.completed_at?.slice(0, 16) ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">Запусков ещё не было.</p>
            )}
          </section>
        </>
      ) : null}

      {/* Create modal */}
      {showCreate ? (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div>
                <div className="eyebrow">Новая кампания</div>
                <h2>Создать кампанию</h2>
              </div>
            </div>
            <div className="stack-form">
              <label className="field">
                <span>Название кампании</span>
                <input
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="Например: Продвижение бренда — март 2026"
                />
              </label>

              <label className="field">
                <span>Тип кампании</span>
                <select value={formType} onChange={(e) => setFormType(e.target.value)}>
                  {Object.entries(TYPE_LABELS).map(([v, l]) => (
                    <option key={v} value={v}>{l}</option>
                  ))}
                </select>
              </label>

              <div className="field">
                <span>Аккаунты ({formAccountIds.length} выбрано)</span>
                <div className="thread-list" style={{ maxHeight: 160, overflowY: "auto" }}>
                  {accounts.map((acc) => (
                    <label
                      key={acc.id}
                      style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 0", cursor: "pointer" }}
                    >
                      <input
                        type="checkbox"
                        checked={formAccountIds.includes(acc.id)}
                        onChange={() => toggleAccount(acc.id)}
                      />
                      <span>{acc.phone}</span>
                      <span className={`pill ${acc.health_status === "alive" ? "badge-green" : "badge-gray"}`}>
                        {acc.health_status}
                      </span>
                    </label>
                  ))}
                  {accounts.length === 0 && <p className="muted">Нет доступных аккаунтов.</p>}
                </div>
              </div>

              <label className="field">
                <span>База каналов</span>
                <select
                  value={formChannelDbId ?? ""}
                  onChange={(e) => setFormChannelDbId(e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">— выберите базу —</option>
                  {channelDbs.map((db) => (
                    <option key={db.id} value={db.id}>{db.name}</option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span>AI-промпт</span>
                <textarea
                  className="assistant-textarea"
                  value={formPrompt}
                  onChange={(e) => setFormPrompt(e.target.value)}
                  placeholder="Опишите стиль и содержание комментариев..."
                />
              </label>

              <div className="two-column-grid" style={{ gap: 12 }}>
                <label className="field">
                  <span>Тональность</span>
                  <select value={formTone} onChange={(e) => setFormTone(e.target.value)}>
                    <option value="neutral">Нейтральный</option>
                    <option value="hater">Хейтер</option>
                    <option value="flirt">Флирт</option>
                    <option value="native">Нативный</option>
                    <option value="custom">Свой</option>
                  </select>
                </label>
                <label className="field">
                  <span>Язык</span>
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
                <span>Тип расписания</span>
                <select value={formSchedule} onChange={(e) => setFormSchedule(e.target.value)}>
                  {Object.entries(SCHEDULE_LABELS).map(([v, l]) => (
                    <option key={v} value={v}>{l}</option>
                  ))}
                </select>
              </label>

              <div className="two-column-grid" style={{ gap: 12 }}>
                <label className="field">
                  <span>Дневной лимит действий</span>
                  <input
                    type="number"
                    min={1}
                    value={formDailyBudget}
                    onChange={(e) => setFormDailyBudget(Number(e.target.value))}
                  />
                </label>
                <label className="field">
                  <span>Общий лимит (необязательно)</span>
                  <input
                    type="number"
                    min={1}
                    value={formTotalBudget}
                    onChange={(e) => setFormTotalBudget(e.target.value)}
                    placeholder="без лимита"
                  />
                </label>
              </div>

              <div className="actions-row">
                <button className="primary-button" type="button" disabled={busy} onClick={() => void handleCreate()}>
                  {busy ? "Создаём…" : "Создать кампанию"}
                </button>
                <button className="ghost-button" type="button" onClick={() => setShowCreate(false)}>
                  Отмена
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
