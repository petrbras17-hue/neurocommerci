import { FormEvent, useEffect, useMemo, useState, useCallback, DragEvent } from "react";
import { motion } from "framer-motion";
import {
  Upload,
  Shield,
  Link,
  FileText,
  Clock,
  AlertTriangle,
  CheckCircle,
  User,
  Activity,
  Flame,
  Play,
  Square,
  Filter,
  ChevronLeft,
  ChevronRight,
  Archive,
  Users,
} from "lucide-react";
import { apiFetch } from "../api";
import { useAuth } from "../auth";

type TimelineItem = {
  kind: string;
  title: string;
  notes: string | null;
  result: string;
  created_at: string | null;
};

type AccountRow = {
  id: number;
  phone: string;
  proxy: string | null;
  proxy_id: number | null;
  session_status: string;
  last_active: string | null;
  ban_risk_level: string;
  status: string;
  health_status: string;
  lifecycle_stage: string;
  recommended_next_action: string;
  manual_notes: string | null;
  recent_steps: Array<{ id: number; step_title: string; result: string; created_at: string | null }>;
};

type AccountsResponse = {
  items: AccountRow[];
  total: number;
};

type ProxiesResponse = {
  items: Array<{ id: number; url: string; health_status: string; tenant_owned: boolean }>;
  total: number;
  summary: Record<string, unknown>;
};

type UploadResponse = {
  account_id: number;
  phone: string;
  bundle_ready: boolean;
  db_status: string;
};

type TimelineResponse = {
  account: {
    id: number;
    phone: string;
    manual_notes: string | null;
    recommended_next_action: string;
  };
  items: TimelineItem[];
  total: number;
};

type AccountStats = {
  total: number;
  active: number;
  frozen: number;
  warming_up: number;
  proxied: number;
  unproxied: number;
  dead: number;
};

type BulkImportResult = {
  imported: number;
  skipped: number;
  auto_proxied: number;
  errors: string[];
};

type BulkActionResult = {
  affected: number;
  action: string;
};

/* ── animation helpers ── */

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.07 } },
};

const item = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.16, 1, 0.3, 1] as const } },
};

/* ── risk pill color helper ── */

function riskColor(level: string): { bg: string; fg: string } {
  const l = level.toLowerCase();
  if (l === "low" || l === "safe") return { bg: "rgba(0,255,136,0.15)", fg: "var(--accent)" };
  if (l === "medium" || l === "moderate") return { bg: "rgba(255,170,0,0.15)", fg: "var(--warning)" };
  return { bg: "rgba(255,68,68,0.15)", fg: "var(--danger)" };
}

/* ── status pill color helper ── */

function statusColor(status: string): { bg: string; fg: string } {
  const s = status.toLowerCase();
  if (s === "active" || s === "ok" || s === "healthy" || s === "connected")
    return { bg: "rgba(0,255,136,0.15)", fg: "var(--accent)" };
  if (s === "frozen" || s === "quarantine" || s === "warning")
    return { bg: "rgba(255,170,0,0.15)", fg: "var(--warning)" };
  if (s === "banned" || s === "error" || s === "dead")
    return { bg: "rgba(255,68,68,0.15)", fg: "var(--danger)" };
  return { bg: "rgba(68,136,255,0.15)", fg: "var(--info)" };
}

/* ── timeline event icon ── */

function timelineIcon(kind: string) {
  const k = kind.toLowerCase();
  if (k.includes("audit") || k.includes("check")) return <Shield size={14} />;
  if (k.includes("note") || k.includes("manual")) return <FileText size={14} />;
  if (k.includes("proxy") || k.includes("bind")) return <Link size={14} />;
  if (k.includes("upload") || k.includes("import")) return <Upload size={14} />;
  return <Clock size={14} />;
}

const STATUS_FILTERS = [
  { value: "", label: "Все" },
  { value: "active", label: "Активные" },
  { value: "frozen", label: "Замороженные" },
  { value: "warming_up", label: "На прогреве" },
  { value: "dead", label: "Мёртвые" },
  { value: "banned", label: "Забаненные" },
  { value: "unknown", label: "Неизвестно" },
];

const PAGE_SIZE = 25;
const monoFont = "'JetBrains Mono Variable', monospace";

