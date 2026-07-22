import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, api, Page } from "../api/client";
import { Badge, DeleteButton, Empty, Loading } from "../components/ui";

const SEVERITIES = ["", "CRITICAL", "HIGH", "MEDIUM", "LOW"];

const SOURCE_LABELS: Record<string, string> = {
  "": "All kinds",
  financial: "Event data problems",
  cash: "Waiting on cash",
  session: "KonaOS connection",
};

/** Open problems, each one clickable through to a page that explains how to
 *  fix it. Every row leads with the EVENT, because "rate per serving is
 *  missing" is unactionable until you know whose event it's about. */
export default function Alerts() {
  const [data, setData] = useState<Page<Alert> | null>(null);
  const [severity, setSeverity] = useState("");
  const [source, setSource] = useState("");
  const [showResolved, setShowResolved] = useState(false);
  const navigate = useNavigate();

  async function load() {
    setData(null);
    const params: Record<string, string> = {};
    if (severity) params.severity = severity;
    if (source) params.source = source;
    if (!showResolved) params.resolved = "false";
    setData(await api.alerts(params));
  }
  useEffect(() => { load(); }, [severity, source, showResolved]);

  async function resolve(e: React.MouseEvent, id: number) {
    e.stopPropagation();
    await api.resolveAlert(id);
    load();
  }

  return (
    <>
      <h1 className="page-title">Needs Attention</h1>
      <p className="page-sub">
        Things the automation couldn't finish on its own. Click any one to see what's
        wrong, which event it's about, and how to put it right.
      </p>

      <div className="toolbar">
        <select className="select" value={severity} onChange={(e) => setSeverity(e.target.value)}>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>{s || "All severities"}</option>
          ))}
        </select>
        <select className="select" value={source} onChange={(e) => setSource(e.target.value)}>
          {Object.entries(SOURCE_LABELS).map(([v, label]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
        <label className="chk">
          <input type="checkbox" checked={showResolved}
            onChange={(e) => setShowResolved(e.target.checked)} />
          Include sorted
        </label>
        {data && <span className="count">{data.total} alerts</span>}
      </div>

      {!data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <Empty text="Nothing needs attention. Everything's clean 🎉" />
      ) : (
        data.items.map((a) => (
          <div
            key={a.id}
            className={`alert-row ${a.severity}`}
            style={{ cursor: "pointer" }}
            onClick={() => navigate(`/alerts/${a.id}`)}
          >
            <div className="flex between">
              <div className="flex" style={{ flexWrap: "wrap", gap: 8 }}>
                <Badge kind={a.severity}>{a.severity}</Badge>
                <span className="badge gray">{SOURCE_LABELS[a.source] || a.source}</span>
                {a.resolved && <span className="badge green">sorted</span>}
              </div>
              <div className="flex" style={{ gap: 6 }} onClick={(e) => e.stopPropagation()}>
                {!a.resolved && (
                  <button className="btn icon-btn" onClick={(e) => resolve(e, a.id)}>
                    Mark sorted
                  </button>
                )}
                <DeleteButton
                  title="Delete this alert (marking it sorted keeps it as history)"
                  onDelete={async () => { await api.deleteAlert(a.id); await load(); }}
                />
              </div>
            </div>

            {/* Event first — it's what makes the rest of the row mean anything. */}
            {a.event_name ? (
              <div style={{ marginTop: 8, fontWeight: 700 }}>
                {a.event_name}
                <span className="muted" style={{ fontWeight: 400, fontSize: 12.5 }}>
                  {a.event_date ? ` · ${a.event_date}` : ""}
                  {a.brand ? ` · ${a.brand}` : ""}
                  {a.crm_event_id ? ` · ${a.crm_event_id}` : ""}
                </span>
              </div>
            ) : (
              <div style={{ marginTop: 8, fontWeight: 700 }} className="muted">
                System-wide — not tied to one event
              </div>
            )}

            <div style={{ fontWeight: 600, marginTop: 4 }}>{a.issue}</div>
            <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>👉 {a.action}</div>
            <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
              Click for the full fix-it steps →
            </div>
          </div>
        ))
      )}
    </>
  );
}
