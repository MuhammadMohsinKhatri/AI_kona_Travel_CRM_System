import { CSSProperties, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, CrmAuditResponse } from "../api/client";
import { AuditDetail, Badge, Empty, Loading } from "../components/ui";

/** Rows per page. Ten keeps the whole table on screen without scrolling, which
 *  matters more here than density: each row can carry a multi-line summary, a
 *  before/after diff, and an expandable error dump. */
const PAGE_SIZE = 10;

/** Plain-language names for each logged change. The backend keys stay as-is —
 *  this is display only.
 *
 *  source_changed is the one INBOUND action: somebody edited the booking in
 *  Kona OS after we'd processed it. Everything else is something the automation
 *  did TO Kona OS. */
const ACTION_LABELS: Record<string, string> = {
  invoice_created: "Created an invoice",
  invoice_deleted: "Removed an invoice",
  invoice_skipped: "No invoice needed",
  invoice_deferred: "Waiting for cash",
  cash_updated: "Cash recorded",
  event_updated: "Updated the event",
  source_changed: "Edited in Kona OS",
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
  // In the URL like every other filter, so a link to page 3 of "errors in July"
  // reopens exactly that. Clamped at 1 so a hand-edited ?page=0 can't ask the
  // API for a negative offset.
  const page = Math.max(1, Number(searchParams.get("page") || 1) || 1);
  const fromDate = searchParams.get("from_date") || "";
  const toDate = searchParams.get("to_date") || "";
  const urlSearch = searchParams.get("search") || "";
  const [searchInput, setSearchInput] = useState(urlSearch);
  const [debounced, setDebounced] = useState(urlSearch);
  const [data, setData] = useState<CrmAuditResponse | null>(null);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const location = useLocation();

  /** Any filter change resets to page 1 unless the patch sets `page` itself.
   *  Without this, narrowing a filter while on page 6 shows an empty table and
   *  looks like "no results" rather than "you're past the end". */
  function updateParams(patch: Record<string, string | undefined>) {
    const next = new URLSearchParams(searchParams);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v); else next.delete(k);
    }
    if (!("page" in patch)) next.delete("page");
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
    const p: Record<string, string> = {
      page_size: String(PAGE_SIZE), page: String(page),
    };
    if (action) p.action = action;
    if (fromDate) p.from_date = fromDate;
    if (toDate) p.to_date = toDate;
    if (debounced.trim()) p.search = debounced.trim();
    return p;
  }, [action, fromDate, toDate, debounced, page]);

  useEffect(() => {
    setData(null);
    setError("");
    api.crmAudit(params).then(setData).catch((e: any) => setError(e?.message || "Failed to load."));
  }, [params]);

  const hasFilters = !!(action || fromDate || toDate || searchInput);
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  function clearFilters() {
    setSearchParams(new URLSearchParams(), { replace: true });
    setSearchInput("");
  }

  return (
    <>
      <h1 className="page-title">KonaOS Change Log</h1>
      <p className="page-sub">
        A two-way history of every event. Outbound: what the automation changed in KonaOS —
        figures written onto an event, invoices created or removed. Inbound: bookings
        <strong> edited in KonaOS</strong> after we'd already processed them (a driver's
        serving count typed in the next morning, a time moved) — those are re-run
        automatically, and the row says which fields moved. Anything that
        <strong> failed</strong> is listed too, with the reason.
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
        <div className="table-wrap fit">
          <table>
            {/* Percentages, not rem: fixed column widths don't shrink, so on a
                narrower window they ate the row and left Details at 29px. These
                scale, and the table's min-width (styles.css .table-wrap.fit)
                stops them shrinking past readable. */}
            <colgroup>
              <col style={{ width: "11%" }} />
              <col style={{ width: "23%" }} />
              <col style={{ width: "14%" }} />
              <col />
              <col style={{ width: "11%" }} />
            </colgroup>
            <thead>
              <tr>
                <th>Event date</th>
                <th>Event</th>
                {/* Not "what the automation did" — source_changed rows are
                    KonaOS changing under us, the opposite direction. */}
                <th>What happened</th>
                <th>Details</th>
                <th>When</th>
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
                  <td className="keep" style={{ fontWeight: 700 }}>{e.event_date || "—"}</td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{e.event_name || e.crm_event_id || "—"}</div>
                    {/* The KonaOS id is 32 characters and would drive the whole
                        table wider than the screen. Truncated with the full value
                        on hover — it's here to identify the row, and the event
                        name above already does that at a glance. */}
                    <div
                      className="muted"
                      title={e.crm_event_id}
                      style={{
                        fontSize: 12, whiteSpace: "nowrap",
                        overflow: "hidden", textOverflow: "ellipsis",
                      }}
                    >
                      {e.crm_event_id}
                    </div>
                  </td>
                  <td><Badge kind={e.action}>{ACTION_LABELS[e.action] || e.action}</Badge></td>
                  <td
                    title={JSON.stringify(e.detail, null, 2)}
                    style={{
                      fontSize: 13,
                      color: e.action === "error" ? "var(--crit)" : undefined,
                    }}
                  >
                    {e.summary}
                    <AuditDetail detail={e.detail} />
                    <ErrorDiagnostic detail={e.detail} />
                  </td>
                  {/* Date and time on two lines rather than one long string —
                      a full toLocaleString() was the other column forcing the
                      table wide. Exact value on hover. */}
                  <td
                    className="muted keep"
                    style={{ fontSize: 12 }}
                    title={e.created_at ? new Date(e.created_at).toString() : ""}
                  >
                    {e.created_at ? (
                      <>
                        <div>{new Date(e.created_at).toLocaleDateString(undefined, {
                          day: "numeric", month: "short",
                        })}</div>
                        <div>{new Date(e.created_at).toLocaleTimeString(undefined, {
                          hour: "numeric", minute: "2-digit",
                        })}</div>
                      </>
                    ) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && data.total > 0 && (
        <div className="pager">
          <span>
            {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, data.total)}
            {" of "}{data.total}
          </span>
          <button
            className="btn"
            disabled={page <= 1}
            onClick={() => updateParams({ page: page > 2 ? String(page - 1) : undefined })}
          >
            ← Previous
          </button>
          <button
            className="btn"
            disabled={page >= totalPages}
            onClick={() => updateParams({ page: String(page + 1) })}
          >
            Next →
          </button>
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
