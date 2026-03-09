import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { useAuth } from "./auth";
import { LoginPage } from "./pages/LoginPage";
import { ProfileCompletionPage } from "./pages/ProfileCompletionPage";
import { DashboardPage } from "./pages/DashboardPage";
import { AccountsPage } from "./pages/AccountsPage";
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
          <Route
            path="/campaigns"
            element={<PlaceholderPage title="Campaigns" description="Campaign entities и approval flows придут в Sprint 5." />}
          />
          <Route
            path="/parser"
            element={<PlaceholderPage title="Parser" description="Parser dashboard и saved searches будут развиты в Sprint 5." />}
          />
          <Route
            path="/analytics"
            element={<PlaceholderPage title="Analytics" description="Usage dashboards и activation metrics придут в Sprint 6." />}
          />
          <Route
            path="/billing"
            element={<PlaceholderPage title="Billing" description="Stripe/YooKassa и планы запускаются в Sprint 4." />}
          />
          <Route
            path="/settings"
            element={<PlaceholderPage title="Settings" description="Workspace settings и повторный onboarding будут расширены в следующих спринтах." />}
          />
        </Route>
      </Route>
    </Routes>
  );
}
