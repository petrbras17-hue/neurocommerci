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
  Download,
  Settings,
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

type ProxyLoadItem = {
  proxy_id: number;
  host: string;
  port: number;
  bindings_count: number;
  health_status: string;
  last_checked: string | null;
};

type AutoAssignResult = {
  ok: boolean;
  account_id: number;
  proxy_id: number;
  proxy_host: string;
  strategy: string;
};

type MassAssignResult = {
  assigned: Array<{ account_id: number; proxy_id: number }>;
  skipped: number[];
  errors: Array<{ account_id: number; reason: string }>;
};

type BulkActionResult = {
  affected: number;
  action: string;
};

type PendingReviewAccount = {
  id: number;
  phone: string;
  lifecycle_stage: string;
  health_status: string;
  status: string;
  health_score: number | null;
  survivability_score: number | null;
  warmup_sessions_completed: number;
  warmup_sessions_total: number;
  warmup_actions_performed: number;
  proxy_status: "bound" | "unbound";
  proxy_id: number | null;
  manual_notes: string | null;
  created_at: string | null;
  last_active_at: string | null;
};

type PendingReviewResponse = {
  items: PendingReviewAccount[];
  total: number;
};


type DuplicateEntry = {
  phone: string;
  existing_account_id: number;
};

type CheckDuplicatesResult = {
  duplicates: DuplicateEntry[];
  new: string[];
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
  const [tdataPasscode, setTdataPasscode] = useState("");
  const [discoveringPhone, setDiscoveringPhone] = useState<number | null>(null);
  const [duplicateWarnings, setDuplicateWarnings] = useState<DuplicateEntry[]>([]);
  const [exportBusy, setExportBusy] = useState(false);

  /* ── Approval gate state ── */
  const [showReviewTab, setShowReviewTab] = useState(false);
  const [pendingReview, setPendingReview] = useState<PendingReviewResponse>({ items: [], total: 0 });
  const [reviewCheckedIds, setReviewCheckedIds] = useState<Set<number>>(new Set());
  const [reviewBusy, setReviewBusy] = useState(false);
  const [showRejectModal, setShowRejectModal] = useState<number | null>(null); // account_id
  const [rejectReason, setRejectReason] = useState("");

  /* ── Batch settings modal state ── */
  const [showBatchSettings, setShowBatchSettings] = useState(false);
  const [batchProxyStrategy, setBatchProxyStrategy] = useState("");
  const [batchAiProtection, setBatchAiProtection] = useState("");
  const [batchCommentLanguage, setBatchCommentLanguage] = useState("");
  const [batchWarmupMode, setBatchWarmupMode] = useState("");
  const [batchSettingsBusy, setBatchSettingsBusy] = useState(false);

  /* ── Smart proxy routing state ── */
  const [autoAssignStrategy, setAutoAssignStrategy] = useState<"healthiest" | "round_robin" | "random">("healthiest");
  const [proxyLoad, setProxyLoad] = useState<ProxyLoadItem[]>([]);
  const [proxyLoadVisible, setProxyLoadVisible] = useState(false);

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
    await Promise.all([loadState(), loadStats(), loadPendingReview()]);
  }, [loadState, loadStats]); // eslint-disable-line react-hooks/exhaustive-deps

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

  /* ── Smart proxy routing handlers ── */

  const autoAssignProxy = async () => {
    if (!accessToken || !selectedAccountId) return;
    setBusy(true);
    setStatusMessage("");
    try {
      const result = await apiFetch<AutoAssignResult>("/v1/proxies/auto-assign", {
        method: "POST",
        accessToken,
        json: { account_id: selectedAccountId, strategy: autoAssignStrategy },
      });
      setStatusMessage(`Авто-назначен прокси ${result.proxy_host} (стратегия: ${result.strategy}).`);
      await reload();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "auto_assign_failed");
    } finally {
      setBusy(false);
    }
  };

  const massAssignProxies = async () => {
    if (!accessToken) return;
    setBusy(true);
    setStatusMessage("");
    try {
      const unproxiedIds = accounts.items
        .filter(a => a.proxy_id === null)
        .map(a => a.id);
      if (unproxiedIds.length === 0) {
        setStatusMessage("Все аккаунты уже имеют прокси.");
        return;
      }
      const result = await apiFetch<MassAssignResult>("/v1/proxies/mass-assign", {
        method: "POST",
        accessToken,
        json: { account_ids: unproxiedIds, strategy: autoAssignStrategy },
      });
      setStatusMessage(
        `Назначено: ${result.assigned.length}, пропущено: ${result.skipped.length}, ошибок: ${result.errors.length}.`
      );
      await reload();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "mass_assign_failed");
    } finally {
      setBusy(false);
    }
  };

  const loadProxyLoad = async () => {
    if (!accessToken) return;
    try {
      const data = await apiFetch<{ items: ProxyLoadItem[]; total: number }>("/v1/proxies/load", { accessToken });
      setProxyLoad(data.items);
      setProxyLoadVisible(true);
    } catch {
      setStatusMessage("Не удалось загрузить статистику прокси.");
    }
  };

  const cleanupDeadBindings = async () => {
    if (!accessToken) return;
    setBusy(true);
    setStatusMessage("");
    try {
      const result = await apiFetch<{ unbound_count: number; affected_accounts: number[] }>(
        "/v1/proxies/cleanup-dead",
        { method: "POST", accessToken }
      );
      setStatusMessage(`Очищено мёртвых привязок: ${result.unbound_count}.`);
      await reload();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "cleanup_failed");
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
    setDuplicateWarnings([]);

    // Pre-check duplicates before uploading
    const dupes = await checkDuplicates(bulkFiles);
    if (dupes.length > 0) {
      setDuplicateWarnings(dupes);
      // Still proceed — server will upsert; warnings are informational
    }

    try {
      const body = new FormData();
      for (const f of bulkFiles) {
        body.append("files", f);
      }
      if (tdataPasscode) {
        body.append("tdata_passcode", tdataPasscode);
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

  /* ── Duplicate pre-check before bulk import ── */

  const checkDuplicates = async (files: File[]): Promise<DuplicateEntry[]> => {
    if (!accessToken) return [];
    // Extract phone digits from .session and .json filenames
    const phones: string[] = [];
    for (const f of files) {
      const lower = f.name.toLowerCase();
      if (lower.endsWith(".session") || lower.endsWith(".json")) {
        const stem = f.name.replace(/\.[^.]+$/, "");
        const digits = stem.replace(/\D/g, "");
        if (digits) phones.push(`+${digits}`);
      }
    }
    if (phones.length === 0) return [];
    try {
      const result = await apiFetch<CheckDuplicatesResult>("/v1/accounts/check-duplicates", {
        method: "POST",
        accessToken,
        json: { phones: [...new Set(phones)] },
      });
      return result.duplicates;
    } catch {
      return [];
    }
  };

  /* ── Export ── */

  const handleExport = async (format: "csv" | "json") => {
    if (!accessToken) return;
    setExportBusy(true);
    try {
      const params = new URLSearchParams({ format });
      if (statusFilter) params.set("status", statusFilter);
      const resp = await fetch(`/v1/accounts/export?${params.toString()}`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!resp.ok) {
        setStatusMessage(`Экспорт не удался: ${resp.status}`);
        return;
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = format === "json" ? "accounts.json" : "accounts.csv";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "export_failed");
    } finally {
      setExportBusy(false);
    }
  };

  /* ── Phone discovery for TData accounts ── */
  const handleDiscoverPhone = async (accountId: number) => {
    if (!accessToken) return;
    setDiscoveringPhone(accountId);
    try {
      const result = await apiFetch<{
        ok: boolean;
        phone?: string;
        first_name?: string;
        error?: string;
        renamed?: boolean;
      }>(`/v1/accounts/${accountId}/discover-phone`, { method: "POST", accessToken });
      if (result.ok) {
        setStatusMessage(`Телефон определён: ${result.phone}${result.first_name ? ` (${result.first_name})` : ""}${result.renamed ? " — файлы переименованы" : ""}`);
        await reload();
      } else {
        setStatusMessage(`Ошибка определения телефона: ${result.error || "unknown"}`);
      }
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "discover_phone_failed");
    } finally {
      setDiscoveringPhone(null);
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


  /* ── Batch settings ── */

  const handleBatchSettings = async () => {
    if (!accessToken || checkedIds.size === 0) return;
    const settings: Record<string, string> = {};
    if (batchProxyStrategy) settings.proxy_strategy = batchProxyStrategy;
    if (batchAiProtection) settings.ai_protection = batchAiProtection;
    if (batchCommentLanguage) settings.comment_language = batchCommentLanguage;
    if (batchWarmupMode) settings.warmup_mode = batchWarmupMode;
    if (Object.keys(settings).length === 0) {
      setStatusMessage("Выберите хотя бы одну настройку для применения.");
      return;
    }
    setBatchSettingsBusy(true);
    setStatusMessage("");
    try {
      const result = await apiFetch<{ updated: number }>("/v1/accounts/batch-settings", {
        method: "POST",
        accessToken,
        json: {
          account_ids: Array.from(checkedIds),
          settings,
        },
      });
      setStatusMessage(`Настройки применены к ${result.updated} аккаунтам.`);
      setShowBatchSettings(false);
      setBatchProxyStrategy("");
      setBatchAiProtection("");
      setBatchCommentLanguage("");
      setBatchWarmupMode("");
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "batch_settings_failed");
    } finally {
      setBatchSettingsBusy(false);
    }
  };

  /* ── Approval gate actions ── */

  const loadPendingReview = async () => {
    if (!accessToken) return;
    try {
      const data = await apiFetch<PendingReviewResponse>("/v1/accounts/pending-review?limit=100&offset=0", { accessToken });
      setPendingReview(data);
    } catch {
      // silent
    }
  };

  const handleApprove = async (accountId: number) => {
    if (!accessToken) return;
    setReviewBusy(true);
    try {
      await apiFetch(`/v1/accounts/${accountId}/approve`, { method: "POST", accessToken });
      setStatusMessage(`Аккаунт #${accountId} одобрен — статус: execution_ready`);
      await Promise.all([reload(), loadPendingReview()]);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "approve_failed");
    } finally {
      setReviewBusy(false);
    }
  };

  const handleReject = async (accountId: number, reason: string) => {
    if (!accessToken || !reason.trim()) return;
    setReviewBusy(true);
    try {
      await apiFetch(`/v1/accounts/${accountId}/reject`, {
        method: "POST",
        accessToken,
        json: { reason },
      });
      setStatusMessage(`Аккаунт #${accountId} возвращён на прогрев. Причина: ${reason}`);
      setShowRejectModal(null);
      setRejectReason("");
      await Promise.all([reload(), loadPendingReview()]);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "reject_failed");
    } finally {
      setReviewBusy(false);
    }
  };

  const handleBulkApproveReview = async () => {
    if (!accessToken || reviewCheckedIds.size === 0) return;
    setReviewBusy(true);
    try {
      const result = await apiFetch<{ ok: boolean; approved_count: number; errors: Array<{ account_id: number; error: string }> }>(
        "/v1/accounts/bulk-approve",
        {
          method: "POST",
          accessToken,
          json: { account_ids: Array.from(reviewCheckedIds) },
        }
      );
      setStatusMessage(`Одобрено аккаунтов: ${result.approved_count}. Ошибок: ${result.errors.length}`);
      setReviewCheckedIds(new Set());
      await Promise.all([reload(), loadPendingReview()]);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "bulk_approve_failed");
    } finally {
      setReviewBusy(false);
    }
  };

  const toggleReviewCheck = (id: number) => {
    setReviewCheckedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleReviewCheckAll = () => {
    if (reviewCheckedIds.size === pendingReview.items.length) {
      setReviewCheckedIds(new Set());
    } else {
      setReviewCheckedIds(new Set(pendingReview.items.map(a => a.id)));
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
          {checkedIds.size >= 2 && (
            <button
              className="secondary-button"
              type="button"
              disabled={busy || batchSettingsBusy}
              onClick={() => setShowBatchSettings(true)}
              style={{ display: "flex", alignItems: "center", gap: 8, borderColor: "rgba(0,255,136,0.4)" }}
            >
              <Settings size={14} />
              Массовые настройки
            </button>
          )}
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>Экспорт:</span>
            <button
              className="secondary-button"
              type="button"
              disabled={exportBusy}
              onClick={() => void handleExport("csv")}
              style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 12px", fontSize: 12 }}
            >
              <Download size={12} />
              CSV
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={exportBusy}
              onClick={() => void handleExport("json")}
              style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 12px", fontSize: 12 }}
            >
              <Download size={12} />
              JSON
            </button>
          </div>
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
            <li>Принимает pair <code style={{ color: "var(--accent)", fontFamily: monoFont, fontSize: 12 }}>.session + .json</code> или <code style={{ color: "var(--accent)", fontFamily: monoFont, fontSize: 12 }}>TData ZIP</code> и держит canonical storage.</li>
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
              Перетащите файлы (.session + .json пары, TData ZIP, или обычный .zip) или выберите
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
                {bulkFiles.map((f, i) => {
                  const stem = f.name.replace(/\.[^.]+$/, "");
                  const digits = stem.replace(/\D/g, "");
                  const isDuplicate = digits ? duplicateWarnings.some(d => d.phone === `+${digits}`) : false;
                  return (
                  <div key={`${f.name}-${i}`} style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "4px 8px",
                    borderRadius: 6,
                    background: isDuplicate ? "rgba(255,170,0,0.06)" : "var(--surface)",
                    border: isDuplicate ? "1px solid rgba(255,170,0,0.25)" : "1px solid transparent",
                    fontSize: 12,
                  }}>
                    <span style={{ fontFamily: monoFont, color: isDuplicate ? "var(--warning)" : "var(--accent)" }}>
                      {isDuplicate
                        ? <AlertTriangle size={10} style={{ marginRight: 4 }} />
                        : <CheckCircle size={10} style={{ marginRight: 4 }} />
                      }
                      {f.name}
                      {isDuplicate && <span style={{ fontSize: 10, marginLeft: 6, opacity: 0.7 }}>(дубль)</span>}
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
                  );
                })}
              </div>
              {duplicateWarnings.length > 0 && (
                <div style={{
                  marginTop: 8,
                  padding: "8px 12px",
                  borderRadius: 6,
                  background: "rgba(255,170,0,0.08)",
                  border: "1px solid rgba(255,170,0,0.3)",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
                    <AlertTriangle size={12} style={{ color: "var(--warning)" }} />
                    <span style={{ fontSize: 12, color: "var(--warning)", fontWeight: 500 }}>
                      {duplicateWarnings.length} аккаунт{duplicateWarnings.length > 1 ? "а уже существуют" : " уже существует"} — будут обновлены
                    </span>
                  </div>
                  {duplicateWarnings.map(d => (
                    <div key={d.phone} style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: monoFont, paddingLeft: 18 }}>
                      {d.phone} (ID: {d.existing_account_id})
                    </div>
                  ))}
                </div>
              )}

              {bulkFiles.some(f => f.name.toLowerCase().endsWith(".zip")) && (
                <div style={{ marginTop: 8 }}>
                  <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                    TData passcode (если архив зашифрован, обычно пусто):
                  </label>
                  <input
                    type="password"
                    value={tdataPasscode}
                    onChange={e => setTdataPasscode(e.target.value)}
                    placeholder="Оставьте пустым если нет пароля"
                    style={{
                      padding: "6px 10px",
                      borderRadius: 6,
                      border: "1px solid var(--border)",
                      background: "var(--surface)",
                      color: "var(--text)",
                      fontSize: 12,
                      width: "100%",
                      fontFamily: monoFont,
                    }}
                  />
                </div>
              )}
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
            <h2 style={{ color: "var(--text)" }}>Загрузите .session + .json или TData</h2>
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

          {/* ── Smart proxy routing ── */}
          <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "16px 0" }} />
          <div className="eyebrow" style={{ marginBottom: 8 }}>Авто-назначение прокси</div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
            <select
              value={autoAssignStrategy}
              onChange={(e) => setAutoAssignStrategy(e.target.value as "healthiest" | "round_robin" | "random")}
              style={{
                padding: "8px 12px",
                borderRadius: 8,
                border: "1px solid var(--border)",
                background: "var(--surface-2)",
                color: "var(--text)",
                fontSize: 13,
                flex: "1 1 140px",
              }}
            >
              <option value="healthiest">Лучший по здоровью</option>
              <option value="round_robin">По кругу</option>
              <option value="random">Случайный</option>
            </select>
            <button
              className="secondary-button"
              type="button"
              disabled={busy || !selectedAccountId}
              onClick={() => void autoAssignProxy()}
              style={{ display: "flex", alignItems: "center", gap: 6, flex: "1 1 auto" }}
            >
              <Link size={13} />
              Авто-назначить прокси
            </button>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              className="ghost-button"
              type="button"
              disabled={busy}
              onClick={() => void massAssignProxies()}
              style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, padding: "6px 10px" }}
            >
              <Users size={12} />
              Назначить всем
            </button>
            <button
              className="ghost-button"
              type="button"
              disabled={busy}
              onClick={() => void loadProxyLoad()}
              style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, padding: "6px 10px" }}
            >
              <Activity size={12} />
              Загрузка прокси
            </button>
            <button
              className="ghost-button"
              type="button"
              disabled={busy}
              onClick={() => void cleanupDeadBindings()}
              style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, padding: "6px 10px" }}
            >
              <Archive size={12} />
              Очистить мёртвые
            </button>
          </div>

          {/* ── Proxy load stats ── */}
          {proxyLoadVisible && proxyLoad.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                <span style={{ color: "var(--text-secondary)", fontSize: 12, fontWeight: 500 }}>
                  Загрузка прокси ({proxyLoad.length})
                </span>
                <button
                  className="ghost-button"
                  type="button"
                  onClick={() => setProxyLoadVisible(false)}
                  style={{ fontSize: 11, padding: "2px 6px" }}
                >
                  скрыть
                </button>
              </div>
              <div style={{ maxHeight: 160, overflowY: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
                {proxyLoad.map((p) => (
                  <div
                    key={p.proxy_id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "6px 10px",
                      borderRadius: 6,
                      background: "var(--surface-2)",
                      border: "1px solid var(--border)",
                      fontSize: 12,
                      fontFamily: monoFont,
                    }}
                  >
                    <span style={{ color: "var(--text)" }}>{p.host}:{p.port}</span>
                    <span style={{
                      color: p.health_status === "alive" ? "var(--accent)" : p.health_status === "failing" ? "var(--warning)" : "var(--text-secondary)",
                    }}>
                      {p.health_status}
                    </span>
                    <span style={{ color: "var(--text-secondary)" }}>
                      {p.bindings_count} привязок
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
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
            <>
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
              {selectedAccount.phone && (selectedAccount.phone.includes("tdata") || (selectedAccount.phone.replace(/\D/g, "").length < 8 && selectedAccount.phone.replace(/\D/g, "").length > 0)) && (
                <button
                  className="primary-button"
                  type="button"
                  disabled={discoveringPhone === selectedAccount.id}
                  onClick={() => void handleDiscoverPhone(selectedAccount.id)}
                  style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 8 }}
                >
                  <Shield size={14} />
                  {discoveringPhone === selectedAccount.id ? "Определяем телефон..." : "Определить телефон через Telegram"}
                </button>
              )}
            </>
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

            <button
              type="button"
              onClick={() => { setShowReviewTab(!showReviewTab); void loadPendingReview(); }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 14px",
                borderRadius: 8,
                border: showReviewTab ? "1px solid var(--accent)" : "1px solid var(--border)",
                background: showReviewTab ? "rgba(0,255,136,0.12)" : "var(--surface-2)",
                color: showReviewTab ? "var(--accent)" : "var(--text-secondary)",
                cursor: "pointer",
                fontSize: 12,
                fontWeight: 600,
              }}
            >
              <CheckCircle size={13} />
              На проверке
              {pendingReview.total > 0 && (
                <span style={{
                  background: "var(--accent)",
                  color: "#000",
                  borderRadius: 999,
                  padding: "1px 7px",
                  fontSize: 10,
                  fontWeight: 700,
                  marginLeft: 2,
                }}>
                  {pendingReview.total}
                </span>
              )}
            </button>

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
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                        {a.phone}
                        {a.phone && (a.phone.includes("tdata") || (a.phone.replace(/\D/g, "").length < 8 && a.phone.replace(/\D/g, "").length > 0)) && (
                          <button
                            type="button"
                            disabled={discoveringPhone === a.id}
                            onClick={(e) => { e.stopPropagation(); void handleDiscoverPhone(a.id); }}
                            title="Определить телефон через Telegram"
                            style={{
                              background: "rgba(0,255,136,0.12)",
                              border: "1px solid rgba(0,255,136,0.3)",
                              borderRadius: 4,
                              color: "var(--accent)",
                              cursor: "pointer",
                              fontSize: 10,
                              padding: "2px 6px",
                              fontWeight: 600,
                            }}
                          >
                            {discoveringPhone === a.id ? "..." : "ID"}
                          </button>
                        )}
                      </span>
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


      {/* ── Gate Review Section ── */}
      {showReviewTab && (
        <motion.section className="panel wide" variants={item} style={{ borderTop: "2px solid var(--accent)" }}>
          <div className="panel-header">
            <div>
              <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <CheckCircle size={12} /> Approval gate
              </div>
              <h2 style={{ color: "var(--text)" }}>
                На проверке ({pendingReview.total})
              </h2>
            </div>
            {reviewCheckedIds.size > 0 && (
              <button
                type="button"
                className="primary-button"
                disabled={reviewBusy}
                onClick={() => void handleBulkApproveReview()}
                style={{ display: "flex", alignItems: "center", gap: 8 }}
              >
                <CheckCircle size={14} />
                Одобрить выбранные ({reviewCheckedIds.size})
              </button>
            )}
          </div>

          {pendingReview.items.length === 0 ? (
            <div style={{
              padding: "32px 0",
              textAlign: "center",
              color: "var(--muted)",
              fontSize: 13,
            }}>
              <CheckCircle size={24} style={{ marginBottom: 10, opacity: 0.4 }} />
              <p style={{ margin: 0 }}>Нет аккаунтов, ожидающих проверки.</p>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th style={{ width: 36 }}>
                      <input
                        type="checkbox"
                        checked={reviewCheckedIds.size === pendingReview.items.length && pendingReview.items.length > 0}
                        onChange={toggleReviewCheckAll}
                        title="Выбрать все"
                      />
                    </th>
                    <th>Phone</th>
                    <th>Health score</th>
                    <th>Прогрев</th>
                    <th>Прокси</th>
                    <th>Статус</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {pendingReview.items.map((a) => {
                    const score = a.health_score ?? 0;
                    const scoreColor = score >= 70 ? "var(--accent)" : score >= 40 ? "var(--warning)" : "var(--danger)";
                    const scoreBarBg = score >= 70 ? "rgba(0,255,136,0.15)" : score >= 40 ? "rgba(255,170,0,0.15)" : "rgba(255,68,68,0.15)";
                    return (
                      <tr key={a.id}>
                        <td onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={reviewCheckedIds.has(a.id)}
                            onChange={() => toggleReviewCheck(a.id)}
                          />
                        </td>
                        <td style={{ fontFamily: "'JetBrains Mono Variable', monospace", fontSize: 13, fontWeight: 500 }}>
                          {a.phone}
                        </td>
                        <td>
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <div style={{
                              width: 80,
                              height: 6,
                              borderRadius: 3,
                              background: "var(--border)",
                              overflow: "hidden",
                            }}>
                              <div style={{
                                width: `${score}%`,
                                height: "100%",
                                background: scoreColor,
                                borderRadius: 3,
                                transition: "width 0.3s",
                              }} />
                            </div>
                            <span style={{
                              fontFamily: "'JetBrains Mono Variable', monospace",
                              fontSize: 12,
                              color: scoreColor,
                              padding: "2px 7px",
                              borderRadius: 6,
                              background: scoreBarBg,
                              fontWeight: 600,
                            }}>
                              {a.health_score !== null ? a.health_score : "—"}
                            </span>
                          </div>
                        </td>
                        <td style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                          {a.warmup_sessions_completed}/{a.warmup_sessions_total} сессий
                          <span style={{ color: "var(--muted)", marginLeft: 6 }}>
                            ({a.warmup_actions_performed} действий)
                          </span>
                        </td>
                        <td>
                          <span style={{
                            padding: "3px 10px",
                            borderRadius: 999,
                            fontSize: 11,
                            fontWeight: 600,
                            background: a.proxy_status === "bound" ? "rgba(0,255,136,0.15)" : "rgba(255,68,68,0.15)",
                            color: a.proxy_status === "bound" ? "var(--accent)" : "var(--danger)",
                          }}>
                            {a.proxy_status === "bound" ? "Привязан" : "Не привязан"}
                          </span>
                        </td>
                        <td>
                          <span style={{
                            padding: "3px 10px",
                            borderRadius: 999,
                            fontSize: 11,
                            fontWeight: 600,
                            background: "rgba(68,136,255,0.15)",
                            color: "var(--info)",
                          }}>
                            {a.health_status}
                          </span>
                        </td>
                        <td>
                          <div style={{ display: "flex", gap: 8 }}>
                            <button
                              type="button"
                              className="primary-button"
                              disabled={reviewBusy}
                              onClick={() => void handleApprove(a.id)}
                              style={{ fontSize: 12, padding: "5px 12px", display: "flex", alignItems: "center", gap: 6 }}
                            >
                              <CheckCircle size={12} />
                              Одобрить
                            </button>
                            <button
                              type="button"
                              className="ghost-button"
                              disabled={reviewBusy}
                              onClick={() => { setShowRejectModal(a.id); setRejectReason(""); }}
                              style={{ fontSize: 12, padding: "5px 12px", display: "flex", alignItems: "center", gap: 6, color: "var(--danger)", borderColor: "rgba(255,68,68,0.3)" }}
                            >
                              Вернуть на прогрев
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
        </motion.section>
      )}

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

      {/* ── Rejection modal ── */}
      {showRejectModal !== null && (
        <div className="modal-overlay" onClick={() => setShowRejectModal(null)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div>
                <div className="eyebrow">Вернуть на прогрев</div>
                <h2 style={{ color: "var(--text)" }}>Укажите причину</h2>
              </div>
            </div>
            <p style={{ color: "var(--text-secondary)", fontSize: 13, lineHeight: 1.5, margin: "0 0 12px" }}>
              Аккаунт #{showRejectModal} будет возвращён в статус warming_up.
            </p>
            <textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="Например: недостаточно прогрева, низкий health score..."
              rows={3}
              style={{
                width: "100%",
                padding: "10px 12px",
                borderRadius: 8,
                border: "1px solid var(--border)",
                background: "var(--surface-2)",
                color: "var(--text)",
                fontSize: 13,
                resize: "vertical",
                fontFamily: "inherit",
              }}
            />
            <div className="actions-row" style={{ marginTop: 14 }}>
              <button
                type="button"
                className="ghost-button"
                disabled={reviewBusy || !rejectReason.trim()}
                onClick={() => void handleReject(showRejectModal, rejectReason)}
                style={{ color: "var(--danger)", borderColor: "rgba(255,68,68,0.3)" }}
              >
                {reviewBusy ? "Сохраняем..." : "Вернуть на прогрев"}
              </button>
              <button
                type="button"
                className="ghost-button"
                onClick={() => setShowRejectModal(null)}
              >
                Отмена
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Batch settings modal ── */}
      {showBatchSettings && (
        <div className="modal-overlay" onClick={() => setShowBatchSettings(false)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 480 }}>
            <div className="panel-header">
              <div>
                <div className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <Settings size={12} /> Массовые настройки
                </div>
                <h2 style={{ color: "var(--text)" }}>Применить к {checkedIds.size} аккаунтам</h2>
              </div>
            </div>
            <p style={{ color: "var(--text-secondary)", fontSize: 13, lineHeight: 1.5, margin: "0 0 16px" }}>
              Заполните только те поля, которые хотите изменить. Пустые поля будут проигнорированы.
            </p>
            <div className="stack-form" style={{ gap: 12 }}>
              <label className="field">
                <span style={{ color: "var(--text-secondary)", fontSize: 12, fontWeight: 500 }}>
                  Стратегия прокси
                </span>
                <select
                  value={batchProxyStrategy}
                  onChange={(e) => setBatchProxyStrategy(e.target.value)}
                  style={{
                    padding: "9px 12px",
                    borderRadius: 8,
                    border: "1px solid var(--border)",
                    background: "var(--surface-2)",
                    color: batchProxyStrategy ? "var(--text)" : "var(--muted)",
                    fontSize: 13,
                  }}
                >
                  <option value="">— не менять —</option>
                  <option value="round_robin">Round Robin</option>
                  <option value="sticky">Sticky (фиксированный)</option>
                  <option value="geo_match">Geo Match</option>
                </select>
              </label>

              <label className="field">
                <span style={{ color: "var(--text-secondary)", fontSize: 12, fontWeight: 500 }}>
                  AI-защита
                </span>
                <select
                  value={batchAiProtection}
                  onChange={(e) => setBatchAiProtection(e.target.value)}
                  style={{
                    padding: "9px 12px",
                    borderRadius: 8,
                    border: "1px solid var(--border)",
                    background: "var(--surface-2)",
                    color: batchAiProtection ? "var(--text)" : "var(--muted)",
                    fontSize: 13,
                  }}
                >
                  <option value="">— не менять —</option>
                  <option value="off">Выключена</option>
                  <option value="conservative">Консервативная</option>
                  <option value="aggressive">Агрессивная</option>
                </select>
              </label>

              <label className="field">
                <span style={{ color: "var(--text-secondary)", fontSize: 12, fontWeight: 500 }}>
                  Язык комментариев
                </span>
                <select
                  value={batchCommentLanguage}
                  onChange={(e) => setBatchCommentLanguage(e.target.value)}
                  style={{
                    padding: "9px 12px",
                    borderRadius: 8,
                    border: "1px solid var(--border)",
                    background: "var(--surface-2)",
                    color: batchCommentLanguage ? "var(--text)" : "var(--muted)",
                    fontSize: 13,
                  }}
                >
                  <option value="">— не менять —</option>
                  <option value="ru">Русский</option>
                  <option value="en">English</option>
                  <option value="auto">Авто</option>
                </select>
              </label>

              <label className="field">
                <span style={{ color: "var(--text-secondary)", fontSize: 12, fontWeight: 500 }}>
                  Режим прогрева
                </span>
                <select
                  value={batchWarmupMode}
                  onChange={(e) => setBatchWarmupMode(e.target.value)}
                  style={{
                    padding: "9px 12px",
                    borderRadius: 8,
                    border: "1px solid var(--border)",
                    background: "var(--surface-2)",
                    color: batchWarmupMode ? "var(--text)" : "var(--muted)",
                    fontSize: 13,
                  }}
                >
                  <option value="">— не менять —</option>
                  <option value="conservative">Консервативный</option>
                  <option value="moderate">Умеренный</option>
                  <option value="aggressive">Агрессивный</option>
                </select>
              </label>
            </div>

            <div className="actions-row" style={{ marginTop: 20 }}>
              <button
                type="button"
                className="primary-button"
                disabled={
                  batchSettingsBusy ||
                  (!batchProxyStrategy && !batchAiProtection && !batchCommentLanguage && !batchWarmupMode)
                }
                onClick={() => void handleBatchSettings()}
                style={{ display: "flex", alignItems: "center", gap: 8 }}
              >
                <Settings size={14} />
                {batchSettingsBusy
                  ? "Применяем..."
                  : `Применить к ${checkedIds.size} аккаунтам`}
              </button>
              <button
                type="button"
                className="ghost-button"
                disabled={batchSettingsBusy}
                onClick={() => setShowBatchSettings(false)}
              >
                Отмена
              </button>
            </div>
          </div>
        </div>
      )}
    </motion.div>
  );
}
