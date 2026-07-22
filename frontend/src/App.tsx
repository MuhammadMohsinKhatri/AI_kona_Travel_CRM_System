import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import { Loading } from "./components/ui";
import AlertDetail from "./pages/AlertDetail";
import Alerts from "./pages/Alerts";
import ApiExplorer from "./pages/ApiExplorer";
import CrmAudit from "./pages/CrmAudit";
import Dashboard from "./pages/Dashboard";
import EventDetail from "./pages/EventDetail";
import Events from "./pages/Events";
import Financials from "./pages/Financials";
import Guide from "./pages/Guide";
import Invoices from "./pages/Invoices";
import Login from "./pages/Login";
import NewEvent from "./pages/NewEvent";
import Runs from "./pages/Runs";
import Settings from "./pages/Settings";

function Protected({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  if (loading) return <Loading />;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <Protected>
            <Layout />
          </Protected>
        }
      >
        <Route path="/" element={<Dashboard />} />
        <Route path="/events" element={<Events />} />
        <Route path="/events/new" element={<NewEvent />} />
        <Route path="/events/:id" element={<EventDetail />} />
        <Route path="/invoices" element={<Invoices />} />
        <Route path="/financials" element={<Financials />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="/alerts/:id" element={<AlertDetail />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/runs" element={<Runs />} />
        <Route path="/crm-activity" element={<CrmAudit />} />
        <Route path="/api-explorer" element={<ApiExplorer />} />
        <Route path="/guide" element={<Guide />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