export function AccountsPage() {
  const { accessToken } = useAuth();
  const [accounts, setAccounts] = useState<AccountsResponse>({ items: [], total: 0 });
  const [proxies, setProxies] = useState<ProxiesResponse | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [selectedProxyId, setSelectedProxyId] = useState<number | null>(null);
  const [manualProxy, setManualProxy] = useState("");
  const [sessionFile, setSessionFile] = useState<File | null>(null);
  const [metadataFile, setMetadataFile] = useState<File | null>(null);
  const [statusMessage, setStatusMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [notesDraft, setNotesDraft] = useState("");
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [dragOver, setDragOver] = useState(false);

  /* ── New v2 state ── */
  const [stats, setStats] = useState<AccountStats>({ total: 0, active: 0, frozen: 0, warming_up: 0, proxied: 0, unproxied: 0, dead: 0 });
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(0);
  const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set());
  const [bulkFiles, setBulkFiles] = useState<File[]>([]);
  const [bulkImportResult, setBulkImportResult] = useState<BulkImportResult | null>(null);
  const [showBulkConfirm, setShowBulkConfirm] = useState<string | null>(null); // action name or null

  /* ── data loading ── */

  const loadStats = useCallback(async () => {
    if (!accessToken) return;
    try {
      const data = await apiFetch<AccountStats>("/v1/accounts/stats", { accessToken });
      setStats(data);
    } catch {
      // fallback: compute from loaded accounts
    }
  }, [accessToken]);

  const loadState = useCallback(async () => {
    if (!accessToken) return;
    const params = new URLSearchParams();
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(page * PAGE_SIZE));
    if (statusFilter) params.set("status", statusFilter);

    const [accountsPayload, proxiesPayload] = await Promise.all([
      apiFetch<AccountsResponse>(`/v1/web/accounts?${params.toString()}`, { accessToken }),
      apiFetch<ProxiesResponse>("/v1/web/proxies/available", { accessToken }),
    ]);
    setAccounts(accountsPayload);
    setProxies(proxiesPayload);
    if (accountsPayload.items.length && selectedAccountId === null) {
      setSelectedAccountId(accountsPayload.items[0].id);
    }
    if (proxiesPayload.items.length && selectedProxyId === null) {
      setSelectedProxyId(proxiesPayload.items[0].id);
    }

    // Compute stats locally if endpoint not available
    if (stats.total === 0 && accountsPayload.total > 0) {
      const all = accountsPayload.items;
      setStats({
        total: accountsPayload.total,
        active: all.filter(a => ["active", "ok", "healthy", "connected"].includes(a.session_status.toLowerCase())).length,
        frozen: all.filter(a => a.session_status.toLowerCase() === "frozen").length,
        warming_up: all.filter(a => a.lifecycle_stage?.toLowerCase() === "warming_up").length,
        proxied: all.filter(a => a.proxy !== null).length,
        unproxied: all.filter(a => a.proxy === null).length,
        dead: all.filter(a => ["dead", "banned"].includes(a.session_status.toLowerCase())).length,
      });
    }
  }, [accessToken, selectedAccountId, selectedProxyId, page, statusFilter, stats.total]);

  const reload = useCallback(async () => {
    await Promise.all([loadState(), loadStats()]);
  }, [loadState, loadStats]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const selectedAccount = useMemo(
    () => accounts.items.find((item) => item.id === selectedAccountId) || null,
    [accounts.items, selectedAccountId]
  );

  useEffect(() => {
    setNotesDraft(selectedAccount?.manual_notes || "");
    if (!accessToken || !selectedAccountId) {
      setTimeline(null);
      return;
    }
    void apiFetch<TimelineResponse>(`/v1/web/accounts/${selectedAccountId}/timeline`, { accessToken })
      .then(setTimeline)
      .catch(() => setTimeline(null));
  }, [accessToken, selectedAccountId, selectedAccount?.manual_notes]);

  /* ── single pair upload ── */

  const uploadPair = async (event: FormEvent) => {
    event.preventDefault();
    if (!accessToken || !sessionFile || !metadataFile) {
      setStatusMessage("Нужно выбрать оба файла: .session и .json.");
      return;
    }
    const body = new FormData();
    body.set("session_file", sessionFile);
    body.set("metadata_file", metadataFile);
    setBusy(true);
    setStatusMessage("");
    try {
      const result = await apiFetch<UploadResponse>("/v1/web/accounts/upload", {
        method: "POST",
        accessToken,
        body,
      });
      setSelectedAccountId(result.account_id);
      setStatusMessage(`Аккаунт ${result.phone} загружен. Комплект файлов готов.`);
      await reload();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "upload_failed");
    } finally {
      setBusy(false);
    }
  };

  const bindProxy = async () => {
    if (!accessToken || !selectedAccountId) return;
    setBusy(true);
    setStatusMessage("");
    try {
      await apiFetch(`/v1/web/accounts/${selectedAccountId}/bind-proxy`, {
        method: "POST",
        accessToken,
        json: {
          proxy_id: manualProxy ? null : selectedProxyId,
          proxy_string: manualProxy || null,
        },
      });
      setStatusMessage("Прокси привязан к аккаунту.");
      setManualProxy("");
      await reload();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "proxy_bind_failed");
    } finally {
      setBusy(false);
    }
  };

  const runAudit = async (accountId: number) => {
    if (!accessToken) return;
    setBusy(true);
    setStatusMessage("");
    try {
      const result = await apiFetch<{ audit: AccountRow }>(`/v1/web/accounts/${accountId}/audit`, {
        method: "POST",
        accessToken,
      });
      setStatusMessage(`Проверка доступа завершена: ${result.audit.session_status}`);
      await reload();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "audit_failed");
    } finally {
      setBusy(false);
    }
  };

  const saveNotes = async () => {
    if (!accessToken || !selectedAccountId) return;
    setBusy(true);
    setStatusMessage("");
    try {
      await apiFetch(`/v1/web/accounts/${selectedAccountId}/notes`, {
        method: "POST",
        accessToken,
        json: { notes: notesDraft },
      });
      setStatusMessage("Ручная заметка сохранена.");
      await reload();
      const timelinePayload = await apiFetch<TimelineResponse>(`/v1/web/accounts/${selectedAccountId}/timeline`, { accessToken });
      setTimeline(timelinePayload);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "notes_save_failed");
    } finally {
      setBusy(false);
    }
  };

  /* ── drag-and-drop handlers ── */

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = () => setDragOver(false);

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    // For single pair mode
    for (const f of files) {
      if (f.name.endsWith(".session")) setSessionFile(f);
      if (f.name.endsWith(".json")) setMetadataFile(f);
    }
    // For bulk mode: collect all relevant files
    const bulkCandidates = files.filter(
      f => f.name.endsWith(".session") || f.name.endsWith(".json") || f.name.endsWith(".zip")
    );
    if (bulkCandidates.length > 2) {
      setBulkFiles(bulkCandidates);
    }
  };

  /* ── Bulk import ── */

  const handleBulkDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    const relevant = files.filter(
      f => f.name.endsWith(".session") || f.name.endsWith(".json") || f.name.endsWith(".zip")
    );
    setBulkFiles(prev => [...prev, ...relevant]);
  };

  const handleBulkFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    const relevant = files.filter(
      f => f.name.endsWith(".session") || f.name.endsWith(".json") || f.name.endsWith(".zip")
    );
    setBulkFiles(prev => [...prev, ...relevant]);
  };

  const removeBulkFile = (index: number) => {
    setBulkFiles(prev => prev.filter((_, i) => i !== index));
  };

  const handleBulkImport = async () => {
    if (!accessToken || bulkFiles.length === 0) {
      setStatusMessage("Добавьте файлы для массового импорта.");
      return;
    }
    setBusy(true);
    setStatusMessage("");
    setBulkImportResult(null);
    try {
      const body = new FormData();
      for (const f of bulkFiles) {
        body.append("files", f);
      }
      const result = await apiFetch<BulkImportResult>("/v1/accounts/bulk-import", {
        method: "POST",
        accessToken,
        body,
      });
      setBulkImportResult(result);
      setBulkFiles([]);
      setStatusMessage(`Массовый импорт: ${result.imported} загружено, ${result.skipped} пропущено, ${result.auto_proxied} с авто-прокси.`);
      await reload();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "bulk_import_failed");
    } finally {
      setBusy(false);
    }
  };

  /* ── Bulk actions ── */

  const toggleCheck = (id: number) => {
    setCheckedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleCheckAll = () => {
    if (checkedIds.size === accounts.items.length) {
      setCheckedIds(new Set());
    } else {
      setCheckedIds(new Set(accounts.items.map(a => a.id)));
    }
  };

  const handleBulkAction = async (action: string) => {
    if (!accessToken) return;
    setShowBulkConfirm(null);
    setBusy(true);
    setStatusMessage("");
    try {
      const result = await apiFetch<BulkActionResult>("/v1/accounts/bulk-action", {
        method: "POST",
        accessToken,
        json: {
          action,
          account_ids: checkedIds.size > 0 ? Array.from(checkedIds) : null,
        },
      });
      setStatusMessage(`Действие "${action}" выполнено для ${result.affected} аккаунтов.`);
      setCheckedIds(new Set());
      await reload();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "bulk_action_failed");
    } finally {
      setBusy(false);
    }
  };

  /* ── computed ── */

  const totalPages = Math.max(1, Math.ceil(accounts.total / PAGE_SIZE));
  const allChecked = accounts.items.length > 0 && checkedIds.size === accounts.items.length;

  return (
    <motion.div
      className="page-grid"
      variants={container}
      initial="hidden"
      animate="show"
    >
      {/* ── Stats bar ── */}
      <motion.section
        variants={item}
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
          gap: 10,
        }}
      >
        {[
          { label: "Всего", value: stats.total, icon: Users, color: "var(--text)" },
          { label: "Активные", value: stats.active, icon: Activity, color: "var(--accent)" },
          { label: "Замороженные", value: stats.frozen, icon: AlertTriangle, color: "var(--warning)" },
          { label: "На прогреве", value: stats.warming_up, icon: Flame, color: "var(--info)" },
          { label: "С прокси", value: stats.proxied, icon: Shield, color: "var(--accent)" },
          { label: "Без прокси", value: stats.unproxied, icon: AlertTriangle, color: "var(--danger)" },
        ].map((card) => (
          <article key={card.label} className="panel" style={{ borderTop: `2px solid ${card.color}`, padding: "12px 16px" }}>
            <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
              <card.icon size={11} /> {card.label}
            </div>
            <div style={{
              color: card.color,
              fontFamily: monoFont,
              fontSize: 24,
              fontWeight: 600,
            }}>
              {card.value}
            </div>
          </article>
        ))}
      </motion.section>

      {/* ── Bulk actions bar ── */}
      <motion.section className="panel" variants={item}>
        <div className="panel-header">
          <div>
            <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Activity size={12} /> Массовые действия
            </div>
            <h2 style={{ color: "var(--text)" }}>
              {checkedIds.size > 0
                ? `Выбрано аккаунтов: ${checkedIds.size}`
                : "Применить ко всем аккаунтам"}
            </h2>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <button
            className="secondary-button"
            type="button"
            disabled={busy}
            onClick={() => setShowBulkConfirm("warmup_all")}
            style={{ display: "flex", alignItems: "center", gap: 8 }}
          >
            <Flame size={14} />
            Прогрев всех
          </button>
          <button
            className="primary-button"
            type="button"
            disabled={busy}
            onClick={() => setShowBulkConfirm("start_farm")}
            style={{ display: "flex", alignItems: "center", gap: 8 }}
          >
            <Play size={14} />
            Запустить ферму
          </button>
          <button
            className="ghost-button"
            type="button"
            disabled={busy}
            onClick={() => setShowBulkConfirm("stop_farm")}
            style={{ display: "flex", alignItems: "center", gap: 8 }}
          >
            <Square size={14} />
            Остановить ферму
          </button>
        </div>
      </motion.section>

      {/* ── Hero info cards ── */}
      <motion.section className="two-column-grid" variants={item}>
        <article className="panel" style={{ borderTop: "2px solid var(--accent)" }}>
          <div className="panel-header">
            <div>
              <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Shield size={12} /> Operator path
              </div>
              <h2 style={{ color: "var(--text)" }}>Что делает система</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Принимает pair <code style={{ color: "var(--accent)", fontFamily: monoFont, fontSize: 12 }}>.session + .json</code> и держит canonical storage.</li>
            <li>Показывает прокси, audit, lifecycle и следующий рекомендуемый шаг.</li>
            <li>Сохраняет историю действий и ручные заметки без запуска боевых Telegram-side действий.</li>
          </ul>
        </article>
        <article className="panel" style={{ borderTop: "2px solid var(--accent)" }}>
          <div className="panel-header">
            <div>
              <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <User size={12} /> Operator path
              </div>
              <h2 style={{ color: "var(--text)" }}>Что делает оператор вручную</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Загружает pair, проверяет видимость прокси и запускает audit.</li>
            <li>Оставляет заметки по аккаунту и фиксирует ручные шаги в истории.</li>
            <li>Решает, когда аккаунт безопасно двигать дальше, не опираясь на silent automation.</li>
          </ul>
        </article>
      </motion.section>

      {/* ── Bulk import section ── */}
      <motion.section className="panel" variants={item}>
        <div className="panel-header">
          <div>
            <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Archive size={12} /> Массовый импорт
            </div>
            <h2 style={{ color: "var(--text)" }}>Загрузить несколько аккаунтов</h2>
          </div>
        </div>
        <div className="stack-form">
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleBulkDrop}
            style={{
              border: `2px dashed ${dragOver ? "var(--accent)" : "var(--border-bright)"}`,
              borderRadius: 12,
              padding: 24,
              textAlign: "center",
              background: dragOver ? "var(--accent-glow)" : "var(--surface-2)",
              transition: "all 200ms ease",
              cursor: "pointer",
            }}
          >
            <Upload
              size={28}
              style={{ color: dragOver ? "var(--accent)" : "var(--muted)", marginBottom: 8 }}
            />
            <p style={{ color: "var(--text-secondary)", margin: "0 0 12px 0", fontSize: 13 }}>
              Перетащите файлы (.session + .json пары или .zip) или выберите
            </p>
            <input
              type="file"
              multiple
              accept=".session,.json,.zip"
              onChange={handleBulkFileSelect}
              style={{
                padding: "8px 12px",
                borderRadius: 8,
                border: "1px solid var(--border)",
                background: "var(--surface)",
                color: "var(--text)",
                fontSize: 12,
              }}
            />
          </div>

          {bulkFiles.length > 0 ? (
            <div style={{
              padding: "12px 14px",
              borderRadius: 8,
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
            }}>
              <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
                Файлов к импорту: {bulkFiles.length}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 150, overflowY: "auto" }}>
                {bulkFiles.map((f, i) => (
                  <div key={`${f.name}-${i}`} style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "4px 8px",
                    borderRadius: 6,
                    background: "var(--surface)",
                    fontSize: 12,
                  }}>
                    <span style={{ fontFamily: monoFont, color: "var(--accent)" }}>
                      <CheckCircle size={10} style={{ marginRight: 4 }} />
                      {f.name}
                    </span>
                    <button
                      type="button"
                      onClick={() => removeBulkFile(i)}
                      style={{
                        background: "none",
                        border: "none",
                        color: "var(--muted)",
                        cursor: "pointer",
                        fontSize: 11,
                      }}
                    >
                      x
                    </button>
                  </div>
                ))}
              </div>
              <button
                className="primary-button"
                type="button"
                disabled={busy}
                onClick={() => void handleBulkImport()}
                style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 8 }}
              >
                <Upload size={14} />
                {busy ? "Импортируем..." : "Импортировать все"}
              </button>
            </div>
          ) : null}

          {bulkImportResult ? (
            <div style={{
              padding: "10px 14px",
              borderRadius: 8,
              background: "rgba(0,255,136,0.08)",
              border: "1px solid rgba(0,255,136,0.2)",
              fontSize: 13,
              color: "var(--accent)",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}>
              <CheckCircle size={14} />
              Загружено: {bulkImportResult.imported} | Пропущено: {bulkImportResult.skipped} | Авто-прокси: {bulkImportResult.auto_proxied}
              {bulkImportResult.errors.length > 0 ? ` | Ошибки: ${bulkImportResult.errors.length}` : ""}
            </div>
          ) : null}
        </div>
      </motion.section>

      {/* ── Single pair upload section ── */}
      <motion.section className="panel" variants={item}>
        <div className="panel-header">
          <div>
            <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Upload size={12} /> Один аккаунт
            </div>
            <h2 style={{ color: "var(--text)" }}>Загрузите pair .session + .json</h2>
          </div>
        </div>
        <form className="stack-form" onSubmit={uploadPair}>
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            style={{
              border: `2px dashed ${dragOver ? "var(--accent)" : "var(--border-bright)"}`,
              borderRadius: 12,
              padding: 32,
              textAlign: "center",
              background: dragOver ? "var(--accent-glow)" : "var(--surface-2)",
              transition: "all 200ms ease",
              cursor: "pointer",
            }}
          >
            <Upload
              size={32}
              style={{ color: dragOver ? "var(--accent)" : "var(--muted)", marginBottom: 12 }}
            />
            <p style={{ color: "var(--text-secondary)", margin: "0 0 16px 0", fontSize: 13 }}>
              Перетащите файлы сюда или выберите вручную
            </p>
            <div style={{ display: "grid", gap: 12, gridTemplateColumns: "1fr 1fr", maxWidth: 500, margin: "0 auto" }}>
              <label className="field" style={{ textAlign: "left" }}>
                <span style={{ color: "var(--accent)", fontSize: 12, fontWeight: 500 }}>.session</span>
                <input
                  type="file"
                  accept=".session"
                  onChange={(event) => setSessionFile(event.target.files?.[0] || null)}
                  style={{
                    padding: "8px 12px",
                    borderRadius: 8,
                    border: "1px solid var(--border)",
                    background: "var(--surface)",
                    color: "var(--text)",
                    fontSize: 12,
                  }}
                />
                {sessionFile && (
                  <span style={{ fontSize: 11, color: "var(--accent)", fontFamily: monoFont }}>
                    <CheckCircle size={10} style={{ marginRight: 4 }} />{sessionFile.name}
                  </span>
                )}
              </label>
              <label className="field" style={{ textAlign: "left" }}>
                <span style={{ color: "var(--accent)", fontSize: 12, fontWeight: 500 }}>.json</span>
                <input
                  type="file"
                  accept=".json"
                  onChange={(event) => setMetadataFile(event.target.files?.[0] || null)}
                  style={{
                    padding: "8px 12px",
                    borderRadius: 8,
                    border: "1px solid var(--border)",
                    background: "var(--surface)",
                    color: "var(--text)",
                    fontSize: 12,
                  }}
                />
                {metadataFile && (
                  <span style={{ fontSize: 11, color: "var(--accent)", fontFamily: monoFont }}>
                    <CheckCircle size={10} style={{ marginRight: 4 }} />{metadataFile.name}
                  </span>
                )}
              </label>
            </div>
          </div>
          <button className="primary-button" type="submit" disabled={busy} style={{ justifySelf: "start" }}>
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Upload size={14} />
              {busy ? "Загружаем..." : "Загрузить pair"}
            </span>
          </button>
        </form>
      </motion.section>

      {/* ── Proxy bind + Audit ── */}
      <motion.section className="two-column-grid" variants={item}>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Link size={12} /> Step 2
              </div>
              <h2 style={{ color: "var(--text)" }}>Привяжите живой прокси</h2>
            </div>
          </div>
          <div className="field" style={{ marginBottom: 12 }}>
            <span style={{ color: "var(--text-secondary)", fontSize: 12, fontWeight: 500 }}>Аккаунт</span>
            <select
              value={selectedAccountId ?? ""}
              onChange={(event) => setSelectedAccountId(Number(event.target.value))}
              style={{
                padding: "10px 14px",
                borderRadius: 8,
                border: "1px solid var(--border)",
                background: "var(--surface-2)",
                color: "var(--text)",
                fontFamily: monoFont,
                fontSize: 13,
              }}
            >
              {accounts.items.map((a) => (
                <option key={a.id} value={a.id}>{a.phone}</option>
              ))}
            </select>
          </div>
          <div className="field" style={{ marginBottom: 12 }}>
            <span style={{ color: "var(--text-secondary)", fontSize: 12, fontWeight: 500 }}>Прокси из пула</span>
            <select
              value={selectedProxyId ?? ""}
              onChange={(event) => setSelectedProxyId(Number(event.target.value))}
              style={{
                padding: "10px 14px",
                borderRadius: 8,
                border: "1px solid var(--border)",
                background: "var(--surface-2)",
                color: "var(--text)",
                fontSize: 13,
              }}
            >
              {(proxies?.items || []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.url} - {p.health_status}
                </option>
              ))}
            </select>
          </div>
          <label className="field" style={{ marginBottom: 14 }}>
            <span style={{ color: "var(--text-secondary)", fontSize: 12, fontWeight: 500 }}>Или добавьте proxy string вручную</span>
            <input
              value={manualProxy}
              onChange={(event) => setManualProxy(event.target.value)}
              placeholder="socks5://user:pass@host:port"
              style={{
                padding: "10px 14px",
                borderRadius: 8,
                border: "1px solid var(--border)",
                background: "var(--surface-2)",
                color: "var(--text)",
                fontFamily: monoFont,
                fontSize: 13,
              }}
            />
          </label>
          <button
            className="secondary-button"
            type="button"
            disabled={busy || !selectedAccountId}
            onClick={() => void bindProxy()}
            style={{ display: "flex", alignItems: "center", gap: 8 }}
          >
            <Link size={14} />
            Привязать прокси
          </button>
        </article>

        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Shield size={12} /> Step 3
              </div>
              <h2 style={{ color: "var(--text)" }}>Безопасный audit</h2>
            </div>
          </div>
          <p style={{ color: "var(--text-secondary)", fontSize: 13, lineHeight: 1.5, margin: "0 0 16px 0" }}>
            Этот шаг показывает, что сейчас видит система: session status, lifecycle и recommended next action.
          </p>
          <button
            className="ghost-button"
            type="button"
            disabled={busy || !selectedAccountId}
            onClick={() => selectedAccountId && void runAudit(selectedAccountId)}
            style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}
          >
            <Shield size={14} />
            Запустить audit для выбранного аккаунта
          </button>
          {selectedAccount ? (
            <div style={{
              marginTop: 8,
              padding: "10px 14px",
              borderRadius: 8,
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              fontSize: 13,
              color: "var(--text-secondary)",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}>
              <AlertTriangle size={14} style={{ color: "var(--warning)", flexShrink: 0 }} />
              <span>
                Для <span style={{ color: "var(--accent)", fontFamily: monoFont }}>{selectedAccount.phone}</span>: следующий шаг —{" "}
                <strong style={{ color: "var(--text)" }}>{selectedAccount.recommended_next_action}</strong>
              </span>
            </div>
          ) : null}
        </article>
      </motion.section>

      {/* ── Accounts table ── */}
      <motion.section className="panel wide" variants={item}>
        <div className="panel-header">
          <div>
            <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <User size={12} /> Account audit
            </div>
            <h2 style={{ color: "var(--text)" }}>Аккаунты в workspace</h2>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Filter size={14} style={{ color: "var(--muted)" }} />
              <select
                value={statusFilter}
                onChange={(e) => { setStatusFilter(e.target.value); setPage(0); }}
                style={{
                  padding: "6px 10px",
                  borderRadius: 8,
                  border: "1px solid var(--border)",
                  background: "var(--surface-2)",
                  color: "var(--text)",
                  fontSize: 12,
                }}
              >
                {STATUS_FILTERS.map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </select>
            </div>
            <span style={{
              fontFamily: monoFont,
              fontSize: 12,
              color: "var(--muted)",
            }}>
              {accounts.total} total
            </span>
          </div>
        </div>

        {statusMessage ? (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            style={{
              padding: "10px 14px",
              borderRadius: 8,
              background: statusMessage.toLowerCase().includes("fail") || statusMessage.toLowerCase().includes("ошибк")
                ? "rgba(255,68,68,0.1)"
                : "rgba(0,255,136,0.1)",
              color: statusMessage.toLowerCase().includes("fail") || statusMessage.toLowerCase().includes("ошибк")
                ? "var(--danger)"
                : "var(--accent)",
              border: `1px solid ${
                statusMessage.toLowerCase().includes("fail") || statusMessage.toLowerCase().includes("ошибк")
                  ? "rgba(255,68,68,0.2)"
                  : "rgba(0,255,136,0.2)"
              }`,
              fontSize: 13,
              marginBottom: 16,
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            {statusMessage.toLowerCase().includes("fail") || statusMessage.toLowerCase().includes("ошибк")
              ? <AlertTriangle size={14} />
              : <CheckCircle size={14} />}
            {statusMessage}
          </motion.div>
        ) : null}

        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 36 }}>
                  <input
                    type="checkbox"
                    checked={allChecked}
                    onChange={toggleCheckAll}
                    title="Выбрать все"
                  />
                </th>
                <th>Phone</th>
                <th>Proxy</th>
                <th>Session status</th>
                <th>Last active</th>
                <th>Risk</th>
                <th>Lifecycle</th>
                <th>Recommended next action</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {accounts.items.map((a) => {
                const risk = riskColor(a.ban_risk_level);
                const status = statusColor(a.session_status);
                return (
                  <tr
                    key={a.id}
                    onClick={() => setSelectedAccountId(a.id)}
                    style={{
                      cursor: "pointer",
                      background: a.id === selectedAccountId ? "var(--surface-2)" : undefined,
                    }}
                  >
                    <td onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={checkedIds.has(a.id)}
                        onChange={() => toggleCheck(a.id)}
                      />
                    </td>
                    <td style={{
                      fontFamily: monoFont,
                      fontSize: 13,
                      fontWeight: 500,
                      color: "var(--text)",
                    }}>
                      {a.phone}
                    </td>
                    <td style={{
                      fontFamily: monoFont,
                      fontSize: 12,
                      color: a.proxy ? "var(--text-secondary)" : "var(--muted)",
                    }}>
                      {a.proxy || "---"}
                    </td>
                    <td>
                      <span style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "3px 10px",
                        borderRadius: 999,
                        background: status.bg,
                        color: status.fg,
                        fontSize: 11,
                        fontWeight: 600,
                      }}>
                        {a.session_status}
                      </span>
                    </td>
                    <td style={{
                      fontFamily: monoFont,
                      fontSize: 12,
                      color: "var(--muted)",
                    }}>
                      {a.last_active || "---"}
                    </td>
                    <td>
                      <span style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "3px 10px",
                        borderRadius: 999,
                        background: risk.bg,
                        color: risk.fg,
                        fontSize: 11,
                        fontWeight: 600,
                      }}>
                        {a.ban_risk_level}
                      </span>
                    </td>
                    <td style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                      {a.lifecycle_stage}
                    </td>
                    <td style={{ fontSize: 12, color: "var(--text-secondary)", maxWidth: 220 }}>
                      {a.recommended_next_action}
                    </td>
                    <td>
                      <button
                        className="ghost-button"
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedAccountId(a.id);
                          void runAudit(a.id);
                        }}
                        disabled={busy}
                        style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}
                      >
                        <Shield size={12} />
                        Проверить
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {accounts.total > PAGE_SIZE ? (
          <div style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 12,
            marginTop: 16,
            paddingTop: 12,
            borderTop: "1px solid var(--border)",
          }}>
            <button
              className="ghost-button"
              type="button"
              disabled={page === 0}
              onClick={() => setPage(page - 1)}
              style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}
            >
              <ChevronLeft size={14} /> Назад
            </button>
            <span style={{ fontFamily: monoFont, fontSize: 12, color: "var(--text-secondary)" }}>
              {page + 1} / {totalPages}
            </span>
            <button
              className="ghost-button"
              type="button"
              disabled={page >= totalPages - 1}
              onClick={() => setPage(page + 1)}
              style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}
            >
              Далее <ChevronRight size={14} />
            </button>
          </div>
        ) : null}
      </motion.section>

      {/* ── Notes + Timeline ── */}
      <motion.section className="two-column-grid" variants={item}>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <FileText size={12} /> Manual notes
              </div>
              <h2 style={{ color: "var(--text)" }}>Заметки оператора</h2>
            </div>
          </div>
          <textarea
            value={notesDraft}
            onChange={(event) => setNotesDraft(event.target.value)}
            placeholder="Зафиксируйте, что оператор проверил руками и что безопасно делать дальше."
            style={{
              width: "100%",
              minHeight: 150,
              padding: 14,
              borderRadius: 12,
              border: "1px solid var(--border)",
              background: "var(--surface-2)",
              color: "var(--text)",
              resize: "vertical",
              fontFamily: "inherit",
              fontSize: 13,
              lineHeight: 1.5,
            }}
          />
          <button
            className="secondary-button"
            type="button"
            disabled={busy || !selectedAccountId}
            onClick={() => void saveNotes()}
            style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}
          >
            <FileText size={14} />
            Сохранить заметку
          </button>
        </article>

        <article className="panel" style={{ borderLeft: "2px solid var(--accent)" }}>
          <div className="panel-header">
            <div>
              <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Clock size={12} /> Timeline
              </div>
              <h2 style={{ color: "var(--text)" }}>История шагов</h2>
            </div>
          </div>
          <div className="terminal-window" style={{ maxHeight: 360 }}>
            {(timeline?.items || []).length ? (
              (timeline?.items || []).map((t, index) => (
                <div
                  key={`${t.kind}-${t.created_at || index}`}
                  className="terminal-line"
                  style={{
                    padding: "8px 0",
                    borderBottom: index < (timeline?.items || []).length - 1 ? "1px solid var(--border)" : "none",
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 10,
                  }}
                >
                  <span style={{
                    color: "var(--accent)",
                    flexShrink: 0,
                    display: "flex",
                    alignItems: "center",
                    marginTop: 2,
                  }}>
                    {timelineIcon(t.kind)}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                      <strong style={{ color: "var(--text)", fontSize: 13 }}>{t.title}</strong>
                      <span style={{
                        color: "var(--muted)",
                        fontSize: 11,
                        fontFamily: monoFont,
                        flexShrink: 0,
                      }}>
                        {t.created_at || "---"}
                      </span>
                    </div>
                    {t.notes ? (
                      <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: 12, whiteSpace: "pre-wrap" }}>
                        {t.notes}
                      </p>
                    ) : null}
                  </div>
                </div>
              ))
            ) : (
              <div style={{
                padding: "24px 0",
                textAlign: "center",
                color: "var(--muted)",
                fontSize: 13,
                fontFamily: monoFont,
              }}>
                <Clock size={20} style={{ marginBottom: 8, opacity: 0.5 }} />
                <p style={{ margin: 0 }}>Пока нет истории шагов.</p>
                <p style={{ margin: "4px 0 0", fontSize: 11 }}>После audit и заметок она появится здесь.</p>
              </div>
            )}
          </div>
        </article>
      </motion.section>

      {/* ── Bulk action confirm modal ── */}
      {showBulkConfirm ? (
        <div className="modal-overlay" onClick={() => setShowBulkConfirm(null)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div>
                <div className="eyebrow">Подтверждение</div>
                <h2 style={{ color: "var(--text)" }}>
                  {showBulkConfirm === "warmup_all" && "Запустить прогрев?"}
                  {showBulkConfirm === "start_farm" && "Запустить ферму?"}
                  {showBulkConfirm === "stop_farm" && "Остановить ферму?"}
                </h2>
              </div>
            </div>
            <p style={{ color: "var(--text-secondary)", fontSize: 13, lineHeight: 1.5, margin: "0 0 16px" }}>
              {checkedIds.size > 0
                ? `Действие будет применено к ${checkedIds.size} выбранным аккаунтам.`
                : "Действие будет применено ко всем аккаунтам в workspace."}
            </p>
            <div className="actions-row">
              <button
                className="primary-button"
                type="button"
                disabled={busy}
                onClick={() => void handleBulkAction(showBulkConfirm)}
              >
                {busy ? "Выполняем..." : "Подтвердить"}
              </button>
              <button
                className="ghost-button"
                type="button"
                onClick={() => setShowBulkConfirm(null)}
              >
                Отмена
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </motion.div>
  );
}
