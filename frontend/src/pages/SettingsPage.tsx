import { useEffect, useState } from "react";
import { useAuth } from "../auth";
import { apiFetch } from "../api";

type WorkspaceInfo = {
  id: number;
  name: string;
  settings: Record<string, unknown>;
  created_at: string | null;
};

type TeamMember = {
  user_id: number;
  email: string | null;
  first_name: string | null;
  role: string;
  joined_at: string | null;
};

type SessionInfo = {
  id: number;
  user_agent: string | null;
  ip_address: string | null;
  created_at: string | null;
  last_used_at: string | null;
  expires_at: string | null;
  is_current: boolean;
};

function _parseDevice(userAgent: string | null): string {
  if (!userAgent) return "Неизвестное устройство";
  const ua = userAgent.toLowerCase();
  if (ua.includes("mobile") || ua.includes("android") || ua.includes("iphone")) {
    return "Мобильное устройство";
  }
  if (ua.includes("mac")) return "macOS";
  if (ua.includes("windows")) return "Windows";
  if (ua.includes("linux")) return "Linux";
  return "Браузер";
}

function _parseClient(userAgent: string | null): string {
  if (!userAgent) return "";
  const ua = userAgent.toLowerCase();
  if (ua.includes("chrome") && !ua.includes("chromium") && !ua.includes("edg")) return "Chrome";
  if (ua.includes("firefox")) return "Firefox";
  if (ua.includes("safari") && !ua.includes("chrome")) return "Safari";
  if (ua.includes("edg")) return "Edge";
  return "";
}

