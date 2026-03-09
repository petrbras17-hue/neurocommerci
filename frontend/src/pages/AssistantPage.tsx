import { FormEvent, useEffect, useState } from "react";
import { apiFetch } from "../api";
import { useAuth } from "../auth";

type ThreadMessage = {
  id: number;
  role: string;
  content: string;
  created_at: string | null;
};

type ThreadResponse = {
  thread: {
    id: number | null;
    status: string;
    started_at: string | null;
  } | null;
  messages: ThreadMessage[];
  recommendations: Array<{ id: number; title: string; body: string; status: string }>;
  brief: {
    completeness_score: number;
    assistant_ready: boolean;
    missing_fields: string[];
    status: string;
  };
};

export function AssistantPage() {
  const { accessToken } = useAuth();
  const [thread, setThread] = useState<ThreadResponse | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  const loadThread = async () => {
    if (!accessToken) {
      return;
    }
    const payload = await apiFetch<ThreadResponse>("/v1/assistant/thread", { accessToken });
    setThread(payload);
  };

  useEffect(() => {
    void loadThread().catch(() => setThread(null));
  }, [accessToken]);

  const startBrief = async () => {
    if (!accessToken) {
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      const payload = await apiFetch<ThreadResponse>("/v1/assistant/start-brief", {
        method: "POST",
        accessToken,
      });
      setThread(payload);
      setStatusMessage("Ассистент запустил growth-brief и ждёт вашего ответа.");
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "assistant_start_failed");
    } finally {
      setBusy(false);
    }
  };

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault();
    if (!accessToken || !message.trim()) {
      setStatusMessage("Введите сообщение для ассистента.");
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      const payload = await apiFetch<ThreadResponse>("/v1/assistant/message", {
        method: "POST",
        accessToken,
        json: { message },
      });
      setThread(payload);
      setMessage("");
      setStatusMessage("Ответ сохранён. Ассистент обновил контекст и следующий шаг.");
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "assistant_message_failed");
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
              <div className="eyebrow">AI assistant</div>
              <h2>Что делает ассистент</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Собирает growth-brief по продукту, офферу, ЦА и Telegram-целям.</li>
            <li>Подсказывает, чего не хватает для стратегии, черновиков и визуалов.</li>
            <li>Ничего не делает молча: каждое крупное решение остаётся за оператором.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Operator role</div>
              <h2>Что отвечаете вы</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Описываете продукт простым языком, без идеальной структуры.</li>
            <li>Подтверждаете summary и корректируете спорные формулировки.</li>
            <li>Решаете, какие рекомендации и creative drafts реально переходят в работу.</li>
          </ul>
        </article>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Brief status</div>
            <h2>Текущий growth-brief</h2>
          </div>
          <div className="badge-row">
            <span className="pill">Готовность: {Math.round(Number(thread?.brief?.completeness_score || 0) * 100)}%</span>
            <span className="pill">{thread?.brief?.assistant_ready ? "Можно переходить к креативу" : "Нужны ещё ответы"}</span>
          </div>
        </div>
        {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}
        <div className="actions-row">
          <button className="primary-button" type="button" disabled={busy} onClick={() => void startBrief()}>
            {busy ? "Запускаем…" : "Запустить или обновить brief"}
          </button>
        </div>
        <div className="inline-note">
          Не хватает: {(thread?.brief?.missing_fields || []).length ? (thread?.brief?.missing_fields || []).join(", ") : "brief уже достаточно заполнен"}
        </div>
      </section>

      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Dialogue</div>
              <h2>Диалог с ассистентом</h2>
            </div>
          </div>
          <div className="thread-list">
            {(thread?.messages || []).length ? (
              thread!.messages.map((item) => (
                <div className={`thread-item ${item.role === "assistant" ? "assistant" : "user"}`} key={item.id}>
                  <div className="thread-meta">
                    <strong>{item.role === "assistant" ? "Ассистент" : "Вы"}</strong>
                    <span>{item.created_at || "—"}</span>
                  </div>
                  <p>{item.content}</p>
                </div>
              ))
            ) : (
              <p className="muted">Пока нет сообщений. Запустите growth-brief и ответьте на вопросы ассистента.</p>
            )}
          </div>
          <form className="stack-form" onSubmit={sendMessage}>
            <textarea
              className="assistant-textarea"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="Опишите продукт, оффер, аудиторию, цели в Telegram и тон общения."
            />
            <button className="secondary-button" type="submit" disabled={busy}>
              Отправить ответ ассистенту
            </button>
          </form>
        </article>

        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Recommendations</div>
              <h2>Следующие шаги</h2>
            </div>
          </div>
          <div className="thread-list">
            {(thread?.recommendations || []).length ? (
              thread!.recommendations.map((item) => (
                <div className="thread-item assistant" key={item.id}>
                  <div className="thread-meta">
                    <strong>{item.title}</strong>
                    <span>{item.status}</span>
                  </div>
                  <p>{item.body}</p>
                </div>
              ))
            ) : (
              <p className="muted">Когда brief начнёт заполняться, здесь появятся рекомендации по следующему шагу.</p>
            )}
          </div>
        </article>
      </section>
    </div>
  );
}
