import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";

type AccountListItem = {
  id: number;
  phone: string;
  display_name: string | null;
  status: string;
  packaging_status?: string;
};

type ProfileResult = {
  account_id: number;
  profile?: Record<string, string>;
  packaging_status?: string;
  error?: string;
};

export function MassPackagingPage() {
  const { accessToken } = useAuth();
  const [accounts, setAccounts] = useState<AccountListItem[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [results, setResults] = useState<ProfileResult[]>([]);

  // Shared params
  const [gender, setGender] = useState("female");
  const [country, setCountry] = useState("RU");
  const [ageRange, setAgeRange] = useState("25-35");
  const [profession, setProfession] = useState("marketing");

  const loadAccounts = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiFetch<{ items: AccountListItem[] }>(
        "/v1/admin/onboarding/accounts?limit=500",
        { accessToken }
      );
      // Enrich with packaging status
      const enriched = await Promise.all(
        data.items.map(async (a) => {
          try {
            const ps = await apiFetch<{ packaging_status: string }>(
              `/v1/admin/accounts/${a.id}/packaging-status`,
              { accessToken }
            );
            return { ...a, packaging_status: ps.packaging_status };
          } catch {
            return { ...a, packaging_status: "unknown" };
          }
        })
      );
      setAccounts(enriched);
    } catch (e) {
      console.error("Failed to load accounts:", e);
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  const filteredAccounts =
    statusFilter === "all"
      ? accounts
      : accounts.filter((a) =>
          statusFilter === "not_packaged"
            ? a.packaging_status === "not_started" || a.packaging_status === "unknown"
            : a.packaging_status === statusFilter
        );

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === filteredAccounts.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filteredAccounts.map((a) => a.id)));
    }
  };

  const handleMassGenerate = async () => {
    if (selected.size === 0) return;
    setGenerating(true);
    setResults([]);
    try {
      const data = await apiFetch<{ results: ProfileResult[]; total: number }>(
        "/v1/admin/accounts/mass-generate-profiles",
        {
          method: "POST",
          json: {
            account_ids: Array.from(selected),
            params: { gender, country, age_range: ageRange, profession },
          },
          accessToken,
        }
      );
      setResults(data.results);
      // Reload accounts to refresh packaging status
      await loadAccounts();
    } catch (e) {
      console.error("Mass generate failed:", e);
    } finally {
      setGenerating(false);
    }
  };

  const successCount = results.filter((r) => r.profile && !r.error).length;
  const errorCount = results.filter((r) => r.error).length;

  return (
    <div style={{ maxWidth: 1000, margin: "0 auto" }}>
      <h1 style={{ color: "#00ff88", marginBottom: 8 }}>Mass Packaging</h1>
      <p style={{ color: "#888", marginBottom: 24 }}>
        Bulk generate profiles for multiple accounts at once.
      </p>

      {/* Shared params */}
      <div
        style={{
          background: "#111",
          border: "1px solid #222",
          borderRadius: 8,
          padding: 20,
          marginBottom: 24,
        }}
      >
        <h3 style={{ color: "#eee", marginBottom: 16 }}>Profile Parameters</h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 12 }}>
          <div>
            <label style={{ color: "#888", fontSize: 12 }}>Gender</label>
            <select value={gender} onChange={(e) => setGender(e.target.value)} style={selectStyle}>
              <option value="female">Female</option>
              <option value="male">Male</option>
            </select>
          </div>
          <div>
            <label style={{ color: "#888", fontSize: 12 }}>Country</label>
            <select value={country} onChange={(e) => setCountry(e.target.value)} style={selectStyle}>
              <option value="RU">RU</option>
              <option value="KZ">KZ</option>
              <option value="UA">UA</option>
              <option value="BY">BY</option>
              <option value="UZ">UZ</option>
              <option value="US">US</option>
            </select>
          </div>
          <div>
            <label style={{ color: "#888", fontSize: 12 }}>Age Range</label>
            <select
              value={ageRange}
              onChange={(e) => setAgeRange(e.target.value)}
              style={selectStyle}
            >
              <option value="18-24">18-24</option>
              <option value="25-35">25-35</option>
              <option value="35-45">35-45</option>
              <option value="45-55">45-55</option>
            </select>
          </div>
          <div>
            <label style={{ color: "#888", fontSize: 12 }}>Profession</label>
            <input
              value={profession}
              onChange={(e) => setProfession(e.target.value)}
              placeholder="marketing"
              style={inputStyle}
            />
          </div>
        </div>
      </div>

      {/* Status filter + select all */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label style={{ color: "#888", fontSize: 13 }}>Filter:</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            style={{ ...selectStyle, width: "auto" }}
          >
            <option value="all">All</option>
            <option value="not_packaged">Not packaged</option>
            <option value="profile_generated">Profile generated</option>
            <option value="fully_packaged">Fully packaged</option>
          </select>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <span style={{ color: "#888", fontSize: 13 }}>
            Selected: {selected.size} / {filteredAccounts.length}
          </span>
          <button onClick={toggleAll} style={btnSecondary}>
            {selected.size === filteredAccounts.length ? "Deselect All" : "Select All"}
          </button>
          <button
            onClick={handleMassGenerate}
            disabled={generating || selected.size === 0}
            style={btnPrimary}
          >
            {generating ? `Generating (${selected.size})...` : `Generate All Profiles (${selected.size})`}
          </button>
        </div>
      </div>

      {/* Account list */}
      <div
        style={{
          background: "#111",
          border: "1px solid #222",
          borderRadius: 8,
          overflow: "hidden",
        }}
      >
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid #222" }}>
              <th style={thStyle}>
                <input
                  type="checkbox"
                  checked={selected.size === filteredAccounts.length && filteredAccounts.length > 0}
                  onChange={toggleAll}
                />
              </th>
              <th style={thStyle}>Phone</th>
              <th style={thStyle}>Name</th>
              <th style={thStyle}>Status</th>
              <th style={thStyle}>Packaging</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={5} style={{ ...tdStyle, textAlign: "center", color: "#888" }}>
                  Loading...
                </td>
              </tr>
            ) : filteredAccounts.length === 0 ? (
              <tr>
                <td colSpan={5} style={{ ...tdStyle, textAlign: "center", color: "#888" }}>
                  No accounts found
                </td>
              </tr>
            ) : (
              filteredAccounts.map((a) => {
                const resultForThis = results.find((r) => r.account_id === a.id);
                return (
                  <tr
                    key={a.id}
                    style={{
                      borderBottom: "1px solid #1a1a1a",
                      background: selected.has(a.id) ? "#0a1a0a" : "transparent",
                    }}
                  >
                    <td style={tdStyle}>
                      <input
                        type="checkbox"
                        checked={selected.has(a.id)}
                        onChange={() => toggleSelect(a.id)}
                      />
                    </td>
                    <td style={tdStyle}>{a.phone}</td>
                    <td style={tdStyle}>{a.display_name || "-"}</td>
                    <td style={tdStyle}>
                      <span style={{ color: "#4488ff", fontSize: 12 }}>{a.status}</span>
                    </td>
                    <td style={tdStyle}>
                      <span
                        style={{
                          color: pkgColor(a.packaging_status || "not_started"),
                          fontSize: 12,
                          fontWeight: 500,
                        }}
                      >
                        {(a.packaging_status || "not_started").replace(/_/g, " ")}
                      </span>
                      {resultForThis && (
                        <span
                          style={{
                            marginLeft: 8,
                            color: resultForThis.error ? "#ff4444" : "#00ff88",
                            fontSize: 11,
                          }}
                        >
                          {resultForThis.error ? `ERR: ${resultForThis.error}` : "OK"}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Results summary */}
      {results.length > 0 && (
        <div
          style={{
            marginTop: 16,
            padding: 16,
            background: "#111",
            border: "1px solid #222",
            borderRadius: 8,
          }}
        >
          <h4 style={{ color: "#eee", marginBottom: 8 }}>Results</h4>
          <div style={{ color: "#00ff88", fontSize: 14 }}>
            Success: {successCount} | Errors: {errorCount} | Total: {results.length}
          </div>
        </div>
      )}
    </div>
  );
}

function pkgColor(status: string): string {
  const map: Record<string, string> = {
    not_started: "#666",
    unknown: "#666",
    profile_generated: "#4488ff",
    avatar_set: "#ff8844",
    channel_created: "#aa44ff",
    fully_packaged: "#00ff88",
  };
  return map[status] || "#888";
}

const selectStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  background: "#0a0a0b",
  color: "#eee",
  border: "1px solid #333",
  borderRadius: 6,
  fontSize: 13,
  marginTop: 4,
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  background: "#0a0a0b",
  color: "#eee",
  border: "1px solid #333",
  borderRadius: 6,
  fontSize: 13,
  marginTop: 4,
  boxSizing: "border-box",
};

const btnPrimary: React.CSSProperties = {
  padding: "10px 20px",
  background: "#00ff88",
  color: "#0a0a0b",
  border: "none",
  borderRadius: 6,
  fontWeight: 600,
  fontSize: 14,
  cursor: "pointer",
};

const btnSecondary: React.CSSProperties = {
  padding: "8px 14px",
  background: "#222",
  color: "#eee",
  border: "1px solid #444",
  borderRadius: 6,
  fontWeight: 500,
  fontSize: 13,
  cursor: "pointer",
};

const thStyle: React.CSSProperties = {
  padding: "10px 12px",
  textAlign: "left",
  color: "#888",
  fontSize: 12,
  fontWeight: 600,
  textTransform: "uppercase",
};

const tdStyle: React.CSSProperties = {
  padding: "10px 12px",
  color: "#ccc",
  fontSize: 13,
};
