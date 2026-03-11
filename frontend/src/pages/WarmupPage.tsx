import { useEffect, useState } from "react";
import { warmupApi, WarmupConfig, WarmupSession } from "../api";
import { useAuth } from "../auth";

const MODE_LABELS: Record<string, string> = {
  conservative: "Консервативный",
  moderate: "Умеренный",
  aggressive: "Агрессивный",
};

const MODE_DESCRIPTIONS: Record<string, string> = {
  conservative: "Минимальный риск. Мало действий в час, длинные паузы между сессиями. Рекомендуется для новых аккаунтов.",
  moderate: "Баланс скорости и безопасности. Подходит для аккаунтов с историей (2–4 недели).",
  aggressive: "Максимальная скорость прогрева. Только для опытных аккаунтов с хорошим health score.",
};

const SESSION_STATUS_LABELS: Record<string, string> = {
  pending: "Ожидание",
  running: "Выполняется",
  completed: "Завершена",
  failed: "Ошибка",
};

function modeBadgeClass(mode: string): string {
  if (mode === "conservative") return "badge-blue";
  if (mode === "moderate") return "badge-yellow";
  if (mode === "aggressive") return "badge-red";
  return "badge-gray";
}

function sessionStatusBadgeClass(status: string): string {
  if (status === "completed") return "badge-green";
  if (status === "running") return "badge-green";
  if (status === "pending") return "badge-gray";
  if (status === "failed") return "badge-red";
  return "badge-gray";
}

