import { useEffect, useRef, useState } from "react";
import { apiFetch, reactionsApi, ReactionJob } from "../api";
import { useAuth } from "../auth";

type AccountRow = { id: number; phone: string; status: string; health_status: string };

const REACTION_TYPES = [
  { value: "random", label: "Случайные" },
  { value: "thumbs_up", label: "Большой палец вверх" },
  { value: "fire", label: "Огонь" },
  { value: "heart", label: "Сердце" },
];

function statusBadgeClass(status: string): string {
  if (status === "running" || status === "completed") return "badge-green";
  if (status === "pending" || status === "queued") return "badge-yellow";
  if (status === "failed") return "badge-red";
  return "badge-gray";
}

function ProgressBar({ value, total }: { value: number; total: number }) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0;
  const color = pct >= 80 ? "#22c55e" : pct >= 40 ? "#eab308" : "#3b82f6";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ flex: 1, height: 6, background: "#2d2d2d", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: 11, color: "#aaa", minWidth: 36 }}>{value}/{total}</span>
    </div>
  );
}

export function ReactionsPage() {
  const { accessToken } = useAuth();
  const [jobs, setJobs] = useState<ReactionJob[]>([]);
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  // form state
  const [channelUsername, setChannelUsername] = useState("");
  const [reactionType, setReactionType] = useState("random");
  const [selectedAccountIds, setSelectedAccountIds] = useState<number[]>([]);
  const [postId, setPostId] = useState("");

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadJobs = async () => {
    if (!accessToken) return;
    try {
      const payload = await reactionsApi.list(accessToken);
      setJobs(payload.items);
    } catch {
      // silent
    }
  };

  const loadAccounts = async () => {
    if (!accessToken) return;
    try {
      const payload = await apiFetch<{ items: AccountRow[]; total: number }>("/v1/web/accounts", { accessToken });
      setAccounts(payload.items);
    } catch {
      // silent
    }
  };

  useEffect(() => {
    void Promise.all([loadJobs(), loadAccounts()]).catch(() => {});
  }, [accessToken]);

  // auto-refresh running jobs every 5s
  useEffect(() => {
    intervalRef.current = setInterval(() => {
      const hasRunning = jobs.some((j) => j.status === "running" || j.status === "pending");
      if (hasRunning) void loadJobs().catch(() => {});
    }, 5000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [jobs]);

  const toggleAccount = (id: number) => {
    setSelectedAccountIds((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);
  };

  const handleCreate = async () => {
    if (!accessToken) return;
    if (!channelUsername.trim()) { setStatusMessage("Введите юзернейм канала."); return; }
    if (!selectedAccountIds.length) { setStatusMessage("Выберите хотя бы один аккаунт."); return; }
    setBusy(true);
    setStatusMessage("");
    try {
      await reactionsApi.create(accessToken, {
        channel_username: channelUsername.trim(),
        reaction_type: reactionType,
        account_ids: selectedAccountIds,
        ...(postId.trim() ? { post_id: Number(postId.trim()) } : {}),
      });
      setChannelUsername("");
      setPostId("");
      setSelectedAccountIds([]);
      setStatusMessage("Задание на реакции создано.");
      await loadJobs();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "create_reactions_failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-grid">
      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Mass Reactions</div>
              <h2>Массовые реакции</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Выберите канал, тип реакции и аккаунты.</li>
            <li>Можно указать конкретный ID поста или оставить пустым — тогда реакции на последний пост.</li>
            <li>Статус задания обновляется автоматически каждые 5 секунд.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Статус</div>
              <h2>Статистика заданий</h2>
            </div>
          </div>
          <div className="status-grid">
            <div className="info-block"><strong>Всего заданий</strong><span>{jobs.length}</span></div>
            <div className="info-block"><strong>Выполнено</strong><span>{jobs.filter((j) => j.status === "completed").length}</span></div>
            <div className="info-block"><strong>Активных</strong><span>{jobs.filter((j) => j.status === "running" || j.status === "pending").length}</span></div>
            <div className="info-block"><strong>Ошибок</strong><span>{jobs.filter((j) => j.status === "failed").length}</span></div>
          </div>
        </article>
      </section>

      {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Новое задание</div>
            <h2>Запустить реакции</h2>
          </div>
        </div>
        <div className="stack-form">
          <div className="two-column-grid" style={{ gap: 12 }}>
            <label className="field">
              <span>Юзернейм канала</span>
              <input
                value={channelUsername}
                onChange={(e) => setChannelUsername(e.target.value)}
                placeholder="@channel или channel"
              />
            </label>
            <label className="field">
              <span>Тип реакции</span>
              <select value={reactionType} onChange={(e) => setReactionType(e.target.value)}>
                {REACTION_TYPES.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
          </div>
          <label className="field">
            <span>ID поста (необязательно)</span>
            <input
              type="number"
              value={postId}
              onChange={(e) => setPostId(e.target.value)}
              placeholder="Оставьте пустым для последнего поста"
            />
          </label>
          <div className="field">
            <span>Аккаунты ({selectedAccountIds.length} выбрано)</span>
            <div className="thread-list" style={{ maxHeight: 200, overflowY: "auto" }}>
              {accounts.length ? accounts.map((acc) => (
                <label key={acc.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", cursor: "pointer" }}>
                  <input type="checkbox" checked={selectedAccountIds.includes(acc.id)} onChange={() => toggleAccount(acc.id)} />
                  <span>{acc.phone}</span>
                  <span className={`pill ${statusBadgeClass(acc.health_status === "alive" ? "completed" : "failed")}`}>{acc.health_status}</span>
                </label>
              )) : <p className="muted">Нет доступных аккаунтов.</p>}
            </div>
          </div>
          <div className="actions-row">
            <button className="primary-button" type="button" disabled={busy} onClick={() => void handleCreate()}>
              {busy ? "Создаём…" : "Запустить реакции"}
            </button>
          </div>
        </div>
      </section>

      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">История заданий</div>
            <h2>Активные и завершённые задания</h2>
          </div>
          <div className="badge-row">
            <button className="ghost-button" type="button" onClick={() => void loadJobs()}>Обновить</button>
          </div>
        </div>
        {jobs.length ? (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Канал</th>
                  <th>Тип реакции</th>
                  <th>Статус</th>
                  <th>Прогресс</th>
                  <th>Успешно / Ошибок</th>
                  <th>Создано</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id}>
                    <td>{job.id}</td>
                    <td>{job.channel_username}</td>
                    <td>{job.reaction_type}</td>
                    <td><span className={`pill ${statusBadgeClass(job.status)}`}>{job.status}</span></td>
                    <td style={{ minWidth: 120 }}><ProgressBar value={job.successful_reactions} total={job.total_reactions} /></td>
                    <td>{job.successful_reactions} / {job.failed_reactions}</td>
                    <td>{job.created_at ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">Заданий пока нет. Создайте первое задание на реакции.</p>
        )}
      </section>
    </div>
  );
}
