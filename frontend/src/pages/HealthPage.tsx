import { useEffect, useState } from "react";
import { healthApi, quarantineApi, AccountHealthScore, QuarantinedAccount } from "../api";
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
        <div className="modal-overlay" onClick={() => setSelectedScore(null)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div>
                <div className="eyebrow">Детальный анализ</div>
                <h2>{selectedScore.account_phone ?? `Аккаунт #${selectedScore.account_id}`}</h2>
              </div>
              <button className="ghost-button" type="button" onClick={() => setSelectedScore(null)}>
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
