import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { useAuth } from "./auth";
import { LoginPage } from "./pages/LoginPage";
import { ProfileCompletionPage } from "./pages/ProfileCompletionPage";
import { DashboardPage } from "./pages/DashboardPage";
import { AccountsPage } from "./pages/AccountsPage";
import { AssistantPage } from "./pages/AssistantPage";
import { ContextPage } from "./pages/ContextPage";
import { CreativePage } from "./pages/CreativePage";
import { PlaceholderPage } from "./pages/PlaceholderPage";

function ProtectedRoute() {
  const auth = useAuth();
  if (!auth.ready) {
    return <div className="loading-screen">Загружаем workspace…</div>;
  }
  if (auth.status === "profile_incomplete") {
    return <Navigate to="/complete-profile" replace />;
  }
  if (auth.status !== "authenticated") {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/complete-profile" element={<ProfileCompletionPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/assistant" element={<AssistantPage />} />
          <Route path="/context" element={<ContextPage />} />
          <Route path="/creative" element={<CreativePage />} />
          <Route
            path="/campaigns"
            element={<PlaceholderPage title="Кампании" description="Сущности кампаний и approval flows придут в следующем спринте роста." />}
          />
          <Route
            path="/parser"
            element={<PlaceholderPage title="Парсер" description="Parser dashboard, discovery и сохранённые поиски будут усилены после assistant layer." />}
          />
          <Route
            path="/analytics"
            element={<PlaceholderPage title="Аналитика" description="Usage dashboards и activation metrics будут расширены отдельным аналитическим спринтом." />}
          />
          <Route
            path="/billing"
            element={<PlaceholderPage title="Биллинг" description="Биллинг вынесен после assistant/value layer, когда продуктовая ценность уже доказана." />}
          />
          <Route
            path="/settings"
            element={<PlaceholderPage title="Настройки" description="Workspace settings, роли и повторный onboarding будут расширены после stabilisation sprint." />}
          />
        </Route>
      </Route>
    </Routes>
  );
}
