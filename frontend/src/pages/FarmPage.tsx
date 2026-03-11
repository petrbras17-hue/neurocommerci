import { useEffect, useState } from "react";
import { apiFetch, farmApi, channelDbApi, FarmConfig, FarmThread, FarmEvent, ChannelDatabase } from "../api";
import { useAuth } from "../auth";

type AccountRow = {
  id: number;
  phone: string;
  status: string;
  health_status: string;
};

type AccountsResponse = {
  items: AccountRow[];
  total: number;
};

const STATUS_LABELS: Record<string, string> = {
  running: "Запущена",
  stopped: "Остановлена",
  paused: "На паузе",
  idle: "Ожидание",
  subscribing: "Подписка",
  monitoring: "Мониторинг",
  commenting: "Комментирование",
  cooldown: "Охлаждение",
  quarantine: "Карантин",
  error: "Ошибка",
};

const TONE_OPTIONS = [
  { value: "neutral", label: "Нейтральный" },
  { value: "hater", label: "Хейтер" },
  { value: "flirt", label: "Флирт" },
  { value: "native", label: "Нативный" },
  { value: "custom", label: "Свой" },
];

const LANGUAGE_OPTIONS = [
  { value: "auto", label: "Авто" },
  { value: "ru", label: "Русский" },
  { value: "en", label: "English" },
  { value: "uk", label: "Українська" },
  { value: "kz", label: "Қазақша" },
];

const PROTECTION_OPTIONS = [
  { value: "off", label: "Выключено" },
  { value: "conservative", label: "Консервативная" },
  { value: "aggressive", label: "Агрессивная" },
];

const SEVERITY_CLASS: Record<string, string> = {
  info: "event-info",
  warn: "event-warn",
  error: "event-error",
};

function statusBadgeClass(status: string): string {
  if (status === "running") return "badge-green";
  if (status === "paused") return "badge-yellow";
  if (status === "error" || status === "quarantine") return "badge-red";
  return "badge-gray";
}

