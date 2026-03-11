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
            <Route path="/assistant" element={<AssistantPage />} />
            <Route path="/context" element={<ContextPage />} />
            <Route path="/creative" element={<CreativePage />} />
            <Route path="/farm" element={<PlaceholderPage title="Фермы" description="Управление фермами комментинга — запуск, мониторинг потоков и автоматическая ротация аккаунтов." />} />
            <Route path="/warmup" element={<PlaceholderPage title="Прогрев" description="Автоматический прогрев аккаунтов перед запуском — имитация живого поведения." />} />
            <Route path="/health" element={<PlaceholderPage title="Здоровье" description="Мониторинг здоровья аккаунтов — scoring, карантин, автоматические апелляции." />} />
            <Route path="/reactions" element={<PlaceholderPage title="Реакции" description="Массовая расстановка реакций на посты в целевых каналах." />} />
            <Route path="/chatting" element={<PlaceholderPage title="Нейрочаттинг" description="ИИ-чаттинг в комментариях — контекстные ответы на посты." />} />
            <Route path="/dialogs" element={<PlaceholderPage title="Нейродиалоги" description="Симуляция живых диалогов между аккаунтами для прогрева." />} />
            <Route path="/user-parser" element={<PlaceholderPage title="Парсер юзеров" description="Парсинг аудитории каналов — сбор метаданных пользователей." />} />
            <Route path="/folders" element={<PlaceholderPage title="Папки" description="Управление Telegram-папками для организации каналов." />} />
            <Route path="/channel-map" element={<ChannelMapPage />} />
            <Route path="/parser" element={<PlaceholderPage title="Парсер каналов" description="Поиск и анализ каналов по ключевым словам — AI spam rating." />} />
            <Route path="/profiles" element={<PlaceholderPage title="Фабрика профилей" description="ИИ-генерация профилей — аватары, имена, биографии, каналы." />} />
            <Route path="/campaigns" element={<CampaignsPage />} />
            <Route path="/analytics" element={<AnalyticsPage />} />
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
    </ErrorBoundary>
  );
}
