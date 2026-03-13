import React from "react";

const OfflinePage: React.FC = () => (
  <div
    style={{
      minHeight: "100vh",
      background: "#0a0a0b",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      padding: "20px",
      fontFamily: "'Geist Sans', system-ui, sans-serif",
    }}
  >
    <div
      style={{
        fontSize: "64px",
        marginBottom: "24px",
        opacity: 0.5,
      }}
    >
      &#x26A1;
    </div>
    <h1
      style={{
        color: "#00ff88",
        fontSize: "28px",
        fontWeight: 800,
        marginBottom: "12px",
        textAlign: "center",
      }}
    >
      Нет подключения
    </h1>
    <p
      style={{
        color: "#666",
        fontSize: "16px",
        textAlign: "center",
        maxWidth: "320px",
        lineHeight: "1.5",
        marginBottom: "32px",
      }}
    >
      Проверьте интернет-соединение и попробуйте снова
    </p>
    <button
      onClick={() => window.location.reload()}
      style={{
        background: "transparent",
        border: "1px solid #00ff8844",
        color: "#00ff88",
        borderRadius: "8px",
        padding: "12px 32px",
        fontSize: "15px",
        fontWeight: 600,
        cursor: "pointer",
      }}
    >
      Обновить
    </button>
  </div>
);

export default OfflinePage;
