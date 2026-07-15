import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, FinancialsResponse, getToken } from "../api/client";
import { Badge, Empty, Loading, money } from "../components/ui";

/** The financial ledger — replaces the monthly Google Sheet. Rows live in
 *  Postgres and are upserted by every pipeline run. */
export default function Financials() {
  const [months, setMonths] = useState<string[]>([]);
  const [month, setMonth] = useState<string>("");
  const [brand, setBrand] = useState<string>("");
  const [data, setData] = useState<FinancialsResponse | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api.financialMonths().then((ms) => {
      setMonths(ms);
      if (ms.length && !month) setMonth(ms[0]);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const params: Record<string, string> = {};
    if (month) params.month = month;
    if (brand) params.brand = brand;
    setData(null);
    api.financials(params).then(setData);
  }, [month, brand]);

  async function downloadCsv() {
    const params = new URLSearchParams();
    if (month) params.set("month", month);
    if (brand) params.set("brand", brand);
    const res = await fetch("/api/financials/export.csv?" + params, {
      headers: { Authorization: `Bearer ${getToken()}` },
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `kona-financials-${month || "all"}.csv`;
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

      <div className="toolbar">
        <select className="select" value={month} onChange={(e) => setMonth(e.target.value)}>
          <option value="">All months</option>
          {months.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <select className="select" value={brand} onChange={(e) => setBrand(e.target.value)}>
          <option value="">All brands</option>
          {(data?.brands ?? []).map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
        {data && <span className="muted">{data.total} entries</span>}
      </div>

      {!data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <Empty text="No ledger entries yet — run the pipeline." />
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
                  <th>Date</th><th>Event</th><th>Brand</th><th>Type</th>
                  <th className="right">Units</th>
                  <th className="right">Subtotal</th>
                  <th className="right">Tax</th>
                  <th className="right">CC fee</th>
                  <th className="right">Check / Invoice</th>
                  <th className="right">Invoice total</th>
                  <th className="right">Balance due</th>
                  <th className="right">Square net</th>
                  <th className="right">Square CC</th>
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
                    <td className="right">{r.units_served || "—"}</td>
                    <td className="right">{money(r.subtotal)}</td>
                    <td className="right">{money(r.sales_tax)}</td>
                    <td className="right">{money(r.cc_fee)}</td>
                    <td className="right">{r.check_invoice > 0 ? money(r.check_invoice) : "—"}</td>
                    <td className="right"><strong>{money(r.invoice_total)}</strong></td>
                    <td className="right">{money(r.balance_due)}</td>
                    <td className="right">
                      {r.square_net_card > 0 ? (
                        <>{money(r.square_net_card)} <span className="muted" style={{ fontSize: 11 }}>({r.square_orders})</span></>
                      ) : "—"}
                    </td>
                    <td className="right">{r.square_cc_fee > 0 ? money(r.square_cc_fee) : "—"}</td>
                    <td><Badge kind="gray">{r.payment_method || "—"}</Badge></td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr style={{ fontWeight: 700, background: "var(--surface-2)" }}>
                  <td colSpan={4}>Totals ({month || "all"})</td>
                  <td className="right">{data.totals.units_served}</td>
                  <td className="right">{money(data.totals.subtotal)}</td>
                  <td className="right">{money(data.totals.sales_tax)}</td>
                  <td className="right">{money(data.totals.cc_fee)}</td>
                  <td className="right">{money(data.totals.check_invoice)}</td>
                  <td className="right">{money(data.totals.invoice_total)}</td>
                  <td className="right">{money(data.totals.balance_due)}</td>
                  <td className="right">{money(data.totals.square_sales)}</td>
                  <td colSpan={2} />
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
