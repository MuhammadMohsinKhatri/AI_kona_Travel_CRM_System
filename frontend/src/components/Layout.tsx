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

/** Short label for the mobile top bar — the full product name is far too long
 *  to fit in a 375px header next to the hamburger. */
function currentTitle(pathname: string): string {
  const all = [...links, ...bottomLinks];
  const match = all
    .filter((l) => (l.to === "/" ? pathname === "/" : pathname.startsWith(l.to)))
    .sort((a, b) => b.to.length - a.to.length)[0];
  return match?.label ?? "Menu";
}

export default function Layout() {
  const { user, logout } = useAuth();
  const { pathname } = useLocation();
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(STORAGE_KEY) === "1"
  );
  // Off-canvas drawer state, used only on the mobile layout. Desktop ignores
  // it entirely (the sidebar is always in-flow there).
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  // Any navigation closes the drawer — otherwise it would sit open over the
  // page you just navigated to on a phone.
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  // Lock body scroll while the drawer is open so the page behind it doesn't
  // scroll under the finger.
  useEffect(() => {
    document.body.style.overflow = mobileOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileOpen]);

  const isWide = WIDE_ROUTES.some((r) => pathname.startsWith(r));

  const navLink = (l: { to: string; label: string; icon: string; end?: boolean }) => (
    <NavLink
      key={l.to}
      to={l.to}
      end={l.end}
      title={l.label}
      onClick={() => setMobileOpen(false)}
      className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}
    >
      <span className="nav-icon">{l.icon}</span>
      <span className="nav-label">{l.label}</span>
    </NavLink>
  );

  return (
    <div
      className={
        "layout" + (collapsed ? " collapsed" : "") + (mobileOpen ? " drawer-open" : "")
      }
    >
      {/* Mobile-only top bar. Hidden on desktop via CSS. It carries the
          hamburger (the only way to reach the nav on a phone) and the current
          page's short name so you always know where you are. */}
      <header className="mobile-bar">
        <button
          className="hamburger"
          onClick={() => setMobileOpen((o) => !o)}
          aria-label={mobileOpen ? "Close menu" : "Open menu"}
          aria-expanded={mobileOpen}
        >
          <span className="hamburger-icon">{mobileOpen ? "✕" : "☰"}</span>
        </button>
        <div className="mobile-title">
          <span className="dot" />
          <span>{currentTitle(pathname)}</span>
        </div>
      </header>

      {/* Backdrop behind the open drawer — tapping it closes the menu. */}
      <div
        className="drawer-backdrop"
        onClick={() => setMobileOpen(false)}
        aria-hidden="true"
      />

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
