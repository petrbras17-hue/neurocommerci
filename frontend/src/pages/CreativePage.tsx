import { useEffect, useState } from "react";
import { apiFetch, JobStatusResponse, pollJob } from "../api";
import { useAuth } from "../auth";

type CreativeItem = {
  id: number;
  draft_type: string;
  status: string;
  title: string;
  content_text: string;
  variants: Array<string | { title?: string; content?: string }>;
  selected_variant: number;
  created_at: string | null;
};

type CreativeResponse = {
  total: number;
  items: CreativeItem[];
};

type QualitySummary = {
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

const DRAFT_TYPES = [
  { value: "post", label: "Пост" },
  { value: "comment", label: "Комментарий" },
  { value: "ad_copy", label: "Рекламный текст" },
  { value: "image_prompt", label: "Промпт для изображения" },
];

function normalizeVariant(variant: string | { title?: string; content?: string }, index: number) {
  if (typeof variant === "string") {
    return {
      title: `Вариант ${index + 1}`,
      content: variant,
    };
  }
  return {
    title: variant?.title || `Вариант ${index + 1}`,
    content: variant?.content || "",
  };
}

export function CreativePage() {
  const { accessToken } = useAuth();
  const [creative, setCreative] = useState<CreativeResponse>({ total: 0, items: [] });
  const [draftType, setDraftType] = useState("post");
  const [statusMessage, setStatusMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [jobState, setJobState] = useState<JobStatusResponse | null>(null);
  const [quality, setQuality] = useState<QualitySummary | null>(null);

  const loadCreative = async () => {
    if (!accessToken) {
      return;
    }
    const payload = await apiFetch<CreativeResponse>("/v1/creative/drafts", { accessToken });
    setCreative(payload);
  };

  const loadQuality = async () => {
    if (!accessToken) {
      return;
    }
    const payload = await apiFetch<QualitySummary>("/v1/ai/quality-summary", { accessToken });
    setQuality(payload);
  };

  useEffect(() => {
    void loadCreative().catch(() => setCreative({ total: 0, items: [] }));
    void loadQuality().catch(() => setQuality(null));
  }, [accessToken]);

  const generate = async () => {
    if (!accessToken) {
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      const queued = await apiFetch<{ job_id: number; status: string }>("/v1/creative/generate", {
        method: "POST",
        accessToken,
        json: { draft_type: draftType, variant_count: 3 },
      });
      setJobState({
        id: queued.job_id,
        job_type: "creative_generate",
        status: "queued",
        created_at: null,
        started_at: null,
        completed_at: null,
        error_code: null,
        result_summary: {},
      });
      const job = await pollJob(accessToken, queued.job_id, { timeoutMs: 45000, intervalMs: 1200 });
      setJobState(job);
      if (job.status === "failed") {
        throw new Error(job.error_code || "creative_generate_failed");
      }
      setStatusMessage("Черновик создан. Проверьте варианты и утвердите только то, что отражает реальный бизнес-контекст.");
      await Promise.all([loadCreative(), loadQuality()]);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "creative_generate_failed");
    } finally {
      setBusy(false);
    }
  };

  const approve = async (draftId: number, selectedVariant: number) => {
    if (!accessToken) {
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      await apiFetch("/v1/creative/approve", {
        method: "POST",
        accessToken,
        json: { draft_id: draftId, selected_variant: selectedVariant },
      });
      setStatusMessage("Черновик утверждён и сохранён как approved asset.");
      await loadCreative();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "creative_approve_failed");
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
              <div className="eyebrow">Creative flow</div>
              <h2>Что делает система</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Генерирует черновики постов, комментариев, ad copy и image prompts из утверждённого контекста.</li>
            <li>Даёт несколько вариантов, чтобы оператор не зависел от одного ответа модели.</li>
            <li>Хранит approved assets отдельно от обычных draft-ов.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Creative flow</div>
              <h2>Что делает оператор</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Проверяет смысл, тональность и корректность обещаний.</li>
            <li>Выбирает конкретный вариант, который действительно подходит бренду.</li>
            <li>Не отправляет черновик дальше, пока он не выглядит как реальный рабочий материал.</li>
          </ul>
        </article>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Generate</div>
            <h2>Сгенерировать новый черновик</h2>
          </div>
        </div>
        {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}
        <div className="actions-row">
          <select value={draftType} onChange={(event) => setDraftType(event.target.value)}>
            {DRAFT_TYPES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <button className="primary-button" type="button" disabled={busy} onClick={() => void generate()}>
            {busy ? "Генерируем…" : "Сгенерировать 3 варианта"}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="eyebrow">AI quality</div>
            <h2>Качество генерации</h2>
          </div>
        </div>
        <div className="field-list">
          {(() => {
            const item = quality?.latest_by_task?.["creative_variants"];
            if (!item) {
              return <p className="muted">После первой генерации здесь появятся provider, quality score и fallback/repair.</p>;
            }
            return (
              <div className="field-row">
                <strong>Последний creative flow</strong>
                <span className="field-value">
                  {item.provider || "—"} / {item.model || "—"} · score {item.quality_score ?? 0}
                  {item.fallback_used ? " · fallback" : ""}
                  {item.repair_applied ? " · repair" : ""}
                  {item.latency_ms ? ` · ${item.latency_ms}ms` : ""}
                </span>
              </div>
            );
          })()}
        </div>
        {jobState ? (
          <div className="inline-note">
            Последняя job: #{jobState.id} · {jobState.status}
            {jobState.error_code ? ` · ${jobState.error_code}` : ""}
          </div>
        ) : null}
      </section>

      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Draft registry</div>
            <h2>Черновики и approved assets</h2>
          </div>
          <div className="badge-row">
            <span className="pill">Всего: {creative.total}</span>
          </div>
        </div>
        <div className="creative-list">
          {creative.items.length ? (
            creative.items.map((item) => (
              <div className="creative-item" key={item.id}>
                <div className="thread-meta">
                  <strong>{item.title}</strong>
                  <span>{item.status}</span>
                  <span>{item.created_at || "—"}</span>
                </div>
                <p>{item.content_text}</p>
                {(item.variants || []).length ? (
                  <div className="field-list">
                    {item.variants.map((variant, index) => {
                      const normalized = normalizeVariant(variant, index);
                      return (
                        <div className="info-block" key={`${item.id}-${index}`}>
                          <strong>{normalized.title}</strong>
                          <p className="muted">{normalized.content}</p>
                          <div className="actions-row">
                            <button
                              className="ghost-button"
                              type="button"
                              disabled={busy || item.status === "approved"}
                              onClick={() => void approve(item.id, index)}
                            >
                              {item.status === "approved" && item.selected_variant === index ? "Утверждён" : "Утвердить вариант"}
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            ))
          ) : (
            <p className="muted">Пока нет черновиков. Сначала соберите context, затем сгенерируйте первый draft.</p>
          )}
        </div>
      </section>
    </div>
  );
}
