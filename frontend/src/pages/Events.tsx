import { useEffect, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, EventSummary, Page } from "../api/client";
import { Badge, BulkDeleteButton, DeleteButton, Empty, Loading, money } from "../components/ui";

const STATUSES = ["", "processed", "needs_review", "error", "skipped"];

export default function Events() {
  const [data, setData] = useState<Page<EventSummary> | null>(null);
  // Filters live in the URL (like CRM Activity / Financials), so opening an
  // event and clicking "← Events" returns to the exact same filtered view.
  const [searchParams, setSearchParams] = useSearchParams();
  const status = searchParams.get("status") || "";
  const dateFrom = searchParams.get("date_from") || "";
  const dateTo = searchParams.get("date_to") || "";
  const urlQ = searchParams.get("q") || "";
  const [qInput, setQInput] = useState(urlQ);
  const [debouncedQ, setDebouncedQ] = useState(urlQ);
  const [running, setRunning] = useState<number | null>(null);
  const navigate = useNavigate();
  const location = useLocation();

  function updateParams(patch: Record<string, string | undefined>) {
    const next = new URLSearchParams(searchParams);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v); else next.delete(k);
    }
    setSearchParams(next, { replace: true });
  }

  // Debounce the search box into the URL.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(qInput), 300);
    return () => clearTimeout(t);
  }, [qInput]);
  useEffect(() => {
    updateParams({ q: debouncedQ || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ]);

  async function runEvent(e: EventSummary) {
    setRunning(e.id);
    try {
      const res = await api.runPipeline({ eventIds: [e.crm_event_id] });
      navigate("/runs", { state: { justRan: res.run_id } });
    } catch (err: any) {
      alert(err?.message || "Couldn't start the run for this event.");
    } finally {
      setRunning(null);
    }
  }

  function filterParams(): Record<string, string> {
    const params: Record<string, string> = {};
    if (status) params.status = status;
    if (debouncedQ.trim()) params.q = debouncedQ.trim();
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    return params;
  }

  function reload() {
    return api.events(filterParams()).then(setData);
  }

  useEffect(() => {
    setData(null);
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, debouncedQ, dateFrom, dateTo]);

  const hasFilters = Boolean(status || qInput || dateFrom || dateTo);

  return (
    <>
      <div className="topbar">
        <div>
          <h1 className="page-title">Events</h1>
          <p className="page-sub">
            Every booking the system has picked up from KonaOS, and what it worked out for each
            one. Click a row for the full breakdown.
          </p>
        </div>
        <button className="btn primary" onClick={() => navigate("/events/new")}>＋ New event</button>
      </div>

      <div className="toolbar">
        <input
          className="input"
          style={{ maxWidth: 280 }}
          placeholder="Search name / code / id…"
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
        />
        <select className="select" value={status}
          onChange={(e) => updateParams({ status: e.target.value || undefined })}>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s ? s.replace("_", " ") : "All statuses"}
            </option>
          ))}
        </select>
        <label className="muted" htmlFor="ev-date-from" style={{ fontSize: 12 }}>From</label>
        <input
          id="ev-date-from"
          className="input"
          type="date"
          title="Show events on or after this date — also fills 'To' with the same day (edit 'To' to widen the range)"
          value={dateFrom}
          // Picking a From date auto-sets To to the same day (one click = a
          // single-day view); the user can then widen it by editing To.
          onChange={(e) => updateParams({ date_from: e.target.value || undefined, date_to: e.target.value || undefined })}
        />
        <label className="muted" htmlFor="ev-date-to" style={{ fontSize: 12 }}>To</label>
        <input
          id="ev-date-to"
          className="input"
          type="date"
          title="Show events on or before this date"
          value={dateTo}
          onChange={(e) => updateParams({ date_to: e.target.value || undefined })}
        />
        {hasFilters && (
          <button className="btn" title="Clear all filters"
            onClick={() => { setSearchParams(new URLSearchParams(), { replace: true }); setQInput(""); }}>
            ✕ Clear
          </button>
        )}
        {data && <span className="muted">{data.total} events</span>}
        {hasFilters && data && data.total > 0 && (
          <BulkDeleteButton
            count={data.total}
            onDelete={async () => {
              await api.deleteEvents(filterParams());
              await reload();
            }}
          />
        )}
      </div>

      {!data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <Empty text="No events yet. Pick a date on the Dashboard and press Run." />
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
                <th className="actions"></th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((e) => (
                <tr key={e.id} onClick={() => navigate(`/events/${e.id}`, { state: { from: location.pathname + location.search, label: "Events" } })}>
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
                  <td>
                    <Badge kind={e.status}>{e.status}</Badge>
                    {e.status_reason && (
                      <div className="muted" style={{ fontSize: 11.5, marginTop: 2 }}>
                        {e.status_reason}
                      </div>
                    )}
                    {/* Errored events carry the actual failure text here, not
                        status_reason (that's only set for skips) — shown
                        inline so "what broke" doesn't need a click-through. */}
                    {e.status === "error" && e.error && (
                      <div
                        title={e.error}
                        style={{ fontSize: 11.5, marginTop: 2, color: "var(--crit)", maxWidth: 260 }}
                      >
                        {e.error.length > 100 ? e.error.slice(0, 100) + "…" : e.error}
                      </div>
                    )}
                  </td>
                  <td className="right">{money(e.final_invoice_amount)}</td>
                  <td className="actions">
                    <button
                      className="btn"
                      style={{ marginRight: 6 }}
                      disabled={running !== null}
                      title="Re-process just this event (pulls a fresh copy from KonaOS)"
                      onClick={(ev) => { ev.stopPropagation(); runEvent(e); }}
                    >
                      {running === e.id ? <span className="spinner sm" /> : "▶ Run"}
                    </button>
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
