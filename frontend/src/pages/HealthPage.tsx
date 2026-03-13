import { useEffect, useState } from "react";
import { healthApi, quarantineApi, AccountHealthScore, QuarantinedAccount, HealthHistoryPoint } from "../api";
import { useAuth } from "../auth";

type SortKey = "health_score" | "survivability_score";

function ScoreBar({ score, label }: { score: number; label?: string }) {
  const color = score >= 70 ? "#22c55e" : score >= 40 ? "#eab308" : "#ef4444";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ flex: 1, height: 8, background: "#2d2d2d", borderRadius: 4, overflow: "hidden", minWidth: 80 }}>
        <div style={{ width: `${Math.min(100, Math.max(0, score))}%`, height: "100%", background: color, borderRadius: 4 }} />
      </div>
      <span style={{ fontSize: 12, color: "#aaa", minWidth: 32, textAlign: "right" }}>
        {label ?? score}
      </span>
    </div>
  );
}

function ScoreLabel({ score }: { score: number }) {
  const color = score >= 70 ? "#22c55e" : score >= 40 ? "#eab308" : "#ef4444";
  const text = score >= 70 ? "Хорошее" : score >= 40 ? "Среднее" : "Критично";
  return <span style={{ color, fontSize: 12, fontWeight: 600 }}>{text}</span>;
}

function scoreColor(score: number): string {
  return score >= 70 ? "#22c55e" : score >= 40 ? "#eab308" : "#ef4444";
}

function HealthMiniChart({ points }: { points: HealthHistoryPoint[] }) {
  if (points.length === 0) {
    return (
      <div style={{ padding: "20px 0", textAlign: "center", color: "#666", fontSize: 12 }}>
        Нет данных за выбранный период
      </div>
    );
  }

  const W = 320;
  const H = 80;
  const PAD = { top: 8, right: 8, bottom: 20, left: 28 };
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;

  const maxScore = 100;
  const minScore = 0;

  const toX = (i: number) =>
    PAD.left + (points.length > 1 ? (i / (points.length - 1)) * innerW : innerW / 2);
  const toY = (v: number) =>
    PAD.top + innerH - ((v - minScore) / (maxScore - minScore)) * innerH;

  const healthPts = points.map((p, i) => `${toX(i)},${toY(p.score)}`).join(" ");
  const survPts = points.map((p, i) => `${toX(i)},${toY(p.survivability)}`).join(" ");

  // Y-axis labels
  const yTicks = [0, 40, 70, 100];

  // X-axis: show first and last dates
  const firstDate = points[0].date.slice(5); // MM-DD
  const lastDate = points[points.length - 1].date.slice(5);

  return (
    <div>
      <svg width={W} height={H} style={{ display: "block", overflow: "visible" }}>
        {/* Grid lines */}
        {yTicks.map((tick) => (
          <g key={tick}>
            <line
              x1={PAD.left}
              x2={PAD.left + innerW}
              y1={toY(tick)}
              y2={toY(tick)}
              stroke="#2d2d2d"
              strokeWidth={1}
            />
            <text
              x={PAD.left - 4}
              y={toY(tick) + 4}
              textAnchor="end"
              fontSize={9}
              fill="#555"
            >
              {tick}
            </text>
          </g>
        ))}

        {/* Health score polyline (green/yellow/red segments) */}
        {points.length > 1 ? (
          points.slice(1).map((p, i) => {
            const x1 = toX(i);
            const y1 = toY(points[i].score);
            const x2 = toX(i + 1);
            const y2 = toY(p.score);
            const avgScore = (points[i].score + p.score) / 2;
            return (
              <line
                key={i}
                x1={x1}
                y1={y1}
                x2={x2}
                y2={y2}
                stroke={scoreColor(avgScore)}
                strokeWidth={2}
                strokeLinecap="round"
              />
            );
          })
        ) : (
          <polyline
            points={healthPts}
            fill="none"
            stroke={scoreColor(points[0].score)}
            strokeWidth={2}
          />
        )}

        {/* Survivability polyline (dashed blue) */}
        <polyline
          points={survPts}
          fill="none"
          stroke="#4488ff"
          strokeWidth={1.5}
          strokeDasharray="3,3"
          opacity={0.7}
        />

        {/* Data points for health */}
        {points.map((p, i) => (
          <circle
            key={i}
            cx={toX(i)}
            cy={toY(p.score)}
            r={3}
            fill={scoreColor(p.score)}
          />
        ))}

        {/* X-axis labels */}
        <text x={PAD.left} y={H - 2} fontSize={9} fill="#555" textAnchor="middle">
          {firstDate}
        </text>
        {points.length > 1 ? (
          <text x={PAD.left + innerW} y={H - 2} fontSize={9} fill="#555" textAnchor="middle">
            {lastDate}
          </text>
        ) : null}
      </svg>
      <div style={{ display: "flex", gap: 16, marginTop: 6, fontSize: 11, color: "#888" }}>
        <span>
          <span style={{ display: "inline-block", width: 16, height: 2, background: "#22c55e", verticalAlign: "middle", marginRight: 4 }} />
          Health score
        </span>
        <span>
          <span style={{ display: "inline-block", width: 16, height: 2, background: "#4488ff", verticalAlign: "middle", marginRight: 4, borderTop: "1.5px dashed #4488ff" }} />
          Survivability
        </span>
      </div>
    </div>
  );
}

