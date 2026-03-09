import { useEffect, useState } from "react";
import { apiFetch } from "../api";
import { useAuth } from "../auth";

type ContextResponse = {
  brief: {
    product_name: string;
    offer_summary: string;
    target_audience: string;
    competitors: string[];
    tone_of_voice: string;
    pain_points: string[];
    telegram_goals: string[];
    website_url: string;
    channel_url: string;
    bot_url: string;
    summary_text: string;
    completeness_score: number;
    missing_fields: string[];
    assistant_ready: boolean;
    status: string;
    confirmed_at: string | null;
    assets_count: number;
    draft_count: number;
  };
};

export function ContextPage() {
  const { accessToken } = useAuth();
  const [context, setContext] = useState<ContextResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  const loadContext = async () => {
    if (!accessToken) {
      return;
    }
    const payload = await apiFetch<ContextResponse>("/v1/context", { accessToken });
    setContext(payload);
  };

  useEffect(() => {
    void loadContext().catch(() => setContext(null));
  }, [accessToken]);

  const confirm = async () => {
    if (!accessToken) {
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      const payload = await apiFetch<ContextResponse>("/v1/context/confirm", {
        method: "POST",
        accessToken,
      });
      setContext(payload);
      setStatusMessage("Контекст подтверждён и сохранён как основа для следующих AI-черновиков.");
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "context_confirm_failed");
    } finally {
      setBusy(false);
    }
  };

  const brief = context?.brief;

  return (
    <div className="page-grid">
      <section className="hero-panel">
        <div className="eyebrow">Business context</div>
        <h1>Контекст бизнеса, который будет помнить ассистент</h1>
        <p>
          Здесь живёт утверждённый growth-brief: продукт, оффер, ЦА, тональность, цели в Telegram и ссылки на основные assets.
          Это источник правды для следующих черновиков и рекомендаций.
        </p>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Readiness</div>
            <h2>Статус контекста</h2>
          </div>
          <div className="badge-row">
            <span className="pill">Готовность: {Math.round(Number(brief?.completeness_score || 0) * 100)}%</span>
            <span className="pill">{brief?.confirmed_at ? "Подтверждён" : "Ещё не подтверждён"}</span>
          </div>
        </div>
        {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}
        <div className="info-split">
          <div className="info-block">
            <strong>Что делает система</strong>
            <p className="muted">Собирает и хранит единый business context, чтобы drafts и рекомендации не теряли логику бренда.</p>
          </div>
          <div className="info-block">
            <strong>Что делает оператор</strong>
            <p className="muted">Проверяет summary, исправляет смысловые ошибки и подтверждает только уже осмысленный brief.</p>
          </div>
        </div>
        <div className="actions-row">
          <button className="secondary-button" type="button" disabled={busy} onClick={() => void confirm()}>
            Подтвердить текущий контекст
          </button>
        </div>
        <div className="inline-note">
          Не хватает: {(brief?.missing_fields || []).length ? brief!.missing_fields.join(", ") : "обязательные поля уже собраны"}
        </div>
      </section>

      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Summary</div>
              <h2>Сводка brief</h2>
            </div>
          </div>
          <div className="context-item">
            <p>{brief?.summary_text || "Сначала ответьте ассистенту, чтобы здесь появилась сводка бизнеса."}</p>
          </div>
        </article>

        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Assets</div>
              <h2>Что уже готово</h2>
            </div>
          </div>
          <div className="status-grid">
            <div className="info-block">
              <strong>Approved assets</strong>
              <span>{brief?.assets_count || 0}</span>
            </div>
            <div className="info-block">
              <strong>Drafts</strong>
              <span>{brief?.draft_count || 0}</span>
            </div>
            <div className="info-block">
              <strong>Assistant ready</strong>
              <span>{brief?.assistant_ready ? "Да" : "Пока нет"}</span>
            </div>
          </div>
        </article>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Fields</div>
            <h2>Подробные поля контекста</h2>
          </div>
        </div>
        <div className="field-list">
          <div className="field-row">
            <strong>Продукт</strong>
            <span className="field-value">{brief?.product_name || "—"}</span>
          </div>
          <div className="field-row">
            <strong>Оффер</strong>
            <span className="field-value">{brief?.offer_summary || "—"}</span>
          </div>
          <div className="field-row">
            <strong>Целевая аудитория</strong>
            <span className="field-value">{brief?.target_audience || "—"}</span>
          </div>
          <div className="field-row">
            <strong>Тон коммуникации</strong>
            <span className="field-value">{brief?.tone_of_voice || "—"}</span>
          </div>
          <div className="field-row">
            <strong>Конкуренты</strong>
            <span className="field-value">{(brief?.competitors || []).join(", ") || "—"}</span>
          </div>
          <div className="field-row">
            <strong>Боли клиентов</strong>
            <span className="field-value">{(brief?.pain_points || []).join(", ") || "—"}</span>
          </div>
          <div className="field-row">
            <strong>Цели в Telegram</strong>
            <span className="field-value">{(brief?.telegram_goals || []).join(", ") || "—"}</span>
          </div>
          <div className="field-row">
            <strong>Сайт</strong>
            <span className="field-value">{brief?.website_url || "—"}</span>
          </div>
          <div className="field-row">
            <strong>Канал</strong>
            <span className="field-value">{brief?.channel_url || "—"}</span>
          </div>
          <div className="field-row">
            <strong>Бот</strong>
            <span className="field-value">{brief?.bot_url || "—"}</span>
          </div>
        </div>
      </section>
    </div>
  );
}
