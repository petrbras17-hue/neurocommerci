import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/accounts", label: "Accounts" },
  { to: "/campaigns", label: "Campaigns" },
  { to: "/parser", label: "Parser" },
  { to: "/analytics", label: "Analytics" },
  { to: "/billing", label: "Billing" },
  { to: "/settings", label: "Settings" }
];

export function AppShell() {
  const { profile, logout } = useAuth();
  const workspaceName = String(profile?.workspace?.name || "Workspace");
  const userName = String(profile?.user?.first_name || profile?.user?.telegram_username || "User");

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">NC</div>
          <div>
            <strong>NEURO COMMENTING</strong>
            <div className="muted">Telegram Growth OS</div>
          </div>
        </div>
        <nav className="nav-list">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="shell-main">
        <header className="topbar">
          <div>
            <div className="topbar-label">Workspace</div>
            <div className="topbar-title">{workspaceName}</div>
          </div>
          <div className="topbar-actions">
            <div className="avatar-pill">{userName.slice(0, 1).toUpperCase()}</div>
            <button className="ghost-button" type="button" onClick={() => void logout()}>
              Выйти
            </button>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
