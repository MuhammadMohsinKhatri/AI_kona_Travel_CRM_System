import { useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { api, CrmAuditEntry, EventDetail as Detail } from "../api/client";
import { Badge, Loading, money } from "../components/ui";

const ACTION_LABELS: Record<string, string> = {
  invoice_created: "Invoice created",
  invoice_deleted: "Invoice deleted",
  invoice_skipped: "Invoice skipped",
  event_updated: "Event updated",
};

export default function EventDetail() {
  const { id } = useParams();
  const location = useLocation();
  // The list page that opened this detail passes where to return to; on a
  // direct load / refresh there's no state, so fall back to Events.
  const from = (location.state as { from?: string; label?: string } | null) ?? {};
  const backTo = from.from ?? "/events";
  const backLabel = from.label ?? "Events";
  const [ev, setEv] = useState<Detail | null>(null);
  const [audit, setAudit] = useState<CrmAuditEntry[] | null>(null);

  useEffect(() => {
    if (id) api.event(Number(id)).then(setEv);
  }, [id]);

  // KonaOS activity history — every write our system has made to this
  // specific event (see the CRM Activity page for the global feed).
  useEffect(() => {
    if (ev) api.crmAudit({ event_id: String(ev.id), page_size: "20" }).then((r) => setAudit(r.items));
  }, [ev?.id]);

  if (!ev) return <Loading />;

  const calc = ev.calculations || {};
  const cls = ev.classification || {};
  const cl = (ev.cleaned || {}) as any;
  const sq = (ev.square || {}) as any;
  const bd = sq.breakdown || {};
  const equip = sq.equipment || {};
  // Selling events settle at the truck via Square/cash — no client invoice
  // exists, so the invoice card is replaced by the at-event sales card.
  const isSelling = (ev.event_type || "").toLowerCase() === "selling";
  const squareMentioned = String(cls.SQUARE_USED ?? "").toUpperCase() === "TRUE";
  const deviceLabel = equip.equipment_name
    ? `${equip.equipment_name} · ${squareMentioned ? "named by driver" : "assigned to event"}`
    : "—";

  return (
    <>
      <p className="muted"><Link to={backTo}>← {backLabel}</Link></p>
      <div className="topbar">
        <div>
          <h1 className="page-title">{ev.event_name || "(unnamed event)"}</h1>
          <p className="page-sub">
            {ev.event_code || ev.crm_event_id} · {ev.brand} · <strong>{ev.event_date || "no date"}</strong>
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
            <div className="k">Event start</div><div className="v">{fmtDateTime(cl.EVENT_STARTED)}</div>
            <div className="k">Event end</div><div className="v">{fmtDateTime(cl.EVENT_ENDED)}</div>
            <div className="k">Taxable</div><div className="v">{String(cls.TAXABLE ?? "—")}</div>
            <div className="k">Payment method</div><div className="v">{String(cls.PAYMENT_METHOD ?? "—")}</div>
            <div className="k">Units served</div><div className="v">{String(cls.UNITS_SERVED_TOTAL ?? "—")}</div>
            <div className="k">Rate / serving</div><div className="v">{money(Number(cls.RATE_PER_SERVING) || 0)}</div>
            <div className="k">Square device</div><div className="v">{deviceLabel}</div>
          </div>
          {cls.NOTE ? <ReasoningNotes note={String(cls.NOTE)} /> : null}
          <SubtotalBreakdown billingModel={ev.billing_model} cls={cls} calc={calc} />
          <SourceNotes cleaned={cl} />
        </div>

        {isSelling ? (
          <div className="card">
            <div className="section-title" style={{ marginTop: 0 }}>At-event sales</div>
            <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
              Selling event — guests pay at the truck, so no client invoice is raised.
              Card sales are pulled from Square for this event's device during the event window.
            </p>
            <div className="kv">
              <div className="k">Square device</div><div className="v">{deviceLabel}</div>
              <div className="k">Card orders</div><div className="v">{String(sq.order_count ?? 0)}</div>
              <div className="k">Net card sales</div>
              <div className="v" style={{ fontSize: 18, fontWeight: 700 }}>{money(Number(bd.net_card) || 0)}</div>
              <div className="k">Card tax</div><div className="v">{money(Number(bd.card_tax) || 0)}</div>
              <div className="k">Tips</div><div className="v">{money(Number(bd.tips_card) || 0)}</div>
              <div className="k">Cash collected</div><div className="v">{money(Number(cls.CASH_COLLECTED_AMOUNT) || 0)}</div>
            </div>
          </div>
        ) : (
          <div className="card">
            <div className="flex between">
              <div className="section-title" style={{ marginTop: 0 }}>Invoice calculation</div>
              {Number(calc.CC_FEE) > 0 && (
                <button
                  className="btn"
                  title="Client paid by check — recalculate the invoice without the 4% processing fee"
                  onClick={async () => {
                    if (!id) return;
                    setEv(await api.waiveCcFee(Number(id)));
                  }}
                >
                  Paid by check — remove CC fee
                </button>
              )}
              {calc.CC_FEE_WAIVED ? <span className="badge green">CC fee waived</span> : null}
            </div>
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
        )}
      </div>

      {/* Selling events show Square in the at-event sales card above. */}
      {!isSelling && ev.square && Object.keys(ev.square).length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="section-title" style={{ marginTop: 0 }}>Square reconciliation</div>
          <div className="kv">
            <div className="k">Device</div><div className="v">{String(sq.device_id ?? "not mapped")}</div>
            <div className="k">Orders</div><div className="v">{String(sq.order_count ?? 0)}</div>
            <div className="k">Total collected</div><div className="v">{money(Number(sq.total_collected) || 0)}</div>
            {sq.note ? <><div className="k">Note</div><div className="v muted">{String(sq.note)}</div></> : null}
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

      {audit && audit.length > 0 && (
        <>
          <div className="section-title">KonaOS activity ({audit.length})</div>
          <div className="card" style={{ marginBottom: 16 }}>
            <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
              Every write our system has made to this event in KonaOS — see{" "}
              <Link to="/crm-activity">CRM Activity</Link> for the full history across all events.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {audit.map((a) => (
                <div key={a.id} style={{ borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                  <div className="flex between">
                    <Badge kind={a.action}>{ACTION_LABELS[a.action] || a.action}</Badge>
                    <span className="muted" style={{ fontSize: 12 }}>
                      {a.created_at ? new Date(a.created_at).toLocaleString() : "—"}
                    </span>
                  </div>
                  <div style={{ fontSize: 13, marginTop: 4 }}>{a.summary}</div>
                </div>
              ))}
            </div>
          </div>
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

/** Classifier reasoning as a scannable bullet list. New notes are written one
 *  decision per line; older single-paragraph notes are split at sentence
 *  boundaries so they stop reading as a wall of text. */
function ReasoningNotes({ note }: { note: string }) {
  const lines = (note.includes("\n") ? note.split(/\n+/) : note.split(/(?<=\.)\s+(?=[A-Z"'])/))
    .map((s) => s.trim().replace(/^[-•]\s*/, ""))
    .filter(Boolean);
  if (!lines.length) return null;
  return (
    <ul className="muted" style={{ marginTop: 12, fontSize: 13, paddingLeft: 18,
      display: "flex", flexDirection: "column", gap: 4 }}>
      {lines.map((s, i) => <li key={i}>{s}</li>)}
    </ul>
  );
}

/** Format a KonaOS wall-clock ISO string ("2026-07-17T10:00:00.000") without
 *  going through Date() — the string is already America/New_York local time and
 *  has no tz suffix, so Date() would shift it by the viewer's offset. */
function fmtDateTime(iso?: string): string {
  if (!iso) return "—";
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!m) return String(iso);
  const [, y, mo, d, hh, mm] = m;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  let h = Number(hh);
  const ampm = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return `${months[Number(mo) - 1]} ${Number(d)}, ${y} · ${h}:${mm} ${ampm}`;
}

/** Strip CRM note HTML down to readable text (no DOM insertion — avoids any
 *  side effects from setting innerHTML). */
function htmlToText(html: string): string {
  return html
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(p|div|li|tr)>/gi, "\n")
    .replace(/<li[^>]*>/gi, "• ")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

interface SubtotalLine {
  label: string;
  qty?: number;
  rate?: number;
  amount: number;
}

/** Shows how the Subtotal is built up, line by line — derived directly from
 *  the classification inputs + calculation outputs (calculate_invoice's
 *  per-model formulas in billing.py), so it renders for every processed
 *  event regardless of whether an invoice draft exists (selling/MG events
 *  never get one; some invoice events may lack one for other reasons). A
 *  residual "Adjustment" line absorbs any rounding/edge-case gap so the
 *  bullets always foot exactly to the displayed Subtotal. */
function SubtotalBreakdown({
  billingModel,
  cls,
  calc,
}: {
  billingModel: string;
  cls: Record<string, unknown>;
  calc: Record<string, unknown>;
}) {
  const n = (v: unknown) => Number(v) || 0;
  const model = (billingModel || "").toUpperCase();
  const subtotal = n(calc.SUBTOTAL);
  const locationFee = n(cls.LOCATION_FEE);
  const addonAmount = n(calc.ADDON_AMOUNT);
  const unitsTotal = n(cls.UNITS_SERVED_TOTAL);
  const unitsIncluded = n(cls.UNITS_INCLUDED_IN_BASE);
  const rate = n(cls.RATE_PER_SERVING);

  const items: SubtotalLine[] = [];

  if (model === "INVOICE_PER_SERVING" || model === "INVOICE_BASE_FEE_PLUS_SERVINGS") {
    if (model === "INVOICE_BASE_FEE_PLUS_SERVINGS" && n(cls.BASE_AMOUNT)) {
      items.push({ label: "Base Fee", amount: n(cls.BASE_AMOUNT) });
    }
    if (unitsTotal) items.push({ label: "Kona Ice Servings", qty: unitsTotal, rate, amount: n(calc.UNIT_REVENUE) });
  } else if (model === "INVOICE_FIXED_PACKAGE") {
    if (n(cls.BASE_AMOUNT)) items.push({ label: `Base Package (covers ${unitsIncluded || "—"} servings)`, amount: n(cls.BASE_AMOUNT) });
    if (n(calc.OVERAGE_UNITS) > 0) {
      items.push({ label: "Additional Servings (Overage)", qty: n(calc.OVERAGE_UNITS), rate, amount: n(calc.OVERAGE_REVENUE) });
    }
  } else if (model === "INVOICE_HOURLY") {
    if (n(cls.TOTAL_EVENT_HOURS)) {
      items.push({ label: "Event Time", qty: n(cls.TOTAL_EVENT_HOURS), rate: n(cls.HOURLY_RATE), amount: n(calc.HOURLY_REVENUE) });
    }
    if (n(calc.OVERAGE_UNITS) > 0) {
      const label = unitsIncluded > 0 ? "Additional Servings (Overage)" : "Kona Ice Servings";
      items.push({ label, qty: n(calc.OVERAGE_UNITS), rate, amount: n(calc.OVERAGE_REVENUE) });
    }
  } else if (model === "SELLING_OPEN" || model === "SELLING_WITH_GIVEBACK") {
    if (unitsTotal) items.push({ label: "Event Sales", qty: unitsTotal, rate, amount: n(calc.SALES_AMOUNT) });
    if (n(calc.GIVEBACK_AMOUNT) > 0) {
      const pct = n(cls.GIVEBACK_PERCENTAGE);
      items.push({ label: `Giveback${pct ? ` (${(pct * 100).toFixed(0)}%)` : ""}`, amount: -n(calc.GIVEBACK_AMOUNT) });
    }
  } else if (model === "MIN_GUARANTEE_HOURLY") {
    items.push({ label: "Minimum Guarantee", qty: n(cls.TOTAL_EVENT_HOURS), rate: n(cls.MINIMUM_AMOUNT_PER_HOUR), amount: n(calc.MINIMUM_REQUIRED) });
  } else if (model === "MIN_GUARANTEE_FLAT" || model === "HYBRID_SELLING_PLUS_MIN_GUARANTEE") {
    items.push({ label: "Minimum Guarantee", amount: n(calc.MINIMUM_REQUIRED) });
  } else if (model === "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA") {
    if (n(cls.BASE_AMOUNT)) items.push({ label: `Host Package (covers ${unitsIncluded || "—"} servings)`, amount: n(cls.BASE_AMOUNT) });
    if (n(calc.OVERAGE_UNITS) > 0) {
      items.push({ label: "Guest Extra Servings", qty: n(calc.OVERAGE_UNITS), rate, amount: n(calc.OVERAGE_REVENUE) });
    }
  } else if (model === "HYBRID_HOST_SUBSIDY_PLUS_GUEST_PAYMENT") {
    if (unitsTotal) {
      items.push({ label: "Host Subsidy", qty: unitsTotal, rate: n(cls.HOST_SUBSIDY_PER_SERVING), amount: n(calc.HOST_AMOUNT) });
      items.push({ label: "Guest Payment", qty: unitsTotal, rate: n(cls.GUEST_RATE_PER_SERVING), amount: n(calc.GUEST_AMOUNT) });
    }
  } else if (n(cls.BASE_AMOUNT)) {
    items.push({ label: "Event Services", amount: n(cls.BASE_AMOUNT) });
  }

  if (locationFee) items.push({ label: "Location / Destination Fee", amount: locationFee });
  if (addonAmount) items.push({ label: String(calc.ADDON_LABEL || "Add-on"), amount: addonAmount });

  if (!items.length) return null;

  // Safety net: reconcile any gap between the itemized lines and the actual
  // Subtotal (e.g. a billing-model edge case not itemized above) so the
  // bullets never silently disagree with the number shown in the calc card.
  const itemSum = items.reduce((a, it) => a + it.amount, 0);
  const residual = Math.round((subtotal - itemSum) * 100) / 100;
  if (Math.abs(residual) >= 0.01) items.push({ label: "Adjustment", amount: residual });

  return (
    <div style={{ marginTop: 12 }}>
      <div className="section-title" style={{ marginTop: 0, fontSize: 13 }}>
        How the subtotal is calculated
      </div>
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, display: "flex",
        flexDirection: "column", gap: 4 }}>
        {items.map((it, i) => (
          <li key={i}>
            {it.label}
            {it.qty !== undefined && it.rate !== undefined ? (
              <span className="muted"> — {it.qty} × {money(it.rate)}</span>
            ) : null}
            {" = "}
            <strong>{it.amount < 0 ? `-${money(-it.amount)}` : money(it.amount)}</strong>
          </li>
        ))}
      </ul>
      <div style={{ fontSize: 13, marginTop: 6, paddingLeft: 18, fontWeight: 700 }}>
        = Subtotal {money(subtotal)}
      </div>
    </div>
  );
}

/** The raw CRM notes the classifier reasoned over — shown so the calculated
 *  values (rate, minimum, servings, tax) can be traced back to their source. */
function SourceNotes({ cleaned }: { cleaned: Record<string, unknown> }) {
  const items: [string, string][] = [
    ["Admin notes", String(cleaned.ADMIN_NOTES ?? "").trim()],
    ["Driver notes", String(cleaned.DRIVER_NOTES ?? "").trim()],
    ["Event notes", htmlToText(String(cleaned.EVENT_NOTES_HTML ?? ""))],
    ["Location notes", String(cleaned.LOCATION_NOTES ?? "").trim()],
  ];
  const present = items.filter(([, v]) => v);
  if (!present.length) return null;
  return (
    <div style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
      <div className="section-title" style={{ marginTop: 0, fontSize: 13 }}>
        Source notes (from CRM)
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {present.map(([label, text]) => (
          <div key={label}>
            <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".03em", marginBottom: 2 }}>
              {label}
            </div>
            <div style={{ fontSize: 13, whiteSpace: "pre-wrap", lineHeight: 1.4 }}>{text}</div>
          </div>
        ))}
      </div>
    </div>
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
