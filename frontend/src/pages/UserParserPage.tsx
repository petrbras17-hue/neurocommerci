import { useEffect, useState } from "react";
import { apiFetch, userParserApi, UserParsingResult } from "../api";
import { useAuth } from "../auth";

type AccountRow = { id: number; phone: string; status: string; health_status: string };

export function UserParserPage() {
  const { accessToken } = useAuth();
  const [results, setResults] = useState<UserParsingResult[]>([]);
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [channelFilter, setChannelFilter] = useState("");

  // form state
  const [parseChannel, setParseChannel] = useState("");
  const [parseAccountId, setParseAccountId] = useState<number | "">("");

  const loadResults = async (channel?: string) => {
    if (!accessToken) return;
    try {
      const payload = await userParserApi.listResults(accessToken, channel || undefined);
      setResults(payload.items);
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

  useEffect(() => { void Promise.all([loadResults(), loadAccounts()]).catch(() => {}); }, [accessToken]);

  const handleParse = async () => {
    if (!accessToken) return;
    if (!parseChannel.trim()) { setStatusMessage("Введите юзернейм канала."); return; }
    if (parseAccountId === "") { setStatusMessage("Выберите аккаунт для парсинга."); return; }
    setBusy(true);
    setStatusMessage("");
    try {
      const res = await userParserApi.parse(accessToken, {
        channel_username: parseChannel.trim(),
        account_id: Number(parseAccountId),
      });
      setStatusMessage(`Парсинг запущен (job #${res.job_id}). Результаты появятся ниже.`);
      setParseChannel("");
      setParseAccountId("");
      await loadResults();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "parse_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleFilter = async () => {
    await loadResults(channelFilter.trim() || undefined).catch(() => {});
  };

  const handleExport = () => {
    if (!results.length) return;
    const header = "id,channel,telegram_id,username,first_name,last_name,is_premium,last_seen,parsed_at";
    const rows = results.map((r) =>
      [r.id, r.channel_username ?? "", r.user_telegram_id ?? "", r.username ?? "",
       r.first_name ?? "", r.last_name ?? "", r.is_premium ? "1" : "0",
       r.last_seen ?? "", r.parsed_at ?? ""].join(",")
    );
    const csv = [header, ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "user_parser_results.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  const truncate = (s: string | null, n = 60) => {
    if (!s) return "—";
    return s.length > n ? s.slice(0, n) + "…" : s;
  };

  return (
    <div className="page-grid">
      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">User Parser</div>
              <h2>Парсер пользователей</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Собирает участников каналов с профилями: имя, username, bio, премиум-статус.</li>
            <li>Укажите канал и аккаунт для парсинга — результаты сохранятся в базу.</li>
            <li>Поддерживается фильтрация и экспорт в CSV.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Статус</div>
              <h2>Результаты</h2>
            </div>
          </div>
          <div className="status-grid">
            <div className="info-block"><strong>Всего записей</strong><span>{results.length}</span></div>
            <div className="info-block"><strong>С Premium</strong><span>{results.filter((r) => r.is_premium).length}</span></div>
            <div className="info-block"><strong>С username</strong><span>{results.filter((r) => r.username).length}</span></div>
            <div className="info-block"><strong>С bio</strong><span>{results.filter((r) => r.bio).length}</span></div>
          </div>
        </article>
      </section>

      {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Запуск парсинга</div>
            <h2>Спарсить участников канала</h2>
          </div>
        </div>
        <div className="stack-form">
          <div className="two-column-grid" style={{ gap: 12 }}>
            <label className="field">
              <span>Юзернейм канала</span>
              <input
                value={parseChannel}
                onChange={(e) => setParseChannel(e.target.value)}
                placeholder="@channel или channel"
              />
            </label>
            <label className="field">
              <span>Аккаунт для парсинга</span>
              <select value={parseAccountId} onChange={(e) => setParseAccountId(e.target.value === "" ? "" : Number(e.target.value))}>
                <option value="">— выберите аккаунт —</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.phone}</option>)}
              </select>
            </label>
          </div>
          <div className="actions-row">
            <button className="primary-button" type="button" disabled={busy} onClick={() => void handleParse()}>
              {busy ? "Запускаем…" : "Запустить парсинг"}
            </button>
          </div>
        </div>
      </section>

      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Результаты парсинга</div>
            <h2>Пользователи</h2>
          </div>
          <div className="badge-row">
            <input
              style={{ padding: "6px 10px", background: "#1a1a1a", border: "1px solid #333", borderRadius: 6, color: "#fff", fontSize: 13 }}
              value={channelFilter}
              onChange={(e) => setChannelFilter(e.target.value)}
              placeholder="Фильтр по каналу"
            />
            <button className="secondary-button" type="button" onClick={() => void handleFilter()}>
              Применить
            </button>
            <button className="ghost-button" type="button" onClick={handleExport} disabled={!results.length}>
              Экспорт CSV
            </button>
          </div>
        </div>
        {results.length ? (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Telegram ID</th>
                  <th>Username</th>
                  <th>Имя</th>
                  <th>Bio</th>
                  <th>Premium</th>
                  <th>Last seen</th>
                  <th>Канал</th>
                  <th>Спарсен</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r) => (
                  <tr key={r.id}>
                    <td>{r.user_telegram_id ?? "—"}</td>
                    <td>{r.username ? `@${r.username}` : "—"}</td>
                    <td>{[r.first_name, r.last_name].filter(Boolean).join(" ") || "—"}</td>
                    <td style={{ maxWidth: 200 }}>{truncate(r.bio)}</td>
                    <td>
                      {r.is_premium ? <span className="pill badge-green">Premium</span> : <span className="pill badge-gray">Нет</span>}
                    </td>
                    <td>{r.last_seen ?? "—"}</td>
                    <td>{r.channel_username ?? "—"}</td>
                    <td>{r.parsed_at ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">Нет результатов. Запустите парсинг канала.</p>
        )}
      </section>
    </div>
  );
}
