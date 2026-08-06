import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Alert, api, Page } from "../api/client";
import { Badge, DeleteButton, Empty, Loading, Pager } from "../components/ui";

const SEVERITIES = ["", "CRITICAL", "HIGH", "MEDIUM", "LOW"];
const PAGE_SIZE = 25;

const SOURCE_LABELS: Record<string, string> = {
  "": "All kinds",
  verify: "Held before invoicing",
  financial: "Event data problems",
  cash: "Waiting on cash",
  session: "KonaOS connection",
  fuel: "Low fuel",
  clock: "Staff clock in/out",
};

/** Open problems, each one clickable through to a page that explains how to
 *  fix it. Every row leads with the EVENT, because "rate per serving is
 *  missing" is unactionable until you know whose event it's about. */
export default function Alerts() {
  const [data, setData] = useState<Page<Alert> | null>(null);
  // Filters live in the URL so a filtered view can be bookmarked and shared —
  // "the alerts for this date" is exactly the thing you want to send someone.
  const [searchParams, setSearchParams] = useSearchParams();
  const severity = searchParams.get("severity") || "";
  const source = searchParams.get("source") || "";
  const dateFrom = searchParams.get("date_from") || "";
  const dateTo = searchParams.get("date_to") || "";
  const showResolved = searchParams.get("resolved") === "all";
  const urlQ = searchParams.get("q") || "";
  const [qInput, setQInput] = useState(urlQ);
  const [debouncedQ, setDebouncedQ] = useState(urlQ);
  const [page, setPage] = useState(1);
  const navigate = useNavigate();

  function updateParams(patch: Record<string, string | undefined>) {
    const next = new URLSearchParams(searchParams);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v); else next.delete(k);
    }
    setSearchParams(next, { replace: true });
    setPage(1);  // a narrowed list may not have the page you were on
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

  async function load() {
    setData(null);
    const params: Record<string, string> = {
      page: String(page), page_size: String(PAGE_SIZE),
    };
    if (severity) params.severity = severity;
    if (source) params.source = source;
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    if (debouncedQ.trim()) params.q = debouncedQ.trim();
    if (!showResolved) params.resolved = "false";
    setData(await api.alerts(params));
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [severity, source, dateFrom, dateTo, debouncedQ, showResolved, page]);

  const hasFilters = Boolean(
    severity || source || dateFrom || dateTo || debouncedQ || showResolved
  );

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
        <input
          className="input"
          style={{ minWidth: 200 }}
          placeholder="Search event, CRM id, or issue…"
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
        />
        <select className="select" value={severity}
          onChange={(e) => updateParams({ severity: e.target.value })}>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>{s || "All severities"}</option>
          ))}
        </select>
        <select className="select" value={source}
          onChange={(e) => updateParams({ source: e.target.value })}>
          {Object.entries(SOURCE_LABELS).map(([v, label]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
        <label className="field-label" htmlFor="alert-date-from">From</label>
        <input id="alert-date-from" className="input" type="date" value={dateFrom}
          style={{ width: 140 }}
          onChange={(e) => updateParams({ date_from: e.target.value || undefined })}
          title="The EVENT's date, not the day the alert was raised" />
        <label className="field-label" htmlFor="alert-date-to">To</label>
        <input id="alert-date-to" className="input" type="date" value={dateTo}
          style={{ width: 140 }}
          onChange={(e) => updateParams({ date_to: e.target.value || undefined })}
          title="The EVENT's date, not the day the alert was raised" />
        <label className="chk">
          <input type="checkbox" checked={showResolved}
            onChange={(e) => updateParams({ resolved: e.target.checked ? "all" : undefined })} />
          Include sorted
        </label>
        {hasFilters && (
          <button className="btn" onClick={() => { setQInput(""); setSearchParams({}, { replace: true }); setPage(1); }}>
            Clear filters
          </button>
        )}
        {data && <span className="count">{data.total} alerts</span>}
      </div>

      {!data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <Empty text={hasFilters
          ? "No alerts match these filters."
          : "Nothing needs attention. Everything's clean 🎉"} />
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

      {data && data.total > 0 && (
        <Pager
          page={data.page}
          pageSize={data.page_size}
          total={data.total}
          onPage={setPage}
        />
      )}
    </>
  );
}
