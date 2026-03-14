import { useEffect, useRef, useState, useCallback } from "react";

interface LogEntry {
  workspace_id: number;
  module: string;
  action: string;
  status: string;
  detail: string | null;
  account_id: number | null;
  ts: string;
}

const MAX_ENTRIES = 200;

const STATUS_COLORS: Record<string, string> = {
  success: "#00ff88",
  error: "#ff4444",
  warning: "#ffaa00",
};

const MODULES = [
  "warmup",
  "farm",
  "parser",
  "reactions",
  "chatting",
  "dialogs",
  "packaging",
];

interface Props {
  workspaceId: number | string;
  moduleFilter?: string;
  onModuleFilterChange?: (module: string) => void;
  compact?: boolean;
}

export function OperationLogPanel({
  workspaceId,
  moduleFilter,
  onModuleFilterChange,
  compact = false,
}: Props) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const host = window.location.host;
    let url = `${proto}://${host}/v1/ws/logs?workspace_id=${workspaceId}`;
    if (moduleFilter) url += `&module=${moduleFilter}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onclose = () => {
      setConnected(false);
      reconnectTimer.current = setTimeout(connect, 3000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (event) => {
      try {
        const data: LogEntry = JSON.parse(event.data);
        setLogs((prev) => {
          const next = [data, ...prev];
          return next.length > MAX_ENTRIES ? next.slice(0, MAX_ENTRIES) : next;
        });
      } catch {
        /* ignore */
      }
    };
  }, [workspaceId, moduleFilter]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return (
    <div
      style={{
        background: "#111",
        border: "1px solid #222",
        borderRadius: 8,
        padding: compact ? 8 : 16,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: compact ? 11 : 12,
        height: compact ? 300 : "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 8,
          flexShrink: 0,
        }}
      >
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: connected ? "#00ff88" : "#ff4444",
            display: "inline-block",
          }}
        />
        <span style={{ color: "#888", fontSize: 11 }}>
          {connected ? "Live" : "Reconnecting..."}
        </span>
        {onModuleFilterChange && (
          <select
            value={moduleFilter || ""}
            onChange={(e) => onModuleFilterChange(e.target.value)}
            style={{
              background: "#1a1a1a",
              color: "#ccc",
              border: "1px solid #333",
              borderRadius: 4,
              padding: "2px 6px",
              fontSize: 11,
              marginLeft: "auto",
            }}
          >
            <option value="">Все модули</option>
            {MODULES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        )}
        <span style={{ color: "#555", fontSize: 10, marginLeft: "auto" }}>
          {logs.length} записей
        </span>
      </div>

      {/* Log list */}
      <div
        ref={containerRef}
        style={{
          flex: 1,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        {logs.length === 0 && (
          <div style={{ color: "#555", textAlign: "center", padding: 24 }}>
            Ожидание событий...
          </div>
        )}
        {logs.map((entry, i) => {
          const color = STATUS_COLORS[entry.status] || "#888";
          const time = entry.ts
            ? new Date(entry.ts).toLocaleTimeString("ru-RU")
            : "";
          return (
            <div
              key={`${entry.ts}-${i}`}
              style={{
                display: "flex",
                gap: 8,
                padding: "2px 4px",
                borderLeft: `3px solid ${color}`,
                alignItems: "baseline",
              }}
            >
              <span style={{ color: "#555", minWidth: 60 }}>{time}</span>
              <span style={{ color: "#888", minWidth: 70 }}>
                [{entry.module}]
              </span>
              <span style={{ color }}>{entry.action}</span>
              {entry.account_id && (
                <span style={{ color: "#555" }}>acc:{entry.account_id}</span>
              )}
              {entry.detail && (
                <span style={{ color: "#666", flex: 1 }}>{entry.detail}</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
