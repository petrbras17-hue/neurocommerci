import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Palette, Sparkles, Check, Loader, FileText } from "lucide-react";
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

const container = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: { staggerChildren: 0.07 },
  },
};

const cardAnim = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.16, 1, 0.3, 1] as const } },
};

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

  const qualityItem = quality?.latest_by_task?.["creative_variants"];

  return (
    <div className="page-grid">
      {/* Hero */}
      <motion.section
        className="hero-panel"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
          <Palette size={20} color="var(--accent)" />
          <span className="eyebrow">Creative studio</span>
        </div>
        <h1>Черновики и креативы</h1>
        <p className="muted" style={{ marginTop: 8 }}>
          Генерация постов, комментариев, ad copy и image prompts из утверждённого контекста.
        </p>
      </motion.section>

      {/* Quality stat bar */}
      <motion.section
        className="dash-status-bar"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.1 }}
      >
        <div className="dash-status-item">
          <FileText size={14} color="var(--accent)" />
          <span>Всего: {creative.total}</span>
        </div>
        {qualityItem ? (
          <>
            <div className="dash-status-sep" />
            <div className="dash-status-item">
              <span style={{ color: "var(--muted)", marginRight: 4, fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em" }}>Provider</span>
              <span>{qualityItem.provider || "—"}</span>
            </div>
            <div className="dash-status-sep" />
            <div className="dash-status-item">
              <span style={{ color: "var(--muted)", marginRight: 4, fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em" }}>Model</span>
              <span>{qualityItem.model || "—"}</span>
            </div>
            <div className="dash-status-sep" />
            <div className="dash-status-item">
              <span style={{ color: "var(--muted)", marginRight: 4, fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em" }}>Score</span>
              <span style={{ color: qualityItem.quality_score >= 7 ? "var(--accent)" : qualityItem.quality_score >= 4 ? "var(--warning)" : "var(--danger)" }}>
                {qualityItem.quality_score ?? 0}
              </span>
            </div>
            {qualityItem.fallback_used ? (
              <>
                <div className="dash-status-sep" />
                <div className="dash-status-item"><span className="pill warning">fallback</span></div>
              </>
            ) : null}
            {qualityItem.repair_applied ? (
              <>
                <div className="dash-status-sep" />
                <div className="dash-status-item"><span className="pill info">repair</span></div>
              </>
            ) : null}
            {qualityItem.latency_ms ? (
              <>
                <div className="dash-status-sep" />
                <div className="dash-status-item"><span>{qualityItem.latency_ms}ms</span></div>
              </>
            ) : null}
          </>
        ) : null}
      </motion.section>

      {/* Status message */}
      {statusMessage ? (
        <motion.div
          className="status-banner"
          initial={{ opacity: 0, scale: 0.97 }}
          animate={{ opacity: 1, scale: 1 }}
        >
          {statusMessage}
        </motion.div>
      ) : null}

      {/* Generate section */}
      <motion.section
        className="panel"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.15 }}
      >
        <div className="panel-header">
          <div>
            <div className="eyebrow">Generate</div>
            <h2>Сгенерировать новый черновик</h2>
          </div>
        </div>
        <div className="actions-row" style={{ alignItems: "center" }}>
          {/* Segmented control / selector */}
          <div className="crv-type-selector">
            {DRAFT_TYPES.map((dt) => (
              <button
                key={dt.value}
                type="button"
                className={`crv-type-option ${draftType === dt.value ? "crv-type-option--active" : ""}`}
                onClick={() => setDraftType(dt.value)}
              >
                {dt.label}
              </button>
            ))}
          </div>
          <button
            className="primary-button"
            type="button"
            disabled={busy}
            onClick={() => void generate()}
            style={busy ? {} : { boxShadow: "0 0 24px rgba(0, 255, 136, 0.25)" }}
          >
            {busy ? (
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Loader size={14} style={{ animation: "pulse 1.2s ease-in-out infinite" }} />
                Генерируем...
              </span>
            ) : (
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Sparkles size={14} />
                Сгенерировать 3 варианта
              </span>
            )}
          </button>
        </div>
      </motion.section>

      {/* Job state */}
      {jobState ? (
        <motion.div
          className="dash-status-bar"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          style={{ fontFamily: "'JetBrains Mono Variable', 'JetBrains Mono', 'Fira Code', monospace", fontSize: 12 }}
        >
          <div className="dash-status-item">
            {jobState.status === "queued" || jobState.status === "running"
              ? <Loader size={14} color="var(--accent)" style={{ animation: "pulse 1.2s ease-in-out infinite" }} />
              : jobState.status === "succeeded"
                ? <Check size={14} color="var(--accent)" />
                : <Palette size={14} color="var(--danger)" />
            }
            <span>Job #{jobState.id}</span>
          </div>
          <div className="dash-status-sep" />
          <div className="dash-status-item">
            <span>{jobState.status}</span>
          </div>
          {jobState.error_code ? (
            <>
              <div className="dash-status-sep" />
              <div className="dash-status-item">
                <span style={{ color: "var(--danger)" }}>{jobState.error_code}</span>
              </div>
            </>
          ) : null}
        </motion.div>
      ) : null}

      {/* Draft cards */}
      <motion.section
        className="panel"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.25 }}
      >
        <div className="panel-header">
          <div>
            <div className="eyebrow">Draft registry</div>
            <h2>Черновики и approved assets</h2>
          </div>
          <div className="badge-row">
            <span className="pill">Всего: {creative.total}</span>
          </div>
        </div>

        {creative.items.length ? (
          <motion.div
            className="crv-draft-list"
            variants={container}
            initial="hidden"
            animate="show"
          >
            {creative.items.map((draft) => (
              <motion.div className="crv-draft-card" key={draft.id} variants={cardAnim}>
                {/* Draft header */}
                <div className="crv-draft-header">
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <FileText size={16} color="var(--accent)" />
                    <strong>{draft.title}</strong>
                  </div>
                  <div className="badge-row">
                    <span className={`pill ${draft.status === "approved" ? "" : "warning"}`}>
                      {draft.status}
                    </span>
                    <span className="pill info">{draft.draft_type || "draft"}</span>
                  </div>
                </div>

                {/* Draft body text */}
                {draft.content_text ? (
                  <p className="muted" style={{ margin: 0, fontSize: 13 }}>{draft.content_text}</p>
                ) : null}

                {/* Variants as numbered terminal output blocks */}
                {(draft.variants || []).length ? (
                  <div className="crv-variants">
                    {draft.variants.map((variant, index) => {
                      const normalized = normalizeVariant(variant, index);
                      const isSelected = draft.status === "approved" && draft.selected_variant === index;
                      return (
                        <div
                          className={`crv-variant-block ${isSelected ? "crv-variant-block--selected" : ""}`}
                          key={`${draft.id}-${index}`}
                        >
                          <div className="crv-variant-header">
                            <span className="crv-variant-number">{String(index + 1).padStart(2, "0")}</span>
                            <span className="crv-variant-title">{normalized.title}</span>
                            {isSelected ? <Check size={14} color="var(--accent)" /> : null}
                          </div>
                          <pre className="crv-variant-content">{normalized.content}</pre>
                          <div style={{ marginTop: 8 }}>
                            <button
                              className={isSelected ? "pill" : "crv-approve-btn"}
                              type="button"
                              disabled={busy || draft.status === "approved"}
                              onClick={() => void approve(draft.id, index)}
                            >
                              {isSelected ? (
                                <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                  <Check size={12} /> Утверждён
                                </span>
                              ) : (
                                <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                  <Check size={12} /> Утвердить
                                </span>
                              )}
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : null}

                {/* Meta */}
                <div className="thread-meta">
                  <span>{draft.created_at || "—"}</span>
                </div>
              </motion.div>
            ))}
          </motion.div>
        ) : (
          <div className="dash-empty">
            Пока нет черновиков. Соберите context, затем сгенерируйте первый draft.
          </div>
        )}
      </motion.section>
    </div>
  );
}
