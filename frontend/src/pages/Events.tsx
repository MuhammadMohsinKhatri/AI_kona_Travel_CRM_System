import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, EventSummary, Page } from "../api/client";
import { Badge, DeleteButton, Empty, Loading, money } from "../components/ui";

const STATUSES = ["", "processed", "needs_review", "error", "skipped"];

export default function Events() {
  const [data, setData] = useState<Page<EventSummary> | null>(null);
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const navigate = useNavigate();

  function reload() {
    const params: Record<string, string> = {};
    if (status) params.status = status;
    if (q) params.q = q;
    return api.events(params).then(setData);
  }

  useEffect(() => {
    setData(null);
    const t = setTimeout(reload, q ? 300 : 0);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, q]);

  return (
    <>
      <div className="topbar">
        <div>
          <h1 className="page-title">Events</h1>
          <p className="page-sub">Every event pulled from the CRM and processed by the pipeline.</p>
        </div>
        <button className="btn primary" onClick={() => navigate("/events/new")}>＋ New event</button>
      </div>

      <div className="toolbar">
        <input
          className="input"
          style={{ maxWidth: 280 }}
          placeholder="Search name / code / id…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select className="select" value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s ? s.replace("_", " ") : "All statuses"}
            </option>
          ))}
        </select>
        {data && <span className="muted">{data.total} events</span>}
      </div>

      {!data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <Empty text="No events. Run the pipeline from the Dashboard." />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Event</th>
                <th>Date</th>
                <th>Brand</th>
                <th>Event type</th>
                <th>Billing model</th>
                <th>Status</th>
                <th className="right">Invoice</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((e) => (
                <tr key={e.id} onClick={() => navigate(`/events/${e.id}`)}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{e.event_name || "(unnamed)"}</div>
                    <div className="muted" style={{ fontSize: 12 }}>{e.event_code || e.crm_event_id}</div>
                  </td>
                  <td>{e.event_date || "—"}</td>
                  <td>{e.brand || "—"}</td>
                  <td>
                    {e.event_type ? (
                      <span className="badge blue" style={{ textTransform: "capitalize" }}>
                        {e.event_type}
                      </span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>{e.billing_model || <span className="muted">—</span>}</td>
                  <td><Badge kind={e.status}>{e.status}</Badge></td>
                  <td className="right">{money(e.final_invoice_amount)}</td>
                  <td className="actions">
                    <DeleteButton
                      title="Delete this event and its invoice, alerts and ledger row"
                      onDelete={async () => { await api.deleteEvent(e.id); await reload(); }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
