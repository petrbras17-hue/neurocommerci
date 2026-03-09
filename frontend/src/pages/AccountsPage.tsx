import { FormEvent, useEffect, useMemo, useState } from "react";
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

  const loadState = async () => {
    if (!accessToken) {
      return;
    }
    const [accountsPayload, proxiesPayload] = await Promise.all([
      apiFetch<AccountsResponse>("/v1/web/accounts", { accessToken }),
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
  };

  useEffect(() => {
    void loadState();
  }, [accessToken]);

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
      await loadState();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "upload_failed");
    } finally {
      setBusy(false);
    }
  };

  const bindProxy = async () => {
    if (!accessToken || !selectedAccountId) {
      return;
    }
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
      await loadState();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "proxy_bind_failed");
    } finally {
      setBusy(false);
    }
  };

  const runAudit = async (accountId: number) => {
    if (!accessToken) {
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      const result = await apiFetch<{ audit: AccountRow }>(`/v1/web/accounts/${accountId}/audit`, {
        method: "POST",
        accessToken,
      });
      setStatusMessage(`Проверка доступа завершена: ${result.audit.session_status}`);
      await loadState();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "audit_failed");
    } finally {
      setBusy(false);
    }
  };

  const saveNotes = async () => {
    if (!accessToken || !selectedAccountId) {
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      await apiFetch(`/v1/web/accounts/${selectedAccountId}/notes`, {
        method: "POST",
        accessToken,
        json: { notes: notesDraft },
      });
      setStatusMessage("Ручная заметка сохранена.");
      await loadState();
      const timelinePayload = await apiFetch<TimelineResponse>(`/v1/web/accounts/${selectedAccountId}/timeline`, { accessToken });
      setTimeline(timelinePayload);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "notes_save_failed");
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
              <div className="eyebrow">Operator path</div>
              <h2>Что делает система</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Принимает pair `.session + .json` и держит canonical storage.</li>
            <li>Показывает прокси, audit, lifecycle и следующий рекомендуемый шаг.</li>
            <li>Сохраняет историю действий и ручные заметки без запуска боевых Telegram-side действий.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Operator path</div>
              <h2>Что делает оператор вручную</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Загружает pair, проверяет видимость прокси и запускает audit.</li>
            <li>Оставляет заметки по аккаунту и фиксирует ручные шаги в истории.</li>
            <li>Решает, когда аккаунт безопасно двигать дальше, не опираясь на silent automation.</li>
          </ul>
        </article>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Step 1</div>
            <h2>Загрузите pair `.session + .json`</h2>
          </div>
        </div>
        <form className="stack-form" onSubmit={uploadPair}>
          <label className="field">
            <span>Файл `.session`</span>
            <input type="file" accept=".session" onChange={(event) => setSessionFile(event.target.files?.[0] || null)} />
          </label>
          <label className="field">
            <span>Файл `.json`</span>
            <input type="file" accept=".json" onChange={(event) => setMetadataFile(event.target.files?.[0] || null)} />
          </label>
          <button className="primary-button" type="submit" disabled={busy}>
            {busy ? "Загружаем…" : "Загрузить pair"}
          </button>
        </form>
      </section>

      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Step 2</div>
              <h2>Привяжите живой прокси</h2>
            </div>
          </div>
          <div className="field">
            <span>Аккаунт</span>
            <select value={selectedAccountId ?? ""} onChange={(event) => setSelectedAccountId(Number(event.target.value))}>
              {accounts.items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.phone}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <span>Прокси из пула</span>
            <select value={selectedProxyId ?? ""} onChange={(event) => setSelectedProxyId(Number(event.target.value))}>
              {(proxies?.items || []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.url} • {item.health_status}
                </option>
              ))}
            </select>
          </div>
          <label className="field">
            <span>Или добавьте proxy string вручную</span>
            <input
              value={manualProxy}
              onChange={(event) => setManualProxy(event.target.value)}
              placeholder="socks5://user:pass@host:port"
            />
          </label>
          <button className="secondary-button" type="button" disabled={busy || !selectedAccountId} onClick={() => void bindProxy()}>
            Привязать прокси
          </button>
        </article>

        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Step 3</div>
              <h2>Безопасный audit</h2>
            </div>
          </div>
          <p className="muted">
            Этот шаг показывает, что сейчас видит система: session status, lifecycle и recommended next action. До конца Sprint 3
            это safe shell без реального execution path.
          </p>
          <button
            className="ghost-button"
            type="button"
            disabled={busy || !selectedAccountId}
            onClick={() => selectedAccountId && void runAudit(selectedAccountId)}
          >
            Запустить audit для выбранного аккаунта
          </button>
          {selectedAccount ? (
            <div className="inline-note">
              Для {selectedAccount.phone}: следующий рекомендуемый шаг — {selectedAccount.recommended_next_action}
            </div>
          ) : null}
        </article>
      </section>

      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Account audit</div>
            <h2>Аккаунты в workspace</h2>
          </div>
        </div>
        {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
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
              {accounts.items.map((item) => (
                <tr key={item.id}>
                  <td>{item.phone}</td>
                  <td>{item.proxy || "—"}</td>
                  <td>{item.session_status}</td>
                  <td>{item.last_active || "—"}</td>
                  <td>{item.ban_risk_level}</td>
                  <td>{item.lifecycle_stage}</td>
                  <td>{item.recommended_next_action}</td>
                  <td>
                    <button
                      className="ghost-button"
                      type="button"
                      onClick={() => {
                        setSelectedAccountId(item.id);
                        void runAudit(item.id);
                      }}
                      disabled={busy}
                    >
                      Проверить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Manual notes</div>
              <h2>Заметки оператора</h2>
            </div>
          </div>
          <textarea
            className="notes-input"
            value={notesDraft}
            onChange={(event) => setNotesDraft(event.target.value)}
            placeholder="Зафиксируйте, что оператор проверил руками и что безопасно делать дальше."
          />
          <button className="secondary-button" type="button" disabled={busy || !selectedAccountId} onClick={() => void saveNotes()}>
            Сохранить заметку
          </button>
        </article>

        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Timeline</div>
              <h2>История шагов</h2>
            </div>
          </div>
          <div className="timeline-list">
            {(timeline?.items || []).length ? (
              (timeline?.items || []).map((item, index) => (
                <div className="timeline-item" key={`${item.kind}-${item.created_at || index}`}>
                  <strong>{item.title}</strong>
                  <span>{item.created_at || "—"}</span>
                  {item.notes ? <p>{item.notes}</p> : null}
                </div>
              ))
            ) : (
              <p className="muted">Пока нет истории шагов. После audit и заметок она появится здесь.</p>
            )}
          </div>
        </article>
      </section>
    </div>
  );
}
