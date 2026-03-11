import { FormEvent, useEffect, useState } from "react";
import { apiFetch, JobStatusResponse, pollJob } from "../api";
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

type QualitySummary = {
  overview: {
    total_requests: number;
    avg_quality_score: number;
    fallback_rate: number;
    repair_rate: number;
  };
  latest_by_task: Record<
    string,
    {
      provider: string | null;
      model: string | null;
      quality_score: number;
      fallback_used: boolean;
      repair_applied: boolean;
      latency_ms: number | null;
    }
  >;
};

export function AssistantPage() {
  const { accessToken } = useAuth();
  const [thread, setThread] = useState<ThreadResponse | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [jobState, setJobState] = useState<JobStatusResponse | null>(null);
  const [quality, setQuality] = useState<QualitySummary | null>(null);

  const loadThread = async () => {
    if (!accessToken) {
      return;
    }
    const payload = await apiFetch<ThreadResponse>("/v1/assistant/thread", { accessToken });
    setThread(payload);
  };

  const loadQuality = async () => {
    if (!accessToken) {
      return;
    }
    const payload = await apiFetch<QualitySummary>("/v1/ai/quality-summary", { accessToken });
    setQuality(payload);
  };

  useEffect(() => {
    void loadThread().catch(() => setThread(null));
    void loadQuality().catch(() => setQuality(null));
  }, [accessToken]);

  const runQueuedAction = async (path: string, json?: unknown, successMessage?: string) => {
    if (!accessToken) {
      return;
    }
    setBusy(true);
    setStatusMessage("");
    setJobState(null);
    try {
      const payload = await apiFetch<{ job_id: number; status: string }>(path, {
        method: "POST",
        accessToken,
        json,
      });
      setJobState({
        id: payload.job_id,
        job_type: path,
        status: "queued",
        created_at: null,
        started_at: null,
        completed_at: null,
        error_code: null,
        result_summary: {},
      });
      setStatusMessage("Задача поставлена в очередь. Ждём завершения фоновой обработки.");
      const job = await pollJob(accessToken, payload.job_id, { timeoutMs: 45000, intervalMs: 1200 });
      setJobState(job);
      if (job.status === "failed") {
        throw new Error(job.error_code || "job_failed");
      }
      await Promise.all([loadThread(), loadQuality()]);
      setStatusMessage(successMessage || "Фоновая задача завершена успешно.");
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "assistant_job_failed");
    } finally {
      setBusy(false);
    }
  };

  const startBrief = async () => {
    await runQueuedAction("/v1/assistant/start-brief", undefined, "Ассистент запустил growth-brief и обновил следующий шаг.");
  };

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault();
    if (!accessToken || !message.trim()) {
      setStatusMessage("Введите сообщение для ассистента.");
      return;
    }
    await runQueuedAction("/v1/assistant/message", { message }, "Ответ обработан. Ассистент обновил контекст и рекомендации.");
    setMessage("");
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
        {jobState ? (
          <div className="inline-note">
            Последняя job: #{jobState.id} · {jobState.status}
            {jobState.error_code ? ` · ${jobState.error_code}` : ""}
          </div>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="eyebrow">AI quality</div>
            <h2>Качество последней генерации</h2>
          </div>
        </div>
        <div className="status-grid">
          <div className="info-block">
            <strong>Средний score</strong>
            <span>{quality?.overview?.avg_quality_score ?? 0}</span>
          </div>
          <div className="info-block">
            <strong>Fallback rate</strong>
            <span>{Math.round(Number(quality?.overview?.fallback_rate || 0) * 100)}%</span>
          </div>
          <div className="info-block">
            <strong>Repair rate</strong>
            <span>{Math.round(Number(quality?.overview?.repair_rate || 0) * 100)}%</span>
          </div>
        </div>
        <div className="field-list">
          {["brief_extraction", "assistant_reply"].map((task) => {
            const item = quality?.latest_by_task?.[task];
            if (!item) {
              return null;
            }
            return (
              <div className="field-row" key={task}>
                <strong>{task}</strong>
                <span className="field-value">
                  {item.provider || "—"} / {item.model || "—"} · score {item.quality_score ?? 0}
                  {item.fallback_used ? " · fallback" : ""}
                  {item.repair_applied ? " · repair" : ""}
                  {item.latency_ms ? ` · ${item.latency_ms}ms` : ""}
                </span>
              </div>
            );
          })}
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
