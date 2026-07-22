import { CSSProperties, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, CrmAuditResponse } from "../api/client";
import { AuditDetail, Badge, Empty, Loading } from "../components/ui";

/** Plain-language names for what the automation did. The backend keys stay
 *  as-is — this is display only. */
const ACTION_LABELS: Record<string, string> = {
  invoice_created: "Created an invoice",
  invoice_deleted: "Removed an invoice",
  invoice_skipped: "No invoice needed",
  event_updated: "Updated the event",
  error: "Failed",
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
      <h1 className="page-title">KonaOS Change Log</h1>
      <p className="page-sub">
        Everything the automation has changed in KonaOS — figures written onto an event,
        invoices created or removed — with the date and time it happened. Anything that
        <strong> failed</strong> is listed too, with the reason. Use this to answer
        "did the system change this, and when?"
      </p>

      <div className="toolbar" style={{ flexWrap: "wrap", gap: 8 }}>
        <select className="select" value={action}
          onChange={(e) => updateParams({ action: e.target.value || undefined })}>
          <option value="">Everything</option>
          {(data?.actions ?? []).map((a) => (
            <option key={a} value={a}>{ACTION_LABELS[a] || a}</option>
          ))}
        </select>
        <label className="field-label" htmlFor="audit-date-from">From</label>
        <input id="audit-date-from" className="input" type="date" value={fromDate} style={{ width: 140 }}
          onChange={(e) => updateParams({ from_date: e.target.value || undefined, to_date: e.target.value || undefined })}
          title="Events on or after this date — the event's own date, not the day the change was made" />
        <label className="field-label" htmlFor="audit-date-to">To</label>
        <input id="audit-date-to" className="input" type="date" value={toDate} style={{ width: 140 }}
          onChange={(e) => updateParams({ to_date: e.target.value || undefined })}
          title="Events on or before this date — the event's own date, not the day the change was made" />
        <input className="input" placeholder="Search event name or KonaOS id…" value={searchInput}
          style={{ width: 220 }} onChange={(e) => setSearchInput(e.target.value)} />
        {hasFilters && (
          <button className="btn" onClick={clearFilters} title="Clear all filters">✕ Clear filters</button>
        )}
        {data && <span className="count">{data.total} changes{data.total > data.items.length ? ` (showing latest ${data.items.length})` : ""}</span>}
      </div>

      {error ? (
        <div className="card" style={{ borderColor: "var(--crit)" }}>
          <strong>Couldn't load the change log:</strong> {error}
        </div>
      ) : !data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <Empty text={hasFilters ? "No changes match these filters." : "The automation hasn't changed anything in KonaOS yet."} />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Event date</th>
                <th>Event</th>
                <th>What the automation did</th>
                <th>What changed in KonaOS</th>
                <th>When it happened</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((e) => (
                <tr
                  key={e.id}
                  onClick={() => e.event_id && navigate(`/events/${e.event_id}`, {
                    state: { from: location.pathname + location.search, label: "KonaOS Change Log" },
                  })}
                  style={{
                    cursor: e.event_id ? "pointer" : "default",
                    borderLeft: e.action === "error" ? "3px solid var(--crit)" : undefined,
                  }}
                >
                  <td style={{ fontWeight: 700, whiteSpace: "nowrap" }}>{e.event_date || "—"}</td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{e.event_name || e.crm_event_id || "—"}</div>
                    <div className="muted" style={{ fontSize: 12 }}>{e.crm_event_id}</div>
                  </td>
                  <td><Badge kind={e.action}>{ACTION_LABELS[e.action] || e.action}</Badge></td>
                  <td
                    title={JSON.stringify(e.detail, null, 2)}
                    style={{
                      whiteSpace: "normal", minWidth: 280, maxWidth: 460, fontSize: 13,
                      color: e.action === "error" ? "var(--crit)" : undefined,
                    }}
                  >
                    {e.summary}
                    <AuditDetail detail={e.detail} />
                    <ErrorDiagnostic detail={e.detail} />
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

/** For an `error` audit row, a collapsible dump of the exact request body we
 *  sent to KonaOS and the raw response — the evidence for diagnosing a
 *  server-side 500 (which returns no field detail of its own). Renders
 *  nothing unless the diagnostic fields are present. Click to expand; the
 *  <pre> is selectable so it can be copied out. */
function ErrorDiagnostic({ detail }: { detail: Record<string, unknown> | null | undefined }) {
  if (!detail) return null;
  const attempted = detail.attempted_payload;
  const response = detail.konaos_response;
  if (attempted === undefined && response === undefined) return null;

  const pre: CSSProperties = {
    marginTop: 6, padding: 8, background: "var(--surface-2)", border: "1px solid var(--border)",
    borderRadius: 8, fontSize: 11.5, lineHeight: 1.45, maxHeight: 260, overflow: "auto",
    whiteSpace: "pre-wrap", wordBreak: "break-word", color: "var(--text)",
  };

  return (
    <details style={{ marginTop: 6 }} onClick={(e) => e.stopPropagation()}>
      <summary style={{ cursor: "pointer", color: "var(--text-dim)", fontSize: 12 }}>
        Show technical detail (for your developer)
      </summary>
      {response !== undefined && (
        <>
          <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>KonaOS response:</div>
          <pre style={pre}>{String(response)}</pre>
        </>
      )}
      {attempted !== undefined && (
        <>
          <div className="muted" style={{ fontSize: 11.5 }}>Request body we sent:</div>
          <pre style={pre}>{JSON.stringify(attempted, null, 2)}</pre>
        </>
      )}
    </details>
  );
}
