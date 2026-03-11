import { FormEvent, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Brain, Send, Loader, Sparkles, BarChart2 } from "lucide-react";
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

const fadeUp = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -8 },
  transition: { duration: 0.25, ease: [0.16, 1, 0.3, 1] as const },
};

const staggerContainer = {
  animate: { transition: { staggerChildren: 0.06 } },
};

export function AssistantPage() {
  const { accessToken } = useAuth();
  const [thread, setThread] = useState<ThreadResponse | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [jobState, setJobState] = useState<JobStatusResponse | null>(null);
  const [quality, setQuality] = useState<QualitySummary | null>(null);
  const threadEndRef = useRef<HTMLDivElement>(null);

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

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [thread?.messages]);

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

  const completeness = Math.round(Number(thread?.brief?.completeness_score || 0) * 100);
  const missingFields = thread?.brief?.missing_fields || [];

  return (
    <motion.div
      className="page-grid"
      variants={staggerContainer}
      initial="initial"
      animate="animate"
    >
      {/* ── Quality Stats Bar ── */}
      <motion.div variants={fadeUp} style={qualityBarStyle}>
        <div style={qualityStatStyle}>
          <BarChart2 size={14} style={{ color: "var(--accent)" }} />
          <span style={qualityLabelStyle}>Requests</span>
          <span style={qualityValueStyle}>{quality?.overview?.total_requests ?? 0}</span>
        </div>
        <div style={qualitySepStyle} />
        <div style={qualityStatStyle}>
          <Sparkles size={14} style={{ color: "var(--accent)" }} />
          <span style={qualityLabelStyle}>Avg Score</span>
          <span style={qualityValueStyle}>{quality?.overview?.avg_quality_score ?? 0}</span>
        </div>
        <div style={qualitySepStyle} />
        <div style={qualityStatStyle}>
          <span style={qualityLabelStyle}>Fallback</span>
          <span style={qualityValueStyle}>{Math.round(Number(quality?.overview?.fallback_rate || 0) * 100)}%</span>
        </div>
        <div style={qualitySepStyle} />
        <div style={qualityStatStyle}>
          <span style={qualityLabelStyle}>Repair</span>
          <span style={qualityValueStyle}>{Math.round(Number(quality?.overview?.repair_rate || 0) * 100)}%</span>
        </div>

        {["brief_extraction", "assistant_reply"].map((task) => {
          const item = quality?.latest_by_task?.[task];
          if (!item) return null;
          return (
            <div key={task} style={{ display: "flex", alignItems: "center", gap: 6, marginLeft: 8 }}>
              <div style={qualitySepStyle} />
              <span style={{ ...qualityLabelStyle, textTransform: "none" }}>{task}</span>
              <span style={{ ...qualityValueStyle, fontSize: 11 }}>
                {item.provider || "\u2014"}/{item.model || "\u2014"} s{item.quality_score ?? 0}
                {item.fallback_used ? " fb" : ""}
                {item.repair_applied ? " rp" : ""}
                {item.latency_ms ? ` ${item.latency_ms}ms` : ""}
              </span>
            </div>
          );
        })}
      </motion.div>

      {/* ── Brief Status Panel ── */}
      <motion.section variants={fadeUp} className="hero-panel">
        <div className="panel-header">
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={iconBoxStyle}>
              <Brain size={18} />
            </div>
            <div>
              <div className="eyebrow">Growth Brief</div>
              <h2 style={{ fontSize: "1.3rem" }}>
                {thread?.brief?.assistant_ready ? "Brief ready" : "Brief in progress"}
              </h2>
            </div>
          </div>
          <div className="badge-row">
            <span className="pill">{completeness}%</span>
            <span className={`pill ${thread?.brief?.assistant_ready ? "" : "warning"}`}>
              {thread?.brief?.assistant_ready ? "Ready for creative" : "Needs more input"}
            </span>
          </div>
        </div>

        <AnimatePresence mode="wait">
          {statusMessage && (
            <motion.div
              key="status"
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              style={statusBannerStyle}
            >
              {statusMessage}
            </motion.div>
          )}
        </AnimatePresence>

        {/* Processing animation */}
        <AnimatePresence>
          {busy && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              style={processingBarStyle}
            >
              <Loader size={14} style={{ animation: "spin 1s linear infinite" }} />
              <span style={{ fontFamily: "\"JetBrains Mono Variable\", \"JetBrains Mono\", monospace", fontSize: 13 }}>
                Processing
              </span>
              <span style={blinkingCursorStyle}>_</span>
            </motion.div>
          )}
        </AnimatePresence>

        <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 8 }}>
          <motion.button
            className="primary-button"
            type="button"
            disabled={busy}
            onClick={() => void startBrief()}
            whileHover={{ scale: 1.02, boxShadow: "0 0 24px rgba(0, 255, 136, 0.3)" }}
            whileTap={{ scale: 0.98 }}
            style={{ display: "flex", alignItems: "center", gap: 8 }}
          >
            <Brain size={15} />
            {busy ? "Launching..." : "Start / Update Brief"}
          </motion.button>

          {jobState && (
            <span style={{ fontFamily: "\"JetBrains Mono Variable\", \"JetBrains Mono\", monospace", fontSize: 12, color: "var(--muted)" }}>
              job #{jobState.id} {jobState.status}
              {jobState.error_code ? ` \u00b7 ${jobState.error_code}` : ""}
            </span>
          )}
        </div>

        {missingFields.length > 0 && (
          <div style={{ marginTop: 12, fontFamily: "\"JetBrains Mono Variable\", \"JetBrains Mono\", monospace", fontSize: 12, color: "var(--warning)" }}>
            Missing: {missingFields.join(", ")}
          </div>
        )}
        {missingFields.length === 0 && thread && (
          <div style={{ marginTop: 12, fontFamily: "\"JetBrains Mono Variable\", \"JetBrains Mono\", monospace", fontSize: 12, color: "var(--accent)" }}>
            Brief sufficiently filled
          </div>
        )}
      </motion.section>

      {/* ── Two-column: Chat + Recommendations ── */}
      <div className="two-column-grid">
        {/* Chat Thread */}
        <motion.article variants={fadeUp} className="panel" style={{ display: "flex", flexDirection: "column" }}>
          <div className="panel-header">
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={iconBoxStyle}>
                <Sparkles size={16} />
              </div>
              <div>
                <div className="eyebrow">Dialogue</div>
                <h2 style={{ fontSize: "1.1rem" }}>Assistant Thread</h2>
              </div>
            </div>
          </div>

          <div style={threadContainerStyle}>
            <AnimatePresence initial={false}>
              {(thread?.messages || []).length ? (
                thread!.messages.map((item) => (
                  <motion.div
                    key={item.id}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.2 }}
                    style={item.role === "assistant" ? assistantMsgStyle : userMsgStyle}
                  >
                    <div style={msgMetaStyle}>
                      <strong style={{ color: item.role === "assistant" ? "var(--accent)" : "var(--text)" }}>
                        {item.role === "assistant" ? "> Assistant" : "You"}
                      </strong>
                      <span style={{ color: "var(--muted)", fontSize: 11, fontFamily: "\"JetBrains Mono Variable\", \"JetBrains Mono\", monospace" }}>
                        {item.created_at || "\u2014"}
                      </span>
                    </div>
                    <p style={{
                      margin: 0,
                      whiteSpace: "pre-wrap",
                      color: "var(--text-secondary)",
                      fontFamily: item.role === "assistant"
                        ? "\"JetBrains Mono Variable\", \"JetBrains Mono\", \"Fira Code\", monospace"
                        : "inherit",
                      fontSize: item.role === "assistant" ? 13 : 14,
                      lineHeight: 1.6,
                    }}>
                      {item.content}
                    </p>
                  </motion.div>
                ))
              ) : (
                <motion.p
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  style={{
                    color: "var(--muted)",
                    textAlign: "center",
                    fontFamily: "\"JetBrains Mono Variable\", \"JetBrains Mono\", monospace",
                    fontSize: 13,
                    padding: "32px 0",
                  }}
                >
                  No messages yet. Start a growth-brief to begin.
                </motion.p>
              )}
            </AnimatePresence>
            <div ref={threadEndRef} />
          </div>

          {/* Input Area */}
          <form onSubmit={sendMessage} style={{ marginTop: "auto", display: "grid", gap: 10, paddingTop: 16 }}>
            <textarea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="Describe your product, offer, audience, Telegram goals, and tone of voice..."
              style={textareaStyle}
              rows={3}
            />
            <motion.button
              className="primary-button"
              type="submit"
              disabled={busy}
              whileHover={{ scale: 1.01 }}
              whileTap={{ scale: 0.98 }}
              style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}
            >
              <Send size={14} />
              {busy ? "Sending..." : "Send Message"}
            </motion.button>
          </form>
        </motion.article>

        {/* Recommendations */}
        <motion.article variants={fadeUp} className="panel">
          <div className="panel-header">
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={iconBoxStyle}>
                <BarChart2 size={16} />
              </div>
              <div>
                <div className="eyebrow">Recommendations</div>
                <h2 style={{ fontSize: "1.1rem" }}>Next Steps</h2>
              </div>
            </div>
          </div>

          <div style={{ display: "grid", gap: 8 }}>
            <AnimatePresence initial={false}>
              {(thread?.recommendations || []).length ? (
                thread!.recommendations.map((item, idx) => (
                  <motion.div
                    key={item.id}
                    initial={{ opacity: 0, x: -12 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.2, delay: idx * 0.05 }}
                    style={recItemStyle}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                      <strong style={{
                        fontFamily: "\"JetBrains Mono Variable\", \"JetBrains Mono\", monospace",
                        fontSize: 13,
                        color: "var(--accent)",
                      }}>
                        {">"} {item.title}
                      </strong>
                      <span className="pill" style={{ fontSize: 10 }}>{item.status}</span>
                    </div>
                    <p style={{
                      margin: 0,
                      whiteSpace: "pre-wrap",
                      color: "var(--text-secondary)",
                      fontFamily: "\"JetBrains Mono Variable\", \"JetBrains Mono\", monospace",
                      fontSize: 12,
                      lineHeight: 1.5,
                    }}>
                      {item.body}
                    </p>
                  </motion.div>
                ))
              ) : (
                <motion.p
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  style={{
                    color: "var(--muted)",
                    textAlign: "center",
                    fontFamily: "\"JetBrains Mono Variable\", \"JetBrains Mono\", monospace",
                    fontSize: 13,
                    padding: "32px 0",
                  }}
                >
                  Recommendations will appear as the brief fills in.
                </motion.p>
              )}
            </AnimatePresence>
          </div>
        </motion.article>
      </div>

      {/* Inline keyframes for spinner */}
      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </motion.div>
  );
}

