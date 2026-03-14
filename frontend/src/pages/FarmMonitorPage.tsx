import { useEffect, useState, useCallback } from "react";
import { apiFetch } from "../api";
import { useAuth } from "../auth";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type FarmLiveStats = {
  active_farms_count: number;
  total_threads_running: number;
  comments_today: number;
  bans_today: number;
  avg_health_score: number;
  thread_count: number;
};

type QualityScore = {
  style: string;
  count: number;
  score: number;
  avg_reactions: number;
  deletion_rate: number;
};

type CommentQuality = {
  total_comments: number;
  style_distribution: Record<string, number>;
  quality_scores: QualityScore[];
  avg_reactions: number;
  avg_replies: number;
  deletion_rate: number;
  flagged_count: number;
};

type FarmConfig = {
  id: number;
  name: string;
  status: string;
  max_threads: number;
  comment_tone: string;
  updated_at: string | null;
};

type RecentEvent = {
  id: number;
  farm_id: number;
  thread_id: number | null;
  event_type: string;
  severity: string;
  message: string | null;
  created_at: string | null;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function scoreColor(score: number): string {
  if (score >= 70) return "#00ff88";
  if (score >= 40) return "#eab308";
  return "#ef4444";
}

function severityColor(severity: string): string {
  if (severity === "error") return "#ef4444";
  if (severity === "warn") return "#eab308";
  return "#00ff88";
}

function StatCard({
  label,
  value,
  accent,
  sub,
}: {
  label: string;
  value: string | number;
  accent?: string;
  sub?: string;
}) {
  return (
    <div
      style={{
        background: "#111",
        border: "1px solid #222",
        borderRadius: 8,
        padding: "18px 22px",
        minWidth: 140,
        flex: 1,
      }}
    >
      <div style={{ fontSize: 11, color: "#666", textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>
        {label}
      </div>
      <div style={{ fontSize: 28, fontWeight: 700, color: accent || "#00ff88", fontFamily: "JetBrains Mono, monospace" }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: "#555", marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

function QualityBar({ style, count, score }: { style: string; count: number; score: number }) {
  const color = scoreColor(score);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
      <div style={{ width: 130, fontSize: 12, color: "#ccc", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {style}
      </div>
      <div style={{ flex: 1, height: 8, background: "#222", borderRadius: 4, overflow: "hidden", minWidth: 80 }}>
        <div style={{ width: `${Math.min(100, score)}%`, height: "100%", background: color, borderRadius: 4, transition: "width 0.4s" }} />
      </div>
      <div style={{ width: 36, textAlign: "right", fontSize: 12, color, fontWeight: 600 }}>{score}</div>
      <div style={{ width: 40, textAlign: "right", fontSize: 11, color: "#555" }}>x{count}</div>
    </div>
  );
}

function FarmStatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    running: "#00ff88",
    paused: "#eab308",
    stopped: "#555",
  };
  const color = colors[status] || "#aaa";
  return (
    <span
      style={{
        display: "inline-block",
        background: color + "22",
        color,
        border: `1px solid ${color}44`,
        borderRadius: 4,
        padding: "2px 8px",
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: 0.5,
      }}
    >
      {status.toUpperCase()}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function FarmMonitorPage() {
  const { token } = useAuth();

  const [liveStats, setLiveStats] = useState<FarmLiveStats | null>(null);
  const [quality, setQuality] = useState<CommentQuality | null>(null);
  const [farms, setFarms] = useState<FarmConfig[]>([]);
  const [events, setEvents] = useState<RecentEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchAll = useCallback(async () => {
    if (!token) return;
    try {
      const [statsData, qualityData, farmsData] = await Promise.all([
        apiFetch<FarmLiveStats>("/v1/farm/stats/live", { accessToken: token }),
        apiFetch<CommentQuality>("/v1/farm/comment-quality", { accessToken: token }),
        apiFetch<{ items: FarmConfig[]; total: number }>("/v1/farm", { accessToken: token }),
      ]);
      setLiveStats(statsData);
      setQuality(qualityData);
      setFarms(farmsData.items || []);

      // Fetch events from all active farms (first running one)
      const activeFarm = (farmsData.items || []).find(f => f.status === "running");
      if (activeFarm) {
        const eventsData = await apiFetch<{ items: RecentEvent[]; total: number }>(
          `/v1/farm/${activeFarm.id}/events?limit=20`,
          { accessToken: token }
        );
        setEvents(eventsData.items || []);
      }
      setLastRefresh(new Date());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  // Auto-refresh every 15 seconds
  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      void fetchAll();
    }, 15000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchAll]);

  const PAGE_STYLE: React.CSSProperties = {
    background: "#0a0a0b",
    minHeight: "100vh",
    color: "#e0e0e0",
    fontFamily: "Geist Sans, Inter, sans-serif",
    padding: "28px 24px",
    maxWidth: 1200,
  };

  const SECTION_TITLE: React.CSSProperties = {
    fontSize: 13,
    fontWeight: 600,
    color: "#888",
    textTransform: "uppercase" as const,
    letterSpacing: 1.2,
    marginBottom: 14,
    marginTop: 32,
  };

  if (loading) {
    return (
      <div style={{ ...PAGE_STYLE, display: "flex", alignItems: "center", justifyContent: "center", minHeight: 300 }}>
        <span style={{ color: "#00ff88", fontFamily: "JetBrains Mono, monospace" }}>Загружаем мониторинг фермы…</span>
      </div>
    );
  }

  return (
    <div style={PAGE_STYLE}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0, color: "#fff" }}>
            Мониторинг Фермы
          </h1>
          <div style={{ fontSize: 12, color: "#555", marginTop: 4 }}>
            {lastRefresh
              ? `Обновлено: ${lastRefresh.toLocaleTimeString("ru-RU")}`
              : "Ожидание данных…"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "#888", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={e => setAutoRefresh(e.target.checked)}
              style={{ accentColor: "#00ff88" }}
            />
            Авто-обновление (15с)
          </label>
          <button
            onClick={() => void fetchAll()}
            style={{
              background: "#0a0a0b",
              border: "1px solid #333",
              borderRadius: 6,
              color: "#00ff88",
              padding: "7px 16px",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: 600,
            }}
          >
            Обновить
          </button>
        </div>
      </div>

      {error && (
        <div style={{ background: "#1a0505", border: "1px solid #ef444433", borderRadius: 8, padding: "12px 16px", color: "#ef4444", marginBottom: 20, fontSize: 13 }}>
          {error}
        </div>
      )}

      {/* Live Stats Cards */}
      <div style={SECTION_TITLE}>Real-Time Stats</div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" as const }}>
        <StatCard label="Активных ферм" value={liveStats?.active_farms_count ?? 0} />
        <StatCard label="Потоков запущено" value={liveStats?.total_threads_running ?? 0} />
        <StatCard
          label="Коммент. всего"
          value={(liveStats?.comments_today ?? 0).toLocaleString("ru-RU")}
          sub="за всё время (из потоков)"
        />
        <StatCard
          label="Блокировок (24ч)"
          value={liveStats?.bans_today ?? 0}
          accent={liveStats && liveStats.bans_today > 10 ? "#ef4444" : "#00ff88"}
        />
        <StatCard
          label="Ср. здоровье"
          value={`${liveStats?.avg_health_score ?? 100}%`}
          accent={scoreColor(liveStats?.avg_health_score ?? 100)}
          sub={`из ${liveStats?.thread_count ?? 0} потоков`}
        />
      </div>

      {/* Active Farms List */}
      <div style={SECTION_TITLE}>Активные Фермы</div>
      {farms.length === 0 ? (
        <div style={{ color: "#555", fontSize: 13, padding: "16px 0" }}>Нет настроенных ферм.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column" as const, gap: 8 }}>
          {farms.map(farm => (
            <div
              key={farm.id}
              style={{
                background: "#111",
                border: farm.status === "running" ? "1px solid #00ff8833" : "1px solid #222",
                borderRadius: 8,
                padding: "14px 18px",
                display: "flex",
                alignItems: "center",
                gap: 16,
              }}
            >
              {farm.status === "running" && (
                <span style={{
                  display: "inline-block",
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: "#00ff88",
                  boxShadow: "0 0 8px #00ff88",
                  flexShrink: 0,
                }} />
              )}
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600, fontSize: 14, color: "#fff" }}>{farm.name}</div>
                <div style={{ fontSize: 11, color: "#555", marginTop: 2 }}>
                  Тон: {farm.comment_tone} · Макс потоков: {farm.max_threads}
                </div>
              </div>
              <FarmStatusBadge status={farm.status} />
            </div>
          ))}
        </div>
      )}

      {/* Comment Quality Bar Chart */}
      <div style={SECTION_TITLE}>Качество Комментариев по Стилям</div>
      {!quality || quality.total_comments === 0 ? (
        <div style={{ color: "#555", fontSize: 13, padding: "16px 0" }}>
          Нет данных по комментариям. Запустите ферму, чтобы накопить статистику.
        </div>
      ) : (
        <div style={{ background: "#111", border: "1px solid #222", borderRadius: 8, padding: "20px 22px" }}>
          <div style={{ display: "flex", gap: 24, marginBottom: 20, flexWrap: "wrap" as const }}>
            <div>
              <div style={{ fontSize: 11, color: "#666", marginBottom: 4 }}>Всего комментариев</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: "#00ff88", fontFamily: "JetBrains Mono, monospace" }}>
                {quality.total_comments.toLocaleString("ru-RU")}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: "#666", marginBottom: 4 }}>Ср. реакций</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: "#aaa", fontFamily: "JetBrains Mono, monospace" }}>
                {quality.avg_reactions.toFixed(1)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: "#666", marginBottom: 4 }}>Удалено</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: quality.deletion_rate > 0.1 ? "#ef4444" : "#aaa", fontFamily: "JetBrains Mono, monospace" }}>
                {(quality.deletion_rate * 100).toFixed(1)}%
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: "#666", marginBottom: 4 }}>Флагов</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: quality.flagged_count > 0 ? "#eab308" : "#aaa", fontFamily: "JetBrains Mono, monospace" }}>
                {quality.flagged_count}
              </div>
            </div>
          </div>
          <div style={{ borderTop: "1px solid #222", paddingTop: 16 }}>
            {quality.quality_scores.length === 0 ? (
              <div style={{ color: "#555", fontSize: 13 }}>Нет данных по стилям.</div>
            ) : (
              quality.quality_scores.map(qs => (
                <QualityBar key={qs.style} style={qs.style} count={qs.count} score={qs.score} />
              ))
            )}
          </div>
        </div>
      )}

      {/* Recent Events Timeline */}
      <div style={SECTION_TITLE}>События (последние 20)</div>
      {events.length === 0 ? (
        <div style={{ color: "#555", fontSize: 13, padding: "16px 0" }}>
          Нет событий. Выберите активную ферму для просмотра логов.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column" as const, gap: 6 }}>
          {events.map(event => (
            <div
              key={event.id}
              style={{
                background: "#111",
                border: `1px solid ${severityColor(event.severity)}22`,
                borderLeft: `3px solid ${severityColor(event.severity)}`,
                borderRadius: "0 6px 6px 0",
                padding: "10px 14px",
                display: "flex",
                gap: 14,
                alignItems: "flex-start",
              }}
            >
              <div style={{ fontSize: 11, color: "#555", minWidth: 80, flexShrink: 0, fontFamily: "JetBrains Mono, monospace" }}>
                {event.created_at ? new Date(event.created_at).toLocaleTimeString("ru-RU") : "—"}
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: severityColor(event.severity), marginBottom: 2 }}>
                  {event.event_type}
                  {event.thread_id && (
                    <span style={{ color: "#555", fontWeight: 400 }}> · поток #{event.thread_id}</span>
                  )}
                </div>
                {event.message && (
                  <div style={{ fontSize: 12, color: "#888" }}>{event.message}</div>
                )}
              </div>
              <span
                style={{
                  fontSize: 10,
                  color: severityColor(event.severity),
                  background: severityColor(event.severity) + "22",
                  borderRadius: 3,
                  padding: "2px 6px",
                  flexShrink: 0,
                }}
              >
                {event.severity.toUpperCase()}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
