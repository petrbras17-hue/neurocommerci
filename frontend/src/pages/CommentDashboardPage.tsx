import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  MessageSquare,
  TrendingUp,
  Zap,
  AlertTriangle,
  RefreshCw,
  Filter,
  Eye,
  BarChart2,
  Search,
  Plus,
  Edit3,
  Trash2,
  X,
  ToggleLeft,
  ToggleRight,
} from "lucide-react";
import { apiFetch } from "../api";
import { useAuth } from "../auth";

// ─── Types ───────────────────────────────────────────────────────────────────

interface CommentFeedItem {
  id: number;
  style_name: string;
  tone: string | null;
  channel_username: string | null;
  account_id: number | null;
  reactions_count: number;
  replies_count: number;
  was_deleted: boolean;
  posted_at: string | null;
  measured_at: string | null;
}

interface CommentStats {
  today: { comments: number; avg_reactions: number };
  week: { comments: number; ban_rate: number; top_style: string | null };
  month: { comments: number };
}

interface StyleABStats {
  total_comments: number;
  avg_reactions: number;
  avg_replies: number;
  deletion_rate: number;
}

interface StyleInfo {
  id: string;
  instruction: string;
  default_tone: string;
  ab_stats: StyleABStats;
}

interface PreviewResult {
  generated_comment: string | null;
  post_analysis: {
    topic: string;
    sentiment: string;
    language: string;
    suggested_angle: string;
  };
  would_comment: boolean;
  decision_reason: string;
  tone: string;
}

