import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { FileText, CheckCircle, AlertCircle, Loader } from "lucide-react";
import { apiFetch, JobStatusResponse, pollJob } from "../api";
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

const container = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: { staggerChildren: 0.06 },
  },
};

const item = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.16, 1, 0.3, 1] as const } },
};

type FieldCardProps = {
  label: string;
  value: string;
};

function FieldCard({ label, value }: FieldCardProps) {
  const isEmpty = !value || value === "—";
  return (
    <motion.div variants={item} className="ctx-field-card">
      <span className="ctx-field-label">{label}</span>
      <span className={isEmpty ? "ctx-field-value ctx-field-value--empty" : "ctx-field-value"}>
        {isEmpty ? "Не заполнено" : value}
      </span>
    </motion.div>
  );
}

export function ContextPage() {
  const { accessToken } = useAuth();
  const [context, setContext] = useState<ContextResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [jobState, setJobState] = useState<JobStatusResponse | null>(null);
  const [quality, setQuality] = useState<QualitySummary | null>(null);

  const loadContext = async () => {
    if (!accessToken) {
      return;
    }
    const payload = await apiFetch<ContextResponse>("/v1/context", { accessToken });
    setContext(payload);
  };

  const loadQuality = async () => {
    if (!accessToken) {
      return;
    }
    const payload = await apiFetch<QualitySummary>("/v1/ai/quality-summary", { accessToken });
    setQuality(payload);
  };

  useEffect(() => {
    void loadContext().catch(() => setContext(null));
    void loadQuality().catch(() => setQuality(null));
  }, [accessToken]);

  const confirm = async () => {
    if (!accessToken) {
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      const queued = await apiFetch<{ job_id: number; status: string }>("/v1/context/confirm", {
        method: "POST",
        accessToken,
      });
      setJobState({
        id: queued.job_id,
        job_type: "context_confirm",
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
        throw new Error(job.error_code || "context_confirm_failed");
      }
      await Promise.all([loadContext(), loadQuality()]);
      setStatusMessage("Контекст подтверждён и сохранён как основа для следующих AI-черновиков.");
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "context_confirm_failed");
    } finally {
      setBusy(false);
    }
  };

  const brief = context?.brief;
  const completeness = Math.round(Number(brief?.completeness_score || 0) * 100);
  const isConfirmed = !!brief?.confirmed_at;
  const qualityItem = quality?.latest_by_task?.["assistant_reply"];

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
          <FileText size={20} color="var(--accent)" />
          <span className="eyebrow">Business context</span>
        </div>
        <h1>Контекст бизнеса</h1>
        <p className="muted" style={{ marginTop: 8 }}>
          Утверждённый growth-brief: продукт, оффер, ЦА, тональность, цели в Telegram.
          Источник правды для черновиков и рекомендаций.
        </p>
      </motion.section>

      {/* Status bar */}
      <motion.section
        className="dash-status-bar"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.1 }}
      >
        <div className="dash-status-item">
          <div className={`dash-status-dot ${completeness >= 80 ? "dash-status-dot--green" : completeness >= 40 ? "dash-status-dot--amber" : "dash-status-dot--red"}`} />
          <span>Готовность {completeness}%</span>
        </div>
        <div className="dash-status-sep" />
        <div className="dash-status-item">
          {isConfirmed
            ? <><CheckCircle size={14} color="var(--accent)" /> <span style={{ color: "var(--accent)" }}>Подтверждён</span></>
            : <><AlertCircle size={14} color="var(--warning)" /> <span style={{ color: "var(--warning)" }}>Ещё не подтверждён</span></>
          }
        </div>
        <div className="dash-status-sep" />
        <div className="dash-status-item">
          <span>Assets: {brief?.assets_count || 0}</span>
        </div>
        <div className="dash-status-sep" />
        <div className="dash-status-item">
          <span>Drafts: {brief?.draft_count || 0}</span>
        </div>
        <div className="dash-status-sep" />
        <div className="dash-status-item">
          <span>Assistant: {brief?.assistant_ready ? "Ready" : "Pending"}</span>
        </div>
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
                ? <CheckCircle size={14} color="var(--accent)" />
                : <AlertCircle size={14} color="var(--danger)" />
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

      {/* Summary terminal window */}
      <motion.section
        className="panel"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.15 }}
      >
        <div className="panel-header">
          <div>
            <div className="eyebrow">Summary</div>
            <h2>Сводка brief</h2>
          </div>
        </div>
        <div className="terminal-window">
          <div className="terminal-line">
            <span className="timestamp">$</span>
            <span className={brief?.summary_text ? "message white" : "message"}>
              {brief?.summary_text || "// Сначала ответьте ассистенту, чтобы появилась сводка бизнеса."}
            </span>
          </div>
        </div>
      </motion.section>

      {/* Context fields — 2-column card grid */}
      <motion.section
        className="panel"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
      >
        <div className="panel-header">
          <div>
            <div className="eyebrow">Fields</div>
            <h2>Подробные поля контекста</h2>
          </div>
        </div>
        <motion.div
          className="ctx-field-grid"
          variants={container}
          initial="hidden"
          animate="show"
        >
          <FieldCard label="Продукт" value={brief?.product_name || "—"} />
          <FieldCard label="Оффер" value={brief?.offer_summary || "—"} />
          <FieldCard label="Целевая аудитория" value={brief?.target_audience || "—"} />
          <FieldCard label="Тон коммуникации" value={brief?.tone_of_voice || "—"} />
          <FieldCard label="Конкуренты" value={(brief?.competitors || []).join(", ") || "—"} />
          <FieldCard label="Боли клиентов" value={(brief?.pain_points || []).join(", ") || "—"} />
          <FieldCard label="Цели в Telegram" value={(brief?.telegram_goals || []).join(", ") || "—"} />
          <FieldCard label="Сайт" value={brief?.website_url || "—"} />
          <FieldCard label="Канал" value={brief?.channel_url || "—"} />
          <FieldCard label="Бот" value={brief?.bot_url || "—"} />
        </motion.div>
      </motion.section>

      {/* Missing fields */}
      <motion.div
        className="dash-status-bar"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.25 }}
        style={{ fontFamily: "'JetBrains Mono Variable', 'JetBrains Mono', 'Fira Code', monospace", fontSize: 12 }}
      >
        <div className="dash-status-item">
          <AlertCircle size={14} color="var(--warning)" />
          <span>
            Не хватает: {(brief?.missing_fields || []).length ? brief!.missing_fields.join(", ") : "обязательные поля уже собраны"}
          </span>
        </div>
      </motion.div>

      {/* Confirm action */}
      <motion.section
        className="panel"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
      >
        <div className="panel-header">
          <div>
            <div className="eyebrow">Action</div>
            <h2>Подтвердить контекст</h2>
          </div>
        </div>
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
        <div className="actions-row" style={{ marginTop: 18 }}>
          <button
            className="primary-button"
            type="button"
            disabled={busy}
            onClick={() => void confirm()}
            style={busy ? {} : { boxShadow: "0 0 24px rgba(0, 255, 136, 0.25)" }}
          >
            {busy ? (
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Loader size={14} style={{ animation: "pulse 1.2s ease-in-out infinite" }} />
                Подтверждаем...
              </span>
            ) : (
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <CheckCircle size={14} />
                Подтвердить текущий контекст
              </span>
            )}
          </button>
        </div>
      </motion.section>

      {/* AI Quality */}
      <motion.section
        className="panel"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.35 }}
      >
        <div className="panel-header">
          <div>
            <div className="eyebrow">AI quality</div>
            <h2>Качество подтверждения контекста</h2>
          </div>
        </div>
        {qualityItem ? (
          <div className="dash-status-bar" style={{ background: "transparent", border: "none", padding: 0 }}>
            <div className="dash-status-item">
              <span className="ctx-field-label" style={{ marginRight: 4 }}>Provider</span>
              <span>{qualityItem.provider || "—"}</span>
            </div>
            <div className="dash-status-sep" />
            <div className="dash-status-item">
              <span className="ctx-field-label" style={{ marginRight: 4 }}>Model</span>
              <span>{qualityItem.model || "—"}</span>
            </div>
            <div className="dash-status-sep" />
            <div className="dash-status-item">
              <span className="ctx-field-label" style={{ marginRight: 4 }}>Score</span>
              <span style={{ color: qualityItem.quality_score >= 7 ? "var(--accent)" : qualityItem.quality_score >= 4 ? "var(--warning)" : "var(--danger)" }}>
                {qualityItem.quality_score ?? 0}
              </span>
            </div>
            {qualityItem.fallback_used ? (
              <>
                <div className="dash-status-sep" />
                <div className="dash-status-item">
                  <span className="pill warning">fallback</span>
                </div>
              </>
            ) : null}
            {qualityItem.repair_applied ? (
              <>
                <div className="dash-status-sep" />
                <div className="dash-status-item">
                  <span className="pill info">repair</span>
                </div>
              </>
            ) : null}
            {qualityItem.latency_ms ? (
              <>
                <div className="dash-status-sep" />
                <div className="dash-status-item">
                  <span>{qualityItem.latency_ms}ms</span>
                </div>
              </>
            ) : null}
          </div>
        ) : (
          <p className="muted" style={{ fontFamily: "'JetBrains Mono Variable', 'JetBrains Mono', monospace", fontSize: 13 }}>
            Качество появится после обработки brief и подтверждения контекста.
          </p>
        )}
      </motion.section>
    </div>
  );
}
