import { useEffect, useState } from "react";
import { apiFetch } from "../api";
import { useAuth } from "../auth";

type CreativeItem = {
  id: number;
  draft_type: string;
  status: string;
  title: string;
  content_text: string;
  variants: string[];
  selected_variant: number;
  created_at: string | null;
};

type CreativeResponse = {
  total: number;
  items: CreativeItem[];
};

const DRAFT_TYPES = [
  { value: "post", label: "Пост" },
  { value: "comment", label: "Комментарий" },
  { value: "ad_copy", label: "Рекламный текст" },
  { value: "image_prompt", label: "Промпт для изображения" },
];

export function CreativePage() {
  const { accessToken } = useAuth();
  const [creative, setCreative] = useState<CreativeResponse>({ total: 0, items: [] });
  const [draftType, setDraftType] = useState("post");
  const [statusMessage, setStatusMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const loadCreative = async () => {
    if (!accessToken) {
      return;
    }
    const payload = await apiFetch<CreativeResponse>("/v1/creative/drafts", { accessToken });
    setCreative(payload);
  };

  useEffect(() => {
    void loadCreative().catch(() => setCreative({ total: 0, items: [] }));
  }, [accessToken]);

  const generate = async () => {
    if (!accessToken) {
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      await apiFetch("/v1/creative/generate", {
        method: "POST",
        accessToken,
        json: { draft_type: draftType, variant_count: 3 },
      });
      setStatusMessage("Черновик создан. Проверьте варианты и утвердите только то, что отражает реальный бизнес-контекст.");
      await loadCreative();
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
                    {item.variants.map((variant, index) => (
                      <div className="info-block" key={`${item.id}-${index}`}>
                        <strong>Вариант {index + 1}</strong>
                        <p className="muted">{variant}</p>
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
                    ))}
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
