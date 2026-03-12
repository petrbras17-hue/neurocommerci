import { useEffect, useState } from "react";
import {
  RefreshCw,
  CheckCircle,
  AlertTriangle,
  XCircle,
  Archive,
  Server,
} from "lucide-react";
import { apiFetch } from "../api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type TopologyItem = {
  phone: string;
  user_id: number | null;
  status_kind: string;
  canonical_complete: boolean;
  safe_to_quarantine: boolean;
  canonical_session: string | null;
  canonical_metadata: string | null;
  flat_session: string | null;
  flat_metadata: string | null;
  legacy_sessions: string[];
  legacy_metadata: string[];
  legacy_dirs: string[];
};

type TopologySummary = {
  phones_total: number;
  status_counts: Record<string, number>;
  canonical_complete: number;
  with_root_copies: number;
  with_legacy_copies: number;
  duplicate_copy_phones: number;
  duplicate_phones: string[];
  safe_to_quarantine: number;
};

type TopologyAudit = {
  items: TopologyItem[];
  summary: TopologySummary;
};

type QuarantineResult = {
  ok: boolean;
  dry_run: boolean;
  quarantine_dir: string;
  moved_files: number;
  moved_phones: string[];
  files: string[];
  skipped: Array<{ phone: string; reason: string }>;
  skipped_count: number;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATUS_META: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  canonical: {
    label: "canonical",
    color: "#00ff88",
    icon: <CheckCircle size={14} />,
  },
  duplicate: {
    label: "duplicate",
    color: "#ff4444",
    icon: <XCircle size={14} />,
  },
  root_only: {
    label: "root only",
    color: "#ffbb33",
    icon: <AlertTriangle size={14} />,
  },
  legacy_only: {
    label: "legacy only",
    color: "#ff8800",
    icon: <AlertTriangle size={14} />,
  },
  canonical_incomplete: {
    label: "incomplete",
    color: "#ffbb33",
    icon: <AlertTriangle size={14} />,
  },
  noncanonical_only: {
    label: "noncanonical",
    color: "#ff8800",
    icon: <AlertTriangle size={14} />,
  },
  missing: {
    label: "missing",
    color: "#888",
    icon: <XCircle size={14} />,
  },
};

function StatusBadge({ kind }: { kind: string }) {
  const meta = STATUS_META[kind] ?? { label: kind, color: "#888", icon: null };
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "4px",
        padding: "2px 8px",
        borderRadius: "4px",
        fontSize: "11px",
        fontFamily: "'JetBrains Mono', monospace",
        fontWeight: 600,
        color: meta.color,
        border: `1px solid ${meta.color}33`,
        background: `${meta.color}11`,
        whiteSpace: "nowrap",
      }}
    >
      {meta.icon}
      {meta.label}
    </span>
  );
}

