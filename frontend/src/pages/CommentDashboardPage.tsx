import { useEffect, useState } from "react";
import { motion } from "framer-motion";
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
    void Promise.all([loadStats(), loadFeed(0), loadStyles()]).catch(() => {});
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
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
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
          onClick={() => void Promise.all([loadStats(), loadFeed(feedOffset), loadStyles()])}
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
    </motion.div>
  );
}
