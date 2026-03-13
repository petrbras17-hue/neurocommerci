import React, { useState, useEffect } from "react";

interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

export const PWAInstallPrompt: React.FC = () => {
  const [showPrompt, setShowPrompt] = useState(false);
  const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [isIOS, setIsIOS] = useState(false);

  useEffect(() => {
    // Don't show if already in standalone mode
    if (window.matchMedia("(display-mode: standalone)").matches) return;
    if ((navigator as any).standalone === true) return;

    // Check dismissal
    const dismissed = localStorage.getItem("pwa-install-dismissed");
    if (dismissed) {
      const dismissedAt = parseInt(dismissed, 10);
      if (Date.now() - dismissedAt < 7 * 24 * 60 * 60 * 1000) return;
    }

    // Detect iOS
    const ua = navigator.userAgent;
    const ios =
      /iPad|iPhone|iPod/.test(ua) ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    setIsIOS(ios);

    if (ios) {
      // iOS doesn't support beforeinstallprompt — show manual instructions
      setShowPrompt(true);
      return;
    }

    // Android/Chrome install prompt
    const handler = (e: Event) => {
      e.preventDefault();
      setDeferredPrompt(e as BeforeInstallPromptEvent);
      setShowPrompt(true);
    };
    window.addEventListener("beforeinstallprompt", handler);
    return () => window.removeEventListener("beforeinstallprompt", handler);
  }, []);

  const handleInstall = async () => {
    if (deferredPrompt) {
      await deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      if (choice.outcome === "accepted") {
        setShowPrompt(false);
      }
      setDeferredPrompt(null);
    }
  };

  const handleDismiss = () => {
    localStorage.setItem("pwa-install-dismissed", String(Date.now()));
    setShowPrompt(false);
  };

  if (!showPrompt) return null;

  return (
    <div
      style={{
        position: "fixed",
        bottom: 0,
        left: 0,
        right: 0,
        zIndex: 9999,
        background:
          "linear-gradient(180deg, rgba(10,10,11,0.95) 0%, #0a0a0b 100%)",
        borderTop: "1px solid #00ff8833",
        padding: "16px 20px",
        paddingBottom: "max(16px, env(safe-area-inset-bottom))",
        display: "flex",
        alignItems: "center",
        gap: "12px",
        backdropFilter: "blur(20px)",
        WebkitBackdropFilter: "blur(20px)",
      }}
    >
      <div style={{ flex: 1 }}>
        <div
          style={{
            color: "#00ff88",
            fontWeight: 700,
            fontSize: "14px",
            marginBottom: "4px",
          }}
        >
          Установить NEURO
        </div>
        <div
          style={{
            color: "#888",
            fontSize: "12px",
            lineHeight: "1.4",
          }}
        >
          {isIOS
            ? "Нажмите «Поделиться» → «На экран Домой»"
            : "Добавить на главный экран для быстрого доступа"}
        </div>
      </div>
      {!isIOS && (
        <button
          onClick={handleInstall}
          style={{
            background: "#00ff88",
            color: "#0a0a0b",
            border: "none",
            borderRadius: "8px",
            padding: "10px 20px",
            fontWeight: 700,
            fontSize: "13px",
            cursor: "pointer",
            whiteSpace: "nowrap",
          }}
        >
          Установить
        </button>
      )}
      <button
        onClick={handleDismiss}
        style={{
          background: "none",
          border: "none",
          color: "#666",
          fontSize: "20px",
          cursor: "pointer",
          padding: "4px",
          lineHeight: 1,
        }}
      >
        ×
      </button>
    </div>
  );
};