function BoolCell({ value }: { value: boolean }) {
  return (
    <span style={{ color: value ? "#00ff88" : "#ff4444", fontFamily: "'JetBrains Mono', monospace" }}>
      {value ? "✓" : "✗"}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Summary card
// ---------------------------------------------------------------------------

type CardProps = {
  label: string;
  value: number;
  color?: string;
  icon?: React.ReactNode;
};

function SummaryCard({ label, value, color = "#e0e0e0", icon }: CardProps) {
  return (
    <div
      style={{
        background: "#111114",
        border: `1px solid ${color}33`,
        borderRadius: "8px",
        padding: "16px 20px",
        minWidth: "130px",
        flex: "1 1 130px",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          color,
          marginBottom: "8px",
          fontSize: "12px",
          fontFamily: "'JetBrains Mono', monospace",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
        }}
      >
        {icon}
        {label}
      </div>
      <div
        style={{
          fontSize: "28px",
          fontWeight: 700,
          fontFamily: "'JetBrains Mono', monospace",
          color,
        }}
      >
        {value}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function SessionTopologyPage() {
  const [topology, setTopology] = useState<TopologyAudit | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quarantineResult, setQuarantineResult] = useState<QuarantineResult | null>(null);
  const [quarantineLoading, setQuarantineLoading] = useState(false);
  const [quarantineError, setQuarantineError] = useState<string | null>(null);
  const [dryRun, setDryRun] = useState(true);
  const [selectedPhones, setSelectedPhones] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);

  const loadTopology = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<TopologyAudit>("/v1/sessions/topology");
      setTopology(data);
      setSelectedPhones(new Set());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка загрузки топологии");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTopology();
  }, []);

  const handleQuarantine = async (phones?: string[]) => {
    setQuarantineLoading(true);
    setQuarantineError(null);
    setQuarantineResult(null);
    try {
      const body: { phones: string[]; dry_run: boolean } = {
        phones: phones ?? [],
        dry_run: dryRun,
      };
      const result = await apiFetch<QuarantineResult>("/v1/sessions/quarantine", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setQuarantineResult(result);
      if (!dryRun) {
        await loadTopology();
      }
    } catch (err: unknown) {
      setQuarantineError(err instanceof Error ? err.message : "Ошибка карантина");
    } finally {
      setQuarantineLoading(false);
      setConfirmOpen(false);
    }
  };

  const togglePhone = (phone: string) => {
    setSelectedPhones((prev) => {
      const next = new Set(prev);
      if (next.has(phone)) next.delete(phone);
      else next.add(phone);
      return next;
    });
  };

  const duplicateItems = topology?.items.filter((i) => i.safe_to_quarantine) ?? [];

  return (
    <div
      style={{
        background: "#0a0a0b",
        color: "#e0e0e0",
        minHeight: "100vh",
        padding: "2rem",
        fontFamily: "Geist Sans, Inter, sans-serif",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1.5rem" }}>
        <div>
          <h1
            style={{
              margin: 0,
              fontSize: "22px",
              fontWeight: 700,
              color: "#00ff88",
              display: "flex",
              alignItems: "center",
              gap: "10px",
            }}
          >
            <Server size={22} />
            Session Topology
          </h1>
          <p style={{ margin: "4px 0 0", color: "#666", fontSize: "13px" }}>
            Аудит canonical / flat / legacy сессий на диске
          </p>
        </div>
        <button
          onClick={loadTopology}
          disabled={loading}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "6px",
            padding: "8px 16px",
            background: loading ? "#1a1a1a" : "#00ff8822",
            border: "1px solid #00ff8844",
            borderRadius: "6px",
            color: "#00ff88",
            fontSize: "13px",
            cursor: loading ? "not-allowed" : "pointer",
            fontFamily: "inherit",
          }}
        >
          <RefreshCw size={14} style={{ animation: loading ? "spin 1s linear infinite" : "none" }} />
          {loading ? "Сканирование…" : "Обновить"}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div
          style={{
            padding: "12px 16px",
            background: "#ff444411",
            border: "1px solid #ff444444",
            borderRadius: "6px",
            color: "#ff6666",
            marginBottom: "1.5rem",
            fontSize: "13px",
          }}
        >
          {error}
        </div>
      )}

      {/* Summary cards */}
      {topology && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", marginBottom: "2rem" }}>
          <SummaryCard
            label="Всего телефонов"
            value={topology.summary.phones_total}
            color="#a0a0b0"
            icon={<Server size={13} />}
          />
          <SummaryCard
            label="Canonical"
            value={topology.summary.canonical_complete}
            color="#00ff88"
            icon={<CheckCircle size={13} />}
          />
          <SummaryCard
            label="Flat копии"
            value={topology.summary.with_root_copies}
            color="#ffbb33"
            icon={<AlertTriangle size={13} />}
          />
          <SummaryCard
            label="Legacy копии"
            value={topology.summary.with_legacy_copies}
            color="#ff8800"
            icon={<AlertTriangle size={13} />}
          />
          <SummaryCard
            label="Дубли"
            value={topology.summary.duplicate_copy_phones}
            color="#ff4444"
            icon={<XCircle size={13} />}
          />
          <SummaryCard
            label="В карантин"
            value={topology.summary.safe_to_quarantine}
            color="#ff8800"
            icon={<Archive size={13} />}
          />
        </div>
      )}

      {/* Session table */}
      {topology && topology.items.length > 0 && (
        <div
          style={{
            background: "#0e0e11",
            border: "1px solid #222",
            borderRadius: "8px",
            overflow: "hidden",
            marginBottom: "2rem",
          }}
        >
          <div
            style={{
              padding: "12px 16px",
              borderBottom: "1px solid #222",
              fontSize: "12px",
              color: "#666",
              fontFamily: "'JetBrains Mono', monospace",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            Сессии ({topology.items.length})
          </div>
          <div style={{ overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: "13px",
              }}
            >
              <thead>
                <tr style={{ borderBottom: "1px solid #222" }}>
                  {duplicateItems.length > 0 && (
                    <th style={thStyle}></th>
                  )}
                  <th style={thStyle}>Телефон</th>
                  <th style={thStyle}>Статус</th>
                  <th style={thStyle}>Canonical .session</th>
                  <th style={thStyle}>Canonical .json</th>
                  <th style={thStyle}>Flat копия</th>
                  <th style={thStyle}>Legacy dirs</th>
                  <th style={thStyle}>Действие</th>
                </tr>
              </thead>
              <tbody>
                {topology.items.map((item) => (
                  <tr
                    key={item.phone}
                    style={{
                      borderBottom: "1px solid #1a1a1a",
                      background: selectedPhones.has(item.phone) ? "#00ff8808" : "transparent",
                    }}
                  >
                    {duplicateItems.length > 0 && (
                      <td style={tdStyle}>
                        {item.safe_to_quarantine && (
                          <input
                            type="checkbox"
                            checked={selectedPhones.has(item.phone)}
                            onChange={() => togglePhone(item.phone)}
                            style={{ accentColor: "#00ff88" }}
                          />
                        )}
                      </td>
                    )}
                    <td style={{ ...tdStyle, fontFamily: "'JetBrains Mono', monospace", color: "#e0e0e0" }}>
                      {item.phone}
                    </td>
                    <td style={tdStyle}>
                      <StatusBadge kind={item.status_kind} />
                    </td>
                    <td style={{ ...tdStyle, textAlign: "center" }}>
                      <BoolCell value={!!item.canonical_session} />
                      {" "}
                      <BoolCell value={!!item.canonical_metadata} />
                    </td>
                    <td style={{ ...tdStyle, textAlign: "center" }}>
                      <BoolCell value={item.canonical_complete} />
                    </td>
                    <td style={{ ...tdStyle, textAlign: "center" }}>
                      <BoolCell value={item.flat_session !== null || item.flat_metadata !== null} />
                    </td>
                    <td style={{ ...tdStyle, fontFamily: "'JetBrains Mono', monospace", fontSize: "11px", color: "#888" }}>
                      {item.legacy_dirs.length > 0 ? item.legacy_dirs.join(", ") : "—"}
                    </td>
                    <td style={tdStyle}>
                      {item.safe_to_quarantine && (
                        <button
                          onClick={() => {
                            setSelectedPhones(new Set([item.phone]));
                            setConfirmOpen(true);
                          }}
                          style={{
                            padding: "3px 10px",
                            background: "transparent",
                            border: "1px solid #ff880044",
                            borderRadius: "4px",
                            color: "#ff8800",
                            fontSize: "11px",
                            cursor: "pointer",
                            fontFamily: "inherit",
                          }}
                        >
                          Карантин
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {topology && topology.items.length === 0 && (
        <div
          style={{
            padding: "40px",
            textAlign: "center",
            color: "#666",
            background: "#0e0e11",
            borderRadius: "8px",
            marginBottom: "2rem",
          }}
        >
          Сессии не найдены. Загрузите аккаунты чтобы начать.
        </div>
      )}

      {/* Quarantine panel */}
      <div
        style={{
          background: "#0e0e11",
          border: "1px solid #222",
          borderRadius: "8px",
          padding: "20px",
        }}
      >
        <h2 style={{ margin: "0 0 16px", fontSize: "16px", fontWeight: 600, color: "#e0e0e0" }}>
          <Archive size={16} style={{ marginRight: "8px", verticalAlign: "middle" }} />
          Карантин дублей
        </h2>

        {/* Controls */}
        <div style={{ display: "flex", alignItems: "center", gap: "16px", flexWrap: "wrap", marginBottom: "16px" }}>
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              fontSize: "13px",
              color: "#e0e0e0",
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              style={{ accentColor: "#00ff88" }}
            />
            Режим предпросмотра (dry run)
          </label>

          <button
            onClick={() => {
              if (selectedPhones.size > 0) {
                setConfirmOpen(true);
              } else {
                setConfirmOpen(true);
              }
            }}
            disabled={quarantineLoading || (topology?.summary.safe_to_quarantine === 0)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              padding: "8px 16px",
              background:
                quarantineLoading || topology?.summary.safe_to_quarantine === 0
                  ? "#1a1a1a"
                  : "#ff880022",
              border: "1px solid #ff880044",
              borderRadius: "6px",
              color: quarantineLoading || topology?.summary.safe_to_quarantine === 0 ? "#555" : "#ff8800",
              fontSize: "13px",
              cursor:
                quarantineLoading || topology?.summary.safe_to_quarantine === 0
                  ? "not-allowed"
                  : "pointer",
              fontFamily: "inherit",
            }}
          >
            <Archive size={14} />
            {selectedPhones.size > 0
              ? `Карантин выбранных (${selectedPhones.size})`
              : "Карантин всех дублей"}
          </button>
        </div>

        {dryRun && (
          <div
            style={{
              padding: "8px 12px",
              background: "#ffbb3311",
              border: "1px solid #ffbb3333",
              borderRadius: "4px",
              fontSize: "12px",
              color: "#ffbb33",
              marginBottom: "16px",
            }}
          >
            Режим предпросмотра активен — файлы не будут перемещены.
          </div>
        )}

        {quarantineError && (
          <div
            style={{
              padding: "10px 14px",
              background: "#ff444411",
              border: "1px solid #ff444444",
              borderRadius: "6px",
              color: "#ff6666",
              fontSize: "13px",
              marginBottom: "12px",
            }}
          >
            {quarantineError}
          </div>
        )}

        {quarantineResult && (
          <div
            style={{
              padding: "14px 16px",
              background: "#00ff8808",
              border: "1px solid #00ff8833",
              borderRadius: "6px",
              fontSize: "13px",
            }}
          >
            <div style={{ color: "#00ff88", fontWeight: 600, marginBottom: "8px" }}>
              {quarantineResult.dry_run ? "Предпросмотр завершён" : "Карантин выполнен"}
            </div>
            <div style={{ color: "#a0a0b0", lineHeight: "1.8", fontFamily: "'JetBrains Mono', monospace", fontSize: "12px" }}>
              <div>Файлов к перемещению: <span style={{ color: "#e0e0e0" }}>{quarantineResult.moved_files}</span></div>
              <div>Телефонов: <span style={{ color: "#e0e0e0" }}>{quarantineResult.moved_phones.length > 0 ? quarantineResult.moved_phones.join(", ") : "—"}</span></div>
              <div>Пропущено: <span style={{ color: "#e0e0e0" }}>{quarantineResult.skipped_count}</span></div>
              {!quarantineResult.dry_run && (
                <div>Папка карантина: <span style={{ color: "#888" }}>{quarantineResult.quarantine_dir}</span></div>
              )}
            </div>
            {quarantineResult.files.length > 0 && (
              <div style={{ marginTop: "10px" }}>
                <div style={{ color: "#666", fontSize: "11px", marginBottom: "4px", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                  Файлы
                </div>
                <div
                  style={{
                    maxHeight: "120px",
                    overflowY: "auto",
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: "11px",
                    color: "#888",
                    lineHeight: "1.6",
                  }}
                >
                  {quarantineResult.files.map((f, i) => (
                    <div key={i}>{f}</div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Confirm dialog */}
      {confirmOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "#000000bb",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
          onClick={() => setConfirmOpen(false)}
        >
          <div
            style={{
              background: "#111114",
              border: "1px solid #333",
              borderRadius: "10px",
              padding: "28px 32px",
              maxWidth: "420px",
              width: "90%",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ margin: "0 0 12px", color: "#e0e0e0", fontSize: "16px" }}>
              Подтвердить карантин
            </h3>
            <p style={{ margin: "0 0 20px", color: "#888", fontSize: "13px", lineHeight: "1.6" }}>
              {dryRun
                ? "Запустить предпросмотр? Файлы перемещены НЕ будут."
                : `Переместить дубли сессий в карантин? ${selectedPhones.size > 0 ? `Выбрано телефонов: ${selectedPhones.size}.` : "Все eligible сессии."}`}
            </p>
            <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
              <button
                onClick={() => setConfirmOpen(false)}
                style={{
                  padding: "8px 16px",
                  background: "transparent",
                  border: "1px solid #333",
                  borderRadius: "6px",
                  color: "#888",
                  cursor: "pointer",
                  fontFamily: "inherit",
                  fontSize: "13px",
                }}
              >
                Отмена
              </button>
              <button
                onClick={() =>
                  handleQuarantine(
                    selectedPhones.size > 0 ? Array.from(selectedPhones) : undefined
                  )
                }
                disabled={quarantineLoading}
                style={{
                  padding: "8px 16px",
                  background: dryRun ? "#00ff8822" : "#ff880022",
                  border: `1px solid ${dryRun ? "#00ff8844" : "#ff880044"}`,
                  borderRadius: "6px",
                  color: dryRun ? "#00ff88" : "#ff8800",
                  cursor: quarantineLoading ? "not-allowed" : "pointer",
                  fontFamily: "inherit",
                  fontSize: "13px",
                }}
              >
                {quarantineLoading ? "Выполняется…" : dryRun ? "Предпросмотр" : "Переместить"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* CSS animation */}
      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

const thStyle: React.CSSProperties = {
  padding: "10px 14px",
  textAlign: "left",
  color: "#555",
  fontSize: "11px",
  fontFamily: "'JetBrains Mono', monospace",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  whiteSpace: "nowrap",
  fontWeight: 600,
};

const tdStyle: React.CSSProperties = {
  padding: "10px 14px",
  color: "#a0a0b0",
  fontSize: "12px",
  verticalAlign: "middle",
};