interface CustomStyle {
  id: number;
  name: string;
  description: string | null;
  system_prompt: string | null;
  examples: string[];
  tone: string | null;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

interface StyleFormState {
  name: string;
  description: string;
  system_prompt: string;
  examples: string; // newline-separated in the textarea
  tone: string;
}

const EMPTY_FORM: StyleFormState = {
  name: "",
  description: "",
  system_prompt: "",
  examples: "",
  tone: "positive",
};

// ─── Style labels (Russian) ───────────────────────────────────────────────────

const STYLE_LABELS: Record<string, string> = {
  question: "Вопрос",
  agree: "Согласие",
  supplement: "Дополнение",
  joke: "Шутка",
  expert: "Эксперт",
  personal: "Личный опыт",
  quote: "Цитата",
  emoji: "Эмодзи",
  controversial: "Спорное",
  gratitude: "Благодарность",
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  accent,
}: {
  icon: typeof MessageSquare;
  label: string;
  value: string | number;
  sub?: string;
  accent?: boolean;
}) {
  return (
    <div
      style={{
        background: "#111",
        border: `1px solid ${accent ? "#00ff88" : "#1e1e1e"}`,
        borderRadius: 12,
        padding: "20px 24px",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        minWidth: 160,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, color: accent ? "#00ff88" : "#888" }}>
        <Icon size={16} />
        <span style={{ fontSize: 12, fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>{label}</span>
      </div>
      <div style={{ fontSize: 28, fontWeight: 700, color: accent ? "#00ff88" : "#fff", fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: "#555" }}>{sub}</div>}
    </div>
  );
}

function BarChart({ data }: { data: { label: string; value: number; max: number }[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {data.map((item) => (
        <div key={item.label} style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 100, fontSize: 11, color: "#aaa", textAlign: "right", fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
            {STYLE_LABELS[item.label] ?? item.label}
          </div>
          <div style={{ flex: 1, height: 16, background: "#1e1e1e", borderRadius: 4, overflow: "hidden" }}>
            <div
              style={{
                width: item.max > 0 ? `${Math.min(100, (item.value / item.max) * 100)}%` : "0%",
                height: "100%",
                background: "#00ff88",
                borderRadius: 4,
                transition: "width 0.4s ease",
              }}
            />
          </div>
          <div style={{ width: 40, fontSize: 11, color: "#666", textAlign: "right", fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
            {item.value.toFixed(2)}
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export function CommentDashboardPage() {
  const { accessToken } = useAuth();

  const [stats, setStats] = useState<CommentStats | null>(null);
  const [feed, setFeed] = useState<CommentFeedItem[]>([]);
  const [styles, setStyles] = useState<StyleInfo[]>([]);
  const [feedTotal, setFeedTotal] = useState(0);
  const [feedOffset, setFeedOffset] = useState(0);
  const FEED_LIMIT = 20;

  // Filters
  const [filterChannel, setFilterChannel] = useState("");
  const [filterStyle, setFilterStyle] = useState("");
  const [filterAccount, setFilterAccount] = useState("");

  // Preview
  const [previewPostText, setPreviewPostText] = useState("");
  const [previewStyle, setPreviewStyle] = useState("");
  const [previewTone, setPreviewTone] = useState("positive");
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState("");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Tab
  const [activeTab, setActiveTab] = useState<"dashboard" | "custom-styles">("dashboard");

  // Custom styles
  const [customStyles, setCustomStyles] = useState<CustomStyle[]>([]);
  const [customStylesLoading, setCustomStylesLoading] = useState(false);
  const [customStylesError, setCustomStylesError] = useState("");

  // Modal
  const [modalOpen, setModalOpen] = useState(false);
  const [editingStyle, setEditingStyle] = useState<CustomStyle | null>(null);
  const [form, setForm] = useState<StyleFormState>(EMPTY_FORM);
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState("");

  // ── Loaders ───────────────────────────────────────────────────────────────

  const loadStats = async () => {
    if (!accessToken) return;
    try {
      const data = await apiFetch<CommentStats>("/v1/comments/stats", { accessToken });
      setStats(data);
    } catch {
      // Non-blocking
    }
  };

  const loadCustomStyles = async () => {
    if (!accessToken) return;
    setCustomStylesLoading(true);
    setCustomStylesError("");
    try {
      const data = await apiFetch<{ items: CustomStyle[]; total: number }>(
        "/v1/comments/custom-styles",
        { accessToken }
      );
      setCustomStyles(data.items);
    } catch (err) {
      setCustomStylesError(err instanceof Error ? err.message : "load_failed");
    } finally {
      setCustomStylesLoading(false);
    }
  };

  const openCreateModal = () => {
    setEditingStyle(null);
    setForm(EMPTY_FORM);
    setFormError("");
    setModalOpen(true);
  };

  const openEditModal = (style: CustomStyle) => {
    setEditingStyle(style);
    setForm({
      name: style.name,
      description: style.description ?? "",
      system_prompt: style.system_prompt ?? "",
      examples: (style.examples ?? []).join("\n"),
      tone: style.tone ?? "positive",
    });
    setFormError("");
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setEditingStyle(null);
    setForm(EMPTY_FORM);
    setFormError("");
  };

  const handleSaveStyle = async () => {
    if (!accessToken || !form.name.trim()) return;
    setFormBusy(true);
    setFormError("");
    try {
      const examples = form.examples
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      const body = {
        name: form.name.trim(),
        description: form.description.trim(),
        system_prompt: form.system_prompt.trim(),
        examples,
        tone: form.tone,
      };
      if (editingStyle) {
        await apiFetch(`/v1/comments/custom-styles/${editingStyle.id}`, {
          method: "PUT",
          accessToken,
          json: body,
        });
      } else {
        await apiFetch("/v1/comments/custom-styles", {
          method: "POST",
          accessToken,
          json: body,
        });
      }
      closeModal();
      await loadCustomStyles();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "save_failed");
    } finally {
      setFormBusy(false);
    }
  };

  const handleToggleActive = async (style: CustomStyle) => {
    if (!accessToken) return;
    try {
      await apiFetch(`/v1/comments/custom-styles/${style.id}`, {
        method: "PUT",
        accessToken,
        json: { is_active: !style.is_active },
      });
      await loadCustomStyles();
    } catch {
      // Non-blocking
    }
  };

  const handleDeleteStyle = async (style: CustomStyle) => {
    if (!accessToken) return;
    if (!confirm(`Удалить стиль "${style.name}"?`)) return;
    try {
      await apiFetch(`/v1/comments/custom-styles/${style.id}`, {
        method: "DELETE",
        accessToken,
      });
      await loadCustomStyles();
    } catch {
      // Non-blocking
    }
  };

  const loadFeed = async (offset = 0) => {
    if (!accessToken) return;
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: String(FEED_LIMIT), offset: String(offset) });
      if (filterChannel) params.set("channel_username", filterChannel);
      if (filterStyle) params.set("style_name", filterStyle);
      if (filterAccount) params.set("account_id", filterAccount);
      const data = await apiFetch<{ items: CommentFeedItem[]; total: number }>(
        `/v1/comments/feed?${params.toString()}`,
        { accessToken }
      );
      setFeed(data.items);
      setFeedTotal(data.total);
      setFeedOffset(offset);
    } catch (err) {
      setError(err instanceof Error ? err.message : "load_failed");
    } finally {
      setLoading(false);
    }
  };

  const loadStyles = async () => {
    if (!accessToken) return;
    try {
      const data = await apiFetch<{ styles: StyleInfo[] }>("/v1/comments/styles", { accessToken });
      setStyles(data.styles);
    } catch {
      // Non-blocking
    }
  };

  useEffect(() => {
    void Promise.all([loadStats(), loadFeed(0), loadStyles(), loadCustomStyles()]).catch(() => {});
  }, [accessToken]);

  // ── Preview ───────────────────────────────────────────────────────────────

  const handlePreview = async () => {
    if (!accessToken || !previewPostText.trim()) return;
    setPreviewBusy(true);
    setPreviewError("");
    setPreviewResult(null);
    try {
      const result = await apiFetch<PreviewResult>("/v1/comments/preview", {
        method: "POST",
        accessToken,
        json: {
          post_text: previewPostText,
          style: previewStyle,
          tone: previewTone,
          existing_comments: [],
        },
      });
      setPreviewResult(result);
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : "preview_failed");
    } finally {
      setPreviewBusy(false);
    }
  };

  // ── Chart data ────────────────────────────────────────────────────────────

  const chartData = styles
    .filter((s) => s.ab_stats.total_comments > 0)
    .map((s) => ({ label: s.id, value: s.ab_stats.avg_reactions, max: 0 }))
    .sort((a, b) => b.value - a.value);
  const maxReactions = Math.max(...chartData.map((d) => d.value), 0.01);
  const chartDataNorm = chartData.map((d) => ({ ...d, max: maxReactions }));

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      style={{ padding: "24px", maxWidth: 1200, margin: "0 auto" }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <MessageSquare size={24} color="#00ff88" />
          <div>
            <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: "#fff" }}>
              Comment Dashboard
            </h1>
            <div style={{ fontSize: 12, color: "#555", fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
              A/B тестирование стилей комментариев
            </div>
          </div>
        </div>
        <button
          onClick={() => void Promise.all([loadStats(), loadFeed(feedOffset), loadStyles(), loadCustomStyles()])}
          style={{
            background: "#111",
            border: "1px solid #1e1e1e",
            borderRadius: 8,
            padding: "8px 14px",
            color: "#888",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 12,
          }}
        >
          <RefreshCw size={14} />
          Обновить
        </button>
      </div>

      {/* Tab navigation */}
      <div style={{ display: "flex", gap: 4, marginBottom: 24, borderBottom: "1px solid #1e1e1e", paddingBottom: 0 }}>
        {[
          { key: "dashboard", label: "Дашборд" },
          { key: "custom-styles", label: `Кастомные стили${customStyles.length > 0 ? ` (${customStyles.length})` : ""}` },
        ].map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setActiveTab(key as "dashboard" | "custom-styles")}
            style={{
              background: "transparent",
              border: "none",
              borderBottom: activeTab === key ? "2px solid #00ff88" : "2px solid transparent",
              color: activeTab === key ? "#00ff88" : "#555",
              padding: "10px 18px",
              fontSize: 13,
              fontWeight: activeTab === key ? 600 : 400,
              cursor: "pointer",
              fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)",
              transition: "color 0.15s",
              marginBottom: -1,
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ── Dashboard tab ─────────────────────────────────────────────────────── */}
      {activeTab === "dashboard" && <>

      {/* Stat Cards */}
      {stats && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 16, marginBottom: 32 }}>
          <StatCard
            icon={MessageSquare}
            label="Комментариев сегодня"
            value={stats.today.comments}
            accent
          />
          <StatCard
            icon={TrendingUp}
            label="Ср. реакций сегодня"
            value={stats.today.avg_reactions.toFixed(1)}
            sub="среднее за день"
          />
          <StatCard
            icon={Zap}
            label="Лучший стиль (7д)"
            value={stats.week.top_style ? (STYLE_LABELS[stats.week.top_style] ?? stats.week.top_style) : "—"}
            sub={`${stats.week.comments} комментариев`}
          />
          <StatCard
            icon={AlertTriangle}
            label="Удалений (7д)"
            value={`${(stats.week.ban_rate * 100).toFixed(1)}%`}
            sub="ban rate"
          />
          <StatCard
            icon={BarChart2}
            label="Комментариев за месяц"
            value={stats.month.comments}
          />
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24, marginBottom: 32 }}>
        {/* A/B Chart */}
        <div
          style={{
            background: "#111",
            border: "1px solid #1e1e1e",
            borderRadius: 12,
            padding: 24,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 20 }}>
            <BarChart2 size={16} color="#00ff88" />
            <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "#fff" }}>
              A/B результаты по стилям
            </h2>
          </div>
          {chartDataNorm.length === 0 ? (
            <div style={{ color: "#555", fontSize: 13, textAlign: "center", padding: "32px 0" }}>
              Нет данных. Запустите ферму и дождитесь первых комментариев.
            </div>
          ) : (
            <BarChart data={chartDataNorm} />
          )}
        </div>

        {/* Preview */}
        <div
          style={{
            background: "#111",
            border: "1px solid #1e1e1e",
            borderRadius: 12,
            padding: 24,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 20 }}>
            <Eye size={16} color="#00ff88" />
            <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "#fff" }}>
              Предпросмотр комментария
            </h2>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <textarea
              placeholder="Вставьте текст поста..."
              value={previewPostText}
              onChange={(e) => setPreviewPostText(e.target.value)}
              rows={4}
              style={{
                background: "#0a0a0b",
                border: "1px solid #2a2a2a",
                borderRadius: 8,
                color: "#e0e0e0",
                padding: "10px 12px",
                fontSize: 13,
                resize: "vertical",
                fontFamily: "inherit",
              }}
            />
            <div style={{ display: "flex", gap: 10 }}>
              <select
                value={previewStyle}
                onChange={(e) => setPreviewStyle(e.target.value)}
                style={{
                  flex: 1,
                  background: "#0a0a0b",
                  border: "1px solid #2a2a2a",
                  borderRadius: 8,
                  color: "#e0e0e0",
                  padding: "8px 10px",
                  fontSize: 13,
                }}
              >
                <option value="">Авто стиль</option>
                {Object.entries(STYLE_LABELS).map(([id, label]) => (
                  <option key={id} value={id}>{label}</option>
                ))}
              </select>
              <select
                value={previewTone}
                onChange={(e) => setPreviewTone(e.target.value)}
                style={{
                  flex: 1,
                  background: "#0a0a0b",
                  border: "1px solid #2a2a2a",
                  borderRadius: 8,
                  color: "#e0e0e0",
                  padding: "8px 10px",
                  fontSize: 13,
                }}
              >
                <option value="positive">Позитивный</option>
                <option value="hater">Скептик</option>
                <option value="emotional">Эмоциональный</option>
                <option value="expert">Эксперт</option>
                <option value="witty">Остроумный</option>
              </select>
            </div>
            <button
              onClick={() => void handlePreview()}
              disabled={previewBusy || !previewPostText.trim()}
              style={{
                background: previewBusy ? "#1a2a1a" : "#00ff88",
                color: previewBusy ? "#00ff88" : "#000",
                border: "none",
                borderRadius: 8,
                padding: "10px 16px",
                fontWeight: 600,
                fontSize: 13,
                cursor: previewBusy ? "not-allowed" : "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
              }}
            >
              <Search size={14} />
              {previewBusy ? "Генерация..." : "Сгенерировать"}
            </button>
            {previewError && (
              <div style={{ color: "#ef4444", fontSize: 12 }}>{previewError}</div>
            )}
            {previewResult && (
              <div
                style={{
                  background: "#0a0a0b",
                  border: "1px solid #1e3a1e",
                  borderRadius: 8,
                  padding: 12,
                  fontSize: 13,
                }}
              >
                {previewResult.generated_comment ? (
                  <div style={{ color: "#00ff88", fontStyle: "italic", marginBottom: 8 }}>
                    "{previewResult.generated_comment}"
                  </div>
                ) : (
                  <div style={{ color: "#ef4444", marginBottom: 8 }}>Комментарий не сгенерирован</div>
                )}
                <div style={{ fontSize: 11, color: "#555" }}>
                  Тема: {previewResult.post_analysis?.topic} · Язык: {previewResult.post_analysis?.language} ·{" "}
                  {previewResult.would_comment ? (
                    <span style={{ color: "#22c55e" }}>Будет опубликован</span>
                  ) : (
                    <span style={{ color: "#eab308" }}>Пропущен: {previewResult.decision_reason}</span>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Feed */}
      <div
        style={{
          background: "#111",
          border: "1px solid #1e1e1e",
          borderRadius: 12,
          padding: 24,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Filter size={16} color="#00ff88" />
            <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "#fff" }}>
              Лента комментариев
            </h2>
            {feedTotal > 0 && (
              <span style={{ fontSize: 11, color: "#555", fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
                ({feedTotal} всего)
              </span>
            )}
          </div>
          {/* Filters */}
          <div style={{ display: "flex", gap: 8 }}>
            <input
              placeholder="Канал..."
              value={filterChannel}
              onChange={(e) => setFilterChannel(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void loadFeed(0)}
              style={{
                background: "#0a0a0b",
                border: "1px solid #2a2a2a",
                borderRadius: 6,
                color: "#e0e0e0",
                padding: "6px 10px",
                fontSize: 12,
                width: 110,
              }}
            />
            <select
              value={filterStyle}
              onChange={(e) => { setFilterStyle(e.target.value); void loadFeed(0); }}
              style={{
                background: "#0a0a0b",
                border: "1px solid #2a2a2a",
                borderRadius: 6,
                color: "#e0e0e0",
                padding: "6px 10px",
                fontSize: 12,
              }}
            >
              <option value="">Все стили</option>
              {Object.entries(STYLE_LABELS).map(([id, label]) => (
                <option key={id} value={id}>{label}</option>
              ))}
            </select>
            <button
              onClick={() => void loadFeed(0)}
              style={{
                background: "#1a1a1a",
                border: "1px solid #2a2a2a",
                borderRadius: 6,
                color: "#aaa",
                padding: "6px 12px",
                fontSize: 12,
                cursor: "pointer",
              }}
            >
              <Search size={12} />
            </button>
          </div>
        </div>

        {error && (
          <div style={{ color: "#ef4444", fontSize: 13, marginBottom: 16 }}>{error}</div>
        )}

        {loading ? (
          <div style={{ color: "#555", fontSize: 13, padding: "24px 0", textAlign: "center" }}>Загрузка...</div>
        ) : feed.length === 0 ? (
          <div style={{ color: "#555", fontSize: 13, padding: "24px 0", textAlign: "center" }}>
            Нет комментариев. Данные появятся после запуска фермы.
          </div>
        ) : (
          <>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #1e1e1e" }}>
                  {["Стиль", "Тон", "Канал", "Аккаунт", "Реакции", "Ответы", "Удалён", "Опубликован"].map((h) => (
                    <th
                      key={h}
                      style={{
                        textAlign: "left",
                        padding: "8px 10px",
                        fontSize: 11,
                        color: "#555",
                        fontWeight: 500,
                        fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)",
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {feed.map((item) => (
                  <tr
                    key={item.id}
                    style={{ borderBottom: "1px solid #111", transition: "background 0.1s" }}
                    onMouseEnter={(e) => ((e.currentTarget as HTMLTableRowElement).style.background = "#141414")}
                    onMouseLeave={(e) => ((e.currentTarget as HTMLTableRowElement).style.background = "transparent")}
                  >
                    <td style={{ padding: "10px 10px", fontSize: 12 }}>
                      <span style={{
                        background: "#0d1f0d",
                        color: "#00ff88",
                        padding: "2px 8px",
                        borderRadius: 4,
                        fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)",
                        fontSize: 11,
                      }}>
                        {STYLE_LABELS[item.style_name] ?? item.style_name}
                      </span>
                    </td>
                    <td style={{ padding: "10px 10px", fontSize: 11, color: "#888" }}>{item.tone ?? "—"}</td>
                    <td style={{ padding: "10px 10px", fontSize: 12, color: "#ccc" }}>
                      {item.channel_username ? `@${item.channel_username}` : "—"}
                    </td>
                    <td style={{ padding: "10px 10px", fontSize: 12, color: "#888", fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
                      {item.account_id ?? "—"}
                    </td>
                    <td style={{ padding: "10px 10px", fontSize: 13, color: "#22c55e", fontWeight: 600, fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
                      {item.reactions_count}
                    </td>
                    <td style={{ padding: "10px 10px", fontSize: 12, color: "#aaa", fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
                      {item.replies_count}
                    </td>
                    <td style={{ padding: "10px 10px" }}>
                      {item.was_deleted ? (
                        <span style={{ color: "#ef4444", fontSize: 11 }}>Да</span>
                      ) : (
                        <span style={{ color: "#555", fontSize: 11 }}>Нет</span>
                      )}
                    </td>
                    <td style={{ padding: "10px 10px", fontSize: 11, color: "#555", fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
                      {item.posted_at ? new Date(item.posted_at).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* Pagination */}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
              <button
                onClick={() => void loadFeed(Math.max(0, feedOffset - FEED_LIMIT))}
                disabled={feedOffset === 0}
                style={{
                  background: "#1a1a1a",
                  border: "1px solid #2a2a2a",
                  borderRadius: 6,
                  color: feedOffset === 0 ? "#333" : "#888",
                  padding: "6px 14px",
                  fontSize: 12,
                  cursor: feedOffset === 0 ? "not-allowed" : "pointer",
                }}
              >
                Назад
              </button>
              <span style={{ fontSize: 11, color: "#555", alignSelf: "center", fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
                {feedOffset + 1}–{Math.min(feedOffset + FEED_LIMIT, feedTotal)} из {feedTotal}
              </span>
              <button
                onClick={() => void loadFeed(feedOffset + FEED_LIMIT)}
                disabled={feedOffset + FEED_LIMIT >= feedTotal}
                style={{
                  background: "#1a1a1a",
                  border: "1px solid #2a2a2a",
                  borderRadius: 6,
                  color: feedOffset + FEED_LIMIT >= feedTotal ? "#333" : "#888",
                  padding: "6px 14px",
                  fontSize: 12,
                  cursor: feedOffset + FEED_LIMIT >= feedTotal ? "not-allowed" : "pointer",
                }}
              >
                Вперёд
              </button>
            </div>
          </>
        )}
      </div>

      {/* close dashboard tab fragment */}
      </>}

      {/* ── Custom Styles tab ─────────────────────────────────────────────────── */}
      {activeTab === "custom-styles" && (
        <div>
          {/* Toolbar */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
            <div style={{ fontSize: 13, color: "#888" }}>
              Создавайте собственные стили с системным промптом и примерами.
            </div>
            <button
              onClick={openCreateModal}
              style={{
                background: "#00ff88",
                color: "#000",
                border: "none",
                borderRadius: 8,
                padding: "9px 18px",
                fontWeight: 700,
                fontSize: 13,
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <Plus size={14} />
              Создать стиль
            </button>
          </div>

          {customStylesError && (
            <div style={{ color: "#ef4444", fontSize: 13, marginBottom: 16 }}>{customStylesError}</div>
          )}

          {customStylesLoading ? (
            <div style={{ color: "#555", fontSize: 13, padding: "32px 0", textAlign: "center" }}>Загрузка...</div>
          ) : customStyles.length === 0 ? (
            <div
              style={{
                background: "#111",
                border: "1px dashed #2a2a2a",
                borderRadius: 12,
                padding: "48px 24px",
                textAlign: "center",
                color: "#555",
                fontSize: 13,
              }}
            >
              Нет кастомных стилей. Нажмите «Создать стиль» чтобы добавить первый.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {customStyles.map((style) => (
                <div
                  key={style.id}
                  style={{
                    background: "#111",
                    border: `1px solid ${style.is_active ? "#1e3a1e" : "#1e1e1e"}`,
                    borderRadius: 12,
                    padding: "18px 20px",
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 16,
                    opacity: style.is_active ? 1 : 0.55,
                  }}
                >
                  {/* Name + meta */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                      <span style={{ fontWeight: 700, fontSize: 14, color: "#fff" }}>{style.name}</span>
                      {style.tone && (
                        <span
                          style={{
                            background: "#0d1f0d",
                            color: "#00ff88",
                            padding: "2px 8px",
                            borderRadius: 4,
                            fontSize: 11,
                            fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)",
                          }}
                        >
                          {style.tone}
                        </span>
                      )}
                      {!style.is_active && (
                        <span style={{ fontSize: 11, color: "#555", fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
                          неактивен
                        </span>
                      )}
                    </div>
                    {style.description && (
                      <div style={{ fontSize: 12, color: "#888", marginBottom: 6 }}>{style.description}</div>
                    )}
                    {style.system_prompt && (
                      <div
                        style={{
                          fontSize: 11,
                          color: "#555",
                          background: "#0a0a0b",
                          border: "1px solid #1e1e1e",
                          borderRadius: 6,
                          padding: "6px 10px",
                          fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)",
                          whiteSpace: "pre-wrap",
                          maxHeight: 72,
                          overflow: "hidden",
                          marginBottom: 6,
                        }}
                      >
                        {style.system_prompt.slice(0, 300)}{style.system_prompt.length > 300 ? "…" : ""}
                      </div>
                    )}
                    {style.examples && style.examples.length > 0 && (
                      <div style={{ fontSize: 11, color: "#666", marginTop: 4 }}>
                        Примеры: {style.examples.length} шт.
                      </div>
                    )}
                  </div>

                  {/* Actions */}
                  <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                    <button
                      onClick={() => void handleToggleActive(style)}
                      title={style.is_active ? "Деактивировать" : "Активировать"}
                      style={{
                        background: "transparent",
                        border: "none",
                        cursor: "pointer",
                        color: style.is_active ? "#00ff88" : "#555",
                        padding: 4,
                        display: "flex",
                        alignItems: "center",
                      }}
                    >
                      {style.is_active ? <ToggleRight size={20} /> : <ToggleLeft size={20} />}
                    </button>
                    <button
                      onClick={() => openEditModal(style)}
                      title="Редактировать"
                      style={{
                        background: "#1a1a1a",
                        border: "1px solid #2a2a2a",
                        borderRadius: 6,
                        padding: "5px 8px",
                        color: "#888",
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                      }}
                    >
                      <Edit3 size={13} />
                    </button>
                    <button
                      onClick={() => void handleDeleteStyle(style)}
                      title="Удалить"
                      style={{
                        background: "#1a0a0a",
                        border: "1px solid #3a1a1a",
                        borderRadius: 6,
                        padding: "5px 8px",
                        color: "#ef4444",
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                      }}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Create/Edit Modal ─────────────────────────────────────────────────── */}
      <AnimatePresence>
        {modalOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.75)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 1000,
              padding: 16,
            }}
            onClick={(e) => { if (e.target === e.currentTarget) closeModal(); }}
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              transition={{ duration: 0.15 }}
              style={{
                background: "#0f0f0f",
                border: "1px solid #1e3a1e",
                borderRadius: 14,
                padding: 28,
                width: "100%",
                maxWidth: 560,
                maxHeight: "90vh",
                overflowY: "auto",
              }}
            >
              {/* Modal header */}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
                <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: "#fff" }}>
                  {editingStyle ? "Редактировать стиль" : "Создать стиль"}
                </h3>
                <button
                  onClick={closeModal}
                  style={{ background: "transparent", border: "none", color: "#555", cursor: "pointer", padding: 4 }}
                >
                  <X size={18} />
                </button>
              </div>

              {/* Form fields */}
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                {/* Name */}
                <div>
                  <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 6, fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
                    Название *
                  </label>
                  <input
                    value={form.name}
                    onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                    placeholder="Например: Продающий эксперт"
                    maxLength={100}
                    style={{
                      width: "100%",
                      background: "#0a0a0b",
                      border: "1px solid #2a2a2a",
                      borderRadius: 8,
                      color: "#e0e0e0",
                      padding: "9px 12px",
                      fontSize: 13,
                      boxSizing: "border-box",
                    }}
                  />
                </div>

                {/* Description */}
                <div>
                  <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 6, fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
                    Описание
                  </label>
                  <input
                    value={form.description}
                    onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                    placeholder="Краткое описание стиля"
                    maxLength={500}
                    style={{
                      width: "100%",
                      background: "#0a0a0b",
                      border: "1px solid #2a2a2a",
                      borderRadius: 8,
                      color: "#e0e0e0",
                      padding: "9px 12px",
                      fontSize: 13,
                      boxSizing: "border-box",
                    }}
                  />
                </div>

                {/* Tone */}
                <div>
                  <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 6, fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
                    Тональность
                  </label>
                  <select
                    value={form.tone}
                    onChange={(e) => setForm((f) => ({ ...f, tone: e.target.value }))}
                    style={{
                      width: "100%",
                      background: "#0a0a0b",
                      border: "1px solid #2a2a2a",
                      borderRadius: 8,
                      color: "#e0e0e0",
                      padding: "9px 12px",
                      fontSize: 13,
                      boxSizing: "border-box",
                    }}
                  >
                    <option value="positive">Позитивный</option>
                    <option value="hater">Скептик</option>
                    <option value="emotional">Эмоциональный</option>
                    <option value="expert">Эксперт</option>
                    <option value="witty">Остроумный</option>
                  </select>
                </div>

                {/* System prompt */}
                <div>
                  <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 6, fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
                    Системный промпт (инструкции для AI)
                  </label>
                  <textarea
                    value={form.system_prompt}
                    onChange={(e) => setForm((f) => ({ ...f, system_prompt: e.target.value }))}
                    placeholder="Пишите как эксперт в нише финтех. Используй деловой тон. Не более 20 слов..."
                    rows={5}
                    maxLength={8000}
                    style={{
                      width: "100%",
                      background: "#0a0a0b",
                      border: "1px solid #2a2a2a",
                      borderRadius: 8,
                      color: "#e0e0e0",
                      padding: "9px 12px",
                      fontSize: 12,
                      resize: "vertical",
                      fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)",
                      boxSizing: "border-box",
                    }}
                  />
                </div>

                {/* Examples */}
                <div>
                  <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 6, fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)" }}>
                    Примеры комментариев (каждый с новой строки)
                  </label>
                  <textarea
                    value={form.examples}
                    onChange={(e) => setForm((f) => ({ ...f, examples: e.target.value }))}
                    placeholder={"Интересная статистика! Использую схожий подход уже 3 года.\nА как это влияет на конверсию в B2B?"}
                    rows={4}
                    style={{
                      width: "100%",
                      background: "#0a0a0b",
                      border: "1px solid #2a2a2a",
                      borderRadius: 8,
                      color: "#e0e0e0",
                      padding: "9px 12px",
                      fontSize: 12,
                      resize: "vertical",
                      fontFamily: "inherit",
                      boxSizing: "border-box",
                    }}
                  />
                  <div style={{ fontSize: 10, color: "#444", marginTop: 4 }}>
                    {form.examples.split("\n").filter((s) => s.trim()).length} примеров
                  </div>
                </div>

                {formError && (
                  <div style={{ color: "#ef4444", fontSize: 12 }}>{formError}</div>
                )}

                {/* Actions */}
                <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 4 }}>
                  <button
                    onClick={closeModal}
                    style={{
                      background: "#1a1a1a",
                      border: "1px solid #2a2a2a",
                      borderRadius: 8,
                      padding: "9px 20px",
                      color: "#888",
                      fontSize: 13,
                      cursor: "pointer",
                    }}
                  >
                    Отмена
                  </button>
                  <button
                    onClick={() => void handleSaveStyle()}
                    disabled={formBusy || !form.name.trim()}
                    style={{
                      background: formBusy || !form.name.trim() ? "#1a2a1a" : "#00ff88",
                      color: formBusy || !form.name.trim() ? "#00ff88" : "#000",
                      border: "none",
                      borderRadius: 8,
                      padding: "9px 24px",
                      fontWeight: 700,
                      fontSize: 13,
                      cursor: formBusy || !form.name.trim() ? "not-allowed" : "pointer",
                    }}
                  >
                    {formBusy ? "Сохранение..." : editingStyle ? "Сохранить" : "Создать"}
                  </button>
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
