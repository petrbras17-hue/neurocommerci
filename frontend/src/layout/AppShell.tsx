import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { useAuth } from "../auth";
import {
  LayoutDashboard,
  Users,
  Server,
  Bot,
  Flame,
  HeartPulse,
  Sparkles,
  MessageSquare,
  MessagesSquare,
  Map,
  Search,
  UserSearch,
  Megaphone,
  UserCog,
  FolderOpen,
  Brain,
  FileText,
  Palette,
  BarChart3,
  CreditCard,
  Settings,
  Menu,
  X,
  LogOut,
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
      { to: "/warmup", label: "Прогрев", icon: Flame },
      { to: "/health", label: "Здоровье", icon: HeartPulse },
    ],
  },
  {
    section: "Контент",
    items: [
      { to: "/reactions", label: "Реакции", icon: Sparkles },
      { to: "/chatting", label: "Нейрочаттинг", icon: MessageSquare },
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
    section: "Система",
    items: [
      { to: "/analytics", label: "Аналитика", icon: BarChart3 },
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

  const workspaceName = String(
    profile?.workspace?.name || "Workspace"
  );
  const userName = String(
    profile?.user?.first_name ||
      profile?.user?.telegram_username ||
      "User"
  );

  const closeSidebar = () => setSidebarOpen(false);

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

        <nav>
          {NAV_GROUPS.map((group) => (
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
