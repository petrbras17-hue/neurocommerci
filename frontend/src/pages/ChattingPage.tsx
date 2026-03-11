import { useEffect, useState } from "react";
import { apiFetch, chattingApi, ChattingConfig } from "../api";
import { useAuth } from "../auth";

const MODE_OPTIONS = [
  { value: "comment_reply", label: "Ответы на комментарии" },
  { value: "post_reaction", label: "Реакция на посты" },
  { value: "custom", label: "Свой режим" },
];

function statusBadgeClass(status: string): string {
  if (status === "running") return "badge-green";
  if (status === "paused") return "badge-yellow";
  if (status === "error") return "badge-red";
  return "badge-gray";
}

const STATUS_LABELS: Record<string, string> = {
  running: "Активен",
  stopped: "Остановлен",
  paused: "На паузе",
  error: "Ошибка",
};

export function ChattingPage() {
  const { accessToken } = useAuth();
  const [configs, setConfigs] = useState<ChattingConfig[]>([]);
  const [selected, setSelected] = useState<ChattingConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);

  // form state
  const [name, setName] = useState("");
  const [mode, setMode] = useState("comment_reply");
  const [targetChannels, setTargetChannels] = useState("");
  const [promptTemplate, setPromptTemplate] = useState("");
  const [maxMessagesPerHour, setMaxMessagesPerHour] = useState(10);
  const [minDelay, setMinDelay] = useState(30);
  const [maxDelay, setMaxDelay] = useState(120);

  const loadConfigs = async () => {
    if (!accessToken) return;
    try {
      const payload = await chattingApi.list(accessToken);
      setConfigs(payload.items);
    } catch {
      // silent
    }
  };

  useEffect(() => { void loadConfigs().catch(() => {}); }, [accessToken]);

  const handleCreate = async () => {
    if (!accessToken || !name.trim()) { setStatusMessage("Введите название конфига."); return; }
    setBusy(true);
    setStatusMessage("");
    try {
      const channels = targetChannels.split("\n").map((s) => s.trim()).filter(Boolean);
      await chattingApi.create(accessToken, {
        name: name.trim(),
        mode,
        target_channels: channels,
        prompt_template: promptTemplate.trim() || null,
        max_messages_per_hour: maxMessagesPerHour,
        min_delay_seconds: minDelay,
        max_delay_seconds: maxDelay,
      });
      setShowCreateModal(false);
      setName("");
      setTargetChannels("");
      setPromptTemplate("");
      setStatusMessage("Конфиг создан.");
      await loadConfigs();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "create_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleAction = async (action: "start" | "stop" | "delete") => {
    if (!accessToken || !selected) return;
    setBusy(true);
    setStatusMessage("");
    try {
      if (action === "start") await chattingApi.start(accessToken, selected.id);
      else if (action === "stop") await chattingApi.stop(accessToken, selected.id);
      else if (action === "delete") {
        await chattingApi.delete(accessToken, selected.id);
        setSelected(null);
      }
      const actionLabel = action === "start" ? "запущен" : action === "stop" ? "остановлен" : "удалён";
      setStatusMessage(`Конфиг ${actionLabel}.`);
      await loadConfigs();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "action_failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-grid">
      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Neuro Chatting</div>
              <h2>Нейрочаттинг</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Конфиг определяет набор каналов, режим и AI-промпт для автоматических сообщений.</li>
            <li>Каждый конфиг независимо запускается и останавливается.</li>
            <li>Лимит — количество сообщений в час на весь конфиг.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Статус</div>
              <h2>Конфиги</h2>
            </div>
          </div>
          <div className="status-grid">
            <div className="info-block"><strong>Всего</strong><span>{configs.length}</span></div>
            <div className="info-block"><strong>Активных</strong><span>{configs.filter((c) => c.status === "running").length}</span></div>
            <div className="info-block"><strong>Остановлено</strong><span>{configs.filter((c) => c.status === "stopped").length}</span></div>
            <div className="info-block"><strong>Ошибок</strong><span>{configs.filter((c) => c.status === "error").length}</span></div>
          </div>
        </article>
      </section>

      {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Список конфигов</div>
            <h2>Ваши конфиги чаттинга</h2>
          </div>
          <div className="badge-row">
            <button className="primary-button" type="button" onClick={() => setShowCreateModal(true)}>
              + Создать конфиг
            </button>
          </div>
        </div>
        {configs.length ? (
          <div className="creative-list">
            {configs.map((cfg) => (
              <div
                key={cfg.id}
                className={`creative-item ${selected?.id === cfg.id ? "selected" : ""}`}
                style={{ cursor: "pointer" }}
                onClick={() => setSelected(selected?.id === cfg.id ? null : cfg)}
              >
                <div className="thread-meta">
                  <strong>{cfg.name}</strong>
                  <span className={`pill ${statusBadgeClass(cfg.status)}`}>{STATUS_LABELS[cfg.status] ?? cfg.status}</span>
                  <span className="muted">Режим: {cfg.mode}</span>
                  <span className="muted">Каналов: {cfg.target_channels.length} · {cfg.max_messages_per_hour} сообщ/ч</span>
                </div>
                {selected?.id === cfg.id ? (
                  <div className="actions-row" style={{ marginTop: 8 }} onClick={(e) => e.stopPropagation()}>
                    <button
                      className="primary-button"
                      type="button"
                      disabled={busy || cfg.status === "running"}
                      onClick={() => void handleAction("start")}
                    >
                      Запустить
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={busy || cfg.status !== "running"}
                      onClick={() => void handleAction("stop")}
                    >
                      Остановить
                    </button>
                    <button
                      className="ghost-button"
                      type="button"
                      disabled={busy}
                      onClick={() => void handleAction("delete")}
                    >
                      Удалить
                    </button>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">Нет конфигов. Создайте первый конфиг нейрочаттинга.</p>
        )}
      </section>

      {showCreateModal ? (
        <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div>
                <div className="eyebrow">Новый конфиг</div>
                <h2>Создать конфиг чаттинга</h2>
              </div>
            </div>
            <div className="stack-form">
              <label className="field">
                <span>Название</span>
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Например: Комментинг в топ-каналах" />
              </label>
              <label className="field">
                <span>Режим</span>
                <select value={mode} onChange={(e) => setMode(e.target.value)}>
                  {MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label className="field">
                <span>Целевые каналы (каждый с новой строки)</span>
                <textarea
                  className="assistant-textarea"
                  value={targetChannels}
                  onChange={(e) => setTargetChannels(e.target.value)}
                  placeholder="@channel1&#10;@channel2"
                  rows={4}
                />
              </label>
              <label className="field">
                <span>AI-промпт (необязательно)</span>
                <textarea
                  className="assistant-textarea"
                  value={promptTemplate}
                  onChange={(e) => setPromptTemplate(e.target.value)}
                  placeholder="Опишите стиль и содержание сообщений..."
                  rows={3}
                />
              </label>
              <label className="field">
                <span>Максимум сообщений в час: {maxMessagesPerHour}</span>
                <input
                  type="range"
                  min={1}
                  max={60}
                  value={maxMessagesPerHour}
                  onChange={(e) => setMaxMessagesPerHour(Number(e.target.value))}
                />
              </label>
              <div className="two-column-grid" style={{ gap: 12 }}>
                <label className="field">
                  <span>Мин. задержка (сек)</span>
                  <input type="number" min={1} value={minDelay} onChange={(e) => setMinDelay(Number(e.target.value))} />
                </label>
                <label className="field">
                  <span>Макс. задержка (сек)</span>
                  <input type="number" min={1} value={maxDelay} onChange={(e) => setMaxDelay(Number(e.target.value))} />
                </label>
              </div>
              <div className="actions-row">
                <button className="primary-button" type="button" disabled={busy} onClick={() => void handleCreate()}>
                  {busy ? "Создаём…" : "Создать конфиг"}
                </button>
                <button className="ghost-button" type="button" onClick={() => setShowCreateModal(false)}>
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
