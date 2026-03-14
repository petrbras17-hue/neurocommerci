import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";

interface MonitoringConfig {
  id: number;
  workspace_id: number;
  channel_id: number;
  channel_title: string | null;
  reaction_emoji: string;
  react_within_seconds: number;
  is_active: boolean;
  accounts_assigned: number[] | null;
  max_reactions_per_hour: number;
  reactions_this_hour: number;
  use_channel_reaction: boolean;
  created_at: string | null;
  is_running: boolean;
}

interface BlacklistEntry {
  id: number;
  workspace_id: number;
  channel_id: number;
  channel_title: string | null;
  reason: string | null;
  created_at: string | null;
}

type TabId = "monitoring" | "blacklist";

const EMOJI_OPTIONS = [
  { value: "\uD83D\uDC4D", label: "\uD83D\uDC4D" },
  { value: "\u2764\uFE0F", label: "\u2764\uFE0F" },
  { value: "\uD83D\uDD25", label: "\uD83D\uDD25" },
  { value: "\uD83C\uDF89", label: "\uD83C\uDF89" },
  { value: "\uD83D\uDE02", label: "\uD83D\uDE02" },
  { value: "\uD83D\uDE31", label: "\uD83D\uDE31" },
  { value: "\uD83D\uDC4F", label: "\uD83D\uDC4F" },
  { value: "\uD83E\uDD14", label: "\uD83E\uDD14" },
];

