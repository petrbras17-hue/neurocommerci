import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { useAuth } from "./auth";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { LoginPage } from "./pages/LoginPage";
import { ProfileCompletionPage } from "./pages/ProfileCompletionPage";
import { DashboardPage } from "./pages/DashboardPage";
import { AccountsPage } from "./pages/AccountsPage";
import { AssistantPage } from "./pages/AssistantPage";
import { ContextPage } from "./pages/ContextPage";
import { CreativePage } from "./pages/CreativePage";
import { ChannelMapPage } from "./pages/ChannelMapPage";
import { CampaignsPage } from "./pages/CampaignsPage";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { FarmPage } from "./pages/FarmPage";
import { ParserPage } from "./pages/ParserPage";
import { WarmupPage } from "./pages/WarmupPage";
import { HealthPage } from "./pages/HealthPage";
import { ReactionsPage } from "./pages/ReactionsPage";
import { ChattingPage } from "./pages/ChattingPage";
import { DialogsPage } from "./pages/DialogsPage";
import { UserParserPage } from "./pages/UserParserPage";
import { FoldersPage } from "./pages/FoldersPage";
import { ProfilesPage } from "./pages/ProfilesPage";
import { BillingPage } from "./pages/BillingPage";
import { ProxiesPage } from "./pages/ProxiesPage";
import { SettingsPage } from "./pages/SettingsPage";

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
  const location = useLocation();
  return (
    <ErrorBoundary locationKey={location.pathname}>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/complete-profile" element={<ProfileCompletionPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/accounts" element={<AccountsPage />} />
            <Route path="/proxies" element={<ProxiesPage />} />
            <Route path="/assistant" element={<AssistantPage />} />
            <Route path="/context" element={<ContextPage />} />
            <Route path="/creative" element={<CreativePage />} />
            <Route path="/farm" element={<FarmPage />} />
            <Route path="/warmup" element={<WarmupPage />} />
            <Route path="/health" element={<HealthPage />} />
            <Route path="/reactions" element={<ReactionsPage />} />
            <Route path="/chatting" element={<ChattingPage />} />
            <Route path="/dialogs" element={<DialogsPage />} />
            <Route path="/user-parser" element={<UserParserPage />} />
            <Route path="/folders" element={<FoldersPage />} />
            <Route path="/channel-map" element={<ChannelMapPage />} />
            <Route path="/parser" element={<ParserPage />} />
            <Route path="/profiles" element={<ProfilesPage />} />
            <Route path="/campaigns" element={<CampaignsPage />} />
            <Route path="/analytics" element={<AnalyticsPage />} />
            <Route path="/billing" element={<BillingPage />} />
            <Route
              path="/settings"
              element={<SettingsPage />}
            />
          </Route>
        </Route>
      </Routes>
    </ErrorBoundary>
  );
}
