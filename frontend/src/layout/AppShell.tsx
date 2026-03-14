import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { useAuth, isPlatformAdmin } from "../auth";
import { AdminModeToggle, useAdminMode } from "../components/admin/AdminModeToggle";
import {
  LayoutDashboard,
  Users,
  Server,
  Bot,
  Flame,
  HeartPulse,
  Sparkles,
  MessageCircle,
  Inbox,
  MessageSquare,
  MessagesSquare,
  Map,
  Search,
  UserSearch,
  Megaphone,
  Rocket,
  UserCog,
  FolderOpen,
  Brain,
  FileText,
  Palette,
  BarChart3,
  CreditCard,
  Settings,
  Shield,
  Activity,
  Menu,
  X,
  LogOut,
  MonitorCheck,
  Building2,
  Upload,
  Plug,
  ClipboardList,
  ShieldCheck,
  ScrollText,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}

interface NavGroup {
  section: string;
  items: NavItem[];
}

const ADMIN_NAV_GROUPS: NavGroup[] = [
  {
    section: "🔴 ADMIN",
    items: [
      { to: "/admin-dashboard", label: "Command Center", icon: ShieldCheck },
    ],
  },
  {
    section: "Onboarding",
    items: [
      { to: "/admin-onboarding", label: "Загрузить аккаунт", icon: Upload },
      { to: "/admin-proxies", label: "Менеджер прокси", icon: Plug },
      { to: "/admin-packaging", label: "Packaging", icon: UserCog },
      { to: "/admin-mass-packaging", label: "Mass Packaging", icon: Users },
    ],
  },
  {
    section: "Мониторинг",
    items: [
      { to: "/admin-ops-log", label: "Лог операций", icon: ClipboardList },
      { to: "/admin-warmup", label: "Warmup v2", icon: Flame },
      { to: "/admin-logs", label: "Live Logs", icon: ScrollText },
      { to: "/admin-commenting", label: "Commenting v2", icon: MessageSquare },
      { to: "/admin-chatting", label: "Chatting v2", icon: MessageCircle },
      { to: "/admin-inbox", label: "Unified Inbox", icon: Inbox },
      { to: "/admin-parser", label: "Parser v2", icon: Search },
      { to: "/admin-reactions", label: "Reactions v2", icon: HeartPulse },
      { to: "/admin-monitoring", label: "Monitoring", icon: Activity },
      { to: "/admin-farm-launch", label: "Farm Launch", icon: Rocket },
      { to: "/admin-antifraud", label: "Anti-Fraud", icon: Shield },
    ],
  },
];

const NAV_GROUPS: NavGroup[] = [
  {
    section: "Обзор",
    items: [
      { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
    ],
  },
  {
    section: "Аккаунты",
    items: [
      { to: "/accounts", label: "Аккаунты", icon: Users },
      { to: "/proxies", label: "Прокси", icon: Server },
      { to: "/farm", label: "Фермы", icon: Bot },
      { to: "/farm-monitor", label: "Мониторинг", icon: MonitorCheck },
      { to: "/warmup", label: "Прогрев", icon: Flame },
      { to: "/health", label: "Здоровье", icon: HeartPulse },
    ],
  },
  {
    section: "Контент",
    items: [
      { to: "/comments", label: "Комментарии", icon: MessageSquare },
      { to: "/reactions", label: "Реакции", icon: Sparkles },
      { to: "/chatting", label: "Нейрочаттинг", icon: MessagesSquare },
      { to: "/dialogs", label: "Нейродиалоги", icon: MessagesSquare },
    ],
  },
  {
    section: "Каналы",
    items: [
      { to: "/channel-map", label: "Карта каналов", icon: Map },
      { to: "/parser", label: "Парсер каналов", icon: Search },
      { to: "/user-parser", label: "Парсер юзеров", icon: UserSearch },
    ],
  },
  {
    section: "Маркетинг",
    items: [
      { to: "/onboarding", label: "Запустить кампанию", icon: Rocket },
      { to: "/campaigns", label: "Кампании", icon: Megaphone },
      { to: "/profiles", label: "Фабрика профилей", icon: UserCog },
      { to: "/folders", label: "Папки", icon: FolderOpen },
    ],
  },
  {
    section: "ИИ",
    items: [
      { to: "/assistant", label: "ИИ-ассистент", icon: Brain },
      { to: "/context", label: "Контекст бизнеса", icon: FileText },
      { to: "/creative", label: "Черновики и креатив", icon: Palette },
    ],
  },
  {
    section: "Агентство",
    items: [
      { to: "/agency", label: "Агентство", icon: Building2 },
    ],
  },
  {
    section: "Система",
    items: [
      { to: "/analytics", label: "Аналитика", icon: BarChart3 },
      { to: "/platform-health", label: "Platform Health", icon: Activity },
      { to: "/admin", label: "Admin", icon: Shield },
      { to: "/billing", label: "Биллинг", icon: CreditCard },
      { to: "/settings", label: "Настройки", icon: Settings },
    ],
  },
];

const pageTransition = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -8 },
  transition: { duration: 0.2, ease: [0.16, 1, 0.3, 1] as const },
};

