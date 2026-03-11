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
        <div className="eyebrow">NEURO COMMENTING</div>
        <h1>Вход в платформу</h1>
        <p className="auth-copy">
          Авторизуйтесь через Telegram для доступа к вашему рабочему пространству.
          После входа заполните email и название компании.
        </p>
        <TelegramLoginWidget />
      </div>
    </div>
  );
}
