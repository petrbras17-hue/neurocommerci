import { useEffect, useState, useCallback } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";

// ── Types ──

interface BlacklistEntry {
  id: number;
  channel_id: number;
  channel_username: string | null;
  channel_title: string | null;
  reason: string | null;
  created_at: string | null;
}

interface WhitelistEntry {
  id: number;
  channel_id: number;
  channel_username: string | null;
  channel_title: string | null;
  successful_comments: number;
  created_at: string | null;
}

interface PresetEntry {
  id: number;
  name: string;
  config: Record<string, unknown>;
  targeting_mode: string;
  comment_as_channel: boolean;
  auto_dm_enabled: boolean;
  language: string;
  created_at: string | null;
}

interface AutoDmEntry {
  id: number;
  farm_id: number;
  message: string;
  is_active: boolean;
  max_dms_per_day: number;
  dms_sent_today: number;
  created_at: string | null;
}

type Tab = "setup" | "blacklist" | "whitelist" | "presets" | "autodm";

// ── Component ──

export function NeurocommentingPage() {
  const { accessToken } = useAuth();
  const [tab, setTab] = useState<Tab>("setup");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Blacklist state
  const [blacklist, setBlacklist] = useState<BlacklistEntry[]>([]);
  const [blChannelId, setBlChannelId] = useState("");
  const [blUsername, setBlUsername] = useState("");
  const [blTitle, setBlTitle] = useState("");

  // Whitelist state
  const [whitelist, setWhitelist] = useState<WhitelistEntry[]>([]);
  const [wlChannelId, setWlChannelId] = useState("");
  const [wlUsername, setWlUsername] = useState("");
  const [wlTitle, setWlTitle] = useState("");

  // Presets state
  const [presets, setPresets] = useState<PresetEntry[]>([]);
  const [presetName, setPresetName] = useState("");
  const [presetConfig, setPresetConfig] = useState("{}");

  // Auto-DM state
  const [autoDm, setAutoDm] = useState<AutoDmEntry | null>(null);
  const [dmFarmId, setDmFarmId] = useState("");
  const [dmMessage, setDmMessage] = useState("");
  const [dmMaxPerDay, setDmMaxPerDay] = useState("10");

  // Farm setup state
  const [targetingMode, setTargetingMode] = useState("all");
  const [targetingPct, setTargetingPct] = useState("30");
  const [targetingKeywords, setTargetingKeywords] = useState("");
  const [commentAsChannel, setCommentAsChannel] = useState(false);
  const [language, setLanguage] = useState("auto");
  const [folderName, setFolderName] = useState("");

  // ── Load data ──

  const loadBlacklist = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: BlacklistEntry[] }>("/v1/admin/blacklist", { accessToken });
      setBlacklist(data.items || []);
    } catch (e: unknown) { setError(String(e)); }
  }, [accessToken]);

  const loadWhitelist = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: WhitelistEntry[] }>("/v1/admin/whitelist", { accessToken });
      setWhitelist(data.items || []);
    } catch (e: unknown) { setError(String(e)); }
  }, [accessToken]);

  const loadPresets = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: PresetEntry[] }>("/v1/admin/presets", { accessToken });
      setPresets(data.items || []);
    } catch (e: unknown) { setError(String(e)); }
  }, [accessToken]);

  useEffect(() => {
    if (tab === "blacklist") { void loadBlacklist(); }
    if (tab === "whitelist") { void loadWhitelist(); }
    if (tab === "presets") { void loadPresets(); }
  }, [tab, loadBlacklist, loadWhitelist, loadPresets]);

  // ── Actions ──

  const addBlacklist = async () => {
    if (!blChannelId) return;
    setLoading(true);
    setError(null);
    try {
      await apiFetch("/v1/admin/blacklist", {
        accessToken,
        method: "POST",
        body: { channel_id: Number(blChannelId), channel_username: blUsername || undefined, channel_title: blTitle || undefined },
      });
      setBlChannelId(""); setBlUsername(""); setBlTitle("");
      await loadBlacklist();
    } catch (e: unknown) { setError(String(e)); }
    setLoading(false);
  };

  const removeBlacklist = async (channelId: number) => {
    setLoading(true);
    try {
      await apiFetch(`/v1/admin/blacklist/${channelId}`, { accessToken, method: "DELETE" });
      await loadBlacklist();
    } catch (e: unknown) { setError(String(e)); }
    setLoading(false);
  };

  const addWhitelist = async () => {
    if (!wlChannelId) return;
    setLoading(true);
    setError(null);
    try {
      await apiFetch("/v1/admin/whitelist", {
        accessToken,
        method: "POST",
        body: { channel_id: Number(wlChannelId), channel_username: wlUsername || undefined, channel_title: wlTitle || undefined },
      });
      setWlChannelId(""); setWlUsername(""); setWlTitle("");
      await loadWhitelist();
    } catch (e: unknown) { setError(String(e)); }
    setLoading(false);
  };

  const removeWhitelist = async (channelId: number) => {
    setLoading(true);
    try {
      await apiFetch(`/v1/admin/whitelist/${channelId}`, { accessToken, method: "DELETE" });
      await loadWhitelist();
    } catch (e: unknown) { setError(String(e)); }
    setLoading(false);
  };

  const savePreset = async () => {
    if (!presetName) return;
    setLoading(true);
    setError(null);
    try {
      const configObj = JSON.parse(presetConfig);
      await apiFetch("/v1/admin/presets", {
        accessToken,
        method: "POST",
        body: {
          name: presetName,
          config: configObj,
          targeting_mode: targetingMode,
          targeting_params: targetingMode === "random_pct" ? { pct: Number(targetingPct) }
            : targetingMode === "keyword_match" ? { keywords: targetingKeywords.split(",").map(k => k.trim()).filter(Boolean) }
            : undefined,
          comment_as_channel: commentAsChannel,
          language,
        },
      });
      setPresetName("");
      await loadPresets();
    } catch (e: unknown) { setError(String(e)); }
    setLoading(false);
  };

  const deletePreset = async (id: number) => {
    setLoading(true);
    try {
      await apiFetch(`/v1/admin/presets/${id}`, { accessToken, method: "DELETE" });
      await loadPresets();
    } catch (e: unknown) { setError(String(e)); }
    setLoading(false);
  };

  const setupAutoDm = async () => {
    if (!dmFarmId || !dmMessage) return;
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<AutoDmEntry>(`/v1/admin/farm/${dmFarmId}/auto-dm`, {
        accessToken,
        method: "POST",
        body: { message: dmMessage, max_dms_per_day: Number(dmMaxPerDay) },
      });
      setAutoDm(data);
    } catch (e: unknown) { setError(String(e)); }
    setLoading(false);
  };

  const loadAutoDm = async () => {
    if (!dmFarmId) return;
    try {
      const data = await apiFetch<AutoDmEntry>(`/v1/admin/farm/${dmFarmId}/auto-dm`, { accessToken });
      setAutoDm(data);
      setDmMessage(data.message);
      setDmMaxPerDay(String(data.max_dms_per_day));
    } catch { setAutoDm(null); }
  };

  const deleteAutoDm = async () => {
    if (!dmFarmId) return;
    setLoading(true);
    try {
      await apiFetch(`/v1/admin/farm/${dmFarmId}/auto-dm`, { accessToken, method: "DELETE" });
      setAutoDm(null);
      setDmMessage("");
    } catch (e: unknown) { setError(String(e)); }
    setLoading(false);
  };

  const importFolder = async () => {
    if (!folderName || !dmFarmId) return;
    setLoading(true);
    setError(null);
    try {
      await apiFetch(`/v1/admin/farm/${dmFarmId}/import-folder`, {
        accessToken,
        method: "POST",
        body: { folder_name: folderName },
      });
    } catch (e: unknown) { setError(String(e)); }
    setLoading(false);
  };

  // ── Styles ──

  const S = {
    page: { padding: "24px", color: "#e0e0e0", fontFamily: "'Geist Sans', 'Inter', sans-serif" } as const,
    tabs: { display: "flex", gap: "8px", marginBottom: "24px", flexWrap: "wrap" as const },
    tab: (active: boolean) => ({
      padding: "8px 16px",
      borderRadius: "6px",
      border: "1px solid " + (active ? "#00ff88" : "#333"),
      background: active ? "rgba(0,255,136,0.1)" : "#111",
      color: active ? "#00ff88" : "#888",
      cursor: "pointer" as const,
      fontSize: "13px",
      fontWeight: active ? 600 : 400,
    }),
    card: { background: "#111", border: "1px solid #222", borderRadius: "8px", padding: "16px", marginBottom: "16px" } as const,
    input: {
      background: "#0a0a0b", border: "1px solid #333", borderRadius: "4px", padding: "8px 12px",
      color: "#e0e0e0", fontSize: "13px", fontFamily: "'JetBrains Mono', monospace", width: "100%",
    } as const,
    btn: {
      padding: "8px 16px", borderRadius: "6px", border: "1px solid #00ff88",
      background: "rgba(0,255,136,0.1)", color: "#00ff88", cursor: "pointer" as const, fontSize: "13px",
    } as const,
    btnDanger: {
      padding: "6px 12px", borderRadius: "4px", border: "1px solid #ff4444",
      background: "rgba(255,68,68,0.1)", color: "#ff4444", cursor: "pointer" as const, fontSize: "12px",
    } as const,
    label: { fontSize: "12px", color: "#888", marginBottom: "4px", display: "block" as const } as const,
    row: { display: "flex", gap: "8px", alignItems: "center", marginBottom: "8px", flexWrap: "wrap" as const },
    table: { width: "100%", borderCollapse: "collapse" as const, fontSize: "13px" } as const,
    th: { textAlign: "left" as const, padding: "8px", borderBottom: "1px solid #333", color: "#00ff88", fontSize: "11px", textTransform: "uppercase" as const } as const,
    td: { padding: "8px", borderBottom: "1px solid #1a1a1a" } as const,
    error: { color: "#ff4444", fontSize: "13px", marginBottom: "12px" } as const,
    select: {
      background: "#0a0a0b", border: "1px solid #333", borderRadius: "4px", padding: "8px 12px",
      color: "#e0e0e0", fontSize: "13px",
    } as const,
    textarea: {
      background: "#0a0a0b", border: "1px solid #333", borderRadius: "4px", padding: "8px 12px",
      color: "#e0e0e0", fontSize: "13px", fontFamily: "'JetBrains Mono', monospace",
      width: "100%", minHeight: "80px", resize: "vertical" as const,
    } as const,
  };

  return (
    <div style={S.page}>
      <h2 style={{ color: "#00ff88", margin: "0 0 16px", fontSize: "18px" }}>
        Neurocommenting v2
      </h2>

      {error && <div style={S.error}>{error}</div>}

      <div style={S.tabs}>
        {([
          ["setup", "Farm Setup"],
          ["blacklist", "Blacklist"],
          ["whitelist", "Whitelist"],
          ["presets", "Presets"],
          ["autodm", "Auto-DM"],
        ] as [Tab, string][]).map(([key, label]) => (
          <button key={key} style={S.tab(tab === key)} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>

      {/* ── Farm Setup ── */}
      {tab === "setup" && (
        <div style={S.card}>
          <h3 style={{ color: "#ccc", marginTop: 0, fontSize: "14px" }}>Farm Setup</h3>

          <div style={S.row}>
            <div style={{ flex: 1 }}>
              <label style={S.label}>Targeting Mode</label>
              <select style={S.select} value={targetingMode} onChange={e => setTargetingMode(e.target.value)}>
                <option value="all">All Posts</option>
                <option value="random_pct">Random %</option>
                <option value="keyword_match">Keyword Match</option>
              </select>
            </div>
            {targetingMode === "random_pct" && (
              <div style={{ flex: 1 }}>
                <label style={S.label}>Percentage</label>
                <input style={S.input} type="number" min="1" max="100" value={targetingPct}
                  onChange={e => setTargetingPct(e.target.value)} />
              </div>
            )}
            {targetingMode === "keyword_match" && (
              <div style={{ flex: 2 }}>
                <label style={S.label}>Keywords (comma-separated)</label>
                <input style={S.input} value={targetingKeywords}
                  onChange={e => setTargetingKeywords(e.target.value)} placeholder="crypto, AI, marketing" />
              </div>
            )}
          </div>

          <div style={S.row}>
            <label style={{ ...S.label, display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
              <input type="checkbox" checked={commentAsChannel} onChange={e => setCommentAsChannel(e.target.checked)} />
              Comment as Channel
            </label>
          </div>

          <div style={S.row}>
            <div style={{ flex: 1 }}>
              <label style={S.label}>Language</label>
              <select style={S.select} value={language} onChange={e => setLanguage(e.target.value)}>
                <option value="auto">Auto-detect</option>
                <option value="ru">Russian</option>
                <option value="en">English</option>
                <option value="uk">Ukrainian</option>
                <option value="kz">Kazakh</option>
              </select>
            </div>
          </div>

          <div style={{ ...S.row, marginTop: "16px" }}>
            <div style={{ flex: 1 }}>
              <label style={S.label}>Import from TG Folder</label>
              <div style={{ display: "flex", gap: "8px" }}>
                <input style={{ ...S.input, flex: 1 }} value={folderName}
                  onChange={e => setFolderName(e.target.value)} placeholder="Folder name" />
                <input style={{ ...S.input, width: "100px" }} value={dmFarmId}
                  onChange={e => setDmFarmId(e.target.value)} placeholder="Farm ID" />
                <button style={S.btn} onClick={importFolder} disabled={loading}>Import</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Blacklist ── */}
      {tab === "blacklist" && (
        <div style={S.card}>
          <h3 style={{ color: "#ccc", marginTop: 0, fontSize: "14px" }}>Channel Blacklist</h3>
          <div style={S.row}>
            <input style={{ ...S.input, width: "140px" }} value={blChannelId}
              onChange={e => setBlChannelId(e.target.value)} placeholder="Channel ID" />
            <input style={{ ...S.input, width: "140px" }} value={blUsername}
              onChange={e => setBlUsername(e.target.value)} placeholder="@username" />
            <input style={{ ...S.input, width: "200px" }} value={blTitle}
              onChange={e => setBlTitle(e.target.value)} placeholder="Title" />
            <button style={S.btn} onClick={addBlacklist} disabled={loading}>Add</button>
          </div>
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>Channel ID</th>
                <th style={S.th}>Username</th>
                <th style={S.th}>Title</th>
                <th style={S.th}>Reason</th>
                <th style={S.th}>Date</th>
                <th style={S.th}></th>
              </tr>
            </thead>
            <tbody>
              {blacklist.map(b => (
                <tr key={b.id}>
                  <td style={S.td}>{b.channel_id}</td>
                  <td style={S.td}>{b.channel_username || "-"}</td>
                  <td style={S.td}>{b.channel_title || "-"}</td>
                  <td style={S.td}><span style={{ color: b.reason === "auto_ban" ? "#ff4444" : "#888" }}>{b.reason}</span></td>
                  <td style={S.td}>{b.created_at ? new Date(b.created_at).toLocaleDateString() : "-"}</td>
                  <td style={S.td}><button style={S.btnDanger} onClick={() => removeBlacklist(b.channel_id)}>Remove</button></td>
                </tr>
              ))}
              {blacklist.length === 0 && (
                <tr><td style={{ ...S.td, color: "#555" }} colSpan={6}>No blacklisted channels</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Whitelist ── */}
      {tab === "whitelist" && (
        <div style={S.card}>
          <h3 style={{ color: "#ccc", marginTop: 0, fontSize: "14px" }}>Channel Whitelist</h3>
          <div style={S.row}>
            <input style={{ ...S.input, width: "140px" }} value={wlChannelId}
              onChange={e => setWlChannelId(e.target.value)} placeholder="Channel ID" />
            <input style={{ ...S.input, width: "140px" }} value={wlUsername}
              onChange={e => setWlUsername(e.target.value)} placeholder="@username" />
            <input style={{ ...S.input, width: "200px" }} value={wlTitle}
              onChange={e => setWlTitle(e.target.value)} placeholder="Title" />
            <button style={S.btn} onClick={addWhitelist} disabled={loading}>Add</button>
          </div>
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>Channel ID</th>
                <th style={S.th}>Username</th>
                <th style={S.th}>Title</th>
                <th style={S.th}>Comments</th>
                <th style={S.th}>Date</th>
                <th style={S.th}></th>
              </tr>
            </thead>
            <tbody>
              {whitelist.map(w => (
                <tr key={w.id}>
                  <td style={S.td}>{w.channel_id}</td>
                  <td style={S.td}>{w.channel_username || "-"}</td>
                  <td style={S.td}>{w.channel_title || "-"}</td>
                  <td style={S.td}><span style={{ color: "#00ff88" }}>{w.successful_comments}</span></td>
                  <td style={S.td}>{w.created_at ? new Date(w.created_at).toLocaleDateString() : "-"}</td>
                  <td style={S.td}><button style={S.btnDanger} onClick={() => removeWhitelist(w.channel_id)}>Remove</button></td>
                </tr>
              ))}
              {whitelist.length === 0 && (
                <tr><td style={{ ...S.td, color: "#555" }} colSpan={6}>No whitelisted channels</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Presets ── */}
      {tab === "presets" && (
        <div style={S.card}>
          <h3 style={{ color: "#ccc", marginTop: 0, fontSize: "14px" }}>Farm Presets</h3>
          <div style={S.row}>
            <input style={{ ...S.input, width: "200px" }} value={presetName}
              onChange={e => setPresetName(e.target.value)} placeholder="Preset name" />
            <button style={S.btn} onClick={savePreset} disabled={loading}>Save Current Config</button>
          </div>
          <div style={{ marginBottom: "12px" }}>
            <label style={S.label}>Config JSON</label>
            <textarea style={S.textarea} value={presetConfig}
              onChange={e => setPresetConfig(e.target.value)} />
          </div>
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>Name</th>
                <th style={S.th}>Targeting</th>
                <th style={S.th}>As Channel</th>
                <th style={S.th}>Language</th>
                <th style={S.th}>Date</th>
                <th style={S.th}></th>
              </tr>
            </thead>
            <tbody>
              {presets.map(p => (
                <tr key={p.id}>
                  <td style={S.td}>{p.name}</td>
                  <td style={S.td}>{p.targeting_mode}</td>
                  <td style={S.td}>{p.comment_as_channel ? "Yes" : "No"}</td>
                  <td style={S.td}>{p.language}</td>
                  <td style={S.td}>{p.created_at ? new Date(p.created_at).toLocaleDateString() : "-"}</td>
                  <td style={S.td}><button style={S.btnDanger} onClick={() => deletePreset(p.id)}>Delete</button></td>
                </tr>
              ))}
              {presets.length === 0 && (
                <tr><td style={{ ...S.td, color: "#555" }} colSpan={6}>No presets saved</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Auto-DM ── */}
      {tab === "autodm" && (
        <div style={S.card}>
          <h3 style={{ color: "#ccc", marginTop: 0, fontSize: "14px" }}>Auto-DM Configuration</h3>
          <div style={S.row}>
            <div style={{ width: "120px" }}>
              <label style={S.label}>Farm ID</label>
              <input style={S.input} value={dmFarmId}
                onChange={e => setDmFarmId(e.target.value)} placeholder="Farm ID" />
            </div>
            <div style={{ alignSelf: "flex-end" }}>
              <button style={S.btn} onClick={loadAutoDm} disabled={loading}>Load</button>
            </div>
          </div>
          <div style={{ marginBottom: "12px" }}>
            <label style={S.label}>Auto-DM Message</label>
            <textarea style={S.textarea} value={dmMessage}
              onChange={e => setDmMessage(e.target.value)} placeholder="Hello! Thanks for reaching out..." />
          </div>
          <div style={S.row}>
            <div style={{ width: "150px" }}>
              <label style={S.label}>Max DMs per day</label>
              <input style={S.input} type="number" min="1" max="100" value={dmMaxPerDay}
                onChange={e => setDmMaxPerDay(e.target.value)} />
            </div>
          </div>
          <div style={{ ...S.row, marginTop: "12px" }}>
            <button style={S.btn} onClick={setupAutoDm} disabled={loading}>
              {autoDm ? "Update" : "Enable"} Auto-DM
            </button>
            {autoDm && (
              <button style={S.btnDanger} onClick={deleteAutoDm} disabled={loading}>Disable</button>
            )}
          </div>
          {autoDm && (
            <div style={{ marginTop: "12px", fontSize: "12px", color: "#888" }}>
              Status: <span style={{ color: autoDm.is_active ? "#00ff88" : "#ff4444" }}>
                {autoDm.is_active ? "Active" : "Inactive"}
              </span>
              {" | "}Sent today: {autoDm.dms_sent_today} / {autoDm.max_dms_per_day}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