export function AppShell() {
  const { profile, logout } = useAuth();
  const location = useLocation();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [adminMode] = useAdminMode();
  const isAdmin = isPlatformAdmin(profile);

  const workspaceName = String(
    profile?.workspace?.name || "Workspace"
  );
  const userName = String(
    profile?.user?.first_name ||
      profile?.user?.telegram_username ||
      "User"
  );

  const closeSidebar = () => setSidebarOpen(false);

  const navGroups = adminMode === "admin" && isAdmin
    ? [...ADMIN_NAV_GROUPS, ...NAV_GROUPS]
    : NAV_GROUPS;

  return (
    <div className="shell">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div className="mobile-overlay" onClick={closeSidebar} />
      )}

      {/* Sidebar */}
      <aside className={`sidebar${sidebarOpen ? " open" : ""}`}>
        <div className="brand-block">
          <div className="brand-mark">NC</div>
          <div className="brand-text">
            <strong>NEURO COMMENTING</strong>
            <div className="muted">Telegram AI Growth OS</div>
          </div>
        </div>

        {isAdmin && (
          <div style={{ padding: "8px 16px" }}>
            <AdminModeToggle />
          </div>
        )}

        <nav>
          {navGroups.map((group) => (
            <div className="nav-section" key={group.section}>
              <div className="nav-section-label">{group.section}</div>
              <div className="nav-list">
                {group.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    onClick={closeSidebar}
                    className={({ isActive }) =>
                      isActive ? "nav-link active" : "nav-link"
                    }
                  >
                    <item.icon className="nav-icon" size={18} />
                    <span className="nav-link-label">{item.label}</span>
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        <div className="sidebar-spacer" />
      </aside>

      {/* Main content */}
      <div className="shell-main">
        <header className="topbar">
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <button
              className="mobile-menu-btn"
              type="button"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              aria-label="Toggle menu"
            >
              {sidebarOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
            <div>
              <div className="topbar-label">Пространство</div>
              <div className="topbar-title">{workspaceName}</div>
            </div>
          </div>
          <div className="topbar-actions">
            <div className="avatar-pill">
              {userName.slice(0, 1).toUpperCase()}
            </div>
            <button
              className="ghost-button"
              type="button"
              onClick={() => void logout()}
              style={{ display: "flex", alignItems: "center", gap: 6 }}
            >
              <LogOut size={14} />
              <span>Выйти</span>
            </button>
          </div>
        </header>

        <main className="content">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={pageTransition.initial}
              animate={pageTransition.animate}
              exit={pageTransition.exit}
              transition={pageTransition.transition}
            >
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}