const FACTOR_LABELS: Record<string, string> = {
  flood_wait_penalty: "Штраф flood_wait",
  spam_block_penalty: "Штраф спам-блок",
  successful_actions_bonus: "Бонус успешных действий",
  account_age_bonus: "Бонус возраста аккаунта",
  quarantine_penalty: "Штраф карантина",
  proxy_stability_bonus: "Бонус стабильности прокси",
};

export function HealthPage() {
  const { accessToken } = useAuth();

  const [scores, setScores] = useState<AccountHealthScore[]>([]);
  const [quarantined, setQuarantined] = useState<QuarantinedAccount[]>([]);
  const [selectedScore, setSelectedScore] = useState<AccountHealthScore | null>(null);
  const [historyPoints, setHistoryPoints] = useState<HealthHistoryPoint[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("health_score");
  const [sortAsc, setSortAsc] = useState(false);

  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  const loadScores = async () => {
    if (!accessToken) return;
    const payload = await healthApi.listScores(accessToken);
    setScores(payload.items);
  };

  const loadQuarantined = async () => {
    if (!accessToken) return;
    const payload = await quarantineApi.list(accessToken);
    setQuarantined(payload.items);
  };

  useEffect(() => {
    void Promise.all([loadScores(), loadQuarantined()]).catch(() => {});
  }, [accessToken]);

  const handleRecalculateAll = async () => {
    if (!accessToken) return;
    setBusy(true);
    setStatusMessage("");
    try {
      await healthApi.recalculate(accessToken);
      setStatusMessage("Пересчёт запущен. Обновите страницу через несколько секунд.");
      await loadScores();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "recalculate_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleLiftQuarantine = async (accountId: number) => {
    if (!accessToken) return;
    setBusy(true);
    setStatusMessage("");
    try {
      await quarantineApi.liftQuarantine(accessToken, accountId);
      setStatusMessage("Карантин снят.");
      await loadQuarantined();
      await loadScores();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "lift_quarantine_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleGetDetail = async (accountId: number) => {
    if (!accessToken) return;
    try {
      const detail = await healthApi.getScore(accessToken, accountId);
      setSelectedScore(detail);
      setHistoryPoints([]);
      setHistoryLoading(true);
      try {
        const hist = await healthApi.getHistory(accessToken, accountId, 30);
        setHistoryPoints(hist.items);
      } catch {
        // history is best-effort; silently ignore
      } finally {
        setHistoryLoading(false);
      }
    } catch {
      setStatusMessage("Не удалось загрузить детали аккаунта.");
    }
  };

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc((prev) => !prev);
    } else {
      setSortKey(key);
      setSortAsc(false);
    }
  };

  const sortedScores = [...scores].sort((a, b) => {
    const aVal = a[sortKey] ?? 0;
    const bVal = b[sortKey] ?? 0;
    return sortAsc ? aVal - bVal : bVal - aVal;
  });

  const avgHealth =
    scores.length > 0
      ? Math.round(scores.reduce((sum, s) => sum + (s.health_score ?? 0), 0) / scores.length)
      : 0;

  const criticalCount = scores.filter((s) => (s.health_score ?? 0) < 40).length;
  const goodCount = scores.filter((s) => (s.health_score ?? 0) >= 70).length;

  return (
    <div className="page-grid">
      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Health Monitor</div>
              <h2>Здоровье аккаунтов</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Health score отражает готовность аккаунта к активной работе без риска блокировки.</li>
            <li>Survivability score показывает вероятность выживания аккаунта в ближайшую неделю.</li>
            <li>Аккаунты с health score ниже 40 рекомендуется временно отключить от фарминга.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Сводка</div>
              <h2>Общее состояние фермы</h2>
            </div>
          </div>
          <div className="status-grid">
            <div className="info-block">
              <strong>Аккаунтов отслеживается</strong>
              <span>{scores.length}</span>
            </div>
            <div className="info-block">
              <strong>Средний health</strong>
              <span>{avgHealth}</span>
            </div>
            <div className="info-block">
              <strong>Здоровых (&gt;70)</strong>
              <span>{goodCount}</span>
            </div>
            <div className="info-block">
              <strong>Критичных (&lt;40)</strong>
              <span style={{ color: criticalCount > 0 ? "#ef4444" : "inherit" }}>{criticalCount}</span>
            </div>
          </div>
        </article>
      </section>

      {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

      {/* Health dashboard */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Показатели здоровья</div>
            <h2>Все аккаунты</h2>
          </div>
          <div className="badge-row">
            <button
              className="ghost-button"
              type="button"
              disabled={busy}
              onClick={() => void handleRecalculateAll()}
            >
              {busy ? "Пересчёт…" : "Пересчитать все"}
            </button>
          </div>
        </div>
        {sortedScores.length ? (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Аккаунт</th>
                  <th
                    style={{ cursor: "pointer", userSelect: "none" }}
                    onClick={() => handleSort("health_score")}
                  >
                    Health score {sortKey === "health_score" ? (sortAsc ? "↑" : "↓") : ""}
                  </th>
                  <th
                    style={{ cursor: "pointer", userSelect: "none" }}
                    onClick={() => handleSort("survivability_score")}
                  >
                    Survivability {sortKey === "survivability_score" ? (sortAsc ? "↑" : "↓") : ""}
                  </th>
                  <th>Flood wait</th>
                  <th>Spam blocks</th>
                  <th>Успешных действий</th>
                  <th>Статус</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {sortedScores.map((s) => (
                  <tr
                    key={s.account_id}
                    style={{ cursor: "pointer" }}
                    onClick={() => void handleGetDetail(s.account_id)}
                  >
                    <td>{s.account_phone ?? `#${s.account_id}`}</td>
                    <td style={{ minWidth: 140 }}>
                      <ScoreBar score={s.health_score ?? 0} />
                    </td>
                    <td style={{ minWidth: 140 }}>
                      <ScoreBar score={s.survivability_score ?? 0} />
                    </td>
                    <td>{s.flood_wait_count}</td>
                    <td>{s.spam_block_count}</td>
                    <td>{s.successful_actions}</td>
                    <td>
                      <ScoreLabel score={s.health_score ?? 0} />
                    </td>
                    <td>
                      <button
                        className="ghost-button"
                        type="button"
                        style={{ fontSize: 11, padding: "2px 8px" }}
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleGetDetail(s.account_id);
                        }}
                      >
                        Детали
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">
            Нет данных о здоровье аккаунтов. Запустите прогрев или фарм для накопления метрик.
          </p>
        )}
      </section>

      {/* Quarantined accounts */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Карантин</div>
            <h2>Аккаунты на карантине</h2>
          </div>
          <div className="badge-row">
            <span className="pill" style={{ background: quarantined.length > 0 ? "#ef4444" : undefined }}>
              {quarantined.length} на карантине
            </span>
          </div>
        </div>
        {quarantined.length ? (
          <div className="creative-list">
            {quarantined.map((q) => (
              <div key={q.account_id} className="creative-item">
                <div className="thread-meta">
                  <strong>{q.account_phone ?? `Аккаунт #${q.account_id}`}</strong>
                  <span className="pill badge-red">На карантине</span>
                  <span className="muted">Причина: {q.quarantine_reason ?? "не указана"}</span>
                  {q.quarantine_until ? (
                    <span className="muted">До: {q.quarantine_until}</span>
                  ) : null}
                </div>
                <div className="actions-row" style={{ marginTop: 8 }}>
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={busy}
                    onClick={() => void handleLiftQuarantine(q.account_id)}
                  >
                    Снять карантин
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">Нет аккаунтов на карантине. Хороший знак.</p>
        )}
      </section>

      {/* Account detail drawer */}
      {selectedScore ? (
        <div className="modal-overlay" onClick={() => { setSelectedScore(null); setHistoryPoints([]); }}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div>
                <div className="eyebrow">Детальный анализ</div>
                <h2>{selectedScore.account_phone ?? `Аккаунт #${selectedScore.account_id}`}</h2>
              </div>
              <button className="ghost-button" type="button" onClick={() => { setSelectedScore(null); setHistoryPoints([]); }}>
                Закрыть
              </button>
            </div>

            <div className="stack-form">
              {/* Summary scores */}
              <div className="two-column-grid" style={{ gap: 12 }}>
                <div className="panel" style={{ padding: 16 }}>
                  <div className="eyebrow">Health score</div>
                  <div style={{ fontSize: 32, fontWeight: 700, color: (selectedScore.health_score ?? 0) >= 70 ? "#22c55e" : (selectedScore.health_score ?? 0) >= 40 ? "#eab308" : "#ef4444" }}>
                    {selectedScore.health_score ?? 0}
                  </div>
                  <ScoreBar score={selectedScore.health_score ?? 0} />
                </div>
                <div className="panel" style={{ padding: 16 }}>
                  <div className="eyebrow">Survivability</div>
                  <div style={{ fontSize: 32, fontWeight: 700, color: (selectedScore.survivability_score ?? 0) >= 70 ? "#22c55e" : (selectedScore.survivability_score ?? 0) >= 40 ? "#eab308" : "#ef4444" }}>
                    {selectedScore.survivability_score ?? 0}
                  </div>
                  <ScoreBar score={selectedScore.survivability_score ?? 0} />
                </div>
              </div>

              {/* Raw metrics */}
              <div className="field-list">
                <div className="field-row">
                  <strong>Flood wait события</strong>
                  <span className="field-value">{selectedScore.flood_wait_count}</span>
                </div>
                <div className="field-row">
                  <strong>Spam-блоки</strong>
                  <span className="field-value">{selectedScore.spam_block_count}</span>
                </div>
                <div className="field-row">
                  <strong>Успешных действий</strong>
                  <span className="field-value">{selectedScore.successful_actions}</span>
                </div>
                <div className="field-row">
                  <strong>Последнее обновление</strong>
                  <span className="field-value">{selectedScore.calculated_at ?? "—"}</span>
                </div>
              </div>

              {/* Factor breakdown */}
              {selectedScore.factors && Object.keys(selectedScore.factors).length > 0 ? (
                <div>
                  <div className="eyebrow" style={{ marginBottom: 10 }}>Вклад факторов</div>
                  <div className="field-list">
                    {Object.entries(selectedScore.factors).map(([key, value]) => (
                      <div key={key} className="field-row" style={{ alignItems: "center" }}>
                        <span style={{ fontSize: 12 }}>{FACTOR_LABELS[key] ?? key}</span>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, maxWidth: 220 }}>
                          <div style={{ flex: 1, height: 6, background: "#2d2d2d", borderRadius: 3, overflow: "hidden" }}>
                            <div
                              style={{
                                width: `${Math.min(100, Math.max(0, Math.abs(Number(value))))}%`,
                                height: "100%",
                                background: Number(value) >= 0 ? "#22c55e" : "#ef4444",
                                borderRadius: 3,
                              }}
                            />
                          </div>
                          <span style={{ fontSize: 11, color: Number(value) >= 0 ? "#22c55e" : "#ef4444", minWidth: 36, textAlign: "right" }}>
                            {Number(value) >= 0 ? "+" : ""}{String(value)}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {/* Health history chart */}
              <div>
                <div className="eyebrow" style={{ marginBottom: 10 }}>История здоровья (30 дней)</div>
                {historyLoading ? (
                  <div style={{ color: "#666", fontSize: 12 }}>Загрузка...</div>
                ) : (
                  <HealthMiniChart points={historyPoints} />
                )}
              </div>

              {/* Event history */}
              {selectedScore.recent_events && selectedScore.recent_events.length > 0 ? (
                <div>
                  <div className="eyebrow" style={{ marginBottom: 10 }}>История событий</div>
                  <div
                    className="thread-list"
                    style={{ maxHeight: 200, overflowY: "auto", fontFamily: "monospace", fontSize: 11 }}
                  >
                    {selectedScore.recent_events.map((ev, i) => (
                      <div key={i} className="thread-item">
                        <div className="thread-meta">
                          <span
                            className={`pill ${ev.severity === "error" ? "badge-red" : ev.severity === "warn" ? "badge-yellow" : "badge-gray"}`}
                          >
                            {String(ev.severity ?? "info").toUpperCase()}
                          </span>
                          <span>{String(ev.event_type ?? "")}</span>
                          <span className="muted">{String(ev.created_at ?? "—")}</span>
                        </div>
                        {ev.message ? <p style={{ margin: 0 }}>{String(ev.message)}</p> : null}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
