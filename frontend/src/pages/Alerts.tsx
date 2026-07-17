import { useEffect, useState } from "react";
import { Alert, api, Page } from "../api/client";
import { Badge, DeleteButton, Empty, Loading } from "../components/ui";

const SEVERITIES = ["", "CRITICAL", "HIGH", "MEDIUM", "LOW"];

export default function Alerts() {
  const [data, setData] = useState<Page<Alert> | null>(null);
  const [severity, setSeverity] = useState("");
  const [showResolved, setShowResolved] = useState(false);

  async function load() {
    setData(null);
    const params: Record<string, string> = {};
    if (severity) params.severity = severity;
    if (!showResolved) params.resolved = "false";
    setData(await api.alerts(params));
  }
  useEffect(() => {
    load();
  }, [severity, showResolved]);

  async function resolve(id: number) {
    await api.resolveAlert(id);
    load();
  }

  return (
    <>
      <h1 className="page-title">Alerts</h1>
      <p className="page-sub">Financial issues flagged by the pipeline that need human review.</p>

      <div className="toolbar">
        <select className="select" value={severity} onChange={(e) => setSeverity(e.target.value)}>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>{s || "All severities"}</option>
          ))}
        </select>
        <label className="flex" style={{ gap: 6 }}>
          <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} />
          Include resolved
        </label>
        {data && <span className="muted">{data.total} alerts</span>}
      </div>

      {!data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <Empty text="No alerts. Everything looks clean 🎉" />
      ) : (
        data.items.map((a) => (
          <div key={a.id} className={`alert-row ${a.severity}`}>
            <div className="flex between">
              <div className="flex">
                <Badge kind={a.severity}>{a.severity}</Badge>
                {a.resolved && <span className="badge green">resolved</span>}
              </div>
              <div className="flex" style={{ gap: 6 }}>
                {!a.resolved && (
                  <button className="btn" onClick={() => resolve(a.id)}>Mark resolved</button>
                )}
                <DeleteButton
                  title="Delete this alert (resolve keeps it as history)"
                  onDelete={async () => { await api.deleteAlert(a.id); await load(); }}
                />
              </div>
            </div>
            <div style={{ fontWeight: 600, marginTop: 8 }}>{a.issue}</div>
            <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>👉 {a.action}</div>
          </div>
        ))
      )}
    </>
  );
}