export function SettingsPage() {
  const { accessToken, profile } = useAuth();

  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [team, setTeam] = useState<TeamMember[]>([]);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [sessionError, setSessionError] = useState("");

  // Profile info from auth
  const user = profile?.user as Record<string, unknown> | undefined;
  const tenant = profile?.tenant as Record<string, unknown> | undefined;

  useEffect(() => {
    if (!accessToken) return;
    void (async () => {
      try {
        const ws = await apiFetch<{ items: WorkspaceInfo[] }>("/v1/me/workspace", { accessToken });
        if (ws && ws.items && ws.items.length > 0) {
          setWorkspace(ws.items[0]);
        }
      } catch {
        /* ignore */
      }
      try {
        const t = await apiFetch<{ items: TeamMember[] }>("/v1/me/team", { accessToken });
        if (t && t.items) {
          setTeam(t.items);
        }
      } catch {
        /* ignore */
      }
      void loadSessions();
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

  const loadSessions = async () => {
    if (!accessToken) return;
    setSessionsLoading(true);
    setSessionError("");
    try {
      const data = await apiFetch<{ items: SessionInfo[] }>("/auth/sessions", { accessToken });
      setSessions(data.items || []);
    } catch {
      setSessionError("Не удалось загрузить сессии");
    } finally {
      setSessionsLoading(false);
    }
  };

  const handleRevokeSession = async (sessionId: number) => {
    if (!accessToken) return;
    setSessionError("");
    try {
      await apiFetch(`/auth/sessions/${sessionId}`, { method: "DELETE", accessToken });
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Ошибка";
      if (msg === "cannot_revoke_current_session") {
        setSessionError("Нельзя завершить текущую сессию");
      } else {
        setSessionError("Не удалось завершить сессию");
      }
    }
  };

  const handleRevokeAllSessions = async () => {
    if (!accessToken) return;
    setSessionError("");
    try {
      await apiFetch("/auth/sessions", { method: "DELETE", accessToken });
      await loadSessions();
    } catch {
      setSessionError("Не удалось завершить другие сессии");
    }
  };

  const handleLogout = async () => {
    setBusy(true);
    try {
      await apiFetch("/auth/logout", { method: "POST", accessToken });
      window.location.href = "/app/login";
    } catch {
      setStatusMessage("Ошибка при выходе");
    } finally {
      setBusy(false);
    }
  };

  const otherSessionsCount = sessions.filter((s) => !s.is_current).length;

  return (
    <div style={{ maxWidth: 700, margin: "0 auto", padding: "24px 16px" }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 24 }}>Настройки</h1>

      {/* Profile section */}
      <section className="dash-card" style={{ marginBottom: 20 }}>
        <h2 className="terminal-header" style={{ fontSize: 13, marginBottom: 12 }}>ПРОФИЛЬ</h2>
        <div style={{ display: "grid", gap: 8 }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span className="muted">Имя</span>
            <span>{String(user?.first_name || "—")}</span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span className="muted">Email</span>
            <span>{String(user?.email || "—")}</span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span className="muted">Telegram</span>
            <span>{user?.telegram_username ? `@${String(user.telegram_username)}` : "—"}</span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span className="muted">Роль</span>
            <span>{String(user?.role || "owner")}</span>
          </div>
        </div>
      </section>

      {/* Tenant section */}
      <section className="dash-card" style={{ marginBottom: 20 }}>
        <h2 className="terminal-header" style={{ fontSize: 13, marginBottom: 12 }}>ОРГАНИЗАЦИЯ</h2>
        <div style={{ display: "grid", gap: 8 }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span className="muted">Компания</span>
            <span>{String(tenant?.company || "—")}</span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span className="muted">Tenant ID</span>
            <span style={{ fontFamily: "'JetBrains Mono Variable', 'JetBrains Mono', monospace", fontSize: 12 }}>
              {String(tenant?.id || "—")}
            </span>
          </div>
        </div>
      </section>

      {/* Workspace section */}
      <section className="dash-card" style={{ marginBottom: 20 }}>
        <h2 className="terminal-header" style={{ fontSize: 13, marginBottom: 12 }}>ПРОСТРАНСТВО</h2>
        {workspace ? (
          <div style={{ display: "grid", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span className="muted">Название</span>
              <span>{workspace.name}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span className="muted">Создано</span>
              <span>{workspace.created_at ? new Date(workspace.created_at).toLocaleDateString("ru") : "—"}</span>
            </div>
          </div>
        ) : (
          <p className="muted">Загрузка...</p>
        )}
      </section>

      {/* Team section */}
      <section className="dash-card" style={{ marginBottom: 20 }}>
        <h2 className="terminal-header" style={{ fontSize: 13, marginBottom: 12 }}>КОМАНДА</h2>
        {team.length > 0 ? (
          <div style={{ display: "grid", gap: 6 }}>
            {team.map((m) => (
              <div key={m.user_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span>{m.first_name || m.email || `User #${m.user_id}`}</span>
                <span className="badge badge-blue" style={{ fontSize: 11 }}>{m.role}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">Только вы в команде.</p>
        )}
      </section>

      {/* Active Sessions section */}
      <section className="dash-card" style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h2 className="terminal-header" style={{ fontSize: 13, margin: 0 }}>АКТИВНЫЕ СЕССИИ</h2>
          {otherSessionsCount > 0 && (
            <button
              className="btn btn-danger"
              style={{ fontSize: 11, padding: "4px 10px" }}
              onClick={handleRevokeAllSessions}
            >
              Завершить все ({otherSessionsCount})
            </button>
          )}
        </div>

        {sessionError && (
          <p style={{ color: "#ff4444", fontSize: 13, marginBottom: 8 }}>{sessionError}</p>
        )}

        {sessionsLoading ? (
          <p className="muted">Загрузка...</p>
        ) : sessions.length === 0 ? (
          <p className="muted">Нет активных сессий.</p>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {sessions.map((s) => {
              const device = _parseDevice(s.user_agent);
              const client = _parseClient(s.user_agent);
              const label = client ? `${device} — ${client}` : device;
              const createdDate = s.created_at ? new Date(s.created_at).toLocaleString("ru") : "—";
              const lastUsed = s.last_used_at ? new Date(s.last_used_at).toLocaleString("ru") : null;
              return (
                <div
                  key={s.id}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    gap: 12,
                    padding: "8px 0",
                    borderBottom: "1px solid var(--border, #1a1a1a)",
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <span style={{ fontWeight: 500, fontSize: 13 }}>{label}</span>
                      {s.is_current && (
                        <span
                          className="badge badge-green"
                          style={{ fontSize: 10, padding: "2px 6px" }}
                        >
                          текущая
                        </span>
                      )}
                    </div>
                    <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>
                      {s.ip_address && <span style={{ marginRight: 8 }}>{s.ip_address}</span>}
                      <span>Создана: {createdDate}</span>
                      {lastUsed && <span style={{ marginLeft: 8 }}>· Использована: {lastUsed}</span>}
                    </div>
                  </div>
                  {!s.is_current && (
                    <button
                      className="btn btn-danger"
                      style={{ fontSize: 11, padding: "4px 10px", flexShrink: 0 }}
                      onClick={() => handleRevokeSession(s.id)}
                    >
                      Завершить
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Actions */}
      <section className="dash-card" style={{ marginBottom: 20 }}>
        <h2 className="terminal-header" style={{ fontSize: 13, marginBottom: 12 }}>ДЕЙСТВИЯ</h2>
        <button
          className="btn btn-danger"
          onClick={handleLogout}
          disabled={busy}
          style={{ width: "100%" }}
        >
          {busy ? "Выход..." : "Выйти из аккаунта"}
        </button>
        {statusMessage && <p style={{ color: "#ff4444", marginTop: 8, fontSize: 13 }}>{statusMessage}</p>}
      </section>
    </div>
  );
}
