import { useState, useCallback } from "react";
import { useAuth } from "../../auth";
import { OperationLogPanel } from "../../components/admin/OperationLogPanel";

export function LiveLogsPage() {
  const { profile } = useAuth();
  const workspaceId = profile?.workspace?.id as number | undefined;
  const [moduleFilter, setModuleFilter] = useState("");

  const handleCopyLogs = useCallback(() => {
    const container = document.querySelector("[data-log-container]");
    if (container) {
      navigator.clipboard.writeText(container.textContent || "");
    }
  }, []);

  return (
    <div
      style={{
        padding: 24,
        height: "calc(100vh - 64px)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <h1>Live Logs</h1>
        <button className="btn btn-ghost" onClick={handleCopyLogs}>
          Copy to clipboard
        </button>
      </div>

      <div style={{ flex: 1, minHeight: 0 }} data-log-container>
        {workspaceId ? (
          <OperationLogPanel
            workspaceId={workspaceId}
            moduleFilter={moduleFilter || undefined}
            onModuleFilterChange={setModuleFilter}
          />
        ) : (
          <div className="muted" style={{ padding: 40, textAlign: "center" }}>
            Workspace not loaded
          </div>
        )}
      </div>
    </div>
  );
}
