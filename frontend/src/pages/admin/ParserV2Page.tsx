import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";

/* ── Types ── */

interface GroupJob {
  id: number;
  keywords: string[];
  status: string;
  filters: Record<string, unknown> | null;
  results_count: number;
  progress: number;
  error: string | null;
  created_at: string | null;
  completed_at: string | null;
}

interface MessageJob {
  id: number;
  channel_id: number;
  keywords: string[] | null;
  date_from: string | null;
  date_to: string | null;
  status: string;
  results_count: number;
  progress: number;
  error: string | null;
  created_at: string | null;
  completed_at: string | null;
}

interface MessageResult {
  id: number;
  user_id: number | null;
  username: string | null;
  first_name: string | null;
  message_text: string | null;
  message_date: string | null;
  channel_id: number | null;
  channel_title: string | null;
}

interface Template {
  id: number;
  workspace_id: number | null;
  name: string;
  category: string | null;
  keywords: string[];
  filters: Record<string, unknown> | null;
  description: string | null;
  is_system: boolean;
  created_at: string | null;
}

type TabId = "channels" | "groups" | "messages" | "templates";

/* ── Styles ── */

const S = {
  page: { padding: 24, color: "#e0e0e0", maxWidth: 1200, margin: "0 auto" } as const,
  h1: { color: "#00ff88", fontSize: 22, fontWeight: 700, marginBottom: 16 } as const,
  tabs: { display: "flex", gap: 4, marginBottom: 20, borderBottom: "1px solid #1a1a2e" } as const,
  tab: (a: boolean) => ({
    padding: "8px 18px",
    cursor: "pointer",
    borderBottom: a ? "2px solid #00ff88" : "2px solid transparent",
    color: a ? "#00ff88" : "#888",
    fontWeight: a ? 700 : 400,
    background: "none",
    border: "none",
    fontSize: 14,
  }),
  card: {
    background: "#111118",
    border: "1px solid #1a1a2e",
    borderRadius: 8,
    padding: 16,
    marginBottom: 12,
  } as const,
  input: {
    background: "#0d0d14",
    border: "1px solid #2a2a3e",
    borderRadius: 6,
    padding: "8px 12px",
    color: "#e0e0e0",
    width: "100%",
    fontSize: 14,
    outline: "none",
  } as const,
  textarea: {
    background: "#0d0d14",
    border: "1px solid #2a2a3e",
    borderRadius: 6,
    padding: "8px 12px",
    color: "#e0e0e0",
    width: "100%",
    fontSize: 14,
    minHeight: 80,
    outline: "none",
    resize: "vertical" as const,
  } as const,
  btn: {
    background: "#00ff88",
    color: "#0a0a0b",
    border: "none",
    borderRadius: 6,
    padding: "8px 18px",
    fontWeight: 700,
    cursor: "pointer",
    fontSize: 14,
  } as const,
  btnDanger: {
    background: "#ff4444",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    padding: "6px 14px",
    fontWeight: 600,
    cursor: "pointer",
    fontSize: 13,
  } as const,
  btnSecondary: {
    background: "#1a1a2e",
    color: "#00ff88",
    border: "1px solid #2a2a3e",
    borderRadius: 6,
    padding: "6px 14px",
    fontWeight: 600,
    cursor: "pointer",
    fontSize: 13,
  } as const,
  badge: (s: string) => ({
    display: "inline-block",
    padding: "2px 10px",
    borderRadius: 12,
    fontSize: 12,
    fontWeight: 600,
    color: "#fff",
    background:
      s === "completed" ? "#00aa55" :
      s === "running" ? "#2288ff" :
      s === "cancelled" ? "#aa8800" :
      s === "error" ? "#cc3333" :
      "#555",
  }),
  progress: (p: number) => ({
    height: 6,
    borderRadius: 3,
    background: "#1a1a2e",
    overflow: "hidden" as const,
    marginTop: 6,
  }),
  progressBar: (p: number) => ({
    height: "100%",
    width: `${p}%`,
    background: "#00ff88",
    borderRadius: 3,
    transition: "width 0.3s",
  }),
  label: { fontSize: 12, color: "#888", marginBottom: 4, display: "block" } as const,
  row: { display: "flex", gap: 12, marginBottom: 12, alignItems: "flex-end" } as const,
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 } as const,
  table: { width: "100%", borderCollapse: "collapse" as const, fontSize: 13 } as const,
  th: { textAlign: "left" as const, padding: "8px 10px", borderBottom: "1px solid #2a2a3e", color: "#888", fontWeight: 600 } as const,
  td: { padding: "8px 10px", borderBottom: "1px solid #111118" } as const,
};