export function WarmupPage() {
  const { accessToken } = useAuth();

  const [configs, setConfigs] = useState<WarmupConfig[]>([]);
  const [selectedConfig, setSelectedConfig] = useState<WarmupConfig | null>(null);
  const [sessions, setSessions] = useState<WarmupSession[]>([]);

  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  // Create modal state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [formName, setFormName] = useState("");
  const [formMode, setFormMode] = useState<"conservative" | "moderate" | "aggressive">("conservative");
  const [formSafetyLimit, setFormSafetyLimit] = useState(20);
  const [formActiveHoursStart, setFormActiveHoursStart] = useState("09:00");
  const [formActiveHoursEnd, setFormActiveHoursEnd] = useState("22:00");
  const [formSessionDuration, setFormSessionDuration] = useState(30);
  const [formIntervalHours, setFormIntervalHours] = useState(4);
  const [formEnableReactions, setFormEnableReactions] = useState(true);
  const [formEnableReadChannels, setFormEnableReadChannels] = useState(true);
  const [formEnableDialogs, setFormEnableDialogs] = useState(false);
  const [formTargetChannels, setFormTargetChannels] = useState("");

  const loadConfigs = async () => {
    if (!accessToken) return;
    const payload = await warmupApi.list(accessToken);
    setConfigs(payload.items);
  };

  const loadSessions = async (configId: number) => {
    if (!accessToken) return;
    const payload = await warmupApi.getSessions(accessToken, configId);
    setSessions(payload.items);
  };

  useEffect(() => {
    void loadConfigs().catch(() => {});
  }, [accessToken]);

  useEffect(() => {
    if (selectedConfig) {
      void loadSessions(selectedConfig.id).catch(() => {});
    } else {
      setSessions([]);
    }
  }, [selectedConfig?.id]);

  const handleCreateConfig = async () => {
    if (!accessToken || !formName.trim()) {
      setStatusMessage("Введите название конфигурации.");
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      await warmupApi.create(accessToken, {
        name: formName.trim(),
        mode: formMode,
        safety_limit_per_hour: formSafetyLimit,
        active_hours_start: formActiveHoursStart,
        active_hours_end: formActiveHoursEnd,
        session_duration_minutes: formSessionDuration,
        interval_between_sessions_hours: formIntervalHours,
        enable_reactions: formEnableReactions,
        enable_read_channels: formEnableReadChannels,
        enable_dialogs: formEnableDialogs,
        target_channels: formTargetChannels
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
      });
      setShowCreateModal(false);
      resetCreateForm();
      setStatusMessage("Конфигурация прогрева создана.");
      await loadConfigs();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "create_warmup_failed");
    } finally {
      setBusy(false);
    }
  };

  const resetCreateForm = () => {
    setFormName("");
    setFormMode("conservative");
    setFormSafetyLimit(20);
    setFormActiveHoursStart("09:00");
    setFormActiveHoursEnd("22:00");
    setFormSessionDuration(30);
    setFormIntervalHours(4);
    setFormEnableReactions(true);
    setFormEnableReadChannels(true);
    setFormEnableDialogs(false);
    setFormTargetChannels("");
  };

  const handleStartConfig = async (configId: number) => {
    if (!accessToken) return;
    setBusy(true);
    setStatusMessage("");
    try {
      await warmupApi.start(accessToken, configId);
      setStatusMessage("Прогрев запущен.");
      await loadConfigs();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "start_warmup_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleStopConfig = async (configId: number) => {
    if (!accessToken) return;
    setBusy(true);
    setStatusMessage("");
    try {
      await warmupApi.stop(accessToken, configId);
      setStatusMessage("Прогрев остановлен.");
      await loadConfigs();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "stop_warmup_failed");
    } finally {
      setBusy(false);
    }
  };

  const safetyRecommendations: Record<string, string[]> = {
    conservative: [
      "Не более 20 действий в час.",
      "Паузы между сессиями — минимум 4 часа.",
      "Рекомендуется для аккаунтов до 2 недель жизни.",
      "Включите только чтение каналов и реакции на первой неделе.",
    ],
    moderate: [
      "До 50 действий в час.",
      "Паузы между сессиями — 2–3 часа.",
      "Допустимы диалоги с проверенными контактами.",
      "Мониторьте health score ежедневно.",
    ],
    aggressive: [
      "До 100 действий в час. Высокий риск.",
      "Только для аккаунтов с health score выше 70.",
      "При первом flood_wait — немедленно снижайте режим.",
      "Держите quarantine-буфер: не запускайте все аккаунты одновременно.",
    ],
  };

  return (
    <div className="page-grid">
      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Warmup Engine</div>
              <h2>Прогрев аккаунтов</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Прогрев имитирует естественное поведение пользователя: чтение каналов, реакции, диалоги.</li>
            <li>Правильный прогрев снижает риск получения спам-блока на новых аккаунтах.</li>
            <li>Выбирайте режим в зависимости от возраста и истории аккаунта.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Статус</div>
              <h2>Конфигурации прогрева</h2>
            </div>
          </div>
          <div className="status-grid">
            <div className="info-block">
              <strong>Всего конфигов</strong>
              <span>{configs.length}</span>
            </div>
            <div className="info-block">
              <strong>Активных</strong>
              <span>{configs.filter((c) => c.status === "running").length}</span>
            </div>
            <div className="info-block">
              <strong>Консервативных</strong>
              <span>{configs.filter((c) => c.mode === "conservative").length}</span>
            </div>
            <div className="info-block">
              <strong>Агрессивных</strong>
              <span>{configs.filter((c) => c.mode === "aggressive").length}</span>
            </div>
          </div>
        </article>
      </section>

      {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

      {/* Config list */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Список конфигураций</div>
            <h2>Ваши конфигурации прогрева</h2>
          </div>
          <div className="badge-row">
            <button
              className="primary-button"
              type="button"
              onClick={() => {
                resetCreateForm();
                setShowCreateModal(true);
              }}
            >
              + Создать прогрев
            </button>
          </div>
        </div>
        {configs.length ? (
          <div className="creative-list">
            {configs.map((config) => (
              <div
                key={config.id}
                className={`creative-item ${selectedConfig?.id === config.id ? "selected" : ""}`}
                style={{ cursor: "pointer" }}
                onClick={() => setSelectedConfig(config)}
              >
                <div className="thread-meta">
                  <strong>{config.name}</strong>
                  <span className={`pill ${modeBadgeClass(config.mode)}`}>
                    {MODE_LABELS[config.mode] ?? config.mode}
                  </span>
                  <span className={`pill ${config.status === "running" ? "badge-green" : "badge-gray"}`}>
                    {config.status === "running" ? "Активен" : "Остановлен"}
                  </span>
                  <span className="muted">
                    Аккаунтов: {config.account_count} · Лимит: {config.safety_limit_per_hour}/ч · Сессия: {config.session_duration_minutes} мин
                  </span>
                  <span className="muted">
                    Активные часы: {config.active_hours_start}–{config.active_hours_end} · Интервал: {config.interval_between_sessions_hours} ч
                  </span>
                </div>
                {selectedConfig?.id === config.id ? (
                  <div className="actions-row" style={{ marginTop: 8 }} onClick={(e) => e.stopPropagation()}>
                    <button
                      className="primary-button"
                      type="button"
                      disabled={busy || config.status === "running"}
                      onClick={() => void handleStartConfig(config.id)}
                    >
                      Запустить
                    </button>
                    <button
                      className="ghost-button"
                      type="button"
                      disabled={busy || config.status !== "running"}
                      onClick={() => void handleStopConfig(config.id)}
                    >
                      Остановить
                    </button>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">Нет конфигураций прогрева. Создайте первый конфиг для безопасного старта аккаунтов.</p>
        )}
      </section>

      {/* Config detail — sessions */}
      {selectedConfig ? (
        <section className="panel wide">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Сессии прогрева</div>
              <h2>{selectedConfig.name} — сессии</h2>
            </div>
            <div className="badge-row">
              <span className="pill">{sessions.length} сессий</span>
              <button
                className="ghost-button"
                type="button"
                onClick={() => void loadSessions(selectedConfig.id).catch(() => {})}
                disabled={busy}
              >
                Обновить
              </button>
            </div>
          </div>
          {sessions.length ? (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Аккаунт</th>
                    <th>Статус</th>
                    <th>Выполнено действий</th>
                    <th>Начата</th>
                    <th>Следующая сессия</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr key={s.id}>
                      <td>{s.account_phone ?? `Аккаунт #${s.account_id}`}</td>
                      <td>
                        <span className={`pill ${sessionStatusBadgeClass(s.status)}`}>
                          {SESSION_STATUS_LABELS[s.status] ?? s.status}
                        </span>
                      </td>
                      <td>{s.actions_performed}</td>
                      <td>{s.started_at ?? "—"}</td>
                      <td>{s.next_session_at ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">Сессии появятся после запуска прогрева.</p>
          )}
        </section>
      ) : null}

      {/* Safety recommendations */}
      {selectedConfig ? (
        <section className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Рекомендации безопасности</div>
              <h2>Режим: {MODE_LABELS[selectedConfig.mode]}</h2>
            </div>
            <span className={`pill ${modeBadgeClass(selectedConfig.mode)}`}>
              {MODE_LABELS[selectedConfig.mode]}
            </span>
          </div>
          <p className="muted" style={{ marginBottom: 12 }}>{MODE_DESCRIPTIONS[selectedConfig.mode]}</p>
          <ul className="bullet-list">
            {(safetyRecommendations[selectedConfig.mode] ?? []).map((rec, i) => (
              <li key={i}>{rec}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* Create modal */}
      {showCreateModal ? (
        <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div>
                <div className="eyebrow">Новый прогрев</div>
                <h2>Создать конфигурацию прогрева</h2>
              </div>
            </div>
            <div className="stack-form">
              <label className="field">
                <span>Название</span>
                <input
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="Например: Прогрев новых KZ аккаунтов"
                />
              </label>

              <div className="field">
                <span>Режим прогрева</span>
                <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                  {(["conservative", "moderate", "aggressive"] as const).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      className={formMode === mode ? "primary-button" : "ghost-button"}
                      style={{ flex: 1 }}
                      onClick={() => setFormMode(mode)}
                    >
                      {MODE_LABELS[mode]}
                    </button>
                  ))}
                </div>
                <div className="inline-note" style={{ marginTop: 6 }}>
                  {MODE_DESCRIPTIONS[formMode]}
                </div>
              </div>

              <label className="field">
                <span>Лимит действий в час: {formSafetyLimit}</span>
                <input
                  type="range"
                  min={5}
                  max={150}
                  step={5}
                  value={formSafetyLimit}
                  onChange={(e) => setFormSafetyLimit(Number(e.target.value))}
                />
              </label>

              <div className="two-column-grid" style={{ gap: 12 }}>
                <label className="field">
                  <span>Активные часы — начало</span>
                  <input
                    type="time"
                    value={formActiveHoursStart}
                    onChange={(e) => setFormActiveHoursStart(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Активные часы — конец</span>
                  <input
                    type="time"
                    value={formActiveHoursEnd}
                    onChange={(e) => setFormActiveHoursEnd(e.target.value)}
                  />
                </label>
              </div>

              <div className="two-column-grid" style={{ gap: 12 }}>
                <label className="field">
                  <span>Длительность сессии (мин)</span>
                  <input
                    type="number"
                    min={5}
                    max={120}
                    value={formSessionDuration}
                    onChange={(e) => setFormSessionDuration(Number(e.target.value))}
                  />
                </label>
                <label className="field">
                  <span>Интервал между сессиями (ч)</span>
                  <input
                    type="number"
                    min={1}
                    max={24}
                    value={formIntervalHours}
                    onChange={(e) => setFormIntervalHours(Number(e.target.value))}
                  />
                </label>
              </div>

              <div className="field">
                <span>Включить активности</span>
                <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={formEnableReactions}
                      onChange={(e) => setFormEnableReactions(e.target.checked)}
                    />
                    <span>Реакции на посты</span>
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={formEnableReadChannels}
                      onChange={(e) => setFormEnableReadChannels(e.target.checked)}
                    />
                    <span>Чтение каналов</span>
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={formEnableDialogs}
                      onChange={(e) => setFormEnableDialogs(e.target.checked)}
                    />
                    <span>Диалоги (только для опытных аккаунтов)</span>
                  </label>
                </div>
              </div>

              <label className="field">
                <span>Целевые каналы (по одному на строку, необязательно)</span>
                <textarea
                  className="assistant-textarea"
                  value={formTargetChannels}
                  onChange={(e) => setFormTargetChannels(e.target.value)}
                  placeholder="@channel_username&#10;https://t.me/another_channel"
                  rows={4}
                />
              </label>

              <div className="actions-row">
                <button
                  className="primary-button"
                  type="button"
                  disabled={busy}
                  onClick={() => void handleCreateConfig()}
                >
                  {busy ? "Создаём…" : "Создать конфигурацию"}
                </button>
                <button
                  className="ghost-button"
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                >
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
