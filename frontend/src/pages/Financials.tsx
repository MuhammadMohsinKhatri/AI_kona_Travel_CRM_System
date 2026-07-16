import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, FinancialsResponse, getToken } from "../api/client";
import { Badge, Empty, Loading, money } from "../components/ui";

/** The financial ledger — replaces the monthly Google Sheet. Rows live in
 *  Postgres and are upserted by every pipeline run.
 *
 *  Filtering: pick a month for the classic monthly view, or set a custom
 *  from/to date range (setting one clears the other). Brand / event type /
 *  paid / search narrow further. The CSV export honours every active filter. */
export default function Financials() {
  const [months, setMonths] = useState<string[]>([]);
  const [month, setMonth] = useState<string>("");
  const [fromDate, setFromDate] = useState<string>("");
  const [toDate, setToDate] = useState<string>("");
  const [brand, setBrand] = useState<string>("");
  const [eventType, setEventType] = useState<string>("");
  const [paid, setPaid] = useState<string>("");        // "" | "true" | "false"
  const [search, setSearch] = useState<string>("");
  const [debounced, setDebounced] = useState<string>("");
  const [data, setData] = useState<FinancialsResponse | null>(null);
  const [error, setError] = useState<string>("");
  const navigate = useNavigate();

  useEffect(() => {
    api.financialMonths().then((ms) => {
      setMonths(ms);
      if (ms.length && !month) setMonth(ms[0]);
    }).catch(() => { /* surfaced by the list fetch below */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounce the search box so we don't refetch per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  const params = useMemo(() => {
    const p: Record<string, string> = {};
    if (month) p.month = month;
    if (fromDate) p.from_date = fromDate;
    if (toDate) p.to_date = toDate;
    if (brand) p.brand = brand;
    if (eventType) p.event_type = eventType;
    if (paid) p.paid = paid;
    if (debounced.trim()) p.search = debounced.trim();
    return p;
  }, [month, fromDate, toDate, brand, eventType, paid, debounced]);

  useEffect(() => {
    setData(null);
    setError("");
    api.financials(params)
      .then(setData)
      .catch((e: any) => setError(e?.message || "Failed to load the ledger."));
  }, [params]);

  /** Month shortcut and custom range are alternatives — using one clears the other. */
  function pickMonth(m: string) {
    setMonth(m);
    if (m) { setFromDate(""); setToDate(""); }
  }
  function pickRange(patch: { from?: string; to?: string }) {
    if (patch.from !== undefined) setFromDate(patch.from);
    if (patch.to !== undefined) setToDate(patch.to);
    if (patch.from || patch.to) setMonth("");
  }
  function clearFilters() {
    setMonth(""); setFromDate(""); setToDate("");
    setBrand(""); setEventType(""); setPaid(""); setSearch("");
  }
  const hasFilters =
    !!(month || fromDate || toDate || brand || eventType || paid || search);

  async function downloadCsv() {
    const qs = new URLSearchParams(params);
    const res = await fetch("/api/financials/export.csv?" + qs, {
      headers: { Authorization: `Bearer ${getToken()}` },
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `kona-financials-${month || (fromDate || toDate ? `${fromDate || "start"}_${toDate || "now"}` : "all")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <div className="topbar">
        <div>
          <h1 className="page-title">Financials</h1>
          <p className="page-sub">
            The financial ledger — one row per event, updated on every pipeline run. Stored in
            PostgreSQL (replaces the monthly Google Sheet). All 46 sheet columns are saved; the
            key ones are shown here, the full set is in the CSV export.
          </p>
        </div>
        <button className="btn" onClick={downloadCsv} disabled={!data || data.total === 0}>
          ⬇ Download CSV (all 46 columns)
        </button>
      </div>

      <div className="toolbar" style={{ flexWrap: "wrap", gap: 8 }}>
        <select className="select" value={month} onChange={(e) => pickMonth(e.target.value)} title="Month shortcut">
          <option value="">All months</option>
          {months.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <span className="muted" style={{ fontSize: 12 }}>or custom range</span>
        <input className="input" type="date" value={fromDate} style={{ width: 150 }}
          onChange={(e) => pickRange({ from: e.target.value })} title="From date (inclusive)" />
        <span className="muted">→</span>
        <input className="input" type="date" value={toDate} style={{ width: 150 }}
          onChange={(e) => pickRange({ to: e.target.value })} title="To date (inclusive)" />
        <select className="select" value={brand} onChange={(e) => setBrand(e.target.value)}>
          <option value="">All brands</option>
          {(data?.brands ?? []).map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
        <select className="select" value={eventType} onChange={(e) => setEventType(e.target.value)}>
          <option value="">All types</option>
          {(data?.event_types ?? []).map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select className="select" value={paid} onChange={(e) => setPaid(e.target.value)}>
          <option value="">Paid + unpaid</option>
          <option value="true">Paid only</option>
          <option value="false">Unpaid only</option>
        </select>
        <input className="input" placeholder="Search event / code…" value={search}
          style={{ width: 190 }} onChange={(e) => setSearch(e.target.value)} />
        {hasFilters && (
          <button className="btn" onClick={clearFilters} title="Clear all filters">✕ Clear</button>
        )}
        {data && <span className="muted">{data.total} entries</span>}
      </div>

      {error ? (
        <div className="card" style={{ borderColor: "var(--crit)" }}>
          <strong>Couldn't load the ledger:</strong> {error}
          <div className="muted" style={{ marginTop: 6, fontSize: 13 }}>
            Check that the backend is up (<code>docker compose ps</code>) and its logs
            (<code>docker compose logs backend</code>).
          </div>
        </div>
      ) : !data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <Empty
          text={
            hasFilters
              ? "No ledger entries match these filters — clear them or widen the date range."
              : "No ledger entries yet. Rows are written by pipeline runs: open the Dashboard, pick a date with events, and run the pipeline."
          }
        />
      ) : (
        <>
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <Tot label="Invoiced" v={money(data.totals.invoice_total)} />
            <Tot label="Subtotal" v={money(data.totals.subtotal)} />
            <Tot label="Tax + CC fees" v={money(data.totals.sales_tax + data.totals.cc_fee)} />
            <Tot label="Square sales" v={money(data.totals.square_sales)} />
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date</th><th>Event</th><th>Brand</th><th>Type</th><th>Status</th>
                  <th className="right">Units</th>
                  <th className="right">Subtotal</th>
                  <th className="right">Tax</th>
                  <th className="right">CC fee</th>
                  <th className="right">Check / Invoice</th>
                  <th className="right">Invoice total</th>
                  <th className="right">Deposit</th>
                  <th className="right">Balance due</th>
                  <th className="right">Square net</th>
                  <th>Pay</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.id} onClick={() => navigate(`/events/${r.event_id}`)}>
                    <td>{r.event_date || "—"}</td>
                    <td>
                      <div style={{ fontWeight: 600 }}>{r.event_name}</div>
                      <div className="muted" style={{ fontSize: 12 }}>
                        {r.event_code} · {r.billing_model || "—"}
                      </div>
                    </td>
                    <td>{r.brand}</td>
                    <td style={{ textTransform: "capitalize" }}>{r.event_type || "—"}</td>
                    <td>
                      <Badge kind={r.final_status === "completed" ? "green" : "gray"}>
                        {r.final_status || "—"}
                      </Badge>
                    </td>
                    <td className="right">{r.units_served || "—"}</td>
                    <td className="right">{money(r.subtotal)}</td>
                    <td className="right">{money(r.sales_tax)}</td>
                    <td className="right">{money(r.cc_fee)}</td>
                    <td className="right">{r.check_invoice > 0 ? money(r.check_invoice) : "—"}</td>
                    <td className="right"><strong>{money(r.invoice_total)}</strong></td>
                    <td className="right">{r.deposit > 0 ? money(r.deposit) : "—"}</td>
                    <td className="right">{money(r.balance_due)}</td>
                    <td className="right">
                      {r.square_net_card > 0 ? (
                        <>{money(r.square_net_card)} <span className="muted" style={{ fontSize: 11 }}>({r.square_orders})</span></>
                      ) : "—"}
                    </td>
                    <td>
                      <Badge kind={r.paid ? "green" : "gray"}>
                        {(r.payment_method || "—") + (r.paid ? " ✓" : "")}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr style={{ fontWeight: 700, background: "var(--surface-2)" }}>
                  <td colSpan={5}>
                    Totals ({month || ((fromDate || toDate) ? `${fromDate || "…"} → ${toDate || "…"}` : "all")})
                  </td>
                  <td className="right">{data.totals.units_served}</td>
                  <td className="right">{money(data.totals.subtotal)}</td>
                  <td className="right">{money(data.totals.sales_tax)}</td>
                  <td className="right">{money(data.totals.cc_fee)}</td>
                  <td className="right">{money(data.totals.check_invoice)}</td>
                  <td className="right">{money(data.totals.invoice_total)}</td>
                  <td />
                  <td className="right">{money(data.totals.balance_due)}</td>
                  <td className="right">{money(data.totals.square_sales)}</td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
        </>
      )}
    </>
  );
}

function Tot({ label, v }: { label: string; v: string }) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="value small">{v}</div>
    </div>
  );
}