/* ── Components ── */

function ProgressBar({ value }: { value: number }) {
  return (
    <div style={S.progress(value)}>
      <div style={S.progressBar(value)} />
    </div>
  );
}

/* ── Main Page ── */

export function ParserV2Page() {
  const { accessToken } = useAuth();
  const [tab, setTab] = useState<TabId>("groups");

  // Group parsing state
  const [groupJobs, setGroupJobs] = useState<GroupJob[]>([]);
  const [groupKeywords, setGroupKeywords] = useState("");
  const [groupMinMembers, setGroupMinMembers] = useState("");
  const [groupMaxMembers, setGroupMaxMembers] = useState("");
  const [groupActiveOnly, setGroupActiveOnly] = useState(false);
  const [groupMaxSpam, setGroupMaxSpam] = useState("");
  const [groupLoading, setGroupLoading] = useState(false);

  // Message parsing state
  const [msgJobs, setMsgJobs] = useState<MessageJob[]>([]);
  const [msgChannelId, setMsgChannelId] = useState("");
  const [msgKeywords, setMsgKeywords] = useState("");
  const [msgDateFrom, setMsgDateFrom] = useState("");
  const [msgDateTo, setMsgDateTo] = useState("");
  const [msgLoading, setMsgLoading] = useState(false);
  const [selectedMsgJob, setSelectedMsgJob] = useState<number | null>(null);
  const [msgResults, setMsgResults] = useState<MessageResult[]>([]);

  // Templates state
  const [systemTemplates, setSystemTemplates] = useState<Template[]>([]);
  const [userTemplates, setUserTemplates] = useState<Template[]>([]);
  const [newTplName, setNewTplName] = useState("");
  const [newTplCategory, setNewTplCategory] = useState("");
  const [newTplKeywords, setNewTplKeywords] = useState("");
  const [newTplDescription, setNewTplDescription] = useState("");

  // AI suggestions state
  const [seedKeywords, setSeedKeywords] = useState("");
  const [suggestedKeywords, setSuggestedKeywords] = useState<string[]>([]);
  const [aiLoading, setAiLoading] = useState(false);

  /* ── Fetchers ── */

  const fetchGroupJobs = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: GroupJob[] }>("/v1/admin/parser/groups", { accessToken });
      setGroupJobs(data.items);
    } catch (e) { console.error("fetch group jobs:", e); }
  }, [accessToken]);

  const fetchMsgJobs = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: MessageJob[] }>("/v1/admin/parser/messages", { accessToken });
      setMsgJobs(data.items);
    } catch (e) { console.error("fetch msg jobs:", e); }
  }, [accessToken]);

  const fetchTemplates = useCallback(async () => {
    try {
      const data = await apiFetch<{ system: Template[]; user: Template[] }>("/v1/admin/parser/templates", { accessToken });
      setSystemTemplates(data.system);
      setUserTemplates(data.user);
    } catch (e) { console.error("fetch templates:", e); }
  }, [accessToken]);

  const fetchMsgResults = useCallback(async (jobId: number) => {
    try {
      const data = await apiFetch<{ items: MessageResult[] }>(`/v1/admin/parser/messages/${jobId}/results`, { accessToken });
      setMsgResults(data.items);
    } catch (e) { console.error("fetch results:", e); }
  }, [accessToken]);

  useEffect(() => {
    if (tab === "groups") fetchGroupJobs();
    if (tab === "messages") fetchMsgJobs();
    if (tab === "templates") fetchTemplates();
  }, [tab, fetchGroupJobs, fetchMsgJobs, fetchTemplates]);

  // Poll running jobs
  useEffect(() => {
    if (tab !== "groups" && tab !== "messages") return;
    const hasRunning = tab === "groups"
      ? groupJobs.some(j => j.status === "running" || j.status === "pending")
      : msgJobs.some(j => j.status === "running" || j.status === "pending");
    if (!hasRunning) return;
    const id = setInterval(() => {
      if (tab === "groups") fetchGroupJobs();
      else fetchMsgJobs();
    }, 3000);
    return () => clearInterval(id);
  }, [tab, groupJobs, msgJobs, fetchGroupJobs, fetchMsgJobs]);

  /* ── Handlers ── */

  const startGroupParsing = async () => {
    const kw = groupKeywords.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
    if (!kw.length) return;
    setGroupLoading(true);
    try {
      const filters: Record<string, unknown> = {};
      if (groupMinMembers) filters.min_members = parseInt(groupMinMembers);
      if (groupMaxMembers) filters.max_members = parseInt(groupMaxMembers);
      if (groupActiveOnly) filters.active_only = true;
      if (groupMaxSpam) filters.max_spam_score = parseFloat(groupMaxSpam);
      await apiFetch("/v1/admin/parser/groups", {
        accessToken,
        method: "POST",
        json: { keywords: kw, filters: Object.keys(filters).length ? filters : null },
      });
      setGroupKeywords("");
      await fetchGroupJobs();
    } catch (e) { console.error("start group parsing:", e); }
    setGroupLoading(false);
  };

  const cancelGroupJob = async (id: number) => {
    await apiFetch(`/v1/admin/parser/groups/${id}`, { accessToken, method: "DELETE" });
    await fetchGroupJobs();
  };

  const startMessageParsing = async () => {
    if (!msgChannelId) return;
    setMsgLoading(true);
    try {
      const kw = msgKeywords ? msgKeywords.split(/[,\n]/).map(s => s.trim()).filter(Boolean) : null;
      await apiFetch("/v1/admin/parser/messages", {
        accessToken,
        method: "POST",
        json: {
          channel_id: parseInt(msgChannelId),
          keywords: kw,
          date_from: msgDateFrom || null,
          date_to: msgDateTo || null,
        },
      });
      setMsgChannelId("");
      setMsgKeywords("");
      await fetchMsgJobs();
    } catch (e) { console.error("start msg parsing:", e); }
    setMsgLoading(false);
  };

  const cancelMsgJob = async (id: number) => {
    await apiFetch(`/v1/admin/parser/messages/${id}`, { accessToken, method: "DELETE" });
    await fetchMsgJobs();
  };

  const exportResults = async (jobId: number, fmt: string) => {
    try {
      if (fmt === "json") {
        const data = await apiFetch<{ items: MessageResult[] }>(`/v1/admin/parser/messages/${jobId}/results`, { accessToken });
        const blob = new Blob([JSON.stringify(data.items, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a"); a.href = url; a.download = `results_${jobId}.json`; a.click();
        URL.revokeObjectURL(url);
      } else {
        const resp = await fetch(`/v1/admin/parser/messages/${jobId}/export`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
          body: JSON.stringify({ format: fmt }),
        });
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a"); a.href = url; a.download = `results_${jobId}.${fmt}`; a.click();
        URL.revokeObjectURL(url);
      }
    } catch (e) { console.error("export:", e); }
  };

  const suggestKw = async () => {
    const seeds = seedKeywords.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
    if (!seeds.length) return;
    setAiLoading(true);
    try {
      const data = await apiFetch<{ keywords: string[] }>("/v1/admin/parser/suggest-keywords", {
        accessToken,
        method: "POST",
        json: { seed_keywords: seeds },
      });
      setSuggestedKeywords(data.keywords);
    } catch (e) { console.error("suggest:", e); }
    setAiLoading(false);
  };

  const createTemplate = async () => {
    const kw = newTplKeywords.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
    if (!newTplName || !kw.length) return;
    try {
      await apiFetch("/v1/admin/parser/templates", {
        accessToken,
        method: "POST",
        json: {
          name: newTplName,
          category: newTplCategory || null,
          keywords: kw,
          description: newTplDescription || null,
        },
      });
      setNewTplName(""); setNewTplCategory(""); setNewTplKeywords(""); setNewTplDescription("");
      await fetchTemplates();
    } catch (e) { console.error("create template:", e); }
  };

  const deleteTemplate = async (id: number) => {
    await apiFetch(`/v1/admin/parser/templates/${id}`, { accessToken, method: "DELETE" });
    await fetchTemplates();
  };

  const applyTemplate = (t: Template) => {
    setGroupKeywords(t.keywords.join(", "));
    setTab("groups");
  };

  /* ── Render ── */

  return (
    <div style={S.page}>
      <h1 style={S.h1}>Parser v2</h1>

      <div style={S.tabs}>
        {(["channels", "groups", "messages", "templates"] as TabId[]).map(t => (
          <button key={t} style={S.tab(tab === t)} onClick={() => setTab(t)}>
            {t === "channels" ? "Channels" : t === "groups" ? "Groups" : t === "messages" ? "By Messages" : "Templates"}
          </button>
        ))}
      </div>

      {/* ── Channels Tab ── */}
      {tab === "channels" && (
        <div style={S.card}>
          <p style={{ color: "#888", fontSize: 14 }}>
            Используйте основной <a href="/parser" style={{ color: "#00ff88" }}>Парсер каналов</a> для поиска каналов по ключевым словам.
          </p>
        </div>
      )}

      {/* ── Groups Tab ── */}
      {tab === "groups" && (
        <>
          <div style={S.card}>
            <div style={S.row}>
              <div style={{ flex: 2 }}>
                <label style={S.label}>Keywords (comma or newline separated)</label>
                <textarea
                  style={S.textarea}
                  value={groupKeywords}
                  onChange={e => setGroupKeywords(e.target.value)}
                  placeholder="крипто, биткоин, web3..."
                />
              </div>
            </div>
            <div style={S.row}>
              <div>
                <label style={S.label}>Min members</label>
                <input style={{ ...S.input, width: 120 }} type="number" value={groupMinMembers} onChange={e => setGroupMinMembers(e.target.value)} />
              </div>
              <div>
                <label style={S.label}>Max members</label>
                <input style={{ ...S.input, width: 120 }} type="number" value={groupMaxMembers} onChange={e => setGroupMaxMembers(e.target.value)} />
              </div>
              <div>
                <label style={S.label}>Max spam score</label>
                <input style={{ ...S.input, width: 120 }} type="number" step="0.1" value={groupMaxSpam} onChange={e => setGroupMaxSpam(e.target.value)} placeholder="0.5" />
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <input type="checkbox" checked={groupActiveOnly} onChange={e => setGroupActiveOnly(e.target.checked)} />
                <label style={{ color: "#888", fontSize: 13 }}>Active only</label>
              </div>
              <button style={S.btn} onClick={startGroupParsing} disabled={groupLoading}>
                {groupLoading ? "Starting..." : "Start Parsing"}
              </button>
            </div>

            {/* AI Suggest */}
            <div style={{ borderTop: "1px solid #1a1a2e", paddingTop: 12, marginTop: 8 }}>
              <div style={S.row}>
                <div style={{ flex: 1 }}>
                  <label style={S.label}>AI Keyword Expansion</label>
                  <input style={S.input} value={seedKeywords} onChange={e => setSeedKeywords(e.target.value)} placeholder="Enter seed words..." />
                </div>
                <button style={S.btnSecondary} onClick={suggestKw} disabled={aiLoading}>
                  {aiLoading ? "Thinking..." : "Suggest"}
                </button>
              </div>
              {suggestedKeywords.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                  {suggestedKeywords.map((kw, i) => (
                    <span key={i} style={{ background: "#1a1a2e", color: "#00ff88", padding: "3px 10px", borderRadius: 12, fontSize: 12, cursor: "pointer" }}
                      onClick={() => setGroupKeywords(prev => prev ? prev + ", " + kw : kw)}>
                      + {kw}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Job list */}
          {groupJobs.map(j => (
            <div key={j.id} style={S.card}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <span style={{ fontWeight: 600 }}>Job #{j.id}</span>
                  <span style={{ ...S.badge(j.status), marginLeft: 8 }}>{j.status}</span>
                  <span style={{ color: "#888", fontSize: 12, marginLeft: 8 }}>
                    {j.keywords.slice(0, 5).join(", ")}{j.keywords.length > 5 ? "..." : ""}
                  </span>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span style={{ color: "#888", fontSize: 12 }}>{j.results_count} results</span>
                  {(j.status === "pending" || j.status === "running") && (
                    <button style={S.btnDanger} onClick={() => cancelGroupJob(j.id)}>Cancel</button>
                  )}
                </div>
              </div>
              {(j.status === "running" || j.status === "pending") && <ProgressBar value={j.progress} />}
              {j.error && <div style={{ color: "#ff4444", fontSize: 12, marginTop: 4 }}>{j.error}</div>}
            </div>
          ))}
          {groupJobs.length === 0 && <p style={{ color: "#555", fontSize: 13 }}>No group parsing jobs yet.</p>}
        </>
      )}

      {/* ── Messages Tab ── */}
      {tab === "messages" && (
        <>
          <div style={S.card}>
            <div style={S.row}>
              <div style={{ flex: 1 }}>
                <label style={S.label}>Channel ID</label>
                <input style={S.input} value={msgChannelId} onChange={e => setMsgChannelId(e.target.value)} placeholder="-1001234567890" />
              </div>
              <div style={{ flex: 1 }}>
                <label style={S.label}>Keywords (optional)</label>
                <input style={S.input} value={msgKeywords} onChange={e => setMsgKeywords(e.target.value)} placeholder="keyword1, keyword2" />
              </div>
            </div>
            <div style={S.row}>
              <div>
                <label style={S.label}>Date from</label>
                <input style={{ ...S.input, width: 180 }} type="datetime-local" value={msgDateFrom} onChange={e => setMsgDateFrom(e.target.value)} />
              </div>
              <div>
                <label style={S.label}>Date to</label>
                <input style={{ ...S.input, width: 180 }} type="datetime-local" value={msgDateTo} onChange={e => setMsgDateTo(e.target.value)} />
              </div>
              <button style={S.btn} onClick={startMessageParsing} disabled={msgLoading}>
                {msgLoading ? "Starting..." : "Start Parsing"}
              </button>
            </div>
          </div>

          {/* Job list */}
          {msgJobs.map(j => (
            <div key={j.id} style={{ ...S.card, cursor: "pointer", border: selectedMsgJob === j.id ? "1px solid #00ff88" : S.card.border }}
              onClick={() => { setSelectedMsgJob(j.id); fetchMsgResults(j.id); }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <span style={{ fontWeight: 600 }}>Job #{j.id}</span>
                  <span style={{ ...S.badge(j.status), marginLeft: 8 }}>{j.status}</span>
                  <span style={{ color: "#888", fontSize: 12, marginLeft: 8 }}>channel: {j.channel_id}</span>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span style={{ color: "#888", fontSize: 12 }}>{j.results_count} results</span>
                  {j.status === "completed" && j.results_count > 0 && (
                    <>
                      <button style={S.btnSecondary} onClick={e => { e.stopPropagation(); exportResults(j.id, "json"); }}>JSON</button>
                      <button style={S.btnSecondary} onClick={e => { e.stopPropagation(); exportResults(j.id, "csv"); }}>CSV</button>
                      <button style={S.btnSecondary} onClick={e => { e.stopPropagation(); exportResults(j.id, "txt"); }}>TXT</button>
                    </>
                  )}
                  {(j.status === "pending" || j.status === "running") && (
                    <button style={S.btnDanger} onClick={e => { e.stopPropagation(); cancelMsgJob(j.id); }}>Cancel</button>
                  )}
                </div>
              </div>
              {(j.status === "running" || j.status === "pending") && <ProgressBar value={j.progress} />}
            </div>
          ))}

          {/* Results table */}
          {selectedMsgJob && msgResults.length > 0 && (
            <div style={{ ...S.card, marginTop: 16 }}>
              <h3 style={{ color: "#00ff88", fontSize: 15, marginBottom: 8 }}>Results for Job #{selectedMsgJob}</h3>
              <table style={S.table}>
                <thead>
                  <tr>
                    <th style={S.th}>User</th>
                    <th style={S.th}>Name</th>
                    <th style={S.th}>Message</th>
                    <th style={S.th}>Date</th>
                    <th style={S.th}>Channel</th>
                  </tr>
                </thead>
                <tbody>
                  {msgResults.map(r => (
                    <tr key={r.id}>
                      <td style={S.td}>{r.username ? `@${r.username}` : r.user_id || "?"}</td>
                      <td style={S.td}>{r.first_name || ""}</td>
                      <td style={{ ...S.td, maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.message_text || ""}</td>
                      <td style={S.td}>{r.message_date ? new Date(r.message_date).toLocaleDateString() : ""}</td>
                      <td style={S.td}>{r.channel_title || ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {msgJobs.length === 0 && <p style={{ color: "#555", fontSize: 13 }}>No message parsing jobs yet.</p>}
        </>
      )}

      {/* ── Templates Tab ── */}
      {tab === "templates" && (
        <>
          {/* System templates */}
          <h3 style={{ color: "#00ff88", fontSize: 15, marginBottom: 8 }}>System Templates</h3>
          <div style={S.grid}>
            {systemTemplates.map(t => (
              <div key={t.id} style={S.card}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>{t.name}</div>
                <div style={{ color: "#888", fontSize: 12, marginBottom: 6 }}>{t.category} | {t.description}</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
                  {t.keywords.slice(0, 6).map((kw, i) => (
                    <span key={i} style={{ background: "#1a1a2e", color: "#ccc", padding: "2px 8px", borderRadius: 10, fontSize: 11 }}>{kw}</span>
                  ))}
                </div>
                <button style={S.btnSecondary} onClick={() => applyTemplate(t)}>Use</button>
              </div>
            ))}
          </div>

          {/* User templates */}
          <h3 style={{ color: "#00ff88", fontSize: 15, margin: "20px 0 8px" }}>My Templates</h3>
          <div style={S.grid}>
            {userTemplates.map(t => (
              <div key={t.id} style={S.card}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>{t.name}</div>
                <div style={{ color: "#888", fontSize: 12, marginBottom: 6 }}>{t.category || "no category"}</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
                  {t.keywords.slice(0, 6).map((kw, i) => (
                    <span key={i} style={{ background: "#1a1a2e", color: "#ccc", padding: "2px 8px", borderRadius: 10, fontSize: 11 }}>{kw}</span>
                  ))}
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button style={S.btnSecondary} onClick={() => applyTemplate(t)}>Use</button>
                  <button style={S.btnDanger} onClick={() => deleteTemplate(t.id)}>Delete</button>
                </div>
              </div>
            ))}
          </div>

          {/* Create template */}
          <div style={{ ...S.card, marginTop: 20 }}>
            <h3 style={{ color: "#00ff88", fontSize: 15, marginBottom: 8 }}>Create Template</h3>
            <div style={S.row}>
              <div style={{ flex: 1 }}>
                <label style={S.label}>Name</label>
                <input style={S.input} value={newTplName} onChange={e => setNewTplName(e.target.value)} placeholder="My template" />
              </div>
              <div style={{ flex: 1 }}>
                <label style={S.label}>Category</label>
                <input style={S.input} value={newTplCategory} onChange={e => setNewTplCategory(e.target.value)} placeholder="crypto, smm, etc" />
              </div>
            </div>
            <div style={{ marginBottom: 12 }}>
              <label style={S.label}>Keywords</label>
              <textarea style={S.textarea} value={newTplKeywords} onChange={e => setNewTplKeywords(e.target.value)} placeholder="keyword1, keyword2, ..." />
            </div>
            <div style={{ marginBottom: 12 }}>
              <label style={S.label}>Description</label>
              <input style={S.input} value={newTplDescription} onChange={e => setNewTplDescription(e.target.value)} placeholder="Optional description" />
            </div>
            <button style={S.btn} onClick={createTemplate}>Create Template</button>
          </div>
        </>
      )}
    </div>
  );
}
