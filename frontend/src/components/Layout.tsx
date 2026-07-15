import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const links = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/events", label: "Events" },
  { to: "/invoices", label: "Invoices" },
  { to: "/financials", label: "Financials" },
  { to: "/alerts", label: "Alerts" },
  { to: "/runs", label: "Pipeline Runs" },
  { to: "/api-explorer", label: "API Explorer" },
];

export default function Layout() {
  const { user, logout } = useAuth();
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <span className="dot" /> Kona Ice Ops
        </div>
        {links.map((l) => (
          <NavLink
            key={l.to}
            to={l.to}
            end={l.end}
            className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}
          >
            {l.label}
          </NavLink>
        ))}
        <div className="spacer" />
        <div className="muted" style={{ fontSize: 12, padding: "0 12px" }}>
          {user?.email}
        </div>
        <button className="btn" style={{ marginTop: 8 }} onClick={logout}>
          Sign out
        </button>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
