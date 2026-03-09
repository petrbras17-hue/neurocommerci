import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../auth";

declare global {
  interface Window {
    __ncTelegramAuth?: (user: Record<string, unknown>) => void;
  }
}

type WidgetConfig = {
  bot_username: string;
  auth_domain: string;
  origin: string;
  max_age_seconds: number;
};

function widgetRequiresPublicDomain(config: WidgetConfig): boolean {
  try {
    const url = new URL(config.origin);
    const hostname = url.hostname;
    const isLoopback = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
    return url.protocol !== "https:" || isLoopback;
  } catch {
    return true;
  }
}

async function fetchWidgetConfig(): Promise<WidgetConfig> {
  const response = await fetch("/auth/telegram/widget-config");
  if (!response.ok) {
    throw new Error("widget_config_unavailable");
  }
  return response.json();
}

export function TelegramLoginWidget() {
  const { verifyTelegram } = useAuth();
  const [config, setConfig] = useState<WidgetConfig | null>(null);
  const [error, setError] = useState<string>("");
  const widgetId = useMemo(() => `telegram-login-${Math.random().toString(36).slice(2)}`, []);

  useEffect(() => {
    let cancelled = false;
    void fetchWidgetConfig()
      .then((payload) => {
        if (!cancelled) {
          setConfig(payload);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!config) {
      return;
    }
    if (widgetRequiresPublicDomain(config)) {
      return;
    }

    window.__ncTelegramAuth = (user: Record<string, unknown>) => {
      void verifyTelegram(user).catch((err: Error) => {
        setError(err.message || "telegram_auth_failed");
      });
    };

    const root = document.getElementById(widgetId);
    if (!root) {
      return;
    }
    root.innerHTML = "";
    const script = document.createElement("script");
    script.async = true;
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.setAttribute("data-telegram-login", config.bot_username);
    script.setAttribute("data-size", "large");
    script.setAttribute("data-radius", "14");
    script.setAttribute("data-userpic", "false");
    script.setAttribute("data-request-access", "write");
    script.setAttribute("data-onauth", "window.__ncTelegramAuth(user)");
    root.appendChild(script);

    return () => {
      delete window.__ncTelegramAuth;
      root.innerHTML = "";
    };
  }, [config, verifyTelegram, widgetId]);

  if (error) {
    return <div className="widget-error">Ошибка Telegram Login Widget: {error}</div>;
  }
  if (!config) {
    return <div className="widget-loading">Подготавливаем Telegram Login Widget…</div>;
  }
  if (widgetRequiresPublicDomain(config)) {
    return (
      <div className="widget-loading">
        Telegram Login Widget появится на публичном HTTPS-домене после настройки BotFather `/setdomain`.
      </div>
    );
  }

  return <div id={widgetId} className="telegram-widget-slot" />;
}
