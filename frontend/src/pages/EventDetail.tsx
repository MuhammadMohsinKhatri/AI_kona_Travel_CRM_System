import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, EventDetail as Detail } from "../api/client";
import { Badge, Loading, money } from "../components/ui";

export default function EventDetail() {
  const { id } = useParams();
  const [ev, setEv] = useState<Detail | null>(null);

  useEffect(() => {
    if (id) api.event(Number(id)).then(setEv);
  }, [id]);

  if (!ev) return <Loading />;

  const calc = ev.calculations || {};
  const cls = ev.classification || {};

  return (
    <>
      <p className="muted"><Link to="/events">← Events</Link></p>
      <div className="topbar">
        <div>
          <h1 className="page-title">{ev.event_name || "(unnamed event)"}</h1>
          <p className="page-sub">
            {ev.event_code || ev.crm_event_id} · {ev.brand} · {ev.event_date || "no date"}
          </p>
        </div>
        <div className="flex">
          <Badge kind={ev.status}>{ev.status}</Badge>
        </div>
      </div>

      {ev.error && (
        <div className="card" style={{ borderColor: "var(--crit)", marginBottom: 16 }}>
          <strong style={{ color: "var(--crit)" }}>Processing error</strong>
          <pre className="json" style={{ marginTop: 8 }}>{ev.error}</pre>
        </div>
      )}

      <div className="grid cols-2">
        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>Classification</div>
          <div className="kv">
            <div className="k">Event type</div><div className="v">{ev.event_type || "—"}</div>
            <div className="k">Billing model</div><div className="v">{ev.billing_model || "—"}</div>
            <div className="k">Taxable</div><div className="v">{String(cls.TAXABLE ?? "—")}</div>
            <div className="k">Payment method</div><div className="v">{String(cls.PAYMENT_METHOD ?? "—")}</div>
            <div className="k">Units served</div><div className="v">{String(cls.UNITS_SERVED_TOTAL ?? "—")}</div>
            <div className="k">Rate / serving</div><div className="v">{money(Number(cls.RATE_PER_SERVING) || 0)}</div>
            <div className="k">Square used</div><div className="v">{String(cls.SQUARE_USED ?? "—")}</div>
          </div>
          {cls.NOTE ? <p className="muted" style={{ marginTop: 12, fontSize: 13 }}>{String(cls.NOTE)}</p> : null}
        </div>

        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>Invoice calculation</div>
          <div className="kv">
            <div className="k">Subtotal</div><div className="v">{money(Number(calc.SUBTOTAL) || 0)}</div>
            <div className="k">Sales tax</div><div className="v">{money(Number(calc.SALES_TAX) || 0)}</div>
            <div className="k">CC fee</div><div className="v">{money(Number(calc.CC_FEE) || 0)}</div>
            <div className="k">Final invoice</div>
            <div className="v" style={{ fontSize: 18, fontWeight: 700 }}>{money(Number(calc.FINAL_INVOICE_AMOUNT) || 0)}</div>
            <div className="k">Balance due</div><div className="v">{money(Number(calc.BALANCE_DUE) || 0)}</div>
            {calc.HAS_VARIANCE ? (
              <>
                <div className="k">Variance</div>
                <div className="v" style={{ color: "var(--warn)" }}>{money(Number(calc.VARIANCE_AMOUNT) || 0)}</div>
              </>
            ) : null}
          </div>
        </div>
      </div>

      {ev.square && Object.keys(ev.square).length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="section-title" style={{ marginTop: 0 }}>Square reconciliation</div>
          <div className="kv">
            <div className="k">Device</div><div className="v">{String((ev.square as any).device_id ?? "not mapped")}</div>
            <div className="k">Orders</div><div className="v">{String((ev.square as any).order_count ?? 0)}</div>
            <div className="k">Total collected</div><div className="v">{money(Number((ev.square as any).total_collected) || 0)}</div>
          </div>
        </div>
      )}

      {ev.alerts.length > 0 && (
        <>
          <div className="section-title">Alerts ({ev.alerts.length})</div>
          {ev.alerts.map((a) => (
            <div key={a.id} className={`alert-row ${a.severity}`}>
              <div className="flex between">
                <Badge kind={a.severity}>{a.severity}</Badge>
                {a.resolved && <span className="badge green">resolved</span>}
              </div>
              <div style={{ fontWeight: 600, marginTop: 6 }}>{a.issue}</div>
              <div className="muted" style={{ fontSize: 13 }}>👉 {a.action}</div>
            </div>
          ))}
        </>
      )}

      {ev.invoices.length > 0 && (
        <>
          <div className="section-title">Invoice drafts ({ev.invoices.length})</div>
          {ev.invoices.map((inv) => (
            <div key={inv.id} className="card" style={{ marginBottom: 10 }}>
              <div className="flex between">
                <strong>{inv.title}</strong>
                <span>{money(inv.grand_total)}</span>
              </div>
              <div className="muted" style={{ fontSize: 13 }}>
                {inv.invoice_type} · {inv.invoice_number} · {inv.status}
              </div>
              <LineItems payload={inv.payload} />
            </div>
          ))}
        </>
      )}

      <div className="section-title">Raw payloads</div>
      <div className="grid cols-2">
        <Collapsible title="Cleaned event" obj={ev.cleaned} />
        <Collapsible title="Classification" obj={ev.classification} />
        <Collapsible title="Calculations" obj={ev.calculations} />
        <Collapsible title="Raw CRM event" obj={ev.raw} />
      </div>
    </>
  );
}

function LineItems({ payload }: { payload: Record<string, unknown> }) {
  const items = (payload?.clientInvoiceItems as any[]) || [];
  if (!items.length) return null;
  return (
    <table style={{ marginTop: 10 }}>
      <thead>
        <tr><th>Item</th><th className="right">Qty</th><th className="right">Price</th><th className="right">Amount</th></tr>
      </thead>
      <tbody>
        {items.map((it, i) => (
          <tr key={i} style={{ cursor: "default" }}>
            <td>{it.name}</td>
            <td className="right">{it.quantity}</td>
            <td className="right">{money(it.price)}</td>
            <td className="right">{money(it.amount)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Collapsible({ title, obj }: { title: string; obj: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="card">
      <div className="flex between" style={{ cursor: "pointer" }} onClick={() => setOpen(!open)}>
        <strong>{title}</strong>
        <span className="muted">{open ? "▲" : "▼"}</span>
      </div>
      {open && <pre className="json" style={{ marginTop: 10 }}>{JSON.stringify(obj, null, 2)}</pre>}
    </div>
  );
}
