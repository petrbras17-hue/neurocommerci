import { Navigate } from "react-router-dom";
import { TelegramLoginWidget } from "../components/TelegramLoginWidget";
import { useAuth } from "../auth";

export function LoginPage() {
  const auth = useAuth();

  if (auth.status === "authenticated") {
    return <Navigate to="/dashboard" replace />;
  }
  if (auth.status === "profile_incomplete") {
    return <Navigate to="/complete-profile" replace />;
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="eyebrow">Sprint 3 • Telegram-first auth</div>
        <h1>Вход через Telegram</h1>
        <p className="auth-copy">
          Войдите через основной бот, затем заполните email и компанию. После этого появится ваш tenant,
          workspace и web onboarding для аккаунтов.
        </p>
        <TelegramLoginWidget />
      </div>
    </div>
  );
}
