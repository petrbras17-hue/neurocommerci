import React, { Suspense } from "react";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { useAuth } from "./auth";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { LoginPage } from "./pages/LoginPage";
import { ProfileCompletionPage } from "./pages/ProfileCompletionPage";
import { DashboardPage } from "./pages/DashboardPage";
import { AccountsPage } from "./pages/AccountsPage";
import { AccountActivityPage } from "./pages/AccountActivityPage";
import { AssistantPage } from "./pages/AssistantPage";
import { ContextPage } from "./pages/ContextPage";
import { CreativePage } from "./pages/CreativePage";
import { CampaignsPage } from "./pages/CampaignsPage";
import { CampaignDetailPage } from "./pages/CampaignDetailPage";
import { OnboardingPage } from "./pages/OnboardingPage";
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
import { AdminPage } from "./pages/AdminPage";
import { PlatformHealthPage } from "./pages/PlatformHealthPage";
import { CommentDashboardPage } from "./pages/CommentDashboardPage";
import { SessionTopologyPage } from "./pages/SessionTopologyPage";
import { FarmMonitorPage } from "./pages/FarmMonitorPage";
import { AgencyDashboardPage } from "./pages/AgencyDashboardPage";
import OfflinePage from "./pages/OfflinePage";
import { PWAInstallPrompt } from "./components/PWAInstallPrompt";

const ChannelMapPage = React.lazy(() => import("./pages/ChannelMapPageV2"));

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
        <Route path="/offline" element={<OfflinePage />} />
        <Route path="/complete-profile" element={<ProfileCompletionPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/accounts" element={<AccountsPage />} />
            <Route path="/accounts/:id/activity" element={<AccountActivityPage />} />
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
            <Route path="/channel-map" element={<Suspense fallback={<div className="loading-screen">Загружаем Channel Map…</div>}><ChannelMapPage /></Suspense>} />
            <Route path="/parser" element={<ParserPage />} />
            <Route path="/profiles" element={<ProfilesPage />} />
            <Route path="/campaigns" element={<CampaignsPage />} />
            <Route path="/campaigns/:id" element={<CampaignDetailPage />} />
            <Route path="/onboarding" element={<OnboardingPage />} />
            <Route path="/analytics" element={<AnalyticsPage />} />
            <Route path="/admin" element={<AdminPage />} />
            <Route path="/platform-health" element={<PlatformHealthPage />} />
            <Route path="/comments" element={<CommentDashboardPage />} />
            <Route path="/topology" element={<SessionTopologyPage />} />
            <Route path="/farm-monitor" element={<FarmMonitorPage />} />
            <Route path="/billing" element={<BillingPage />} />
            <Route path="/agency" element={<AgencyDashboardPage />} />
            <Route
              path="/settings"
              element={<SettingsPage />}
            />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Route>
        </Route>
      </Routes>
      <PWAInstallPrompt />
    </ErrorBoundary>
  );
}
