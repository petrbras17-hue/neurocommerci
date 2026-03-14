import { useEffect, useState, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Building2, Users, TrendingUp, DollarSign, Search, X,
  Plus, Copy, Trash2, RefreshCw, AlertTriangle, ChevronRight,
  Check,
} from "lucide-react";
import {
  apiFetch,
  agencyApi,
  Agency,
  AgencyClient,
  AgencyInvite,
  AgencyStats,
  AgencyBranding,
} from "../api";
import { useAuth } from "../auth";

// ---------------------------------------------------------------------------
// Design tokens
// ---------------------------------------------------------------------------

const BG = "#0a0a0b";
const SURFACE = "#111113";
const ELEVATED = "#1a1a1f";
const BORDER = "rgba(255,255,255,0.07)";
const ACCENT = "#00ff88";
const DANGER = "#ef4444";
const WARNING = "#f59e0b";
const MUTED = "#888";
const TEXT = "#e8e8e8";
const TEXT2 = "#aaa";
const MONO = "'JetBrains Mono', monospace";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("ru-RU", {
    day: "2-digit", month: "short", year: "numeric",
  });
}

function fmtRub(amount: number): string {
  return `${amount.toLocaleString("ru-RU")} ₽`;
}

function clientStatusColor(s: string): string {
  if (s === "active") return ACCENT;
  if (s === "suspended") return WARNING;
  if (s === "churned") return DANGER;
  return MUTED;
}

function clientStatusLabel(s: string): string {
  const map: Record<string, string> = {
    active: "Активен",
    suspended: "Приостановлен",
    churned: "Ушёл",
    pending: "Ожидает",
  };
  return map[s] ?? s;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatCard({
  label, value, sub, icon, accent,
}: {
  label: string; value: string | number; sub?: string;
  icon: React.ReactNode; accent?: string;
}) {
  return (
    <div style={{
      background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 12,
      padding: "18px 20px", display: "flex", flexDirection: "column", gap: 6,
      transition: "border-color 0.15s",
    }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = `${accent ?? ACCENT}40`; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = BORDER; }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 12, color: TEXT2, letterSpacing: "0.04em", textTransform: "uppercase" }}>{label}</span>
        <span style={{ color: accent ?? ACCENT, opacity: 0.7 }}>{icon}</span>
      </div>
      <span style={{
        fontFamily: MONO, fontSize: "1.6rem", fontWeight: 700,
        color: accent ?? ACCENT, lineHeight: 1.1,
      }}>
        {typeof value === "number" ? value.toLocaleString("ru-RU") : value}
      </span>
      {sub && <span style={{ fontSize: 12, color: MUTED }}>{sub}</span>}
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const color = clientStatusColor(status);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: color, flexShrink: 0 }} />
      {clientStatusLabel(status)}
    </span>
  );
}

function Btn({
  children, onClick, variant = "ghost", disabled = false, small = false,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  variant?: "ghost" | "accent" | "danger";
  disabled?: boolean;
  small?: boolean;
}) {
  const colors = {
    ghost: { color: TEXT2, bg: "transparent", border: `1px solid ${BORDER}`, hover: "#ffffff08" },
    accent: { color: "#000", bg: ACCENT, border: "none", hover: "#00dd77" },
    danger: { color: DANGER, bg: "transparent", border: `1px solid ${DANGER}44`, hover: `${DANGER}15` },
  };
  const c = colors[variant];
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: small ? "5px 10px" : "8px 14px",
        borderRadius: 8, border: c.border, background: c.bg,
        color: c.color, cursor: disabled ? "default" : "pointer",
        fontSize: small ? 12 : 13, fontWeight: 600,
        opacity: disabled ? 0.5 : 1, transition: "background 0.12s",
      }}
      onMouseEnter={(e) => { if (!disabled) (e.currentTarget as HTMLButtonElement).style.background = c.hover; }}
      onMouseLeave={(e) => { if (!disabled) (e.currentTarget as HTMLButtonElement).style.background = c.bg; }}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Modal: Add Client
// ---------------------------------------------------------------------------

