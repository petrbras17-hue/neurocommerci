import { useNavigate } from "react-router-dom";

export function PlaceholderPage({ title, description }: { title: string; description?: string }) {
  const navigate = useNavigate();
  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      minHeight: "60vh",
      gap: 20,
      textAlign: "center",
    }}>
      <div style={{
        width: 64,
        height: 64,
        borderRadius: 16,
        background: "var(--surface-2, #1c1c1f)",
        border: "1px solid var(--border, #2a2a2e)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 28,
      }}>
        🚀
      </div>
      <div>
        <h2 style={{ fontSize: 22, fontWeight: 600, marginBottom: 8 }}>{title}</h2>
        <p style={{ color: "var(--muted)", maxWidth: 440, lineHeight: 1.6 }}>
          {description || "Этот модуль находится в разработке и скоро будет доступен."}
        </p>
      </div>
      <div style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        padding: "8px 16px",
        borderRadius: 999,
        background: "var(--accent-glow, rgba(0,255,136,0.15))",
        color: "var(--accent, #00ff88)",
        fontSize: 12,
        fontWeight: 600,
        letterSpacing: "0.05em",
        textTransform: "uppercase",
      }}>
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: "currentColor", animation: "pulse 2s infinite" }} />
        В разработке
      </div>
      <button className="ghost-button" onClick={() => navigate("/dashboard")} style={{ marginTop: 8 }}>
        ← Вернуться на главную
      </button>
    </div>
  );
}
