import { useState, useEffect } from "react";
import { isPlatformAdmin, useAuth } from "../../auth";

const ADMIN_MODE_KEY = "nc_admin_mode";

export type AdminMode = "admin" | "client";

export function useAdminMode(): [AdminMode, (mode: AdminMode) => void] {
  const { profile } = useAuth();
  const isAdmin = isPlatformAdmin(profile);

  const [mode, setModeState] = useState<AdminMode>(() => {
    if (!isAdmin) return "client";
    const stored = localStorage.getItem(ADMIN_MODE_KEY);
    return stored === "admin" ? "admin" : "client";
  });

  const setMode = (newMode: AdminMode) => {
    if (!isAdmin) return;
    localStorage.setItem(ADMIN_MODE_KEY, newMode);
    setModeState(newMode);
  };

  useEffect(() => {
    if (!isAdmin) {
      setModeState("client");
    }
  }, [isAdmin]);

  return [mode, setMode];
}

export function AdminModeToggle() {
  const { profile } = useAuth();
  const isAdmin = isPlatformAdmin(profile);
  const [mode, setMode] = useAdminMode();

  if (!isAdmin) return null;

  return (
    <button
      type="button"
      onClick={() => setMode(mode === "admin" ? "client" : "admin")}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 12px",
        border: "1px solid",
        borderColor: mode === "admin" ? "#ff4444" : "var(--accent)",
        borderRadius: 6,
        background: mode === "admin" ? "rgba(255,68,68,0.1)" : "rgba(0,255,136,0.1)",
        color: mode === "admin" ? "#ff4444" : "var(--accent)",
        cursor: "pointer",
        fontSize: 12,
        fontWeight: 600,
        fontFamily: "'JetBrains Mono', monospace",
        letterSpacing: "0.05em",
        textTransform: "uppercase",
        transition: "all 0.2s ease",
        width: "100%",
        justifyContent: "center",
      }}
    >
      <span style={{
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: mode === "admin" ? "#ff4444" : "var(--accent)",
        boxShadow: `0 0 6px ${mode === "admin" ? "#ff4444" : "var(--accent)"}`,
      }} />
      {mode === "admin" ? "ADMIN" : "CLIENT"}
    </button>
  );
}
