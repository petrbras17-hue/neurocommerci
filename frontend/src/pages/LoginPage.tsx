import { FormEvent, useState } from "react";
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

        <TelegramLoginWidget />
      </div>
    </div>
  );
}