function HealthBar({ score }: { score: number }) {
  const color = score >= 70 ? "#22c55e" : score >= 40 ? "#eab308" : "#ef4444";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ flex: 1, height: 6, background: "#2d2d2d", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${score}%`, height: "100%", background: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: 11, color: "#aaa", minWidth: 28 }}>{score}</span>
    </div>
  );
}

export function FarmPage() {
  const { accessToken } = useAuth();

  const [farms, setFarms] = useState<FarmConfig[]>([]);
  const [selectedFarm, setSelectedFarm] = useState<FarmConfig | null>(null);
  const [threads, setThreads] = useState<FarmThread[]>([]);
  const [events, setEvents] = useState<FarmEvent[]>([]);
  const [channelDbs, setChannelDbs] = useState<ChannelDatabase[]>([]);
  const [accounts, setAccounts] = useState<AccountRow[]>([]);

  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  // Create farm modal state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [farmName, setFarmName] = useState("");
  const [commentPrompt, setCommentPrompt] = useState("");
  const [commentTone, setCommentTone] = useState("neutral");
  const [commentLanguage, setCommentLanguage] = useState("auto");
  const [aiProtectionMode, setAiProtectionMode] = useState("aggressive");
  const [commentPercentage, setCommentPercentage] = useState(100);
  const [delayMin, setDelayMin] = useState(30);
  const [delayMax, setDelayMax] = useState(120);
  const [autoResponderEnabled, setAutoResponderEnabled] = useState(false);
  const [autoResponderPrompt, setAutoResponderPrompt] = useState("");

  // Start farm modal state
  const [showStartModal, setShowStartModal] = useState(false);
  const [selectedAccountIds, setSelectedAccountIds] = useState<number[]>([]);
  const [selectedChannelDbId, setSelectedChannelDbId] = useState<number | null>(null);

  const loadFarms = async () => {
    if (!accessToken) return;
    const payload = await farmApi.list(accessToken);
    setFarms(payload.items);
  };

  const loadChannelDbs = async () => {
    if (!accessToken) return;
    const payload = await channelDbApi.list(accessToken);
    setChannelDbs(payload.items);
  };

  const loadAccounts = async () => {
    if (!accessToken) return;
    const payload = await apiFetch<AccountsResponse>("/v1/web/accounts", { accessToken });
    setAccounts(payload.items);
  };

  const loadFarmDetail = async (farmId: number) => {
    if (!accessToken) return;
    const [threadsPayload, eventsPayload] = await Promise.all([
      farmApi.getThreads(accessToken, farmId),
      farmApi.getEvents(accessToken, farmId, 100),
    ]);
    setThreads(threadsPayload.items);
    setEvents(eventsPayload.items);
  };

  useEffect(() => {
    void Promise.all([loadFarms(), loadChannelDbs(), loadAccounts()]).catch(() => {});
  }, [accessToken]);

  useEffect(() => {
    if (selectedFarm) {
      void loadFarmDetail(selectedFarm.id).catch(() => {});
    } else {
      setThreads([]);
      setEvents([]);
    }
  }, [selectedFarm?.id]);

  const selectFarm = async (farm: FarmConfig) => {
    setSelectedFarm(farm);
  };

  const handleCreateFarm = async () => {
    if (!accessToken || !farmName.trim()) {
      setStatusMessage("Введите название фермы.");
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      await farmApi.create(accessToken, {
        name: farmName.trim(),
        comment_prompt: commentPrompt || null,
        comment_tone: commentTone,
        comment_language: commentLanguage,
        ai_protection_mode: aiProtectionMode,
        comment_percentage: commentPercentage,
        delay_before_comment_min: delayMin,
        delay_before_comment_max: delayMax,
        auto_responder_enabled: autoResponderEnabled,
        auto_responder_prompt: autoResponderPrompt || null,
      });
      setShowCreateModal(false);
      setFarmName("");
      setCommentPrompt("");
      setAutoResponderPrompt("");
      setStatusMessage("Ферма создана.");
      await loadFarms();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "create_farm_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleStartFarm = async () => {
    if (!accessToken || !selectedFarm) return;
    if (!selectedAccountIds.length) {
      setStatusMessage("Выберите хотя бы один аккаунт.");
      return;
    }
    if (!selectedChannelDbId) {
      setStatusMessage("Выберите базу каналов.");
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      await farmApi.start(accessToken, selectedFarm.id, {
        account_ids: selectedAccountIds,
        channel_database_id: selectedChannelDbId,
      });
      setShowStartModal(false);
      setSelectedAccountIds([]);
      setStatusMessage("Ферма запускается.");
      await loadFarms();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "start_farm_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleFarmAction = async (action: "stop" | "pause" | "resume") => {
    if (!accessToken || !selectedFarm) return;
    setBusy(true);
    setStatusMessage("");
    try {
      if (action === "stop") await farmApi.stop(accessToken, selectedFarm.id);
      if (action === "pause") await farmApi.pause(accessToken, selectedFarm.id);
      if (action === "resume") await farmApi.resume(accessToken, selectedFarm.id);
      setStatusMessage(`Действие «${action}» выполнено.`);
      await loadFarms();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "farm_action_failed");
    } finally {
      setBusy(false);
    }
  };

  const toggleAccountSelection = (id: number) => {
    setSelectedAccountIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  return (
    <div className="page-grid">
      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Farm Orchestrator</div>
              <h2>Управление фермами</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Каждая ферма — это набор потоков (1 поток = 1 аккаунт).</li>
            <li>Поток подписывается на каналы, мониторит посты и оставляет комментарии.</li>
            <li>AI-защита автоматически регулирует поведение для снижения риска.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Статус</div>
              <h2>Текущие фермы</h2>
            </div>
          </div>
          <div className="status-grid">
            <div className="info-block">
              <strong>Всего ферм</strong>
              <span>{farms.length}</span>
            </div>
            <div className="info-block">
              <strong>Запущено</strong>
              <span>{farms.filter((f) => f.status === "running").length}</span>
            </div>
            <div className="info-block">
              <strong>На паузе</strong>
              <span>{farms.filter((f) => f.status === "paused").length}</span>
            </div>
            <div className="info-block">
              <strong>Остановлено</strong>
              <span>{farms.filter((f) => f.status === "stopped").length}</span>
            </div>
          </div>
        </article>
      </section>

      {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

      {/* Farm list */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Список ферм</div>
            <h2>Ваши фермы</h2>
          </div>
          <div className="badge-row">
            <button className="primary-button" type="button" onClick={() => setShowCreateModal(true)}>
              + Создать ферму
            </button>
          </div>
        </div>
        {farms.length ? (
          <div className="creative-list">
            {farms.map((farm) => (
              <div
                key={farm.id}
                className={`creative-item ${selectedFarm?.id === farm.id ? "selected" : ""}`}
                style={{ cursor: "pointer" }}
                onClick={() => void selectFarm(farm)}
              >
                <div className="thread-meta">
                  <strong>{farm.name}</strong>
                  <span className={`pill ${statusBadgeClass(farm.status)}`}>
                    {STATUS_LABELS[farm.status] ?? farm.status}
                  </span>
                  <span className="muted">
                    Тон: {farm.comment_tone} · Язык: {farm.comment_language} · {farm.comment_percentage}% постов
                  </span>
                  <span className="muted">Защита: {farm.ai_protection_mode}</span>
                </div>
                {selectedFarm?.id === farm.id ? (
                  <div className="actions-row" style={{ marginTop: 8 }} onClick={(e) => e.stopPropagation()}>
                    <button
                      className="primary-button"
                      type="button"
                      disabled={busy || farm.status === "running"}
                      onClick={() => {
                        setShowStartModal(true);
                        setSelectedAccountIds([]);
                        setSelectedChannelDbId(channelDbs[0]?.id ?? null);
                      }}
                    >
                      Запустить
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={busy || farm.status !== "running"}
                      onClick={() => void handleFarmAction("pause")}
                    >
                      Пауза
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={busy || farm.status !== "paused"}
                      onClick={() => void handleFarmAction("resume")}
                    >
                      Продолжить
                    </button>
                    <button
                      className="ghost-button"
                      type="button"
                      disabled={busy || farm.status === "stopped"}
                      onClick={() => void handleFarmAction("stop")}
                    >
                      Остановить
                    </button>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">Нет ферм. Создайте первую ферму, чтобы начать комментирование.</p>
        )}
      </section>

      {/* Farm detail — threads */}
      {selectedFarm ? (
        <section className="panel wide">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Потоки фермы</div>
              <h2>{selectedFarm.name} — потоки</h2>
            </div>
            <div className="badge-row">
              <span className="pill">{threads.length} потоков</span>
            </div>
          </div>
          {threads.length ? (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Аккаунт</th>
                    <th>Статус</th>
                    <th>Каналов</th>
                    <th>Комментарии</th>
                    <th>Ошибки</th>
                    <th>Здоровье</th>
                    <th>Последний комментарий</th>
                  </tr>
                </thead>
                <tbody>
                  {threads.map((t) => (
                    <tr key={t.id}>
                      <td>{t.thread_index + 1}</td>
                      <td>{t.account_phone ?? `Аккаунт #${t.account_id}`}</td>
                      <td>
                        <span className={`pill ${statusBadgeClass(t.status)}`}>
                          {STATUS_LABELS[t.status] ?? t.status}
                        </span>
                      </td>
                      <td>{Array.isArray(t.assigned_channels) ? t.assigned_channels.length : 0}</td>
                      <td>{t.stats_comments_sent}</td>
                      <td>{t.stats_comments_failed}</td>
                      <td style={{ minWidth: 100 }}>
                        <HealthBar score={t.health_score} />
                      </td>
                      <td>{t.stats_last_comment_at ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">Потоки появятся после запуска фермы.</p>
          )}
        </section>
      ) : null}

      {/* Farm detail — event log */}
      {selectedFarm ? (
        <section className="panel wide">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Лог событий</div>
              <h2>Последние события</h2>
            </div>
            <div className="badge-row">
              <button
                className="ghost-button"
                type="button"
                onClick={() => void loadFarmDetail(selectedFarm.id)}
                disabled={busy}
              >
                Обновить
              </button>
            </div>
          </div>
          <div
            className="thread-list"
            style={{ maxHeight: 320, overflowY: "auto", fontFamily: "monospace", fontSize: 12 }}
          >
            {events.length ? (
              events.map((ev) => (
                <div key={ev.id} className={`thread-item ${SEVERITY_CLASS[ev.severity] ?? ""}`}>
                  <div className="thread-meta">
                    <span className={`pill ${statusBadgeClass(ev.severity === "error" ? "error" : ev.severity === "warn" ? "paused" : "running")}`}>
                      {ev.severity.toUpperCase()}
                    </span>
                    <span>{ev.event_type}</span>
                    {ev.thread_id ? <span>поток #{ev.thread_id}</span> : null}
                    <span className="muted">{ev.created_at ?? "—"}</span>
                  </div>
                  <p style={{ margin: 0 }}>{ev.message ?? "—"}</p>
                </div>
              ))
            ) : (
              <p className="muted">Нет событий. Запустите ферму для получения лога в реальном времени.</p>
            )}
          </div>
        </section>
      ) : null}

      {/* Auto-responder */}
      {selectedFarm ? (
        <section className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Авто-ответчик</div>
              <h2>Настройки авто-ответчика</h2>
            </div>
          </div>
          <label className="field" style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <input
              type="checkbox"
              checked={selectedFarm.auto_responder_enabled}
              onChange={async () => {
                if (!accessToken) return;
                try {
                  await farmApi.update(accessToken, selectedFarm.id, {
                    auto_responder_enabled: !selectedFarm.auto_responder_enabled,
                  });
                  await loadFarms();
                  setSelectedFarm((prev) =>
                    prev ? { ...prev, auto_responder_enabled: !prev.auto_responder_enabled } : prev
                  );
                } catch {
                  setStatusMessage("Не удалось обновить авто-ответчик.");
                }
              }}
            />
            <span>Включить авто-ответчик на упоминания</span>
          </label>
          {selectedFarm.auto_responder_enabled ? (
            <div className="stack-form" style={{ marginTop: 12 }}>
              <textarea
                className="assistant-textarea"
                defaultValue={selectedFarm.auto_responder_prompt ?? ""}
                placeholder="Промпт для авто-ответа на упоминания и реплаи..."
                onBlur={async (e) => {
                  if (!accessToken) return;
                  try {
                    await farmApi.update(accessToken, selectedFarm.id, {
                      auto_responder_prompt: e.target.value,
                    });
                  } catch {
                    setStatusMessage("Не удалось сохранить промпт.");
                  }
                }}
              />
            </div>
          ) : null}
        </section>
      ) : null}

      {/* Create farm modal */}
      {showCreateModal ? (
        <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div>
                <div className="eyebrow">Новая ферма</div>
                <h2>Создать ферму</h2>
              </div>
            </div>
            <div className="stack-form">
              <label className="field">
                <span>Название фермы</span>
                <input
                  value={farmName}
                  onChange={(e) => setFarmName(e.target.value)}
                  placeholder="Например: Основная нейрокомментинг ферма"
                />
              </label>
              <label className="field">
                <span>Промпт для комментариев</span>
                <textarea
                  className="assistant-textarea"
                  value={commentPrompt}
                  onChange={(e) => setCommentPrompt(e.target.value)}
                  placeholder="Опишите стиль и тематику комментариев..."
                />
              </label>
              <label className="field">
                <span>Тональность</span>
                <select value={commentTone} onChange={(e) => setCommentTone(e.target.value)}>
                  {TONE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Язык комментариев</span>
                <select value={commentLanguage} onChange={(e) => setCommentLanguage(e.target.value)}>
                  {LANGUAGE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>AI-защита</span>
                <select value={aiProtectionMode} onChange={(e) => setAiProtectionMode(e.target.value)}>
                  {PROTECTION_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Процент постов для комментирования: {commentPercentage}%</span>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={commentPercentage}
                  onChange={(e) => setCommentPercentage(Number(e.target.value))}
                />
              </label>
              <div className="two-column-grid" style={{ gap: 12 }}>
                <label className="field">
                  <span>Задержка перед комментарием, мин (сек)</span>
                  <input
                    type="number"
                    min={0}
                    value={delayMin}
                    onChange={(e) => setDelayMin(Number(e.target.value))}
                  />
                </label>
                <label className="field">
                  <span>Задержка перед комментарием, макс (сек)</span>
                  <input
                    type="number"
                    min={0}
                    value={delayMax}
                    onChange={(e) => setDelayMax(Number(e.target.value))}
                  />
                </label>
              </div>
              <div className="actions-row">
                <button className="primary-button" type="button" disabled={busy} onClick={() => void handleCreateFarm()}>
                  {busy ? "Создаём…" : "Создать ферму"}
                </button>
                <button className="ghost-button" type="button" onClick={() => setShowCreateModal(false)}>
                  Отмена
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {/* Start farm modal */}
      {showStartModal && selectedFarm ? (
        <div className="modal-overlay" onClick={() => setShowStartModal(false)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div>
                <div className="eyebrow">Запуск фермы</div>
                <h2>Запустить: {selectedFarm.name}</h2>
              </div>
            </div>
            <div className="stack-form">
              <div className="field">
                <span>Выберите аккаунты ({selectedAccountIds.length} выбрано)</span>
                <div className="thread-list" style={{ maxHeight: 200, overflowY: "auto" }}>
                  {accounts.map((acc) => (
                    <label
                      key={acc.id}
                      style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", cursor: "pointer" }}
                    >
                      <input
                        type="checkbox"
                        checked={selectedAccountIds.includes(acc.id)}
                        onChange={() => toggleAccountSelection(acc.id)}
                      />
                      <span>{acc.phone}</span>
                      <span className={`pill ${statusBadgeClass(acc.health_status === "alive" ? "running" : "stopped")}`}>
                        {acc.health_status}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
              <label className="field">
                <span>База каналов</span>
                <select
                  value={selectedChannelDbId ?? ""}
                  onChange={(e) => setSelectedChannelDbId(Number(e.target.value))}
                >
                  <option value="">— выберите базу —</option>
                  {channelDbs.map((db) => (
                    <option key={db.id} value={db.id}>
                      {db.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="inline-note">
                Ферма создаст по одному потоку на каждый выбранный аккаунт и равномерно распределит каналы.
              </div>
              <div className="actions-row">
                <button
                  className="primary-button"
                  type="button"
                  disabled={busy || !selectedAccountIds.length || !selectedChannelDbId}
                  onClick={() => void handleStartFarm()}
                >
                  {busy ? "Запускаем…" : "Запустить ферму"}
                </button>
                <button className="ghost-button" type="button" onClick={() => setShowStartModal(false)}>
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
