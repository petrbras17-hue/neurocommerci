import { useEffect, useState } from "react";
import { apiFetch, dialogsApi, DialogConfig } from "../api";
import { useAuth } from "../auth";

type AccountRow = { id: number; phone: string; status: string; health_status: string };

const DIALOG_TYPES = [
  { value: "warmup", label: "Прогрев" },
  { value: "engagement", label: "Вовлечение" },
  { value: "support", label: "Поддержка" },
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

export function DialogsPage() {
  const { accessToken } = useAuth();
  const [dialogs, setDialogs] = useState<DialogConfig[]>([]);
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [selected, setSelected] = useState<DialogConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);

  // form state
  const [name, setName] = useState("");
  const [dialogType, setDialogType] = useState("warmup");
  const [promptTemplate, setPromptTemplate] = useState("");
  const [messagesPerSession, setMessagesPerSession] = useState(5);
  const [sessionIntervalHours, setSessionIntervalHours] = useState(24);
  // pairs: each pair is two account IDs
  const [pairA, setPairA] = useState<number | "">("");
  const [pairB, setPairB] = useState<number | "">("");
  const [pairs, setPairs] = useState<number[][]>([]);

  const loadDialogs = async () => {
    if (!accessToken) return;
    try {
      const payload = await dialogsApi.list(accessToken);
      setDialogs(payload.items);
    } catch {
      // silent
    }
  };

  const loadAccounts = async () => {
    if (!accessToken) return;
    try {
      const payload = await apiFetch<{ items: AccountRow[]; total: number }>("/v1/web/accounts", { accessToken });
      setAccounts(payload.items);
    } catch {
      // silent
    }
  };

  useEffect(() => { void Promise.all([loadDialogs(), loadAccounts()]).catch(() => {}); }, [accessToken]);

  const addPair = () => {
    if (pairA === "" || pairB === "") { setStatusMessage("Выберите оба аккаунта для пары."); return; }
    if (pairA === pairB) { setStatusMessage("Нельзя создать пару из одного аккаунта."); return; }
    setPairs((prev) => [...prev, [Number(pairA), Number(pairB)]]);
    setPairA("");
    setPairB("");
    setStatusMessage("");
  };

  const removePair = (idx: number) => {
    setPairs((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleCreate = async () => {
    if (!accessToken || !name.trim()) { setStatusMessage("Введите название."); return; }
    if (!pairs.length) { setStatusMessage("Добавьте хотя бы одну пару аккаунтов."); return; }
    setBusy(true);
    setStatusMessage("");
    try {
      await dialogsApi.create(accessToken, {
        name: name.trim(),
        dialog_type: dialogType,
        account_pairs: pairs,
        prompt_template: promptTemplate.trim() || null,
        messages_per_session: messagesPerSession,
        session_interval_hours: sessionIntervalHours,
      });
      setShowCreateModal(false);
      setName("");
      setPromptTemplate("");
      setPairs([]);
      setStatusMessage("Диалог создан.");
      await loadDialogs();
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
      if (action === "start") await dialogsApi.start(accessToken, selected.id);
      else if (action === "stop") await dialogsApi.stop(accessToken, selected.id);
      else if (action === "delete") {
        await dialogsApi.delete(accessToken, selected.id);
        setSelected(null);
      }
      const actionLabel = action === "start" ? "запущен" : action === "stop" ? "остановлен" : "удалён";
      setStatusMessage(`Диалог ${actionLabel}.`);
      await loadDialogs();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "action_failed");
    } finally {
      setBusy(false);
    }
  };

  const phoneById = (id: number) => accounts.find((a) => a.id === id)?.phone ?? `#${id}`;

  return (
    <div className="page-grid">
      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Neuro Dialogs</div>
              <h2>Нейродиалоги</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Диалог — это сценарий переписки между двумя аккаунтами.</li>
            <li>Используется для прогрева, вовлечения и имитации живого общения.</li>
            <li>AI-промпт задаёт тематику и стиль общения пары.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Статус</div>
              <h2>Текущие диалоги</h2>
            </div>
          </div>
          <div className="status-grid">
            <div className="info-block"><strong>Всего</strong><span>{dialogs.length}</span></div>
            <div className="info-block"><strong>Активных</strong><span>{dialogs.filter((d) => d.status === "running").length}</span></div>
            <div className="info-block"><strong>Остановлено</strong><span>{dialogs.filter((d) => d.status === "stopped").length}</span></div>
            <div className="info-block"><strong>Ошибок</strong><span>{dialogs.filter((d) => d.status === "error").length}</span></div>
          </div>
        </article>
      </section>

      {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Список диалогов</div>
            <h2>Ваши диалоги</h2>
          </div>
          <div className="badge-row">
            <button className="primary-button" type="button" onClick={() => setShowCreateModal(true)}>
              + Создать диалог
            </button>
          </div>
        </div>
        {dialogs.length ? (
          <div className="creative-list">
            {dialogs.map((dlg) => (
              <div
                key={dlg.id}
                className={`creative-item ${selected?.id === dlg.id ? "selected" : ""}`}
                style={{ cursor: "pointer" }}
                onClick={() => setSelected(selected?.id === dlg.id ? null : dlg)}
              >
                <div className="thread-meta">
                  <strong>{dlg.name}</strong>
                  <span className={`pill ${statusBadgeClass(dlg.status)}`}>{STATUS_LABELS[dlg.status] ?? dlg.status}</span>
                  <span className="muted">Тип: {dlg.dialog_type}</span>
                  <span className="muted">Пар: {dlg.account_pairs.length} · {dlg.messages_per_session} сообщ/сессию</span>
                </div>
                {selected?.id === dlg.id ? (
                  <div className="actions-row" style={{ marginTop: 8 }} onClick={(e) => e.stopPropagation()}>
                    <button
                      className="primary-button"
                      type="button"
                      disabled={busy || dlg.status === "running"}
                      onClick={() => void handleAction("start")}
                    >
                      Запустить
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={busy || dlg.status !== "running"}
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
          <p className="muted">Нет диалогов. Создайте первый нейродиалог.</p>
        )}
      </section>

      {showCreateModal ? (
        <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div>
                <div className="eyebrow">Новый диалог</div>
                <h2>Создать нейродиалог</h2>
              </div>
            </div>
            <div className="stack-form">
              <label className="field">
                <span>Название</span>
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Например: Прогрев пара A-B" />
              </label>
              <label className="field">
                <span>Тип диалога</span>
                <select value={dialogType} onChange={(e) => setDialogType(e.target.value)}>
                  {DIALOG_TYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label className="field">
                <span>AI-промпт (необязательно)</span>
                <textarea
                  className="assistant-textarea"
                  value={promptTemplate}
                  onChange={(e) => setPromptTemplate(e.target.value)}
                  placeholder="Опишите тематику и стиль переписки..."
                  rows={3}
                />
              </label>
              <div className="two-column-grid" style={{ gap: 12 }}>
                <label className="field">
                  <span>Сообщений за сессию</span>
                  <input type="number" min={1} max={50} value={messagesPerSession} onChange={(e) => setMessagesPerSession(Number(e.target.value))} />
                </label>
                <label className="field">
                  <span>Интервал сессий (часов)</span>
                  <input type="number" min={1} value={sessionIntervalHours} onChange={(e) => setSessionIntervalHours(Number(e.target.value))} />
                </label>
              </div>
              <div className="field">
                <span>Пары аккаунтов</span>
                <div className="two-column-grid" style={{ gap: 8, marginBottom: 8 }}>
                  <select value={pairA} onChange={(e) => setPairA(e.target.value === "" ? "" : Number(e.target.value))}>
                    <option value="">— Аккаунт A —</option>
                    {accounts.map((a) => <option key={a.id} value={a.id}>{a.phone}</option>)}
                  </select>
                  <select value={pairB} onChange={(e) => setPairB(e.target.value === "" ? "" : Number(e.target.value))}>
                    <option value="">— Аккаунт B —</option>
                    {accounts.map((a) => <option key={a.id} value={a.id}>{a.phone}</option>)}
                  </select>
                </div>
                <button className="secondary-button" type="button" onClick={addPair} style={{ marginBottom: 8 }}>
                  + Добавить пару
                </button>
                {pairs.length > 0 ? (
                  <div className="thread-list" style={{ maxHeight: 160, overflowY: "auto" }}>
                    {pairs.map((pair, idx) => (
                      <div key={idx} className="thread-item" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span>{phoneById(pair[0])} ↔ {phoneById(pair[1])}</span>
                        <button className="ghost-button" type="button" onClick={() => removePair(idx)} style={{ fontSize: 12 }}>
                          Удалить
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="muted" style={{ fontSize: 13 }}>Нет пар. Добавьте хотя бы одну.</p>
                )}
              </div>
              <div className="actions-row">
                <button className="primary-button" type="button" disabled={busy} onClick={() => void handleCreate()}>
                  {busy ? "Создаём…" : "Создать диалог"}
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
