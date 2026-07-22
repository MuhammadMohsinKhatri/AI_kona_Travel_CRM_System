import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, Invoice, Page } from "../api/client";
import { Badge, DeleteButton, Empty, Loading, money } from "../components/ui";

/** Filters live in the URL (like Financials/CRM Activity) so "← Invoices"
 *  from an event's detail page returns to the exact same filtered view.
 *  Date filters are by the underlying event's date (Invoice has no date of
 *  its own) — a month shortcut, or a custom from/to range where picking
 *  "From" defaults "To" to the same day until "To" is edited separately. */
export default function Invoices() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [months, setMonths] = useState<string[]>([]);
  const month = searchParams.get("month") || "";
  const fromDate = searchParams.get("from_date") || "";
  const toDate = searchParams.get("to_date") || "";
  const onlyVariance = searchParams.get("has_variance") === "true";
  const [data, setData] = useState<Page<Invoice> | null>(null);
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
    api.invoiceMonths().then(setMonths).catch(() => {});
  }, []);

  const params = useMemo(() => {
    const p: Record<string, string> = {};
    if (month) p.month = month;
    if (fromDate) p.from_date = fromDate;
    if (toDate) p.to_date = toDate;
    if (onlyVariance) p.has_variance = "true";
    return p;
  }, [month, fromDate, toDate, onlyVariance]);

  const reload = () => api.invoices(params).then(setData);

  useEffect(() => {
    setData(null);
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  function pickMonth(m: string) {
    updateParams({ month: m || undefined, from_date: undefined, to_date: undefined });
  }
  function pickRange(patch: { from?: string; to?: string }) {
    if (patch.from !== undefined) {
      updateParams({ from_date: patch.from || undefined, to_date: patch.from || undefined, month: undefined });
    } else if (patch.to !== undefined) {
      updateParams({ to_date: patch.to || undefined, month: undefined });
    }
  }
  const hasFilters = !!(month || fromDate || toDate || onlyVariance);
  function clearFilters() {
    setSearchParams(new URLSearchParams(), { replace: true });
  }

  return (
    <>
      <h1 className="page-title">Invoices</h1>
      <p className="page-sub">
        Draft invoices the automation has created in KonaOS, ready to send. Events the customer
        already paid for at the truck don't get one.
      </p>

      <div className="toolbar" style={{ flexWrap: "wrap", gap: 8 }}>
        <select className="select" value={month} onChange={(e) => pickMonth(e.target.value)} title="Month shortcut">
          <option value="">All months</option>
          {months.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <span className="muted" style={{ fontSize: 12 }}>or custom range:</span>
        <label className="muted" htmlFor="inv-date-from" style={{ fontSize: 12 }}>From</label>
        <input id="inv-date-from" className="input" type="date" value={fromDate} style={{ width: 150 }}
          onChange={(e) => pickRange({ from: e.target.value })} title="Rows on or after this date (inclusive)" />
        <label className="muted" htmlFor="inv-date-to" style={{ fontSize: 12 }}>To</label>
        <input id="inv-date-to" className="input" type="date" value={toDate} style={{ width: 150 }}
          onChange={(e) => pickRange({ to: e.target.value })} title="Rows on or before this date (inclusive)" />
        <label className="flex" style={{ gap: 6 }}>
          <input
            type="checkbox"
            checked={onlyVariance}
            onChange={(e) => updateParams({ has_variance: e.target.checked ? "true" : undefined })}
          />
          Only with variance
        </label>
        {hasFilters && (
          <button className="btn" onClick={clearFilters} title="Clear all filters">✕ Clear</button>
        )}
        {data && <span className="muted">{data.total} invoices</span>}
      </div>

      {!data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <Empty text={hasFilters ? "No invoices match these filters." : "No invoices yet."} />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Invoice</th>
                <th>Type</th>
                <th>Status</th>
                <th className="right">Subtotal</th>
                <th className="right">Tax</th>
                <th className="right">Total</th>
                <th className="right">Variance</th>
                <th className="actions"></th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((inv) => (
                <tr
                  key={inv.id}
                  onClick={() => navigate(`/events/${inv.event_id}`, {
                    state: { from: location.pathname + location.search, label: "Invoices" },
                  })}
                >
                  <td style={{ fontWeight: 700, whiteSpace: "nowrap" }}>{inv.event_date || "—"}</td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{inv.title}</div>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {inv.invoice_number} · {inv.brand}
                    </div>
                  </td>
                  <td>{inv.invoice_type}</td>
                  <td><Badge kind={inv.status}>{inv.status}</Badge></td>
                  <td className="right">{money(inv.subtotal)}</td>
                  <td className="right">{money(inv.tax_amount)}</td>
                  <td className="right"><strong>{money(inv.grand_total)}</strong></td>
                  <td className="right">
                    {inv.has_variance ? (
                      <span style={{ color: "var(--warn)" }}>{money(inv.variance_amount)}</span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="actions">
                    <DeleteButton
                      title="Delete this invoice record (KonaOS is not touched)"
                      onDelete={async () => { await api.deleteInvoice(inv.id); await reload(); }}
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
