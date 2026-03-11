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

export function SettingsPage() {
  const { accessToken, profile } = useAuth();

  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [team, setTeam] = useState<TeamMember[]>([]);
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

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
    })();
  }, [accessToken]);

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
