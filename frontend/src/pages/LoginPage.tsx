import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Navigate } from "react-router-dom";
import { TelegramLoginWidget } from "../components/TelegramLoginWidget";
import { useAuth } from "../auth";

type Tab = "login" | "register";

export function LoginPage() {
  const auth = useAuth();
  const [tab, setTab] = useState<Tab>("login");

  // Login form state
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);

  // Register form state
  const [regFirstName, setRegFirstName] = useState("");
  const [regEmail, setRegEmail] = useState("");
  const [regCompany, setRegCompany] = useState("");
  const [regPassword, setRegPassword] = useState("");
  const [regPasswordConfirm, setRegPasswordConfirm] = useState("");
  const [regError, setRegError] = useState("");
  const [regBusy, setRegBusy] = useState(false);

  // Bot auth state
  const [botAuthBusy, setBotAuthBusy] = useState(false);
  const [botAuthCode, setBotAuthCode] = useState<string | null>(null);
  const [botAuthError, setBotAuthError] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  if (auth.status === "authenticated") {
    return <Navigate to="/dashboard" replace />;
  }
  if (auth.status === "profile_incomplete") {
    return <Navigate to="/complete-profile" replace />;
  }

  const handleLogin = async (event: FormEvent) => {
    event.preventDefault();
    setLoginError("");
    setLoginBusy(true);
    try {
      await auth.loginWithEmail(loginEmail.trim(), loginPassword);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "login_failed";
      if (msg === "use_telegram_login") {
        setLoginError("Этот аккаунт создан через Telegram. Войдите через кнопку ниже.");
      } else if (msg === "invalid_credentials" || msg.startsWith("http_401") || msg.startsWith("http_403")) {
        setLoginError("Неверный email или пароль.");
      } else {
        setLoginError(msg);
      }
    } finally {
      setLoginBusy(false);
    }
  };

  const handleRegister = async (event: FormEvent) => {
    event.preventDefault();
    setRegError("");

    if (regPassword.length < 8) {
      setRegError("Пароль должен содержать минимум 8 символов.");
      return;
    }
    if (regPassword !== regPasswordConfirm) {
      setRegError("Пароли не совпадают.");
      return;
    }

    setRegBusy(true);
    try {
      await auth.registerWithEmail(regEmail.trim(), regPassword, regFirstName.trim(), regCompany.trim());
    } catch (err) {
      const msg = err instanceof Error ? err.message : "register_failed";
      if (msg === "email_already_registered" || msg.startsWith("http_422")) {
        setRegError("Этот email уже зарегистрирован. Войдите или используйте другой адрес.");
      } else {
        setRegError(msg);
      }
    } finally {
      setRegBusy(false);
    }
  };

  const handleBotAuth = async () => {
    setBotAuthError("");
    setBotAuthBusy(true);
    try {
      const result = await auth.startBotAuth();
      setBotAuthCode(result.code);
      // Open bot deep link in new tab
      window.open(result.deep_link, "_blank");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "bot_auth_failed";
      setBotAuthError(msg);
      setBotAuthBusy(false);
    }
  };

  // Poll for bot auth confirmation
  // eslint-disable-next-line react-hooks/rules-of-hooks
  useEffect(() => {
    if (!botAuthCode) {
      return;
    }
    const poll = setInterval(() => {
      void auth.checkBotAuth(botAuthCode).then((bundle) => {
        if (bundle) {
          // Auth successful — navigation will happen via auth state change
          clearInterval(poll);
          setBotAuthBusy(false);
          setBotAuthCode(null);
        }
      }).catch(() => {
        // Silently retry
      });
    }, 2000);
    pollRef.current = poll;

    // Stop polling after 5 minutes
    const timeout = setTimeout(() => {
      clearInterval(poll);
      setBotAuthBusy(false);
      setBotAuthCode(null);
      setBotAuthError("Время ожидания истекло. Попробуйте снова.");
    }, 300_000);

    return () => {
      clearInterval(poll);
      clearTimeout(timeout);
    };
  }, [botAuthCode, auth]);

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="eyebrow">NEURO COMMENTING</div>
        <h1>{tab === "login" ? "Вход в платформу" : "Регистрация"}</h1>

        <div className="auth-tabs">
          <button
            type="button"
            className={tab === "login" ? "auth-tab auth-tab--active" : "auth-tab"}
            onClick={() => { setTab("login"); setLoginError(""); }}
          >
            Вход
          </button>
          <button
            type="button"
            className={tab === "register" ? "auth-tab auth-tab--active" : "auth-tab"}
            onClick={() => { setTab("register"); setRegError(""); }}
          >
            Регистрация
          </button>
        </div>

        {tab === "login" ? (
          <form onSubmit={(e) => void handleLogin(e)} style={{ display: "grid", gap: "12px" }}>
            <label className="field">
              <span>Email</span>
              <input
                className="auth-input"
                type="email"
                required
                autoComplete="email"
                value={loginEmail}
                onChange={(e) => setLoginEmail(e.target.value)}
                placeholder="you@company.com"
              />
            </label>
            <label className="field">
              <span>Пароль</span>
              <input
                className="auth-input"
                type="password"
                required
                autoComplete="current-password"
                value={loginPassword}
                onChange={(e) => setLoginPassword(e.target.value)}
                placeholder="••••••••"
              />
            </label>
            {loginError ? <div className="auth-error">{loginError}</div> : null}
            <button className="auth-submit" type="submit" disabled={loginBusy}>
              {loginBusy ? "Входим…" : "Войти"}
            </button>
          </form>
        ) : (
          <form onSubmit={(e) => void handleRegister(e)} style={{ display: "grid", gap: "12px" }}>
            <label className="field">
              <span>Имя</span>
              <input
                className="auth-input"
                type="text"
                required
                autoComplete="given-name"
                value={regFirstName}
                onChange={(e) => setRegFirstName(e.target.value)}
                placeholder="Иван"
              />
            </label>
            <label className="field">
              <span>Email</span>
              <input
                className="auth-input"
                type="email"
                required
                autoComplete="email"
                value={regEmail}
                onChange={(e) => setRegEmail(e.target.value)}
                placeholder="you@company.com"
              />
            </label>
            <label className="field">
              <span>Компания</span>
              <input
                className="auth-input"
                type="text"
                required
                autoComplete="organization"
                value={regCompany}
                onChange={(e) => setRegCompany(e.target.value)}
                placeholder="Название компании"
              />
            </label>
            <label className="field">
              <span>Пароль</span>
              <input
                className="auth-input"
                type="password"
                required
                autoComplete="new-password"
                minLength={8}
                value={regPassword}
                onChange={(e) => setRegPassword(e.target.value)}
                placeholder="Минимум 8 символов"
              />
            </label>
            <label className="field">
              <span>Повторите пароль</span>
              <input
                className="auth-input"
                type="password"
                required
                autoComplete="new-password"
                value={regPasswordConfirm}
                onChange={(e) => setRegPasswordConfirm(e.target.value)}
                placeholder="••••••••"
              />
            </label>
            {regError ? <div className="auth-error">{regError}</div> : null}
            <button className="auth-submit" type="submit" disabled={regBusy}>
              {regBusy ? "Регистрируем…" : "Создать аккаунт"}
            </button>
          </form>
        )}

        <div className="auth-divider"><span>или</span></div>

        <div className="auth-social-row">
          <button
            className="auth-social-btn auth-social-btn--bot"
            type="button"
            disabled={botAuthBusy}
            onClick={() => void handleBotAuth()}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 00-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.74-.55 2.92-1.27 4.86-2.11 5.83-2.51 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .38z"/>
            </svg>
            {botAuthBusy
              ? (botAuthCode ? "Ожидаем…" : "…")
              : "Telegram бот"
            }
          </button>
          <div className="auth-social-btn auth-social-btn--widget">
            <TelegramLoginWidget />
          </div>
        </div>
        {botAuthError ? <div className="auth-error" style={{ marginTop: 8 }}>{botAuthError}</div> : null}
        {botAuthCode ? (
          <div className="bot-auth-hint">
            Нажмите START в боте @dartvpn_neurocom_bot, чтобы завершить вход.
            <br />Авторизация произойдёт автоматически.
          </div>
        ) : null}
      </div>
    </div>
  );
}