function AddClientModal({
  onClose, onSave,
}: {
  onClose: () => void;
  onSave: (data: { client_name: string; client_contact_email: string; notes: string }) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const handleSave = async () => {
    if (!name.trim()) { setErr("Укажите имя клиента"); return; }
    setSaving(true);
    setErr("");
    try {
      await onSave({ client_name: name.trim(), client_contact_email: email.trim(), notes: notes.trim() });
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Ошибка");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 300,
    }} onClick={onClose}>
      <div style={{
        background: ELEVATED, border: `1px solid ${BORDER}`, borderRadius: 14,
        padding: 28, maxWidth: 420, width: "100%", display: "flex", flexDirection: "column", gap: 16,
      }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 16, fontWeight: 700, color: TEXT }}>Добавить клиента</span>
          <button type="button" onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: MUTED, padding: 4 }}>
            <X size={16} />
          </button>
        </div>
        {err && (
          <div style={{ color: DANGER, fontSize: 13, padding: "8px 12px", background: `${DANGER}15`, borderRadius: 8, border: `1px solid ${DANGER}30` }}>
            {err}
          </div>
        )}
        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em" }}>Имя клиента *</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="ООО Ромашка"
            style={{
              background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 8,
              padding: "9px 12px", color: TEXT, fontSize: 13, outline: "none", fontFamily: "inherit",
            }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em" }}>Email контакта</span>
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="client@example.com"
            type="email"
            style={{
              background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 8,
              padding: "9px 12px", color: TEXT, fontSize: 13, outline: "none", fontFamily: "inherit",
            }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em" }}>Заметки</span>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Дополнительная информация о клиенте..."
            rows={3}
            style={{
              background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 8,
              padding: "9px 12px", color: TEXT, fontSize: 13, outline: "none",
              fontFamily: "inherit", resize: "vertical",
            }}
          />
        </label>
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <Btn onClick={onClose}>Отмена</Btn>
          <Btn variant="accent" onClick={() => void handleSave()} disabled={saving}>
            {saving ? "Сохраняем..." : "Добавить"}
          </Btn>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Modal: Create Invite
// ---------------------------------------------------------------------------

function CreateInviteModal({
  onClose, onSave,
}: {
  onClose: () => void;
  onSave: (data: { client_email: string; max_uses: number; expires_in_days: number }) => Promise<void>;
}) {
  const [email, setEmail] = useState("");
  const [maxUses, setMaxUses] = useState(1);
  const [expiresIn, setExpiresIn] = useState(7);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const handleSave = async () => {
    setSaving(true);
    setErr("");
    try {
      await onSave({ client_email: email.trim(), max_uses: maxUses, expires_in_days: expiresIn });
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Ошибка");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 300,
    }} onClick={onClose}>
      <div style={{
        background: ELEVATED, border: `1px solid ${BORDER}`, borderRadius: 14,
        padding: 28, maxWidth: 400, width: "100%", display: "flex", flexDirection: "column", gap: 16,
      }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 16, fontWeight: 700, color: TEXT }}>Создать приглашение</span>
          <button type="button" onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: MUTED, padding: 4 }}>
            <X size={16} />
          </button>
        </div>
        {err && (
          <div style={{ color: DANGER, fontSize: 13, padding: "8px 12px", background: `${DANGER}15`, borderRadius: 8, border: `1px solid ${DANGER}30` }}>
            {err}
          </div>
        )}
        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em" }}>Email клиента (необязательно)</span>
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="client@example.com"
            type="email"
            style={{
              background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 8,
              padding: "9px 12px", color: TEXT, fontSize: 13, outline: "none", fontFamily: "inherit",
            }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em" }}>Макс. использований</span>
          <input
            value={maxUses}
            onChange={(e) => setMaxUses(Number(e.target.value))}
            type="number"
            min={1}
            max={100}
            style={{
              background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 8,
              padding: "9px 12px", color: TEXT, fontSize: 13, outline: "none", fontFamily: MONO,
            }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em" }}>Срок действия (дней)</span>
          <input
            value={expiresIn}
            onChange={(e) => setExpiresIn(Number(e.target.value))}
            type="number"
            min={1}
            max={365}
            style={{
              background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 8,
              padding: "9px 12px", color: TEXT, fontSize: 13, outline: "none", fontFamily: MONO,
            }}
          />
        </label>
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <Btn onClick={onClose}>Отмена</Btn>
          <Btn variant="accent" onClick={() => void handleSave()} disabled={saving}>
            {saving ? "Создаём..." : "Создать"}
          </Btn>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Client Detail Panel (slide-in)
// ---------------------------------------------------------------------------

function ClientDetailPanel({
  client, token, onClose, onUpdate,
}: {
  client: AgencyClient;
  token: string;
  onClose: () => void;
  onUpdate: (updated: AgencyClient) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(client.client_name);
  const [email, setEmail] = useState(client.client_contact_email ?? "");
  const [notes, setNotes] = useState(client.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const handleSave = async () => {
    setSaving(true);
    setErr("");
    try {
      const updated = await agencyApi.updateClient(token, client.id, {
        client_name: name,
        client_contact_email: email || undefined,
        notes: notes || undefined,
      });
      onUpdate(updated);
      setEditing(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Ошибка");
    } finally {
      setSaving(false);
    }
  };

  const handleStatusToggle = async () => {
    const next = client.status === "active" ? "suspended" : "active";
    try {
      const updated = await agencyApi.updateClient(token, client.id, { status: next });
      onUpdate(updated);
    } catch {
      // ignore
    }
  };

  const inputStyle: React.CSSProperties = {
    background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 8,
    padding: "8px 12px", color: TEXT, fontSize: 13, outline: "none",
    fontFamily: "inherit", width: "100%", boxSizing: "border-box",
  };

  return (
    <motion.div
      initial={{ x: "100%" }}
      animate={{ x: 0 }}
      exit={{ x: "100%" }}
      transition={{ type: "spring", stiffness: 320, damping: 32 }}
      style={{
        position: "fixed", top: 0, right: 0, bottom: 0, width: "min(460px, 100vw)",
        background: "#0e0e10", borderLeft: `1px solid ${BORDER}`,
        zIndex: 200, overflowY: "auto", padding: "28px 24px",
        display: "flex", flexDirection: "column", gap: 20,
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 16, fontWeight: 700, color: TEXT }}>Клиент #{client.id}</span>
        <button type="button" onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: MUTED, padding: 4 }}>
          <X size={18} />
        </button>
      </div>

      {err && (
        <div style={{ color: DANGER, fontSize: 13, padding: "8px 12px", background: `${DANGER}15`, borderRadius: 8, border: `1px solid ${DANGER}30` }}>
          {err}
        </div>
      )}

      {/* Info card */}
      <div style={{ background: SURFACE, borderRadius: 10, padding: "14px 16px", border: `1px solid ${BORDER}` }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <span style={{ fontWeight: 700, fontSize: 15, color: TEXT }}>{client.client_name}</span>
          <StatusDot status={client.status} />
        </div>
        {client.client_contact_email && (
          <div style={{ fontSize: 12, color: MUTED, marginTop: 4 }}>{client.client_contact_email}</div>
        )}
        <div style={{ fontSize: 12, color: MUTED, marginTop: 4 }}>Добавлен: {fmtDate(client.created_at)}</div>
      </div>

      {/* Revenue */}
      <div style={{ background: SURFACE, borderRadius: 10, padding: "14px 16px", border: `1px solid ${BORDER}` }}>
        <div style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 10 }}>Доход</div>
        <div style={{ display: "flex", gap: 24 }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: ACCENT, fontFamily: MONO }}>{fmtRub(client.total_revenue_rub)}</div>
            <div style={{ fontSize: 11, color: MUTED }}>оборот</div>
          </div>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#3b9eff", fontFamily: MONO }}>{fmtRub(client.agency_earned_rub)}</div>
            <div style={{ fontSize: 11, color: MUTED }}>агентское</div>
          </div>
        </div>
      </div>

      {/* Notes */}
      {client.notes && !editing && (
        <div style={{ background: SURFACE, borderRadius: 10, padding: "14px 16px", border: `1px solid ${BORDER}` }}>
          <div style={{ fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 8 }}>Заметки</div>
          <p style={{ fontSize: 13, color: TEXT, margin: 0, lineHeight: 1.6 }}>{client.notes}</p>
        </div>
      )}

      {/* Edit form */}
      {editing ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div>
            <div style={{ fontSize: 12, color: TEXT2, marginBottom: 5 }}>Имя</div>
            <input value={name} onChange={(e) => setName(e.target.value)} style={inputStyle} />
          </div>
          <div>
            <div style={{ fontSize: 12, color: TEXT2, marginBottom: 5 }}>Email</div>
            <input value={email} onChange={(e) => setEmail(e.target.value)} style={inputStyle} type="email" />
          </div>
          <div>
            <div style={{ fontSize: 12, color: TEXT2, marginBottom: 5 }}>Заметки</div>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} style={{ ...inputStyle, resize: "vertical" }} rows={4} />
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <Btn onClick={() => setEditing(false)}>Отмена</Btn>
            <Btn variant="accent" onClick={() => void handleSave()} disabled={saving}>
              {saving ? "Сохраняем..." : "Сохранить"}
            </Btn>
          </div>
        </div>
      ) : (
        <Btn onClick={() => setEditing(true)}>Редактировать</Btn>
      )}

      {/* Status toggle */}
      <button
        type="button"
        onClick={() => void handleStatusToggle()}
        style={{
          marginTop: 4, padding: "10px 18px", borderRadius: 8, border: "none", cursor: "pointer",
          background: client.status === "active" ? `${DANGER}22` : `${ACCENT}22`,
          color: client.status === "active" ? DANGER : ACCENT,
          fontWeight: 600, fontSize: 13,
        }}
      >
        {client.status === "active" ? "Приостановить клиента" : "Активировать клиента"}
      </button>
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Clients
// ---------------------------------------------------------------------------

function ClientsTab({ token }: { token: string }) {
  const [clients, setClients] = useState<AgencyClient[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [showAddModal, setShowAddModal] = useState(false);
  const [selectedClient, setSelectedClient] = useState<AgencyClient | null>(null);
  const searchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const res = await agencyApi.listClients(token, 50, 0, search || undefined);
      setClients(res.items);
      setTotal(res.total);
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  }, [token, search]);

  useEffect(() => { void load(); }, [load]);

  const handleSearchChange = (val: string) => {
    setSearchInput(val);
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    searchTimeout.current = setTimeout(() => setSearch(val), 400);
  };

  const handleAddClient = async (data: { client_name: string; client_contact_email: string; notes: string }) => {
    const created = await agencyApi.createClient(token, data);
    setClients((prev) => [created, ...prev]);
    setTotal((t) => t + 1);
  };

  const handleClientUpdate = (updated: AgencyClient) => {
    setClients((prev) => prev.map((c) => c.id === updated.id ? updated : c));
    if (selectedClient?.id === updated.id) setSelectedClient(updated);
  };

  return (
    <>
      <div style={{ background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 12, overflow: "hidden" }}>
        {/* Toolbar */}
        <div style={{ padding: "14px 16px", borderBottom: `1px solid ${BORDER}`, display: "flex", alignItems: "center", gap: 10 }}>
          <Search size={15} style={{ color: MUTED, flexShrink: 0 }} />
          <input
            type="text"
            placeholder="Поиск по имени или email..."
            value={searchInput}
            onChange={(e) => handleSearchChange(e.target.value)}
            style={{ flex: 1, background: "transparent", border: "none", outline: "none", fontSize: 13, color: TEXT, fontFamily: "inherit" }}
          />
          {searchInput && (
            <button type="button" onClick={() => { setSearchInput(""); setSearch(""); }} style={{ background: "none", border: "none", cursor: "pointer", color: MUTED, padding: 2 }}>
              <X size={14} />
            </button>
          )}
          <Btn small onClick={() => setShowAddModal(true)}>
            <Plus size={13} />
            Добавить клиента
          </Btn>
        </div>

        {/* Table */}
        {clients.length === 0 && !busy ? (
          <div style={{ padding: "40px 20px", textAlign: "center", color: MUTED, fontSize: 14 }}>
            {search ? "Клиенты не найдены" : "Нет клиентов. Добавьте первого."}
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                  {["#", "Клиент", "Email", "Статус", "Тариф", "Доход", "Агентское", "Дата", ""].map((h) => (
                    <th key={h} style={{ padding: "10px 14px", textAlign: "left", fontSize: 11, color: MUTED, fontWeight: 600, whiteSpace: "nowrap", textTransform: "uppercase", letterSpacing: "0.04em" }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {clients.map((c) => (
                  <tr
                    key={c.id}
                    onClick={() => setSelectedClient(c)}
                    style={{ borderBottom: `1px solid ${BORDER}`, cursor: "pointer", transition: "background 0.12s" }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLTableRowElement).style.background = "#ffffff08"; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLTableRowElement).style.background = "transparent"; }}
                  >
                    <td style={{ padding: "10px 14px" }}>
                      <span style={{ fontFamily: MONO, fontSize: 12, color: MUTED }}>{c.id}</span>
                    </td>
                    <td style={{ padding: "10px 14px", fontWeight: 600, color: TEXT, fontSize: 13, whiteSpace: "nowrap" }}>{c.client_name}</td>
                    <td style={{ padding: "10px 14px", fontSize: 12, color: TEXT2 }}>{c.client_contact_email ?? "—"}</td>
                    <td style={{ padding: "10px 14px" }}>
                      <StatusDot status={c.status} />
                    </td>
                    <td style={{ padding: "10px 14px", fontSize: 12, color: TEXT2 }}>{c.subscription_plan ?? "—"}</td>
                    <td style={{ padding: "10px 14px" }}>
                      <span style={{ fontFamily: MONO, fontSize: 12, color: ACCENT }}>{c.total_revenue_rub > 0 ? fmtRub(c.total_revenue_rub) : "—"}</span>
                    </td>
                    <td style={{ padding: "10px 14px" }}>
                      <span style={{ fontFamily: MONO, fontSize: 12, color: "#3b9eff" }}>{c.agency_earned_rub > 0 ? fmtRub(c.agency_earned_rub) : "—"}</span>
                    </td>
                    <td style={{ padding: "10px 14px" }}>
                      <span style={{ fontFamily: MONO, fontSize: 11, color: MUTED, whiteSpace: "nowrap" }}>{fmtDate(c.created_at)}</span>
                    </td>
                    <td style={{ padding: "10px 14px" }}>
                      <ChevronRight size={14} style={{ color: MUTED }} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Modals & panels */}
      {showAddModal && (
        <AddClientModal
          onClose={() => setShowAddModal(false)}
          onSave={handleAddClient}
        />
      )}

      <AnimatePresence>
        {selectedClient && (
          <>
            <motion.div
              key="overlay"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setSelectedClient(null)}
              style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 199 }}
            />
            <ClientDetailPanel
              key={selectedClient.id}
              client={selectedClient}
              token={token}
              onClose={() => setSelectedClient(null)}
              onUpdate={handleClientUpdate}
            />
          </>
        )}
      </AnimatePresence>
    </>
  );
}

// ---------------------------------------------------------------------------
// Tab: Invites
// ---------------------------------------------------------------------------

function InvitesTab({ token }: { token: string }) {
  const [invites, setInvites] = useState<AgencyInvite[]>([]);
  const [busy, setBusy] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [copiedId, setCopiedId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const res = await agencyApi.listInvites(token);
      setInvites(res.items);
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  }, [token]);

  useEffect(() => { void load(); }, [load]);

  const handleCreate = async (data: { client_email: string; max_uses: number; expires_in_days: number }) => {
    const created = await agencyApi.createInvite(token, data);
    setInvites((prev) => [created, ...prev]);
  };

  const handleDelete = async (id: number) => {
    try {
      await agencyApi.deleteInvite(token, id);
      setInvites((prev) => prev.filter((i) => i.id !== id));
    } catch {
      // ignore
    }
  };

  const handleCopy = (invite: AgencyInvite) => {
    const link = `${window.location.origin}/join?code=${invite.invite_code}`;
    void navigator.clipboard.writeText(link);
    setCopiedId(invite.id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  return (
    <>
      <div style={{ background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 12, overflow: "hidden" }}>
        {/* Toolbar */}
        <div style={{ padding: "14px 16px", borderBottom: `1px solid ${BORDER}`, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: TEXT }}>
            Приглашения{invites.length > 0 ? ` (${invites.length})` : ""}
          </span>
          <Btn small onClick={() => setShowModal(true)}>
            <Plus size={13} />
            Создать приглашение
          </Btn>
        </div>

        {/* List */}
        {invites.length === 0 && !busy ? (
          <div style={{ padding: "40px 20px", textAlign: "center", color: MUTED, fontSize: 14 }}>
            Нет приглашений. Создайте первое.
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                  {["Код", "Email", "Использований", "Срок", "Создано", ""].map((h) => (
                    <th key={h} style={{ padding: "10px 14px", textAlign: "left", fontSize: 11, color: MUTED, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em", whiteSpace: "nowrap" }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {invites.map((inv) => {
                  const expired = inv.expires_at ? new Date(inv.expires_at) < new Date() : false;
                  return (
                    <tr key={inv.id} style={{ borderBottom: `1px solid ${BORDER}` }}>
                      <td style={{ padding: "10px 14px" }}>
                        <span style={{ fontFamily: MONO, fontSize: 12, color: ACCENT }}>{inv.invite_code}</span>
                      </td>
                      <td style={{ padding: "10px 14px", fontSize: 12, color: TEXT2 }}>{inv.client_email ?? "—"}</td>
                      <td style={{ padding: "10px 14px" }}>
                        <span style={{ fontFamily: MONO, fontSize: 12, color: TEXT }}>{inv.used_count} / {inv.max_uses}</span>
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <span style={{ fontSize: 12, color: expired ? DANGER : TEXT2 }}>
                          {inv.expires_at ? fmtDate(inv.expires_at) : "Бессрочно"}
                          {expired && " (истёк)"}
                        </span>
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <span style={{ fontFamily: MONO, fontSize: 11, color: MUTED, whiteSpace: "nowrap" }}>{fmtDate(inv.created_at)}</span>
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <div style={{ display: "flex", gap: 6 }}>
                          <button
                            type="button"
                            onClick={() => handleCopy(inv)}
                            title="Скопировать ссылку"
                            style={{ background: "none", border: "none", cursor: "pointer", color: copiedId === inv.id ? ACCENT : MUTED, padding: 4, transition: "color 0.15s" }}
                          >
                            {copiedId === inv.id ? <Check size={14} /> : <Copy size={14} />}
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleDelete(inv.id)}
                            title="Удалить"
                            style={{ background: "none", border: "none", cursor: "pointer", color: MUTED, padding: 4, transition: "color 0.15s" }}
                            onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = DANGER; }}
                            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = MUTED; }}
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showModal && (
        <CreateInviteModal
          onClose={() => setShowModal(false)}
          onSave={handleCreate}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Tab: Branding (White Label)
// ---------------------------------------------------------------------------

function BrandingTab({ agency, token, onUpdate }: { agency: Agency; token: string; onUpdate: (a: Agency) => void }) {
  const [logoUrl, setLogoUrl] = useState(agency.custom_logo_url ?? "");
  const [brandName, setBrandName] = useState(agency.custom_brand_name ?? "");
  const [accentColor, setAccentColor] = useState(agency.custom_accent_color ?? "#00ff88");
  const [domain, setDomain] = useState(agency.custom_domain ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");

  const handleSave = async () => {
    setSaving(true);
    setErr("");
    try {
      const updated = await agencyApi.updateBranding(token, {
        custom_logo_url: logoUrl || null,
        custom_brand_name: brandName || null,
        custom_accent_color: accentColor || null,
        custom_domain: domain || null,
      });
      onUpdate(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Ошибка");
    } finally {
      setSaving(false);
    }
  };

  const inputStyle: React.CSSProperties = {
    background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 8,
    padding: "9px 12px", color: TEXT, fontSize: 13, outline: "none",
    fontFamily: "inherit", width: "100%", boxSizing: "border-box",
  };

  const labelStyle: React.CSSProperties = {
    display: "flex", flexDirection: "column", gap: 6,
  };

  const sectionLabelStyle: React.CSSProperties = {
    fontSize: 12, color: TEXT2, textTransform: "uppercase", letterSpacing: "0.04em",
  };

  return (
    <div style={{ background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 12, padding: 24, display: "flex", flexDirection: "column", gap: 20, maxWidth: 560 }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: TEXT }}>White Label настройки</div>
      <p style={{ fontSize: 13, color: MUTED, margin: 0, lineHeight: 1.6 }}>
        Настройте брендинг вашего агентства. Клиенты увидят ваш логотип и название вместо стандартных.
      </p>

      {err && (
        <div style={{ color: DANGER, fontSize: 13, padding: "8px 12px", background: `${DANGER}15`, borderRadius: 8, border: `1px solid ${DANGER}30` }}>
          {err}
        </div>
      )}

      {/* Logo */}
      <label style={labelStyle}>
        <span style={sectionLabelStyle}>URL логотипа</span>
        <input
          value={logoUrl}
          onChange={(e) => setLogoUrl(e.target.value)}
          placeholder="https://example.com/logo.png"
          style={inputStyle}
        />
        {logoUrl && (
          <div style={{ marginTop: 8 }}>
            <img
              src={logoUrl}
              alt="logo preview"
              style={{ maxHeight: 48, maxWidth: 180, objectFit: "contain", borderRadius: 6 }}
              onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
            />
          </div>
        )}
      </label>

      {/* Brand name */}
      <label style={labelStyle}>
        <span style={sectionLabelStyle}>Название бренда</span>
        <input
          value={brandName}
          onChange={(e) => setBrandName(e.target.value)}
          placeholder="Моё Агентство"
          style={inputStyle}
        />
      </label>

      {/* Accent color */}
      <label style={labelStyle}>
        <span style={sectionLabelStyle}>Акцентный цвет</span>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <input
            value={accentColor}
            onChange={(e) => setAccentColor(e.target.value)}
            placeholder="#00ff88"
            style={{ ...inputStyle, fontFamily: MONO, width: "auto", flex: 1 }}
          />
          <div style={{
            width: 36, height: 36, borderRadius: 8, flexShrink: 0,
            background: /^#[0-9a-fA-F]{3,8}$/.test(accentColor) ? accentColor : "#333",
            border: `1px solid ${BORDER}`,
          }} />
          <input
            type="color"
            value={/^#[0-9a-fA-F]{6}$/.test(accentColor) ? accentColor : "#00ff88"}
            onChange={(e) => setAccentColor(e.target.value)}
            style={{ width: 36, height: 36, borderRadius: 8, border: `1px solid ${BORDER}`, cursor: "pointer", padding: 2, background: SURFACE }}
          />
        </div>
      </label>

      {/* Custom domain */}
      <label style={labelStyle}>
        <span style={sectionLabelStyle}>Кастомный домен</span>
        <input
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          placeholder="agency.mycompany.com"
          style={inputStyle}
        />
        <span style={{ fontSize: 12, color: MUTED }}>
          Требует настройки DNS — добавьте CNAME запись на наш хост
        </span>
      </label>

      {/* Save */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Btn variant="accent" onClick={() => void handleSave()} disabled={saving}>
          {saving ? "Сохраняем..." : saved ? "Сохранено!" : "Сохранить настройки"}
        </Btn>
        {saved && <Check size={16} style={{ color: ACCENT }} />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main AgencyDashboardPage
// ---------------------------------------------------------------------------

export function AgencyDashboardPage() {
  const { accessToken } = useAuth();

  const [agency, setAgency] = useState<Agency | null>(null);
  const [stats, setStats] = useState<AgencyStats | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState<"clients" | "invites" | "branding">("clients");

  const token = accessToken ?? "";

  const load = useCallback(async () => {
    if (!token) return;
    setBusy(true);
    setError("");
    try {
      const [agencyRes, statsRes] = await Promise.all([
        agencyApi.getOrCreate(token).catch(() => null),
        agencyApi.stats(token).catch(() => null),
      ]);
      if (agencyRes) setAgency(agencyRes);
      if (statsRes) setStats(statsRes);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
    } finally {
      setBusy(false);
    }
  }, [token]);

  useEffect(() => { void load(); }, [load]);

  const tabStyle = (tab: string): React.CSSProperties => ({
    padding: "7px 16px", borderRadius: 8, border: "none", cursor: "pointer",
    fontSize: 13, fontWeight: 600,
    background: activeTab === tab ? `${ACCENT}20` : "transparent",
    color: activeTab === tab ? ACCENT : TEXT2,
    transition: "all 0.15s",
  });

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "24px 16px", display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{
            width: 40, height: 40, borderRadius: 10, background: `${ACCENT}18`,
            display: "flex", alignItems: "center", justifyContent: "center", color: ACCENT, flexShrink: 0,
          }}>
            <Building2 size={20} />
          </div>
          <div>
            <h1 style={{ fontSize: "1.4rem", fontWeight: 800, color: TEXT, margin: 0 }}>
              {agency?.custom_brand_name ?? agency?.name ?? "Агентство"}
            </h1>
            <p style={{ fontSize: 13, color: MUTED, margin: 0 }}>
              Управление клиентами · White Label · Приглашения
            </p>
          </div>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => void load()}
          style={{
            display: "flex", alignItems: "center", gap: 6, padding: "8px 14px",
            borderRadius: 8, border: `1px solid ${BORDER}`, background: "transparent",
            color: TEXT2, cursor: "pointer", fontSize: 13,
          }}
        >
          <RefreshCw size={14} className={busy ? "spin" : ""} />
          Обновить
        </button>
      </div>

      {/* Error */}
      {error && (
        <div style={{
          padding: "10px 14px", borderRadius: 8, background: `${DANGER}15`,
          border: `1px solid ${DANGER}44`, color: DANGER, fontSize: 13,
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <AlertTriangle size={14} />
          {error}
        </div>
      )}

      {/* Stats row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
        <StatCard
          label="Клиентов"
          value={stats?.total_clients ?? "—"}
          sub="всего"
          icon={<Users size={17} />}
        />
        <StatCard
          label="Активных"
          value={stats?.active_clients ?? "—"}
          sub="прямо сейчас"
          icon={<Building2 size={17} />}
          accent={ACCENT}
        />
        <StatCard
          label="Доход клиентов"
          value={stats ? fmtRub(stats.total_revenue_rub) : "—"}
          sub="совокупный оборот"
          icon={<TrendingUp size={17} />}
          accent="#3b9eff"
        />
        <StatCard
          label="Заработано агентством"
          value={stats ? fmtRub(stats.agency_earned_rub) : "—"}
          sub={agency ? `${agency.revenue_share_pct}% комиссия` : undefined}
          icon={<DollarSign size={17} />}
          accent="#a78bfa"
        />
      </div>

      {/* Tab bar */}
      <div style={{
        display: "flex", gap: 4, background: SURFACE, borderRadius: 10,
        padding: 4, border: `1px solid ${BORDER}`, width: "fit-content",
      }}>
        <button type="button" style={tabStyle("clients")} onClick={() => setActiveTab("clients")}>
          Клиенты
          {stats && stats.total_clients > 0 && (
            <span style={{ marginLeft: 4, opacity: 0.7, fontFamily: MONO }}>({stats.total_clients})</span>
          )}
        </button>
        <button type="button" style={tabStyle("invites")} onClick={() => setActiveTab("invites")}>
          Приглашения
        </button>
        <button type="button" style={tabStyle("branding")} onClick={() => setActiveTab("branding")}>
          Брендинг
        </button>
      </div>

      {/* Tab content */}
      {activeTab === "clients" && <ClientsTab token={token} />}
      {activeTab === "invites" && <InvitesTab token={token} />}
      {activeTab === "branding" && agency && (
        <BrandingTab agency={agency} token={token} onUpdate={setAgency} />
      )}
      {activeTab === "branding" && !agency && !busy && (
        <div style={{ color: MUTED, fontSize: 14, padding: "20px 0" }}>
          Загрузка данных агентства...
        </div>
      )}
    </div>
  );
}
