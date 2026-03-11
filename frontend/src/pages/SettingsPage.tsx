import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  User,
  Building2,
  Layers,
  Users,
  Shield,
  LogOut,
  Monitor,
  Smartphone,
  Globe,
  Trash2,
  RefreshCw,
  Copy,
  Check,
  ChevronRight,
} from "lucide-react";
import { useAuth } from "../auth";
import { apiFetch } from "../api";

/* ─── Types ────────────────────────────────────────────────────────────────── */

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

/* ─── Helpers ──────────────────────────────────────────────────────────────── */

function parseDevice(ua: string | null): { label: string; icon: typeof Monitor } {
  if (!ua) return { label: "Неизвестное устройство", icon: Globe };
  const low = ua.toLowerCase();
  if (low.includes("mobile") || low.includes("android") || low.includes("iphone"))
    return { label: "Мобильное", icon: Smartphone };
  if (low.includes("mac")) return { label: "macOS", icon: Monitor };
  if (low.includes("windows")) return { label: "Windows", icon: Monitor };
  if (low.includes("linux")) return { label: "Linux", icon: Monitor };
  return { label: "Браузер", icon: Globe };
}

function parseBrowser(ua: string | null): string {
  if (!ua) return "";
  const low = ua.toLowerCase();
  if (low.includes("chrome") && !low.includes("chromium") && !low.includes("edg")) return "Chrome";
  if (low.includes("firefox")) return "Firefox";
  if (low.includes("safari") && !low.includes("chrome")) return "Safari";
  if (low.includes("edg")) return "Edge";
  return "";
}

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "только что";
  if (mins < 60) return `${mins} мин назад`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} ч назад`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} дн назад`;
  return new Date(iso).toLocaleDateString("ru");
}

/* ─── Animation variants ──────────────────────────────────────────────────── */

const cardVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.07, duration: 0.4, ease: [0.16, 1, 0.3, 1] as const },
  }),
};

/* ─── Sub-components ──────────────────────────────────────────────────────── */

function InfoRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
      <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>{label}</span>
      <span style={{ fontSize: mono ? 12 : 13, fontWeight: 500, fontFamily: mono ? "'JetBrains Mono Variable', 'JetBrains Mono', monospace" : "inherit" }}>
        {value}
      </span>
    </div>
  );
}

function SectionHeader({ icon: Icon, title, count }: { icon: typeof User; title: string; count?: number }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
      <div style={{
        width: 32, height: 32, borderRadius: 8,
        display: "grid", placeItems: "center",
        background: "var(--accent-glow)", color: "var(--accent)",
      }}>
        <Icon size={16} />
      </div>
      <h2 className="dash-panel-title" style={{ margin: 0, fontSize: 12 }}>{title}</h2>
      {count !== undefined && (
        <span className="pill" style={{ fontSize: 10, padding: "2px 8px" }}>{count}</span>
      )}
    </div>
  );
}

/* ─── Main component ──────────────────────────────────────────────────────── */

