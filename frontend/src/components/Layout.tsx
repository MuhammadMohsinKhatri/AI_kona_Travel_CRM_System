import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

/** Nav labels are written for the people who actually read this dashboard —
 *  owners and office staff, not engineers. Icons double as the collapsed-rail
 *  labels, so every entry needs one. */
const links = [
  { to: "/", label: "Dashboard", icon: "📊", end: true },
  { to: "/events", label: "Events", icon: "📅" },
  { to: "/invoices", label: "Invoices", icon: "🧾" },
  { to: "/financials", label: "Event Financials", icon: "💰" },
  { to: "/alerts", label: "Needs Attention", icon: "⚠️" },
  { to: "/runs", label: "Automation Runs", icon: "⚙️" },
  { to: "/crm-activity", label: "KonaOS Change Log", icon: "📝" },
  { to: "/api-explorer", label: "API Explorer", icon: "🔌" },
];

const bottomLinks = [
  { to: "/settings", label: "Settings", icon: "⚙" },
  { to: "/guide", label: "Guide & Tutorials", icon: "📘" },
];

/** Pages whose content is a wide table benefit from the full window width —
 *  the default 1200px cap forces needless horizontal scrolling on them. */
const WIDE_ROUTES = ["/financials", "/crm-activity", "/runs"];

const STORAGE_KEY = "sidebar-collapsed";

export default function Layout() {
  const { user, logout } = useAuth();
  const { pathname } = useLocation();
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(STORAGE_KEY) === "1"
  );

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  const isWide = WIDE_ROUTES.some((r) => pathname.startsWith(r));

  const navLink = (l: { to: string; label: string; icon: string; end?: boolean }) => (
    <NavLink
      key={l.to}
      to={l.to}
      end={l.end}
      title={l.label}
      className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}
    >
      <span className="nav-icon">{l.icon}</span>
      <span className="nav-label">{l.label}</span>
    </NavLink>
  );

  return (
    <div className={"layout" + (collapsed ? " collapsed" : "")}>
      <aside className="sidebar">
        <div className="brand">
          <span className="dot" />
          <span className="brand-name">Conbyt AI Automation Financial System</span>
        </div>

        <button
          className="sidebar-toggle"
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? "Expand the menu" : "Collapse the menu"}
          aria-label={collapsed ? "Expand the menu" : "Collapse the menu"}
          aria-expanded={!collapsed}
        >
          <span className="chev">{collapsed ? "»" : "«"}</span>
          <span className="nav-label">Hide menu</span>
        </button>

        {links.map(navLink)}
        <div className="spacer" />
        {bottomLinks.map(navLink)}
        <div className="sidebar-user" title={user?.email}>
          {user?.email}
        </div>
        <button className="btn sign-out" onClick={logout} title="Sign out">
          <span className="nav-icon">⏻</span>
          <span className="nav-label">Sign out</span>
        </button>
      </aside>
      <main className={"content" + (isWide ? " wide" : "")}>
        <Outlet />
      </main>
    </div>
  );
}
