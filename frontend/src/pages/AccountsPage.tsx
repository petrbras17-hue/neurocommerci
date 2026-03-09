import { FormEvent, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../api";
import { useAuth } from "../auth";

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

  const loadState = async () => {
    if (!accessToken) {
      return;
    }
    const [accountsPayload, proxiesPayload] = await Promise.all([
      apiFetch<AccountsResponse>("/v1/web/accounts", { accessToken }),
      apiFetch<ProxiesResponse>("/v1/web/proxies/available", { accessToken })
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

  const uploadPair = async (event: FormEvent) => {
    event.preventDefault();
    if (!accessToken || !sessionFile || !metadataFile) {
      setStatusMessage("Нужно выбрать оба файла: .session и .json");
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
        body
      });
      setSelectedAccountId(result.account_id);
      setStatusMessage(`Аккаунт ${result.phone} загружен, комплект готов.`);
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
          proxy_string: manualProxy || null
        }
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
        accessToken
      });
      setStatusMessage(`Проверка доступа завершена: ${result.audit.session_status}`);
      await loadState();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "audit_failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-grid">
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Web onboarding</div>
            <h2>Добавь первый Telegram-аккаунт</h2>
          </div>
        </div>
        <form className="stack-form" onSubmit={uploadPair}>
          <label className="field">
            <span>Файл .session</span>
            <input type="file" accept=".session" onChange={(event) => setSessionFile(event.target.files?.[0] || null)} />
          </label>
          <label className="field">
            <span>Файл .json</span>
            <input type="file" accept=".json" onChange={(event) => setMetadataFile(event.target.files?.[0] || null)} />
          </label>
          <button className="primary-button" type="submit" disabled={busy}>
            {busy ? "Загружаем…" : "Загрузить pair"}
          </button>
        </form>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Proxy step</div>
            <h2>Привязка прокси</h2>
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
      </section>

      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Account audit</div>
            <h2>Подключённые аккаунты</h2>
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
                <th>Ban risk</th>
                <th>Lifecycle</th>
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
                  <td>
                    <button className="ghost-button" type="button" onClick={() => void runAudit(item.id)} disabled={busy}>
                      Проверить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {selectedAccount ? (
          <div className="inline-note">
            Следующее действие для {selectedAccount.phone}: {selectedAccount.recommended_next_action}
          </div>
        ) : null}
      </section>
    </div>
  );
}