/* ── Inline style objects ── */

const iconBoxStyle: React.CSSProperties = {
  width: 36,
  height: 36,
  borderRadius: 10,
  display: "grid",
  placeItems: "center",
  background: "var(--accent-glow)",
  color: "var(--accent)",
  flexShrink: 0,
};

const qualityBarStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 16,
  padding: "10px 20px",
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  fontFamily: "\"JetBrains Mono Variable\", \"JetBrains Mono\", \"Fira Code\", monospace",
  fontSize: 12,
  color: "var(--text-secondary)",
  flexWrap: "wrap",
};

const qualityStatStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  whiteSpace: "nowrap",
};

const qualityLabelStyle: React.CSSProperties = {
  fontSize: 10,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "var(--muted)",
};

const qualityValueStyle: React.CSSProperties = {
  fontWeight: 700,
  color: "var(--text)",
  fontSize: 13,
};

const qualitySepStyle: React.CSSProperties = {
  width: 1,
  height: 16,
  background: "var(--border)",
  flexShrink: 0,
};

const statusBannerStyle: React.CSSProperties = {
  padding: "10px 14px",
  borderRadius: 8,
  background: "rgba(0, 255, 136, 0.06)",
  border: "1px solid rgba(0, 255, 136, 0.15)",
  color: "var(--accent)",
  fontFamily: "\"JetBrains Mono Variable\", \"JetBrains Mono\", monospace",
  fontSize: 13,
  overflow: "hidden",
};

const processingBarStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "8px 14px",
  background: "var(--surface-2)",
  borderRadius: 8,
  border: "1px solid var(--border)",
  color: "var(--accent)",
  marginTop: 8,
};

const blinkingCursorStyle: React.CSSProperties = {
  animation: "terminalBlink 1s step-end infinite",
  fontFamily: "\"JetBrains Mono Variable\", \"JetBrains Mono\", monospace",
  fontWeight: 700,
  color: "var(--accent)",
};

const threadContainerStyle: React.CSSProperties = {
  display: "grid",
  gap: 10,
  maxHeight: 480,
  overflowY: "auto",
  paddingRight: 4,
};

const assistantMsgStyle: React.CSSProperties = {
  padding: 14,
  borderRadius: 12,
  background: "var(--surface-2)",
  borderLeft: "3px solid var(--accent)",
  display: "grid",
  gap: 6,
};

const userMsgStyle: React.CSSProperties = {
  padding: 14,
  borderRadius: 12,
  background: "var(--surface)",
  border: "1px solid var(--border)",
  display: "grid",
  gap: 6,
  marginLeft: 32,
};

const msgMetaStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  fontSize: 12,
};

const textareaStyle: React.CSSProperties = {
  width: "100%",
  minHeight: 80,
  padding: 14,
  borderRadius: 12,
  border: "1px solid var(--border)",
  background: "var(--surface-2)",
  color: "var(--text)",
  resize: "vertical",
  fontFamily: "inherit",
  fontSize: 14,
  outline: "none",
};

const recItemStyle: React.CSSProperties = {
  padding: 14,
  borderRadius: 12,
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  display: "grid",
  gap: 8,
};
