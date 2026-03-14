import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";
import { useSearchParams } from "react-router-dom";

type PackagingStatus = {
  account_id: number;
  phone: string;
  packaging_status: string;
  profile_generated: boolean;
  profile_gender: string | null;
  profile_age_range: string | null;
  profile_country: string | null;
  profile_profession: string | null;
  profile_first_name: string | null;
  profile_last_name: string | null;
  profile_username: string | null;
  profile_bio: string | null;
  avatar_path: string | null;
  avatar_ready: boolean;
  profile_applied: boolean;
  profile_applied_at: string | null;
  channel_created: boolean;
  channel_id: number | null;
  channel_username: string | null;
  channel_title: string | null;
  channel_created_at: string | null;
  guard_48h_active: boolean;
  guard_48h_message: string | null;
  profile_change_earliest: string | null;
};

type AccountListItem = {
  id: number;
  phone: string;
  display_name: string | null;
  status: string;
};

const STATUS_COLORS: Record<string, string> = {
  not_started: "#666",
  profile_generated: "#4488ff",
  avatar_set: "#ff8844",
  channel_created: "#aa44ff",
  fully_packaged: "#00ff88",
};

export function AccountPackagingPage() {
  const { accessToken } = useAuth();
  const [searchParams] = useSearchParams();
  const preselectedId = searchParams.get("account_id");

  const [accounts, setAccounts] = useState<AccountListItem[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(
    preselectedId ? Number(preselectedId) : null
  );
  const [pkgStatus, setPkgStatus] = useState<PackagingStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [log, setLog] = useState<string[]>([]);

  // Profile generation params
  const [gender, setGender] = useState("female");
  const [country, setCountry] = useState("RU");
  const [ageRange, setAgeRange] = useState("25-35");
  const [profession, setProfession] = useState("marketing");

  // Channel creation params
  const [channelTitle, setChannelTitle] = useState("");
  const [channelDesc, setChannelDesc] = useState("");
  const [firstPost, setFirstPost] = useState("");

  // Avatar prompt
  const [avatarPrompt, setAvatarPrompt] = useState("professional avatar photo");

  const addLog = useCallback((msg: string) => {
    setLog((prev) => [...prev, `[${new Date().toLocaleTimeString("ru-RU")}] ${msg}`]);
  }, []);

  const loadAccounts = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: AccountListItem[] }>(
        "/v1/admin/onboarding/accounts?limit=200",
        { accessToken }
      );
      setAccounts(data.items);
    } catch (e) {
      console.error("Failed to load accounts:", e);
    }
  }, [accessToken]);

  const loadStatus = useCallback(async () => {
    if (!selectedId) return;
    setLoading(true);
    try {
      const data = await apiFetch<PackagingStatus>(
        `/v1/admin/accounts/${selectedId}/packaging-status`,
        { accessToken }
      );
      setPkgStatus(data);
    } catch (e) {
      addLog(`Error loading status: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [selectedId, accessToken, addLog]);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    if (selectedId) void loadStatus();
  }, [selectedId, loadStatus]);

  const handleGenerateProfile = async () => {
    if (!selectedId) return;
    setActionLoading(true);
    addLog("Generating AI profile...");
    try {
      const result = await apiFetch<{ profile: Record<string, string> }>(
        `/v1/admin/accounts/${selectedId}/generate-profile`,
        {
          method: "POST",
          json: { gender, country, age_range: ageRange, profession },
          accessToken,
        }
      );
      addLog(`Profile generated: ${result.profile.first_name} ${result.profile.last_name}`);
      await loadStatus();
    } catch (e) {
      addLog(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleApplyProfile = async () => {
    if (!selectedId) return;
    setActionLoading(true);
    addLog("Applying profile to Telegram...");
    try {
      const result = await apiFetch<Record<string, unknown>>(
        `/v1/admin/accounts/${selectedId}/apply-profile`,
        { method: "POST", json: {}, accessToken }
      );
      addLog(`Profile applied: name=${String(result.name_set)}, bio=${String(result.bio_set)}`);
      await loadStatus();
    } catch (e) {
      addLog(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleGenerateAvatar = async () => {
    if (!selectedId) return;
    setActionLoading(true);
    addLog("Generating avatar...");
    try {
      await apiFetch(
        `/v1/admin/accounts/${selectedId}/generate-avatar`,
        { method: "POST", json: { prompt: avatarPrompt }, accessToken }
      );
      addLog("Avatar generated");
      await loadStatus();
    } catch (e) {
      addLog(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleUploadAvatar = async (files: FileList | null) => {
    if (!selectedId || !files || files.length === 0) return;
    setActionLoading(true);
    addLog("Uploading avatar...");
    try {
      const formData = new FormData();
      formData.append("avatar", files[0]);
      await apiFetch(
        `/v1/admin/accounts/${selectedId}/upload-avatar`,
        { method: "POST", body: formData, accessToken }
      );
      addLog("Avatar uploaded");
      await loadStatus();
    } catch (e) {
      addLog(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleApplyAvatar = async () => {
    if (!selectedId) return;
    setActionLoading(true);
    addLog("Applying avatar to Telegram...");
    try {
      const result = await apiFetch<Record<string, unknown>>(
        `/v1/admin/accounts/${selectedId}/apply-avatar`,
        { method: "POST", json: {}, accessToken }
      );
      addLog(`Avatar applied: ${String(result.avatar_set)}`);
      await loadStatus();
    } catch (e) {
      addLog(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleCreateChannel = async () => {
    if (!selectedId || !channelTitle.trim()) return;
    setActionLoading(true);
    addLog("Creating Telegram channel...");
    try {
      const result = await apiFetch<Record<string, unknown>>(
        `/v1/admin/accounts/${selectedId}/create-channel`,
        {
          method: "POST",
          json: {
            title: channelTitle,
            description: channelDesc,
            first_post_text: firstPost,
          },
          accessToken,
        }
      );
      addLog(`Channel created: ${String(result.channel_created)}`);
      await loadStatus();
    } catch (e) {
      addLog(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setActionLoading(false);
    }
  };

  const stepDone = (ok: boolean) =>
    ok ? (
      <span style={{ color: "#00ff88", fontWeight: 600 }}>DONE</span>
    ) : (
      <span style={{ color: "#666" }}>---</span>
    );

  return (
    <div style={{ maxWidth: 900, margin: "0 auto" }}>
      <h1 style={{ color: "#00ff88", marginBottom: 8 }}>Account Packaging</h1>
      <p style={{ color: "#888", marginBottom: 24 }}>
        Generate profile, avatar, and create channel for accounts.
      </p>

      {/* Account selector */}
      <div style={{ marginBottom: 24 }}>
        <label style={{ color: "#aaa", display: "block", marginBottom: 6 }}>Select Account</label>
        <select
          value={selectedId ?? ""}
          onChange={(e) => setSelectedId(e.target.value ? Number(e.target.value) : null)}
          style={{
            width: "100%",
            padding: "10px 12px",
            background: "#111",
            color: "#eee",
            border: "1px solid #333",
            borderRadius: 6,
            fontSize: 14,
          }}
        >
          <option value="">-- Select account --</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.phone} {a.display_name ? `(${a.display_name})` : ""} [{a.status}]
            </option>
          ))}
        </select>
      </div>

      {selectedId && pkgStatus && (
        <>
          {/* Status overview */}
          <div
            style={{
              background: "#111",
              border: "1px solid #222",
              borderRadius: 8,
              padding: 20,
              marginBottom: 24,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
              <span
                style={{
                  display: "inline-block",
                  width: 12,
                  height: 12,
                  borderRadius: "50%",
                  background: STATUS_COLORS[pkgStatus.packaging_status] || "#666",
                }}
              />
              <strong style={{ color: "#eee", fontSize: 16 }}>
                {pkgStatus.packaging_status.replace(/_/g, " ").toUpperCase()}
              </strong>
              <span style={{ color: "#888" }}>| {pkgStatus.phone}</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 16 }}>
              <div>
                <div style={{ color: "#888", fontSize: 12, marginBottom: 4 }}>Profile</div>
                {stepDone(pkgStatus.profile_generated)}
              </div>
              <div>
                <div style={{ color: "#888", fontSize: 12, marginBottom: 4 }}>Avatar</div>
                {stepDone(pkgStatus.avatar_ready)}
              </div>
              <div>
                <div style={{ color: "#888", fontSize: 12, marginBottom: 4 }}>Applied</div>
                {stepDone(pkgStatus.profile_applied)}
              </div>
              <div>
                <div style={{ color: "#888", fontSize: 12, marginBottom: 4 }}>Channel</div>
                {stepDone(pkgStatus.channel_created)}
              </div>
            </div>
            {pkgStatus.guard_48h_active && (
              <div
                style={{
                  marginTop: 12,
                  padding: "8px 12px",
                  background: "#331800",
                  border: "1px solid #663300",
                  borderRadius: 6,
                  color: "#ff8844",
                  fontSize: 13,
                }}
              >
                48h guard active: {pkgStatus.guard_48h_message}
              </div>
            )}
          </div>

          {/* Profile generation */}
          <div
            style={{
              background: "#111",
              border: "1px solid #222",
              borderRadius: 8,
              padding: 20,
              marginBottom: 24,
            }}
          >
            <h3 style={{ color: "#eee", marginBottom: 16 }}>1. Generate Profile</h3>
            {pkgStatus.profile_generated && (
              <div
                style={{
                  background: "#0a1a0a",
                  border: "1px solid #1a3a1a",
                  borderRadius: 6,
                  padding: 12,
                  marginBottom: 16,
                }}
              >
                <div style={{ color: "#00ff88", fontSize: 13, marginBottom: 8 }}>
                  Current profile:
                </div>
                <div style={{ color: "#ccc" }}>
                  <strong>{pkgStatus.profile_first_name} {pkgStatus.profile_last_name}</strong>
                  {pkgStatus.profile_username && (
                    <span style={{ color: "#888" }}> @{pkgStatus.profile_username}</span>
                  )}
                </div>
                {pkgStatus.profile_bio && (
                  <div style={{ color: "#aaa", fontStyle: "italic", marginTop: 4 }}>
                    {pkgStatus.profile_bio}
                  </div>
                )}
              </div>
            )}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
              <div>
                <label style={{ color: "#888", fontSize: 12 }}>Gender</label>
                <select
                  value={gender}
                  onChange={(e) => setGender(e.target.value)}
                  style={selectStyle}
                >
                  <option value="female">Female</option>
                  <option value="male">Male</option>
                </select>
              </div>
              <div>
                <label style={{ color: "#888", fontSize: 12 }}>Country</label>
                <select
                  value={country}
                  onChange={(e) => setCountry(e.target.value)}
                  style={selectStyle}
                >
                  <option value="RU">RU</option>
                  <option value="KZ">KZ</option>
                  <option value="UA">UA</option>
                  <option value="BY">BY</option>
                  <option value="UZ">UZ</option>
                  <option value="US">US</option>
                </select>
              </div>
              <div>
                <label style={{ color: "#888", fontSize: 12 }}>Age Range</label>
                <select
                  value={ageRange}
                  onChange={(e) => setAgeRange(e.target.value)}
                  style={selectStyle}
                >
                  <option value="18-24">18-24</option>
                  <option value="25-35">25-35</option>
                  <option value="35-45">35-45</option>
                  <option value="45-55">45-55</option>
                </select>
              </div>
              <div>
                <label style={{ color: "#888", fontSize: 12 }}>Profession</label>
                <input
                  value={profession}
                  onChange={(e) => setProfession(e.target.value)}
                  placeholder="e.g. marketing, design, finance"
                  style={inputStyle}
                />
              </div>
            </div>
            <button
              onClick={handleGenerateProfile}
              disabled={actionLoading}
              style={btnPrimary}
            >
              {actionLoading ? "Generating..." : "Generate Profile"}
            </button>
          </div>

          {/* Avatar */}
          <div
            style={{
              background: "#111",
              border: "1px solid #222",
              borderRadius: 8,
              padding: 20,
              marginBottom: 24,
            }}
          >
            <h3 style={{ color: "#eee", marginBottom: 16 }}>2. Avatar</h3>
            {pkgStatus.avatar_ready && (
              <div style={{ color: "#00ff88", fontSize: 13, marginBottom: 12 }}>
                Avatar ready: {pkgStatus.avatar_path}
              </div>
            )}
            <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
              <div style={{ flex: 1 }}>
                <label style={{ color: "#888", fontSize: 12 }}>Upload avatar image</label>
                <input
                  type="file"
                  accept="image/*"
                  onChange={(e) => handleUploadAvatar(e.target.files)}
                  style={{ ...inputStyle, padding: "8px" }}
                />
              </div>
            </div>
            <div style={{ display: "flex", gap: 12, alignItems: "flex-end" }}>
              <div style={{ flex: 1 }}>
                <label style={{ color: "#888", fontSize: 12 }}>Or generate with AI</label>
                <input
                  value={avatarPrompt}
                  onChange={(e) => setAvatarPrompt(e.target.value)}
                  placeholder="Describe the avatar..."
                  style={inputStyle}
                />
              </div>
              <button onClick={handleGenerateAvatar} disabled={actionLoading} style={btnSecondary}>
                Generate
              </button>
            </div>
            {pkgStatus.avatar_ready && (
              <button
                onClick={handleApplyAvatar}
                disabled={actionLoading || pkgStatus.guard_48h_active}
                style={{ ...btnPrimary, marginTop: 12 }}
              >
                Apply Avatar to Telegram
              </button>
            )}
          </div>

          {/* Apply profile */}
          <div
            style={{
              background: "#111",
              border: "1px solid #222",
              borderRadius: 8,
              padding: 20,
              marginBottom: 24,
            }}
          >
            <h3 style={{ color: "#eee", marginBottom: 16 }}>3. Apply Profile to Telegram</h3>
            <p style={{ color: "#888", fontSize: 13, marginBottom: 12 }}>
              This will set name, bio, and username on the Telegram account.
            </p>
            {pkgStatus.guard_48h_active && (
              <div
                style={{
                  padding: "8px 12px",
                  background: "#331800",
                  border: "1px solid #663300",
                  borderRadius: 6,
                  color: "#ff8844",
                  fontSize: 13,
                  marginBottom: 12,
                }}
              >
                Cannot apply: {pkgStatus.guard_48h_message}
              </div>
            )}
            <button
              onClick={handleApplyProfile}
              disabled={
                actionLoading ||
                !pkgStatus.profile_generated ||
                pkgStatus.guard_48h_active
              }
              style={btnPrimary}
            >
              {actionLoading ? "Applying..." : "Apply Profile"}
            </button>
          </div>

          {/* Channel creation */}
          <div
            style={{
              background: "#111",
              border: "1px solid #222",
              borderRadius: 8,
              padding: 20,
              marginBottom: 24,
            }}
          >
            <h3 style={{ color: "#eee", marginBottom: 16 }}>4. Create Channel</h3>
            {pkgStatus.channel_created && (
              <div
                style={{
                  background: "#0a1a0a",
                  border: "1px solid #1a3a1a",
                  borderRadius: 6,
                  padding: 12,
                  marginBottom: 16,
                }}
              >
                <div style={{ color: "#00ff88", fontSize: 13 }}>
                  Channel: {pkgStatus.channel_title}
                  {pkgStatus.channel_username && ` (@${pkgStatus.channel_username})`}
                </div>
              </div>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 12 }}>
              <div>
                <label style={{ color: "#888", fontSize: 12 }}>Channel Title</label>
                <input
                  value={channelTitle}
                  onChange={(e) => setChannelTitle(e.target.value)}
                  placeholder="My Channel"
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={{ color: "#888", fontSize: 12 }}>Description</label>
                <input
                  value={channelDesc}
                  onChange={(e) => setChannelDesc(e.target.value)}
                  placeholder="Optional channel description"
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={{ color: "#888", fontSize: 12 }}>First Post Text</label>
                <textarea
                  value={firstPost}
                  onChange={(e) => setFirstPost(e.target.value)}
                  placeholder="Optional first pinned post"
                  rows={3}
                  style={{ ...inputStyle, resize: "vertical" }}
                />
              </div>
            </div>
            <button
              onClick={handleCreateChannel}
              disabled={actionLoading || !channelTitle.trim()}
              style={btnPrimary}
            >
              {actionLoading ? "Creating..." : "Create Channel"}
            </button>
          </div>
        </>
      )}

      {/* Log */}
      {log.length > 0 && (
        <div
          style={{
            background: "#0a0a0b",
            border: "1px solid #222",
            borderRadius: 8,
            padding: 16,
            marginTop: 24,
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 12,
            maxHeight: 200,
            overflowY: "auto",
          }}
        >
          {log.map((l, i) => (
            <div key={i} style={{ color: "#888", lineHeight: 1.6 }}>
              {l}
            </div>
          ))}
        </div>
      )}

      {loading && <div style={{ color: "#888", marginTop: 16 }}>Loading...</div>}
    </div>
  );
}

const selectStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  background: "#0a0a0b",
  color: "#eee",
  border: "1px solid #333",
  borderRadius: 6,
  fontSize: 13,
  marginTop: 4,
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  background: "#0a0a0b",
  color: "#eee",
  border: "1px solid #333",
  borderRadius: 6,
  fontSize: 13,
  marginTop: 4,
  boxSizing: "border-box",
};

const btnPrimary: React.CSSProperties = {
  padding: "10px 20px",
  background: "#00ff88",
  color: "#0a0a0b",
  border: "none",
  borderRadius: 6,
  fontWeight: 600,
  fontSize: 14,
  cursor: "pointer",
};

const btnSecondary: React.CSSProperties = {
  padding: "10px 16px",
  background: "#222",
  color: "#eee",
  border: "1px solid #444",
  borderRadius: 6,
  fontWeight: 500,
  fontSize: 13,
  cursor: "pointer",
};
