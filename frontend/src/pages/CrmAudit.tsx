import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, CrmAuditResponse } from "../api/client";
import { AuditDetail, Badge, Empty, Loading } from "../components/ui";

const ACTION_LABELS: Record<string, string> = {
  invoice_created: "Invoice created",
  invoice_deleted: "Invoice deleted",
  invoice_skipped: "Invoice skipped",
  event_updated: "Event updated",
};

/** Structured, filterable record of every write our system has made to
 *  KonaOS — the "what changed, and when" audit trail. Complements the raw
 *  per-run text log on Pipeline Runs with one row per real action, so a
 *  client dispute can be checked by event or date instead of grepping logs.
 *
 *  Filters live in the URL (like Financials) so "← CRM Activity" from an
 *  event's detail page returns to the exact same filtered view. */
export default function CrmAudit() {
  const [searchParams, setSearchParams] = useSearchParams();
  const action = searchParams.get("action") || "";
  const fromDate = searchParams.get("from_date") || "";
  const toDate = searchParams.get("to_date") || "";
  const urlSearch = searchParams.get("search") || "";
  const [searchInput, setSearchInput] = useState(urlSearch);
  const [debounced, setDebounced] = useState(urlSearch);
  const [data, setData] = useState<CrmAuditResponse | null>(null);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const location = useLocation();

  function updateParams(patch: Record<string, string | undefined>) {
    const next = new URLSearchParams(searchParams);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v); else next.delete(k);
    }
    setSearchParams(next, { replace: true });
  }

  useEffect(() => {
    const t = setTimeout(() => setDebounced(searchInput), 300);
    return () => clearTimeout(t);
  }, [searchInput]);
  useEffect(() => {
    updateParams({ search: debounced || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  const params = useMemo(() => {
    const p: Record<string, string> = { page_size: "100" };
    if (action) p.action = action;
    if (fromDate) p.from_date = fromDate;
    if (toDate) p.to_date = toDate;
    if (debounced.trim()) p.search = debounced.trim();
    return p;
  }, [action, fromDate, toDate, debounced]);

  useEffect(() => {
    setData(null);
    setError("");
    api.crmAudit(params).then(setData).catch((e: any) => setError(e?.message || "Failed to load."));
  }, [params]);

  const hasFilters = !!(action || fromDate || toDate || searchInput);
  function clearFilters() {
    setSearchParams(new URLSearchParams(), { replace: true });
    setSearchInput("");
  }

  return (
    <>
      <h1 className="page-title">CRM Activity</h1>
      <p className="page-sub">
        Every write our system has made to KonaOS — event field updates and invoice
        created/deleted/skipped decisions. This is the audit trail for "what did your
        system change, and when."
      </p>

      <div className="toolbar" style={{ flexWrap: "wrap", gap: 8 }}>
        <select className="select" value={action}
          onChange={(e) => updateParams({ action: e.target.value || undefined })}>
          <option value="">All actions</option>
          {(data?.actions ?? []).map((a) => (
            <option key={a} value={a}>{ACTION_LABELS[a] || a}</option>
          ))}
        </select>
        <label className="muted" htmlFor="audit-date-from" style={{ fontSize: 12 }}>From</label>
        <input id="audit-date-from" className="input" type="date" value={fromDate} style={{ width: 150 }}
          onChange={(e) => updateParams({ from_date: e.target.value || undefined, to_date: e.target.value || undefined })}
          title="Events dated on or after this date (inclusive) — the event's own date, not when the write happened" />
        <label className="muted" htmlFor="audit-date-to" style={{ fontSize: 12 }}>To</label>
        <input id="audit-date-to" className="input" type="date" value={toDate} style={{ width: 150 }}
          onChange={(e) => updateParams({ to_date: e.target.value || undefined })}
          title="Events dated on or before this date (inclusive) — the event's own date, not when the write happened" />
        <input className="input" placeholder="Search event / CRM id / summary…" value={searchInput}
          style={{ width: 220 }} onChange={(e) => setSearchInput(e.target.value)} />
        {hasFilters && (
          <button className="btn" onClick={clearFilters} title="Clear all filters">✕ Clear</button>
        )}
        {data && <span className="muted">{data.total} entries{data.total > data.items.length ? ` (showing latest ${data.items.length})` : ""}</span>}
      </div>

      {error ? (
        <div className="card" style={{ borderColor: "var(--crit)" }}>
          <strong>Couldn't load CRM activity:</strong> {error}
        </div>
      ) : !data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <Empty text={hasFilters ? "No activity matches these filters." : "No CRM activity recorded yet."} />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Event Date</th>
                <th>Event</th>
                <th>Action</th>
                <th>Summary</th>
                <th>Written</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((e) => (
                <tr
                  key={e.id}
                  onClick={() => e.event_id && navigate(`/events/${e.event_id}`, {
                    state: { from: location.pathname + location.search, label: "CRM Activity" },
                  })}
                  style={{ cursor: e.event_id ? "pointer" : "default" }}
                >
                  <td style={{ fontWeight: 700, whiteSpace: "nowrap" }}>{e.event_date || "—"}</td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{e.event_name || e.crm_event_id || "—"}</div>
                    <div className="muted" style={{ fontSize: 12 }}>{e.crm_event_id}</div>
                  </td>
                  <td><Badge kind={e.action}>{ACTION_LABELS[e.action] || e.action}</Badge></td>
                  <td
                    title={JSON.stringify(e.detail, null, 2)}
                    style={{ whiteSpace: "normal", minWidth: 280, maxWidth: 460, fontSize: 13 }}
                  >
                    {e.summary}
                    <AuditDetail detail={e.detail} />
                  </td>
                  <td className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                    {e.created_at ? new Date(e.created_at).toLocaleString() : "—"}
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