export function ReactionsV2Page() {
  const { accessToken } = useAuth();
  const [tab, setTab] = useState<TabId>("monitoring");
  const [configs, setConfigs] = useState<MonitoringConfig[]>([]);
  const [blacklist, setBlacklist] = useState<BlacklistEntry[]>([]);
  const [loading, setLoading] = useState(false);

  // Config form
  const [channelId, setChannelId] = useState("");
  const [channelTitle, setChannelTitle] = useState("");
  const [emoji, setEmoji] = useState("\uD83D\uDC4D");
  const [reactWithin, setReactWithin] = useState(30);
  const [accountIds, setAccountIds] = useState("");
  const [maxPerHour, setMaxPerHour] = useState(30);
  const [useChannel, setUseChannel] = useState(false);

  // Blacklist form
  const [blChannelId, setBlChannelId] = useState("");
  const [blChannelTitle, setBlChannelTitle] = useState("");
  const [blReason, setBlReason] = useState("");

  const fetchConfigs = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: MonitoringConfig[] }>("/v1/admin/reactions/monitoring", { accessToken });
      setConfigs(data.items);
    } catch (e) { console.error("fetch configs:", e); }
  }, [accessToken]);

  const fetchBlacklist = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: BlacklistEntry[] }>("/v1/admin/reactions/blacklist", { accessToken });
      setBlacklist(data.items);
    } catch (e) { console.error("fetch blacklist:", e); }
  }, [accessToken]);

  useEffect(() => { fetchConfigs(); fetchBlacklist(); }, [fetchConfigs, fetchBlacklist]);

  const createConfig = async () => {
    if (!channelId) return;
    setLoading(true);
    try {
      const ids = accountIds.split(",").map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n));
      await apiFetch("/v1/admin/reactions/monitoring", {
        accessToken,
        method: "POST",
        json: {
          channel_id: parseInt(channelId, 10),
          channel_title: channelTitle || null,
          reaction_emoji: emoji,
          react_within_seconds: reactWithin,
          accounts_assigned: ids.length > 0 ? ids : null,
          max_reactions_per_hour: maxPerHour,
          use_channel_reaction: useChannel,
        },
      });
      setChannelId(""); setChannelTitle(""); setAccountIds("");
      await fetchConfigs();
    } catch (e) { console.error("create config:", e); }
    setLoading(false);
  };

  const deleteConfig = async (id: number) => {
    try {
      await apiFetch(`/v1/admin/reactions/monitoring/${id}`, { accessToken, method: "DELETE" });
      await fetchConfigs();
    } catch (e) { console.error("delete config:", e); }
  };

  const toggleConfig = async (id: number, action: "start" | "stop") => {
    try {
      await apiFetch(`/v1/admin/reactions/monitoring/${id}/${action}`, { accessToken, method: "POST" });
      await fetchConfigs();
    } catch (e) { console.error(`${action} config:`, e); }
  };

  const addBlacklist = async () => {
    if (!blChannelId) return;
    setLoading(true);
    try {
      await apiFetch("/v1/admin/reactions/blacklist", {
        accessToken,
        method: "POST",
        json: {
          channel_id: parseInt(blChannelId, 10),
          channel_title: blChannelTitle || null,
          reason: blReason || null,
        },
      });
      setBlChannelId(""); setBlChannelTitle(""); setBlReason("");
      await fetchBlacklist();
    } catch (e) { console.error("add blacklist:", e); }
    setLoading(false);
  };

  const removeBlacklist = async (channelId: number) => {
    try {
      await apiFetch(`/v1/admin/reactions/blacklist/${channelId}`, { accessToken, method: "DELETE" });
      await fetchBlacklist();
    } catch (e) { console.error("remove blacklist:", e); }
  };

  const inputStyle: React.CSSProperties = {
    background: "#111", border: "1px solid #333", color: "#e0e0e0",
    padding: "8px 12px", borderRadius: 6, fontSize: 13, width: "100%",
  };
  const btnStyle: React.CSSProperties = {
    background: "#00ff88", color: "#000", border: "none", padding: "8px 16px",
    borderRadius: 6, fontWeight: 600, cursor: "pointer", fontSize: 13,
  };
  const btnDanger: React.CSSProperties = {
    ...btnStyle, background: "#ff4444", color: "#fff",
  };
  const cardStyle: React.CSSProperties = {
    background: "#111", border: "1px solid #222", borderRadius: 8,
    padding: 16, marginBottom: 12,
  };

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: "0 auto" }}>
      <h1 style={{ color: "#00ff88", fontFamily: "JetBrains Mono, monospace", marginBottom: 24 }}>
        Reactions v2
      </h1>

      <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
        {(["monitoring", "blacklist"] as TabId[]).map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            ...btnStyle,
            background: tab === t ? "#00ff88" : "#222",
            color: tab === t ? "#000" : "#888",
          }}>
            {t === "monitoring" ? "Monitoring Setup" : "Blacklist"}
          </button>
        ))}
      </div>

      {tab === "monitoring" && (
        <>
          <div style={{ ...cardStyle, marginBottom: 24 }}>
            <h3 style={{ color: "#888", marginBottom: 12, fontSize: 14 }}>New Monitoring Config</h3>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
              <div>
                <label style={{ color: "#666", fontSize: 11 }}>Channel ID *</label>
                <input value={channelId} onChange={e => setChannelId(e.target.value)}
                       placeholder="e.g. -1001234567890" style={inputStyle} />
              </div>
              <div>
                <label style={{ color: "#666", fontSize: 11 }}>Channel Title</label>
                <input value={channelTitle} onChange={e => setChannelTitle(e.target.value)}
                       placeholder="Optional" style={inputStyle} />
              </div>
              <div>
                <label style={{ color: "#666", fontSize: 11 }}>Emoji</label>
                <select value={emoji} onChange={e => setEmoji(e.target.value)}
                        style={{ ...inputStyle, cursor: "pointer" }}>
                  {EMOJI_OPTIONS.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label style={{ color: "#666", fontSize: 11 }}>
                  React within (sec): {reactWithin}
                </label>
                <input type="range" min={5} max={120} value={reactWithin}
                       onChange={e => setReactWithin(Number(e.target.value))}
                       style={{ width: "100%", accentColor: "#00ff88" }} />
              </div>
              <div>
                <label style={{ color: "#666", fontSize: 11 }}>Account IDs (comma separated)</label>
                <input value={accountIds} onChange={e => setAccountIds(e.target.value)}
                       placeholder="1,2,3" style={inputStyle} />
              </div>
              <div>
                <label style={{ color: "#666", fontSize: 11 }}>Max per hour</label>
                <input type="number" min={1} max={200} value={maxPerHour}
                       onChange={e => setMaxPerHour(Number(e.target.value))} style={inputStyle} />
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
              <label style={{ color: "#666", fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
                <input type="checkbox" checked={useChannel} onChange={e => setUseChannel(e.target.checked)} />
                React as channel (Premium)
              </label>
            </div>
            <button onClick={createConfig} disabled={loading || !channelId} style={{
              ...btnStyle, opacity: (loading || !channelId) ? 0.5 : 1,
            }}>
              Create Config
            </button>
          </div>

          <h3 style={{ color: "#888", marginBottom: 12, fontSize: 14 }}>Active Configs ({configs.length})</h3>
          {configs.map(cfg => (
            <div key={cfg.id} style={cardStyle}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <span style={{ color: "#00ff88", fontWeight: 600 }}>
                    {cfg.channel_title || `Channel ${cfg.channel_id}`}
                  </span>
                  <span style={{ color: "#666", marginLeft: 8, fontSize: 12 }}>
                    ID: {cfg.channel_id}
                  </span>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span style={{
                    display: "inline-block", width: 8, height: 8, borderRadius: "50%",
                    background: cfg.is_running ? "#00ff88" : "#666",
                    boxShadow: cfg.is_running ? "0 0 6px #00ff88" : "none",
                  }} />
                  <span style={{ color: cfg.is_running ? "#00ff88" : "#666", fontSize: 12 }}>
                    {cfg.is_running ? "Running" : "Stopped"}
                  </span>
                </div>
              </div>
              <div style={{ display: "flex", gap: 16, marginTop: 8, color: "#888", fontSize: 12 }}>
                <span>Emoji: {cfg.reaction_emoji}</span>
                <span>Within: {cfg.react_within_seconds}s</span>
                <span>Rate: {cfg.reactions_this_hour}/{cfg.max_reactions_per_hour}/hr</span>
                <span>Accounts: {cfg.accounts_assigned?.length || 0}</span>
                {cfg.use_channel_reaction && <span style={{ color: "#a855f7" }}>PREMIUM</span>}
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                {cfg.is_running ? (
                  <button onClick={() => toggleConfig(cfg.id, "stop")}
                          style={{ ...btnStyle, background: "#f59e0b", color: "#000", fontSize: 12 }}>
                    Stop
                  </button>
                ) : (
                  <button onClick={() => toggleConfig(cfg.id, "start")}
                          style={{ ...btnStyle, fontSize: 12 }}>
                    Start
                  </button>
                )}
                <button onClick={() => deleteConfig(cfg.id)}
                        style={{ ...btnDanger, fontSize: 12 }}>
                  Delete
                </button>
              </div>
            </div>
          ))}
          {configs.length === 0 && (
            <p style={{ color: "#555", fontSize: 13 }}>No monitoring configs yet.</p>
          )}
        </>
      )}

      {tab === "blacklist" && (
        <>
          <div style={{ ...cardStyle, marginBottom: 24 }}>
            <h3 style={{ color: "#888", marginBottom: 12, fontSize: 14 }}>Add to Blacklist</h3>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginBottom: 12 }}>
              <div>
                <label style={{ color: "#666", fontSize: 11 }}>Channel ID *</label>
                <input value={blChannelId} onChange={e => setBlChannelId(e.target.value)}
                       placeholder="e.g. -1001234567890" style={inputStyle} />
              </div>
              <div>
                <label style={{ color: "#666", fontSize: 11 }}>Channel Title</label>
                <input value={blChannelTitle} onChange={e => setBlChannelTitle(e.target.value)}
                       placeholder="Optional" style={inputStyle} />
              </div>
              <div>
                <label style={{ color: "#666", fontSize: 11 }}>Reason</label>
                <input value={blReason} onChange={e => setBlReason(e.target.value)}
                       placeholder="Optional" style={inputStyle} />
              </div>
            </div>
            <button onClick={addBlacklist} disabled={loading || !blChannelId} style={{
              ...btnStyle, opacity: (loading || !blChannelId) ? 0.5 : 1,
            }}>
              Add to Blacklist
            </button>
          </div>

          <h3 style={{ color: "#888", marginBottom: 12, fontSize: 14 }}>Blacklisted Channels ({blacklist.length})</h3>
          {blacklist.map(entry => (
            <div key={entry.id} style={cardStyle}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <span style={{ color: "#ff4444", fontWeight: 600 }}>
                    {entry.channel_title || `Channel ${entry.channel_id}`}
                  </span>
                  <span style={{ color: "#666", marginLeft: 8, fontSize: 12 }}>
                    ID: {entry.channel_id}
                  </span>
                  {entry.reason && (
                    <span style={{ color: "#888", marginLeft: 8, fontSize: 12 }}>
                      — {entry.reason}
                    </span>
                  )}
                </div>
                <button onClick={() => removeBlacklist(entry.channel_id)}
                        style={{ ...btnDanger, fontSize: 12 }}>
                  Remove
                </button>
              </div>
            </div>
          ))}
          {blacklist.length === 0 && (
            <p style={{ color: "#555", fontSize: 13 }}>No blacklisted channels.</p>
          )}
        </>
      )}
    </div>
  );
}
