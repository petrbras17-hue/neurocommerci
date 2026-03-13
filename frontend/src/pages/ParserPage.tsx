import { useEffect, useRef, useState } from "react";
import { apiFetch, channelDbApi, parserApi, ChannelDatabase, ChannelEntry, ParsingJob } from "../api";
import { useAuth } from "../auth";

type AccountRow = {
  id: number;
  phone: string;
  health_status: string;
};

type AccountsResponse = {
  items: AccountRow[];
  total: number;
};

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

const JOB_STATUS_LABELS: Record<string, string> = {
  pending: "Ожидание",
  running: "Запущено",
  completed: "Завершено",
  failed: "Ошибка",
  cancelled: "Отменено",
};

function statusBadgeClass(st: string): string {
  if (st === "completed") return "badge-green";
  if (st === "running") return "badge-green badge-pulse";
  if (st === "failed") return "badge-red";
  if (st === "cancelled") return "badge-yellow";
  return "badge-gray";
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

type ProgressBarProps = {
  progress: number;
  resultsCount: number;
};

function JobProgressBar({ progress, resultsCount }: ProgressBarProps) {
  return (
    <div style={{ marginTop: 6 }}>
      <div
        style={{
          background: "var(--border-color, #1e1e20)",
          borderRadius: 4,
          height: 6,
          overflow: "hidden",
          width: "100%",
        }}
      >
        <div
          style={{
            background: "#00ff88",
            height: "100%",
            width: `${Math.min(100, progress)}%`,
            transition: "width 0.4s ease",
          }}
        />
      </div>
      <span className="muted" style={{ fontSize: 11, marginTop: 2, display: "block" }}>
        {progress}% — Найдено: {resultsCount} каналов
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Keyword tag input
// ---------------------------------------------------------------------------

type KeywordTagsProps = {
  keywords: string[];
  onChange: (kws: string[]) => void;
};

function KeywordTags({ keywords, onChange }: KeywordTagsProps) {
  const [batchText, setBatchText] = useState("");
  const [inputValue, setInputValue] = useState("");

  const addKeyword = (raw: string) => {
    const trimmed = raw.trim();
    if (trimmed && !keywords.includes(trimmed)) {
      onChange([...keywords, trimmed]);
    }
  };

  const handleInputKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addKeyword(inputValue);
      setInputValue("");
    } else if (e.key === "Backspace" && !inputValue && keywords.length > 0) {
      onChange(keywords.slice(0, -1));
    }
  };

  const applyBatch = () => {
    const lines = batchText
      .split(/[\n,]+/)
      .map((l) => l.trim())
      .filter(Boolean);
    const unique = Array.from(new Set([...keywords, ...lines]));
    onChange(unique);
    setBatchText("");
  };

  return (
    <div>
      {/* Tag display */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          padding: "8px 10px",
          background: "var(--input-bg, #111113)",
          border: "1px solid var(--border-color, #1e1e20)",
          borderRadius: 6,
          minHeight: 40,
          cursor: "text",
        }}
        onClick={() => document.getElementById("kw-input")?.focus()}
      >
        {keywords.map((kw) => (
          <span
            key={kw}
            style={{
              background: "#00ff8822",
              color: "#00ff88",
              border: "1px solid #00ff8855",
              borderRadius: 4,
              padding: "2px 8px",
              fontSize: 12,
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            {kw}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onChange(keywords.filter((k) => k !== kw));
              }}
              style={{
                background: "none",
                border: "none",
                color: "#00ff88",
                cursor: "pointer",
                padding: 0,
                fontSize: 14,
                lineHeight: 1,
              }}
            >
              ×
            </button>
          </span>
        ))}
        <input
          id="kw-input"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleInputKey}
          onBlur={() => {
            if (inputValue.trim()) {
              addKeyword(inputValue);
              setInputValue("");
            }
          }}
          placeholder={keywords.length ? "" : "маркетинг, продажи…"}
          style={{
            background: "none",
            border: "none",
            outline: "none",
            color: "inherit",
            fontSize: 13,
            flex: 1,
            minWidth: 120,
          }}
        />
      </div>

      {/* Batch textarea */}
      <details style={{ marginTop: 8 }}>
        <summary
          className="muted"
          style={{ cursor: "pointer", fontSize: 12, userSelect: "none" }}
        >
          Массовый импорт ключевых слов (одно на строку)
        </summary>
        <div style={{ marginTop: 8 }}>
          <textarea
            className="assistant-textarea"
            value={batchText}
            onChange={(e) => setBatchText(e.target.value)}
            placeholder={"маркетинг\nпродажи\nнедвижимость\nIT-стартапы"}
            rows={5}
            style={{ width: "100%", boxSizing: "border-box" }}
          />
          <button
            type="button"
            className="secondary-button"
            style={{ marginTop: 6 }}
            onClick={applyBatch}
            disabled={!batchText.trim()}
          >
            Добавить ключевые слова
          </button>
        </div>
      </details>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function ParserPage() {
  const { accessToken } = useAuth();

  const [databases, setDatabases] = useState<ChannelDatabase[]>([]);
  const [selectedDb, setSelectedDb] = useState<ChannelDatabase | null>(null);
  const [dbChannels, setDbChannels] = useState<ChannelEntry[]>([]);
  const [parsingJobs, setParsingJobs] = useState<ParsingJob[]>([]);
  const [accounts, setAccounts] = useState<AccountRow[]>([]);

  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  // Create DB modal
  const [showCreateDbModal, setShowCreateDbModal] = useState(false);
  const [newDbName, setNewDbName] = useState("");

  // Import channels state
  const [importText, setImportText] = useState("");

  // Parser state — keywords now as array (tags)
  const [parserKeywords, setParserKeywords] = useState<string[]>([]);
  const [parserMinMembers, setParserMinMembers] = useState(0);
  const [parserLanguage, setParserLanguage] = useState("");
  const [parserActiveOnly, setParserActiveOnly] = useState(false);
  const [parserMaxResults, setParserMaxResults] = useState(50);
  const [parserAccountId, setParserAccountId] = useState<number | null>(null);
  const [parserTargetDbId, setParserTargetDbId] = useState<number | null>(null);

  // Polling ref — maps job_id → interval handle
  const pollingRefs = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map());

  const loadDatabases = async () => {
    if (!accessToken) return;
    const payload = await channelDbApi.list(accessToken);
    setDatabases(payload.items);
  };

  const loadDbDetail = async (dbId: number) => {
    if (!accessToken) return;
    const payload = await channelDbApi.listChannels(accessToken, dbId);
    setDbChannels(payload.items);
  };

  const loadJobs = async () => {
    if (!accessToken) return;
    const payload = await parserApi.listJobs(accessToken);
    setParsingJobs(payload.items);
    return payload.items;
  };

  const loadAccounts = async () => {
    if (!accessToken) return;
    const payload = await apiFetch<AccountsResponse>("/v1/web/accounts", { accessToken });
    setAccounts(payload.items);
  };

  // Poll a single job and update it in the state list.
  const pollJob = async (jobId: number) => {
    if (!accessToken) return;
    try {
      const updated = await parserApi.getJob(accessToken, jobId);
      setParsingJobs((prev) =>
        prev.map((j) => (j.id === jobId ? updated : j))
      );
      // Stop polling when terminal state.
      if (["completed", "failed", "cancelled"].includes(updated.status)) {
        stopPolling(jobId);
        if (updated.status === "completed" && selectedDb) {
          void loadDbDetail(selectedDb.id).catch(() => {});
        }
      }
    } catch {
      stopPolling(jobId);
    }
  };

  const startPolling = (jobId: number) => {
    if (pollingRefs.current.has(jobId)) return;
    const handle = setInterval(() => {
      void pollJob(jobId);
    }, 3000);
    pollingRefs.current.set(jobId, handle);
  };

  const stopPolling = (jobId: number) => {
    const handle = pollingRefs.current.get(jobId);
    if (handle !== undefined) {
      clearInterval(handle);
      pollingRefs.current.delete(jobId);
    }
  };

  // On mount: load everything and start polling active jobs.
  useEffect(() => {
    void Promise.all([loadDatabases(), loadJobs(), loadAccounts()])
      .then(([, jobs]) => {
        if (Array.isArray(jobs)) {
          jobs
            .filter((j) => j.status === "running" || j.status === "pending")
            .forEach((j) => startPolling(j.id));
        }
      })
      .catch(() => {});

    return () => {
      // Clean up all polling intervals on unmount.
      pollingRefs.current.forEach((handle) => clearInterval(handle));
      pollingRefs.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

  useEffect(() => {
    if (selectedDb) {
      void loadDbDetail(selectedDb.id).catch(() => setDbChannels([]));
    } else {
      setDbChannels([]);
    }
  }, [selectedDb?.id]);

  const handleCreateDb = async () => {
    if (!accessToken || !newDbName.trim()) {
      setStatusMessage("Введите название базы.");
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      const db = await channelDbApi.create(accessToken, newDbName.trim());
      setShowCreateDbModal(false);
      setNewDbName("");
      setStatusMessage(`База «${db.name}» создана.`);
      await loadDatabases();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "create_db_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleImportChannels = async () => {
    if (!accessToken || !selectedDb) {
      setStatusMessage("Выберите базу каналов.");
      return;
    }
    const links = importText
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
    if (!links.length) {
      setStatusMessage("Введите ссылки на каналы (по одной на строку).");
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      const result = await channelDbApi.importChannels(accessToken, selectedDb.id, links);
      setImportText("");
      setStatusMessage(`Импортировано: ${result.imported}, пропущено: ${result.skipped}.`);
      await loadDbDetail(selectedDb.id);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "import_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleBlacklist = async (channelId: number, current: boolean) => {
    if (!accessToken || !selectedDb) return;
    try {
      await channelDbApi.blacklistChannel(accessToken, selectedDb.id, channelId, !current);
      await loadDbDetail(selectedDb.id);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "blacklist_failed");
    }
  };

  const handleDeleteChannel = async (channelId: number) => {
    if (!accessToken || !selectedDb) return;
    try {
      await channelDbApi.deleteChannel(accessToken, selectedDb.id, channelId);
      await loadDbDetail(selectedDb.id);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "delete_channel_failed");
    }
  };

  const handleStartParsing = async () => {
    if (!accessToken) return;
    if (!parserKeywords.length) {
      setStatusMessage("Введите хотя бы одно ключевое слово.");
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      const filters: Record<string, unknown> = {};
      if (parserMinMembers > 0) filters.min_members = parserMinMembers;
      if (parserLanguage) filters.language = parserLanguage;
      if (parserActiveOnly) filters.active_only = true;

      const created = await parserApi.startChannelParsing(accessToken, {
        keywords: parserKeywords,
        filters,
        max_results: parserMaxResults,
        account_id: parserAccountId,
        target_database_id: parserTargetDbId,
      });
      setStatusMessage("Задача парсинга запущена.");
      await loadJobs();
      // Start polling the new job immediately.
      if (created && typeof (created as { job_id?: number }).job_id === "number") {
        startPolling((created as { job_id: number }).job_id);
      }
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "parsing_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleCancelJob = async (jobId: number) => {
    if (!accessToken) return;
    try {
      await parserApi.cancelJob(accessToken, jobId);
      setStatusMessage(`Задача #${jobId} отменена.`);
      stopPolling(jobId);
      setParsingJobs((prev) =>
        prev.map((j) =>
          j.id === jobId ? { ...j, status: "cancelled" } : j
        )
      );
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "cancel_failed");
    }
  };

  return (
    <div className="page-grid">
      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Channel Parser</div>
              <h2>Парсер каналов</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Ищет Telegram-каналы по ключевым словам и фильтрам.</li>
            <li>Проверяет наличие комментариев, язык, активность и количество участников.</li>
            <li>Сохраняет результаты в базу каналов для последующего использования фермой.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Статус</div>
              <h2>Задачи парсинга</h2>
            </div>
          </div>
          <div className="status-grid">
            <div className="info-block">
              <strong>Баз каналов</strong>
              <span>{databases.length}</span>
            </div>
            <div className="info-block">
              <strong>Задач всего</strong>
              <span>{parsingJobs.length}</span>
            </div>
            <div className="info-block">
              <strong>Активных</strong>
              <span>{parsingJobs.filter((j) => j.status === "running").length}</span>
            </div>
            <div className="info-block">
              <strong>Завершено</strong>
              <span>{parsingJobs.filter((j) => j.status === "completed").length}</span>
            </div>
          </div>
        </article>
      </section>

      {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

      {/* Channel databases list */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Базы каналов</div>
            <h2>Управление базами</h2>
          </div>
          <div className="badge-row">
            <button className="primary-button" type="button" onClick={() => setShowCreateDbModal(true)}>
              + Создать базу
            </button>
          </div>
        </div>
        {databases.length ? (
          <div className="creative-list">
            {databases.map((db) => (
              <div
                key={db.id}
                className={`creative-item ${selectedDb?.id === db.id ? "selected" : ""}`}
                style={{ cursor: "pointer" }}
                onClick={() => setSelectedDb(db)}
              >
                <div className="thread-meta">
                  <strong>{db.name}</strong>
                  <span className="pill badge-gray">{db.source}</span>
                  <span className="muted">Создана: {db.created_at ?? "—"}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">Нет баз каналов. Создайте первую базу для хранения каналов.</p>
        )}
      </section>

      {/* DB detail */}
      {selectedDb ? (
        <section className="panel wide">
          <div className="panel-header">
            <div>
              <div className="eyebrow">База каналов</div>
              <h2>{selectedDb.name} — каналы</h2>
            </div>
            <div className="badge-row">
              <span className="pill">{dbChannels.length} каналов</span>
            </div>
          </div>

          {/* Import channels */}
          <div className="stack-form" style={{ marginBottom: 20 }}>
            <label className="field">
              <span>Импорт каналов (одна ссылка или username на строку)</span>
              <textarea
                className="assistant-textarea"
                value={importText}
                onChange={(e) => setImportText(e.target.value)}
                placeholder="@channel_username&#10;https://t.me/channel&#10;t.me/another_channel"
                rows={4}
              />
            </label>
            <button
              className="secondary-button"
              type="button"
              disabled={busy || !importText.trim()}
              onClick={() => void handleImportChannels()}
            >
              {busy ? "Импортируем…" : "Импортировать каналы"}
            </button>
          </div>

          {dbChannels.length ? (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Username</th>
                    <th>Название</th>
                    <th>Участников</th>
                    <th>Комментарии</th>
                    <th>Язык</th>
                    <th>Успешность</th>
                    <th>Blacklist</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {dbChannels.map((ch) => (
                    <tr key={ch.id} style={{ opacity: ch.blacklisted ? 0.5 : 1 }}>
                      <td>{ch.username ?? "—"}</td>
                      <td>{ch.title ?? "—"}</td>
                      <td>{ch.member_count?.toLocaleString() ?? "—"}</td>
                      <td>
                        <span className={`pill ${ch.has_comments ? "badge-green" : "badge-gray"}`}>
                          {ch.has_comments ? "Да" : "Нет"}
                        </span>
                      </td>
                      <td>{ch.language ?? "—"}</td>
                      <td>{ch.success_rate != null ? `${Math.round(ch.success_rate * 100)}%` : "—"}</td>
                      <td>
                        <button
                          className="ghost-button"
                          type="button"
                          onClick={() => void handleBlacklist(ch.id, ch.blacklisted)}
                        >
                          {ch.blacklisted ? "Снять" : "Заблокировать"}
                        </button>
                      </td>
                      <td>
                        <button
                          className="ghost-button"
                          type="button"
                          onClick={() => void handleDeleteChannel(ch.id)}
                        >
                          Удалить
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">Нет каналов в этой базе. Импортируйте каналы вручную или запустите парсинг.</p>
          )}
        </section>
      ) : null}

      {/* Parser section */}
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Запуск парсинга</div>
            <h2>Поиск каналов</h2>
          </div>
        </div>
        <div className="stack-form">
          <label className="field">
            <span>Ключевые слова</span>
            <KeywordTags keywords={parserKeywords} onChange={setParserKeywords} />
          </label>
          <div className="two-column-grid" style={{ gap: 12 }}>
            <label className="field">
              <span>Минимум участников</span>
              <input
                type="number"
                min={0}
                value={parserMinMembers}
                onChange={(e) => setParserMinMembers(Number(e.target.value))}
              />
            </label>
            <label className="field">
              <span>Язык каналов</span>
              <input
                value={parserLanguage}
                onChange={(e) => setParserLanguage(e.target.value)}
                placeholder="ru, en, uk..."
              />
            </label>
          </div>
          <div className="two-column-grid" style={{ gap: 12 }}>
            <label className="field">
              <span>Максимум результатов</span>
              <input
                type="number"
                min={1}
                max={500}
                value={parserMaxResults}
                onChange={(e) => setParserMaxResults(Number(e.target.value))}
              />
            </label>
            <label className="field" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                type="checkbox"
                checked={parserActiveOnly}
                onChange={(e) => setParserActiveOnly(e.target.checked)}
              />
              <span>Только активные каналы</span>
            </label>
          </div>
          <label className="field">
            <span>Аккаунт для парсинга</span>
            <select
              value={parserAccountId ?? ""}
              onChange={(e) => setParserAccountId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">— любой доступный —</option>
              {accounts.map((acc) => (
                <option key={acc.id} value={acc.id}>
                  {acc.phone} ({acc.health_status})
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Сохранить в базу</span>
            <select
              value={parserTargetDbId ?? ""}
              onChange={(e) => setParserTargetDbId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">— не сохранять —</option>
              {databases.map((db) => (
                <option key={db.id} value={db.id}>
                  {db.name}
                </option>
              ))}
            </select>
          </label>
          <button
            className="primary-button"
            type="button"
            disabled={busy || !parserKeywords.length}
            onClick={() => void handleStartParsing()}
          >
            {busy ? "Запускаем…" : "Начать парсинг"}
          </button>
        </div>
      </section>

      {/* Active jobs */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Задачи парсинга</div>
            <h2>История задач</h2>
          </div>
          <div className="badge-row">
            <button
              className="ghost-button"
              type="button"
              onClick={() => void loadJobs()}
              disabled={busy}
            >
              Обновить
            </button>
          </div>
        </div>
        {parsingJobs.length ? (
          <div className="creative-list">
            {parsingJobs.map((job) => (
              <div key={job.id} className="creative-item">
                <div className="thread-meta" style={{ flexWrap: "wrap", gap: 8 }}>
                  <strong>#{job.id}</strong>
                  <span className={`pill ${statusBadgeClass(job.status)}`}>
                    {JOB_STATUS_LABELS[job.status] ?? job.status}
                  </span>
                  <span className="muted">{job.job_type}</span>
                  <span className="muted">
                    {(job.keywords || []).join(", ") || "—"}
                  </span>
                  <span className="muted">
                    Найдено: {job.results_count} / {job.max_results}
                  </span>
                  {job.started_at ? (
                    <span className="muted">Начато: {job.started_at}</span>
                  ) : null}
                  {job.completed_at ? (
                    <span className="muted">Завершено: {job.completed_at}</span>
                  ) : null}
                  {job.error ? (
                    <span className="muted" style={{ color: "#ff5555" }}>
                      {job.error}
                    </span>
                  ) : null}
                  {(job.status === "pending" || job.status === "running") ? (
                    <button
                      className="ghost-button"
                      type="button"
                      style={{ color: "#ffcc00", borderColor: "#ffcc00" }}
                      onClick={() => void handleCancelJob(job.id)}
                    >
                      Отменить
                    </button>
                  ) : null}
                </div>

                {/* Progress bar — shown while running */}
                {job.status === "running" ? (
                  <JobProgressBar
                    progress={job.progress ?? 0}
                    resultsCount={job.results_count}
                  />
                ) : null}

                {/* Completed progress bar — full green */}
                {job.status === "completed" ? (
                  <JobProgressBar progress={100} resultsCount={job.results_count} />
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">Нет задач парсинга. Запустите первый поиск каналов.</p>
        )}
      </section>

      {/* Create DB modal */}
      {showCreateDbModal ? (
        <div className="modal-overlay" onClick={() => setShowCreateDbModal(false)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div>
                <div className="eyebrow">Новая база</div>
                <h2>Создать базу каналов</h2>
              </div>
            </div>
            <div className="stack-form">
              <label className="field">
                <span>Название базы</span>
                <input
                  value={newDbName}
                  onChange={(e) => setNewDbName(e.target.value)}
                  placeholder="Например: Маркетинг RU"
                />
              </label>
              <div className="actions-row">
                <button className="primary-button" type="button" disabled={busy} onClick={() => void handleCreateDb()}>
                  {busy ? "Создаём…" : "Создать базу"}
                </button>
                <button className="ghost-button" type="button" onClick={() => setShowCreateDbModal(false)}>
                  Отмена
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
