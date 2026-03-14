import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";

interface ChattingConfig {
  id: number;
  workspace_id: number;
  name: string | null;
  mode: string;
  interval_percent: number;
  trigger_keywords: string[] | null;
  semantic_topics: string[] | null;
  product_name: string | null;
  product_description: string | null;
  product_problems_solved: string | null;
  mention_frequency: string;
  context_depth: number;
  is_active: boolean;
  created_at: string | null;
}

interface ChattingPreset {
  id: number;
  name: string;
  config: Record<string, unknown>;
  created_at: string | null;
}

type TabId = "setup" | "presets";

const MODES = [
  { value: "interval", label: "Interval (% сообщений)" },
  { value: "keyword_trigger", label: "Keyword Trigger" },
  { value: "semantic_match", label: "Semantic Match (AI)" },
];

const MENTION_FREQ = [
  { value: "never", label: "Никогда" },
  { value: "subtle", label: "Тонко" },
  { value: "moderate", label: "Умеренно" },
  { value: "aggressive", label: "Агрессивно" },
];

export function ChattingV2Page() {
  const { accessToken } = useAuth();
  const [tab, setTab] = useState<TabId>("setup");
  const [configs, setConfigs] = useState<ChattingConfig[]>([]);
  const [presets, setPresets] = useState<ChattingPreset[]>([]);
  const [loading, setLoading] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);

  // Form state
  const [name, setName] = useState("");
  const [mode, setMode] = useState("interval");
  const [intervalPercent, setIntervalPercent] = useState(10);
  const [keywords, setKeywords] = useState("");
  const [topics, setTopics] = useState("");
  const [productName, setProductName] = useState("");
  const [productDescription, setProductDescription] = useState("");
  const [productProblems, setProductProblems] = useState("");
  const [mentionFrequency, setMentionFrequency] = useState("subtle");
  const [contextDepth, setContextDepth] = useState(5);
  const [presetName, setPresetName] = useState("");

  const fetchConfigs = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: ChattingConfig[] }>("/v1/admin/chatting/configs", {
        accessToken,
      });
      setConfigs(data.items);
    } catch (e) {
      console.error("fetch configs:", e);
    }
  }, [accessToken]);

  const fetchPresets = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: ChattingPreset[] }>("/v1/admin/chatting/presets", {
        accessToken,
      });
      setPresets(data.items);
    } catch (e) {
      console.error("fetch presets:", e);
    }
  }, [accessToken]);

  useEffect(() => {
    void fetchConfigs();
    void fetchPresets();
  }, [fetchConfigs, fetchPresets]);

  const resetForm = () => {
    setEditId(null);
    setName("");
    setMode("interval");
    setIntervalPercent(10);
    setKeywords("");
    setTopics("");
    setProductName("");
    setProductDescription("");
    setProductProblems("");
    setMentionFrequency("subtle");
    setContextDepth(5);
  };

  const loadConfigToForm = (c: ChattingConfig) => {
    setEditId(c.id);
    setName(c.name || "");
    setMode(c.mode);
    setIntervalPercent(c.interval_percent);
    setKeywords((c.trigger_keywords || []).join(", "));
    setTopics((c.semantic_topics || []).join(", "));
    setProductName(c.product_name || "");
    setProductDescription(c.product_description || "");
    setProductProblems(c.product_problems_solved || "");
    setMentionFrequency(c.mention_frequency);
    setContextDepth(c.context_depth);
  };

  const buildPayload = () => ({
    name: name || undefined,
    mode,
    interval_percent: intervalPercent,
    trigger_keywords: keywords ? keywords.split(",").map((k) => k.trim()).filter(Boolean) : null,
    semantic_topics: topics ? topics.split(",").map((t) => t.trim()).filter(Boolean) : null,
    product_name: productName || undefined,
    product_description: productDescription || undefined,
    product_problems_solved: productProblems || undefined,
    mention_frequency: mentionFrequency,
    context_depth: contextDepth,
  });

  const handleSave = async () => {
    setLoading(true);
    try {
      const payload = buildPayload();
      if (editId) {
        await apiFetch(`/v1/admin/chatting/configs/${editId}`, {
          method: "PUT",
          json: payload,
          accessToken,
        });
      } else {
        await apiFetch("/v1/admin/chatting/configs", {
          method: "POST",
          json: payload,
          accessToken,
        });
      }
      resetForm();
      await fetchConfigs();
    } catch (e) {
      console.error("save config:", e);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await apiFetch(`/v1/admin/chatting/configs/${id}`, {
        method: "DELETE",
        accessToken,
      });
      await fetchConfigs();
    } catch (e) {
      console.error("delete config:", e);
    }
  };

  const handleSavePreset = async () => {
    if (!presetName.trim()) return;
    try {
      await apiFetch("/v1/admin/chatting/presets", {
        method: "POST",
        json: { name: presetName, config: buildPayload() },
        accessToken,
      });
      setPresetName("");
      await fetchPresets();
    } catch (e) {
      console.error("save preset:", e);
    }
  };

  const handleLoadPreset = (p: ChattingPreset) => {
    const c = p.config as Record<string, any>;
    setEditId(null);
    setName(String(c.name || ""));
    setMode(String(c.mode || "interval"));
    setIntervalPercent(Number(c.interval_percent || 10));
    setKeywords(Array.isArray(c.trigger_keywords) ? c.trigger_keywords.join(", ") : "");
    setTopics(Array.isArray(c.semantic_topics) ? c.semantic_topics.join(", ") : "");
    setProductName(String(c.product_name || ""));
    setProductDescription(String(c.product_description || ""));
    setProductProblems(String(c.product_problems_solved || ""));
    setMentionFrequency(String(c.mention_frequency || "subtle"));
    setContextDepth(Number(c.context_depth || 5));
    setTab("setup");
  };

  const handleDeletePreset = async (id: number) => {
    try {
      await apiFetch(`/v1/admin/chatting/presets/${id}`, {
        method: "DELETE",
        accessToken,
      });
      await fetchPresets();
    } catch (e) {
      console.error("delete preset:", e);
    }
  };

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ color: "#00ff88", marginBottom: 16, fontSize: 22 }}>Chatting v2</h1>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
        {(["setup", "presets"] as TabId[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              padding: "8px 20px",
              background: tab === t ? "#00ff88" : "#1a1a1d",
              color: tab === t ? "#0a0a0b" : "#aaa",
              border: "1px solid #333",
              borderRadius: 6,
              cursor: "pointer",
              fontWeight: tab === t ? 700 : 400,
            }}
          >
            {t === "setup" ? "Chatting Setup" : "Presets"}
          </button>
        ))}
      </div>

      {tab === "setup" && (
        <div style={{ display: "flex", gap: 32, flexWrap: "wrap" }}>
          {/* Form */}
          <div style={{ flex: "1 1 400px", maxWidth: 520 }}>
            <div style={{ marginBottom: 12 }}>
              <label style={{ color: "#888", fontSize: 12 }}>Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Config name..."
                style={inputStyle}
              />
            </div>

            <div style={{ marginBottom: 12 }}>
              <label style={{ color: "#888", fontSize: 12 }}>Mode</label>
              <select value={mode} onChange={(e) => setMode(e.target.value)} style={inputStyle}>
                {MODES.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>

            {mode === "interval" && (
              <div style={{ marginBottom: 12 }}>
                <label style={{ color: "#888", fontSize: 12 }}>
                  Interval: {intervalPercent}% сообщений
                </label>
                <input
                  type="range"
                  min={1}
                  max={100}
                  value={intervalPercent}
                  onChange={(e) => setIntervalPercent(Number(e.target.value))}
                  style={{ width: "100%" }}
                />
              </div>
            )}

            {mode === "keyword_trigger" && (
              <div style={{ marginBottom: 12 }}>
                <label style={{ color: "#888", fontSize: 12 }}>Keywords (comma-separated)</label>
                <textarea
                  value={keywords}
                  onChange={(e) => setKeywords(e.target.value)}
                  placeholder="marketing, growth, telegram..."
                  rows={3}
                  style={{ ...inputStyle, resize: "vertical" }}
                />
              </div>
            )}

            {mode === "semantic_match" && (
              <div style={{ marginBottom: 12 }}>
                <label style={{ color: "#888", fontSize: 12 }}>Topics (comma-separated)</label>
                <textarea
                  value={topics}
                  onChange={(e) => setTopics(e.target.value)}
                  placeholder="digital marketing, ecommerce, SaaS..."
                  rows={3}
                  style={{ ...inputStyle, resize: "vertical" }}
                />
              </div>
            )}

            <div
              style={{
                background: "#111113",
                border: "1px solid #222",
                borderRadius: 8,
                padding: 16,
                marginBottom: 16,
              }}
            >
              <h3 style={{ color: "#00ff88", fontSize: 14, marginBottom: 12 }}>Product Promotion</h3>
              <div style={{ marginBottom: 8 }}>
                <label style={{ color: "#888", fontSize: 12 }}>Product Name</label>
                <input
                  value={productName}
                  onChange={(e) => setProductName(e.target.value)}
                  placeholder="NEURO COMMENTING"
                  style={inputStyle}
                />
              </div>
              <div style={{ marginBottom: 8 }}>
                <label style={{ color: "#888", fontSize: 12 }}>Description</label>
                <textarea
                  value={productDescription}
                  onChange={(e) => setProductDescription(e.target.value)}
                  rows={2}
                  style={{ ...inputStyle, resize: "vertical" }}
                />
              </div>
              <div style={{ marginBottom: 8 }}>
                <label style={{ color: "#888", fontSize: 12 }}>Problems Solved</label>
                <textarea
                  value={productProblems}
                  onChange={(e) => setProductProblems(e.target.value)}
                  rows={2}
                  style={{ ...inputStyle, resize: "vertical" }}
                />
              </div>
              <div style={{ marginBottom: 8 }}>
                <label style={{ color: "#888", fontSize: 12 }}>Mention Frequency</label>
                <select
                  value={mentionFrequency}
                  onChange={(e) => setMentionFrequency(e.target.value)}
                  style={inputStyle}
                >
                  {MENTION_FREQ.map((f) => (
                    <option key={f.value} value={f.value}>
                      {f.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div style={{ marginBottom: 16 }}>
              <label style={{ color: "#888", fontSize: 12 }}>
                Context Depth: {contextDepth} messages
              </label>
              <input
                type="range"
                min={1}
                max={20}
                value={contextDepth}
                onChange={(e) => setContextDepth(Number(e.target.value))}
                style={{ width: "100%" }}
              />
            </div>

            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={handleSave} disabled={loading} style={btnPrimary}>
                {editId ? "Update Config" : "Create Config"}
              </button>
              {editId && (
                <button onClick={resetForm} style={btnGhost}>
                  Cancel
                </button>
              )}
            </div>
          </div>

          {/* Config List */}
          <div style={{ flex: "1 1 360px" }}>
            <h3 style={{ color: "#ccc", marginBottom: 12, fontSize: 14 }}>
              Active Configs ({configs.length})
            </h3>
            {configs.length === 0 && (
              <p style={{ color: "#555" }}>No configs yet.</p>
            )}
            {configs.map((c) => (
              <div
                key={c.id}
                style={{
                  background: "#111113",
                  border: editId === c.id ? "1px solid #00ff88" : "1px solid #222",
                  borderRadius: 8,
                  padding: 12,
                  marginBottom: 8,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <strong style={{ color: "#eee" }}>{c.name || `Config #${c.id}`}</strong>
                    <div style={{ color: "#888", fontSize: 12 }}>
                      Mode: {c.mode} | Depth: {c.context_depth} | Mention: {c.mention_frequency}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button onClick={() => loadConfigToForm(c)} style={btnSmall}>
                      Edit
                    </button>
                    <button onClick={() => handleDelete(c.id)} style={{ ...btnSmall, color: "#ff4444" }}>
                      Del
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "presets" && (
        <div>
          <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
            <input
              value={presetName}
              onChange={(e) => setPresetName(e.target.value)}
              placeholder="Preset name..."
              style={{ ...inputStyle, maxWidth: 240 }}
            />
            <button onClick={handleSavePreset} style={btnPrimary}>
              Save Current as Preset
            </button>
          </div>

          {presets.length === 0 && <p style={{ color: "#555" }}>No presets saved.</p>}
          {presets.map((p) => (
            <div
              key={p.id}
              style={{
                background: "#111113",
                border: "1px solid #222",
                borderRadius: 8,
                padding: 12,
                marginBottom: 8,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <div>
                <strong style={{ color: "#eee" }}>{p.name}</strong>
                <div style={{ color: "#666", fontSize: 11 }}>
                  {p.created_at ? new Date(p.created_at).toLocaleString() : ""}
                </div>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button onClick={() => handleLoadPreset(p)} style={btnSmall}>
                  Load
                </button>
                <button
                  onClick={() => handleDeletePreset(p.id)}
                  style={{ ...btnSmall, color: "#ff4444" }}
                >
                  Del
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 12px",
  background: "#0a0a0b",
  border: "1px solid #333",
  borderRadius: 6,
  color: "#eee",
  fontSize: 13,
  marginTop: 4,
};

const btnPrimary: React.CSSProperties = {
  padding: "8px 20px",
  background: "#00ff88",
  color: "#0a0a0b",
  border: "none",
  borderRadius: 6,
  fontWeight: 700,
  cursor: "pointer",
};

const btnGhost: React.CSSProperties = {
  padding: "8px 20px",
  background: "transparent",
  color: "#888",
  border: "1px solid #333",
  borderRadius: 6,
  cursor: "pointer",
};

const btnSmall: React.CSSProperties = {
  padding: "4px 10px",
  background: "#1a1a1d",
  color: "#ccc",
  border: "1px solid #333",
  borderRadius: 4,
  cursor: "pointer",
  fontSize: 12,
};