export function SettingsPage() {
  const { accessToken, profile, logout } = useAuth();

  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [team, setTeam] = useState<TeamMember[]>([]);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [sessionError, setSessionError] = useState("");
  const [copiedId, setCopiedId] = useState(false);

  const user = profile?.user as Record<string, unknown> | undefined;
  const tenant = profile?.tenant as Record<string, unknown> | undefined;

  useEffect(() => {
    if (!accessToken) return;
    void (async () => {
      try {
        const ws = await apiFetch<{ items: WorkspaceInfo[] }>("/v1/me/workspace", { accessToken });
        if (ws?.items?.length) setWorkspace(ws.items[0]);
      } catch { /* ignore */ }
      try {
        const t = await apiFetch<{ items: TeamMember[] }>("/v1/me/team", { accessToken });
        if (t?.items) setTeam(t.items);
      } catch { /* ignore */ }
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
      const msg = err instanceof Error ? err.message : "";
      setSessionError(msg === "cannot_revoke_current_session" ? "Нельзя завершить текущую сессию" : "Не удалось завершить сессию");
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

  const handleCopyId = () => {
    const tid = String(tenant?.id || "");
    if (!tid) return;
    navigator.clipboard.writeText(tid);
    setCopiedId(true);
    setTimeout(() => setCopiedId(false), 2000);
  };

  const otherSessions = sessions.filter((s) => !s.is_current);
  const currentSession = sessions.find((s) => s.is_current);

  return (
    <div className="dash">
      {/* Hero */}
      <motion.div
        className="dash-hero"
        initial={{ opacity: 0, y: -12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
      >
        <h1 className="dash-hero-greeting">Настройки</h1>
        <p className="dash-hero-date" style={{ color: "var(--text-secondary)" }}>
          Профиль, организация, сессии и безопасность
        </p>
      </motion.div>

      {/* Profile + Organization — two columns */}
      <div className="dash-columns">
        {/* Profile */}
        <motion.div className="dash-panel" custom={0} initial="hidden" animate="visible" variants={cardVariants}>
          <SectionHeader icon={User} title="ПРОФИЛЬ" />
          <div>
            <InfoRow label="Имя" value={String(user?.first_name || "—")} />
            <InfoRow label="Email" value={String(user?.email || "—")} />
            <InfoRow
              label="Telegram"
              value={user?.telegram_username ? `@${String(user.telegram_username)}` : "—"}
            />
            <InfoRow label="Роль" value={String(user?.role || "owner")} />
          </div>
        </motion.div>

        {/* Organization */}
        <motion.div className="dash-panel" custom={1} initial="hidden" animate="visible" variants={cardVariants}>
          <SectionHeader icon={Building2} title="ОРГАНИЗАЦИЯ" />
          <div>
            <InfoRow label="Компания" value={String(tenant?.company || "—")} />
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
              <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>Tenant ID</span>
              <button
                onClick={handleCopyId}
                className="ghost-button"
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6, padding: "4px 8px",
                  fontFamily: "'JetBrains Mono Variable', 'JetBrains Mono', monospace", fontSize: 12,
                  borderRadius: 6, border: "1px solid var(--border)",
                }}
              >
                {String(tenant?.id || "—")}
                {copiedId ? <Check size={12} color="var(--accent)" /> : <Copy size={12} />}
              </button>
            </div>
          </div>
        </motion.div>
      </div>

      {/* Workspace + Team — two columns */}
      <div className="dash-columns">
        {/* Workspace */}
        <motion.div className="dash-panel" custom={2} initial="hidden" animate="visible" variants={cardVariants}>
          <SectionHeader icon={Layers} title="ПРОСТРАНСТВО" />
          {workspace ? (
            <div>
              <InfoRow label="Название" value={workspace.name} />
              <InfoRow
                label="Создано"
                value={workspace.created_at ? new Date(workspace.created_at).toLocaleDateString("ru") : "—"}
              />
            </div>
          ) : (
            <div className="dash-empty">Загрузка...</div>
          )}
        </motion.div>

        {/* Team */}
        <motion.div className="dash-panel" custom={3} initial="hidden" animate="visible" variants={cardVariants}>
          <SectionHeader icon={Users} title="КОМАНДА" count={team.length} />
          {team.length > 0 ? (
            <div>
              {team.map((m) => (
                <div key={m.user_id} style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  padding: "10px 0", borderBottom: "1px solid var(--border)",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <div style={{
                      width: 28, height: 28, borderRadius: "50%",
                      background: "var(--surface-3)", display: "grid", placeItems: "center",
                      fontSize: 12, fontWeight: 700, color: "var(--text-secondary)",
                    }}>
                      {(m.first_name || m.email || "U").charAt(0).toUpperCase()}
                    </div>
                    <span style={{ fontSize: 13 }}>{m.first_name || m.email || `User #${m.user_id}`}</span>
                  </div>
                  <span className="pill" style={{ fontSize: 10 }}>{m.role}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="dash-empty">Только вы в команде</div>
          )}
        </motion.div>
      </div>

      {/* Active Sessions — full width */}
      <motion.div className="dash-panel" custom={4} initial="hidden" animate="visible" variants={cardVariants}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <SectionHeader icon={Shield} title="АКТИВНЫЕ СЕССИИ" count={sessions.length} />
          <div style={{ display: "flex", gap: 8 }}>
            <button className="ghost-button" onClick={() => void loadSessions()} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <RefreshCw size={13} /> Обновить
            </button>
            {otherSessions.length > 0 && (
              <button className="danger-button" onClick={handleRevokeAllSessions} style={{ fontSize: 12, padding: "6px 12px" }}>
                <Trash2 size={13} style={{ marginRight: 4, verticalAlign: "middle" }} />
                Завершить все ({otherSessions.length})
              </button>
            )}
          </div>
        </div>

        {sessionError && (
          <div className="form-error" style={{ fontSize: 13 }}>{sessionError}</div>
        )}

        {sessionsLoading ? (
          <div style={{ display: "grid", gap: 12 }}>
            {[1, 2].map((i) => <div key={i} className="dash-skeleton" style={{ height: 72, borderRadius: 12 }} />)}
          </div>
        ) : sessions.length === 0 ? (
          <div className="dash-empty">Нет активных сессий</div>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {/* Current session first */}
            {currentSession && (
              <SessionCard session={currentSession} onRevoke={handleRevokeSession} />
            )}
            {/* Other sessions */}
            {otherSessions.map((s) => (
              <SessionCard key={s.id} session={s} onRevoke={handleRevokeSession} />
            ))}
          </div>
        )}
      </motion.div>

      {/* Danger zone */}
      <motion.div
        className="dash-panel"
        custom={5}
        initial="hidden"
        animate="visible"
        variants={cardVariants}
        style={{ borderColor: "rgba(255, 68, 68, 0.2)" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <div style={{
            width: 32, height: 32, borderRadius: 8,
            display: "grid", placeItems: "center",
            background: "rgba(255, 68, 68, 0.15)", color: "var(--danger)",
          }}>
            <LogOut size={16} />
          </div>
          <h2 className="dash-panel-title" style={{ margin: 0, fontSize: 12, color: "var(--danger)" }}>ОПАСНАЯ ЗОНА</h2>
        </div>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: 0, lineHeight: 1.5 }}>
          Выход из аккаунта завершит текущую сессию. Вам нужно будет войти заново.
        </p>
        <div style={{ marginTop: 8 }}>
          <button
            className="danger-button"
            onClick={handleLogout}
            disabled={busy}
            style={{ width: "100%", padding: "12px 0", fontSize: 14, fontWeight: 600 }}
          >
            {busy ? "Выход..." : "Выйти из аккаунта"}
          </button>
        </div>
        {statusMessage && <div className="form-error" style={{ marginTop: 8, fontSize: 13 }}>{statusMessage}</div>}
      </motion.div>
    </div>
  );
}

/* ─── Session Card ─────────────────────────────────────────────────────────── */

function SessionCard({ session, onRevoke }: { session: SessionInfo; onRevoke: (id: number) => void }) {
  const { label: deviceLabel, icon: DeviceIcon } = parseDevice(session.user_agent);
  const browser = parseBrowser(session.user_agent);
  const displayName = browser ? `${deviceLabel} · ${browser}` : deviceLabel;

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 14,
      padding: "14px 16px", borderRadius: 12,
      background: session.is_current ? "var(--accent-glow)" : "var(--surface-2)",
      border: `1px solid ${session.is_current ? "rgba(0, 255, 136, 0.2)" : "var(--border)"}`,
      transition: "border-color 200ms ease",
    }}>
      <div style={{
        width: 36, height: 36, borderRadius: 10,
        display: "grid", placeItems: "center",
        background: session.is_current ? "rgba(0, 255, 136, 0.15)" : "var(--surface-3)",
        color: session.is_current ? "var(--accent)" : "var(--text-secondary)",
        flexShrink: 0,
      }}>
        <DeviceIcon size={18} />
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>{displayName}</span>
          {session.is_current && (
            <span className="pill" style={{ fontSize: 10, padding: "2px 8px" }}>текущая</span>
          )}
        </div>
        <div style={{ display: "flex", gap: 12, marginTop: 4, fontSize: 11, color: "var(--muted)", fontFamily: "'JetBrains Mono Variable', 'JetBrains Mono', monospace" }}>
          {session.ip_address && <span>{session.ip_address}</span>}
          <span>{timeAgo(session.last_used_at || session.created_at)}</span>
        </div>
      </div>

      {!session.is_current && (
        <button
          className="danger-button"
          onClick={() => onRevoke(session.id)}
          style={{ fontSize: 12, padding: "6px 12px", flexShrink: 0, display: "flex", alignItems: "center", gap: 4 }}
        >
          <Trash2 size={12} /> Завершить
        </button>
      )}
      {session.is_current && (
        <ChevronRight size={16} style={{ color: "var(--accent)", opacity: 0.4, flexShrink: 0 }} />
      )}
    </div>
  );
}
