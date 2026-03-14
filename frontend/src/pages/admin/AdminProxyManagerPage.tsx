import { useEffect, useState, useCallback } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";

type ProxyItem = {
  id: number;
  host: string;
  port: number;
  proxy_type: string;
  country: string | null;
  status: string;
  bound_account_id: number | null;
  last_tested_at: string | null;
  last_ip: string | null;
  supports_https_connect: boolean | null;
  created_at: string | null;
};

export function AdminProxyManagerPage() {
  const { accessToken } = useAuth();
  const [proxies, setProxies] = useState<ProxyItem[]>([]);
  const [importText, setImportText] = useState("");
  const [proxyType, setProxyType] = useState("socks5");
  const [country, setCountry] = useState("");
  const [loading, setLoading] = useState(false);
  const [testingAll, setTestingAll] = useState(false);
  const [statusFilter, setStatusFilter] = useState("");

  const loadProxies = useCallback(async () => {
    const params = new URLSearchParams();
    if (statusFilter) params.set("proxy_status", statusFilter);
    const result = await apiFetch<{ items: ProxyItem[] }>(`/v1/admin/proxies?${params}`, { accessToken });
    setProxies(result.items);
  }, [accessToken, statusFilter]);

  useEffect(() => { void loadProxies(); }, [loadProxies]);

  const handleImport = async () => {
    if (!importText.trim()) return;
    setLoading(true);
    try {
      const lines = importText.split("\n").filter((l) => l.trim());
      await apiFetch("/v1/admin/proxies/import", {
        method: "POST",
        json: { lines, proxy_type: proxyType, country: country || undefined },
        accessToken,
      });
      setImportText("");
      await loadProxies();
    } catch (e) {
      alert(`Ошибка: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  const handleTestOne = async (id: number) => {
    try {
      await apiFetch(`/v1/admin/proxies/${id}/test`, { method: "POST", accessToken });
      await loadProxies();
    } catch (e) {
      alert(`Ошибка: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleTestAll = async () => {
    setTestingAll(true);
    try {
      await apiFetch("/v1/admin/proxies/test-all", { method: "POST", accessToken });
      await loadProxies();
    } finally {
      setTestingAll(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Удалить прокси?")) return;
    try {
      await apiFetch(`/v1/admin/proxies/${id}`, { method: "DELETE", accessToken });
      await loadProxies();
    } catch (e) {
      alert(`Ошибка: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleUnbind = async (id: number) => {
    try {
      await apiFetch(`/v1/admin/proxies/${id}/unbind`, { method: "POST", accessToken });
      await loadProxies();
    } catch (e) {
      alert(`Ошибка: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const alive = proxies.filter((p) => p.status === "alive").length;
  const dead = proxies.filter((p) => p.status === "dead").length;
  const free = proxies.filter((p) => p.status === "alive" && !p.bound_account_id).length;

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 24, color: "#ff4444", marginBottom: 24 }}>Менеджер прокси</h1>

      {/* Import section */}
      <div className="card" style={{ marginBottom: 24 }}>
        <h3>Импорт прокси</h3>
        <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
          <select value={proxyType} onChange={(e) => setProxyType(e.target.value)} style={{ padding: "6px 12px" }}>
            <option value="socks5">SOCKS5</option>
            <option value="http">HTTP</option>
          </select>
          <input
            type="text"
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            placeholder="Страна (KZ, RU...)"
            style={{ padding: "6px 12px", width: 120 }}
          />
        </div>
        <textarea
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
          placeholder={"host:port:user:password\nhost:port:user:password"}
          rows={4}
          style={{ width: "100%", marginBottom: 8, fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}
        />
        <button className="btn-accent" onClick={handleImport} disabled={loading || !importText.trim()}>
          {loading ? "Импортируем..." : "Импортировать"}
        </button>
      </div>

      {/* Stats + actions */}
      <div style={{ display: "flex", gap: 16, marginBottom: 16, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: 13 }}>
          Всего: <strong>{proxies.length}</strong> | Живых: <strong style={{ color: "#00ff88" }}>{alive}</strong> | Мёртвых: <strong style={{ color: "#ff4444" }}>{dead}</strong> | Свободных: <strong style={{ color: "#ffcc00" }}>{free}</strong>
        </span>
        <button className="btn-secondary" onClick={handleTestAll} disabled={testingAll}>
          {testingAll ? "Тестируем..." : "Тест всех"}
        </button>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} style={{ padding: "4px 8px" }}>
          <option value="">Все</option>
          <option value="alive">Живые</option>
          <option value="dead">Мёртвые</option>
          <option value="untested">Не проверены</option>
        </select>
      </div>

      {/* Proxy table */}
      <div style={{ overflowX: "auto" }}>
        <table className="data-table" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th>Host:Port</th>
              <th>Тип</th>
              <th>Страна</th>
              <th>Статус</th>
              <th>IP</th>
              <th>HTTPS</th>
              <th>Аккаунт</th>
              <th>Проверен</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {proxies.map((p) => (
              <tr key={p.id}>
                <td style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
                  {p.host}:{p.port}
                </td>
                <td>{p.proxy_type}</td>
                <td>{p.country || "—"}</td>
                <td>
                  <span style={{
                    color: p.status === "alive" ? "#00ff88" : p.status === "dead" ? "#ff4444" : "#888",
                    fontWeight: 600,
                  }}>
                    {p.status}
                  </span>
                </td>
                <td style={{ fontSize: 12 }}>{p.last_ip || "—"}</td>
                <td>{p.supports_https_connect === true ? "✓" : p.supports_https_connect === false ? "✗" : "?"}</td>
                <td>{p.bound_account_id || "—"}</td>
                <td style={{ fontSize: 11, color: "#888" }}>
                  {p.last_tested_at ? new Date(p.last_tested_at).toLocaleString("ru-RU") : "—"}
                </td>
                <td>
                  <div style={{ display: "flex", gap: 4 }}>
                    <button className="btn-sm" onClick={() => handleTestOne(p.id)}>Тест</button>
                    {p.bound_account_id && (
                      <button className="btn-sm" onClick={() => handleUnbind(p.id)}>Отвязать</button>
                    )}
                    {!p.bound_account_id && (
                      <button className="btn-sm btn-danger" onClick={() => handleDelete(p.id)}>✗</button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
