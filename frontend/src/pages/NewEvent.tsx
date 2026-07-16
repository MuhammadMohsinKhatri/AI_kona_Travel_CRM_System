import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, FormOptions, QuickCreateResult } from "../api/client";

/** Structured event intake → creates the event directly in Kona OS.
 *
 * One entry point, no double work. Event type + a predefined billing-model
 * dropdown + structured number fields — no free-text pricing sentences — so
 * the generated notes and the invoice estimate are exact. */

type EventType = "Invoice" | "Selling" | "Min Guarantee" | "Hybrid";
type PayMethod = "Check" | "Credit Card" | "Cash";

/** Predefined billing models, filtered by event type. */
const BILLING_MODELS: { key: string; label: string; type: EventType }[] = [
  { key: "INVOICE_PER_SERVING", label: "Per serving — $X per serving", type: "Invoice" },
  { key: "INVOICE_BASE_FEE_PLUS_SERVINGS", label: "Base fee + $X per serving", type: "Invoice" },
  { key: "INVOICE_FIXED_PACKAGE", label: "Fixed package — floor covers N servings, overage extra", type: "Invoice" },
  { key: "INVOICE_HOURLY", label: "Hourly — $X per hour", type: "Invoice" },
  { key: "SELLING_OPEN", label: "Open selling — guests pay individually", type: "Selling" },
  { key: "SELLING_WITH_GIVEBACK", label: "Selling with giveback %", type: "Selling" },
  { key: "MIN_GUARANTEE_FLAT", label: "Flat minimum guarantee", type: "Min Guarantee" },
  { key: "MIN_GUARANTEE_HOURLY", label: "Minimum guarantee per hour", type: "Min Guarantee" },
  { key: "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA", label: "Host base covers N servings, guests pay extras", type: "Hybrid" },
];

interface F {
  brandId: string; name: string; businessName: string; industryId: string;
  status: string; date: string; startTime: string; endTime: string;
  prepay: boolean; kurbside: boolean;
  address: string; city: string; state: string; zip: string; county: string;
  contactName: string; contactTitle: string; contactEmail: string; contactPhone: string;
  taxExempt: "" | "NO" | "YES"; giveback: string; deposit: string; discount: string;
  eventType: "" | EventType; billing: string;
  ratePerServing: string; baseAmount: string; unitsIncluded: string;
  hourlyRate: string; minFlat: string; mgPerHour: string; guestRate: string;
  locationFee: string;
  addonAmount: string; addonLabel: string; allIn: boolean;
  serveKeep: string; paymentModel: string;
  attendees: string; parking: string; additional: string; cardOnly: boolean;
  paid: boolean; method: "" | PayMethod; cashAmount: string;
  actualCount: string; actualTimes: string; squareDevice: string;
}

const initial: F = {
  brandId: "", name: "", businessName: "", industryId: "", status: "pending",
  date: "", startTime: "10:00", endTime: "12:00", prepay: false, kurbside: false,
  address: "", city: "", state: "Maryland", zip: "", county: "",
  contactName: "", contactTitle: "", contactEmail: "", contactPhone: "",
  taxExempt: "", giveback: "", deposit: "", discount: "",
  eventType: "", billing: "",
  ratePerServing: "", baseAmount: "", unitsIncluded: "",
  hourlyRate: "", minFlat: "", mgPerHour: "", guestRate: "",
  locationFee: "",
  addonAmount: "", addonLabel: "", allIn: false,
  serveKeep: "", paymentModel: "",
  attendees: "", parking: "", additional: "", cardOnly: false,
  paid: false, method: "", cashAmount: "",
  actualCount: "", actualTimes: "", squareDevice: "",
};

const needServe = (t: string) => t === "Invoice" || t === "Hybrid";
const modelsFor = (t: string) => BILLING_MODELS.filter((m) => m.type === t);

/** Which structured pricing fields each billing model needs. */
const FIELD_MAP: Record<string, string[]> = {
  INVOICE_PER_SERVING: ["ratePerServing"],
  INVOICE_BASE_FEE_PLUS_SERVINGS: ["baseAmount", "ratePerServing"],
  INVOICE_FIXED_PACKAGE: ["baseAmount", "unitsIncluded", "ratePerServing"],
  INVOICE_HOURLY: ["hourlyRate"],
  SELLING_OPEN: [],
  SELLING_WITH_GIVEBACK: ["giveback"],
  MIN_GUARANTEE_FLAT: ["minFlat"],
  MIN_GUARANTEE_HOURLY: ["mgPerHour"],
  HYBRID_HOST_BASE_PLUS_GUEST_EXTRA: ["baseAmount", "unitsIncluded", "ratePerServing"],
};
const FIELD_LABELS: Record<string, string> = {
  ratePerServing: "Rate per serving ($)",
  baseAmount: "Base / package amount ($)",
  unitsIncluded: "Servings included",
  hourlyRate: "Hourly rate ($)",
  minFlat: "Flat minimum ($)",
  mgPerHour: "Minimum per hour ($)",
  giveback: "Giveback %",
};

function hoursBetween(f: F): number {
  if (!f.startTime || !f.endTime) return 0;
  const [sh, sm] = f.startTime.split(":").map(Number);
  const [eh, em] = f.endTime.split(":").map(Number);
  return Math.max(0, (eh * 60 + em - sh * 60 - sm) / 60);
}

/** ADMIN NOTES — the free-text pricing/financial quote (matches how KonaOS
 *  Admin Notes are written). This is the billing basis the classifier reads. */
function buildAdminNotes(f: F): string {
  const lines: string[] = [];
  switch (f.billing) {
    case "INVOICE_PER_SERVING":
      lines.push(`$${f.ratePerServing || "0"} per serving. Send invoice.`); break;
    case "INVOICE_BASE_FEE_PLUS_SERVINGS":
      lines.push(`Setup fee $${f.baseAmount || "0"} plus $${f.ratePerServing || "0"} per serving. Send invoice.`); break;
    case "INVOICE_FIXED_PACKAGE":
      lines.push(`$${f.baseAmount || "0"} covers up to ${f.unitsIncluded || "0"} servings, each additional $${f.ratePerServing || "0"} a piece. Send invoice.`); break;
    case "INVOICE_HOURLY":
      lines.push(`$${f.hourlyRate || "0"} per hour. Send invoice.`); break;
    case "SELLING_OPEN":
      lines.push("Open selling event. Guests pay individually."); break;
    case "SELLING_WITH_GIVEBACK":
      lines.push(`Selling event. Giveback percentage: ${f.giveback || "0"}%.`); break;
    case "MIN_GUARANTEE_FLAT":
      lines.push(`Minimum guarantee $${f.minFlat || "0"} flat. Host covers shortfall.`); break;
    case "MIN_GUARANTEE_HOURLY":
      lines.push(`Minimum guarantee $${f.mgPerHour || "0"} per hour. Host covers shortfall.`); break;
    case "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA":
      lines.push(
        `Host pays $${f.baseAmount || "0"} base covering ${f.unitsIncluded || "0"} servings. ` +
        `Additional servings $${f.ratePerServing || "0"} billed to host.` +
        (f.guestRate ? ` Guests pay $${f.guestRate} per serving for extras.` : "")
      ); break;
  }
  if (f.paymentModel.trim()) lines.push(f.paymentModel.trim());
  if (Number(f.giveback)) lines.push(`Giveback percentage: ${f.giveback}%.`);
  if (Number(f.addonAmount)) lines.push(`Plus $${f.addonAmount} for ${f.addonLabel || "add-on"}.`);
  if (Number(f.locationFee)) lines.push(`$${f.locationFee} location fee.`);
  if (Number(f.deposit)) lines.push(`Deposit $${f.deposit} required.`);
  if (Number(f.discount)) lines.push(`Discount $${f.discount} applied.`);
  lines.push(f.taxExempt === "YES" ? "Client is tax exempt." : "Plus tax.");
  if (f.allIn) lines.push("Quoted total is all-in (tax and fee included).");
  if (f.cardOnly) lines.push("Card only, no on-site cash.");
  return lines.join(" ");
}

/** EVENT NOTES — the labeled structure KonaOS uses (EVENT TYPE / ATTENDEES /
 *  SERVE & KEEP COUNT / ADD'L INSTRUCTION). Returns "LABEL: value" lines. */
function buildEventNotes(f: F): string[] {
  const lines: string[] = [];
  lines.push(`EVENT TYPE: ${f.eventType || "—"}`);
  if (f.attendees) lines.push(`ATTENDEES: ${f.attendees} people`);
  const serve = f.serveKeep.trim() || (f.unitsIncluded ? `${f.unitsIncluded} servings included` : "");
  if (serve) lines.push(`SERVE & KEEP COUNT: ${serve}`);
  if (f.parking) lines.push(`PARKING: ${f.parking.trim()}`);
  if (f.additional) lines.push(`ADD'L INSTRUCTION: ${f.additional.trim()}`);
  return lines;
}

/** DRIVER NOTES — the actuals, only when the admin is entering a closed event. */
function buildDriverNotes(f: F): string[] {
  const lines: string[] = [];
  if (f.actualCount) lines.push(`ACTUAL SERVING COUNT: ${f.actualCount}`);
  if (f.paid) {
    lines.push(`PAID: ${f.method ? f.method : "yes"}`);
    if (f.method === "Cash" && f.cashAmount) lines.push(`CASH COLLECTED: $${f.cashAmount}`);
  }
  if (f.actualTimes) lines.push(`ACTUAL TIMES: ${f.actualTimes.trim()}`);
  if (f.squareDevice) lines.push(`SQUARE DEVICE: ${f.squareDevice.trim()}`);
  return lines;
}

/** Exact estimate from the structured fields (mirrors the billing engine). */
function estimate(f: F) {
  const n = (v: string) => Number(v) || 0;
  const count = n(f.actualCount);
  const hours = hoursBetween(f);
  let subtotal = 0;
  let detail = "";
  switch (f.billing) {
    case "INVOICE_PER_SERVING":
      subtotal = count * n(f.ratePerServing);
      detail = `${count} × $${n(f.ratePerServing)}`; break;
    case "INVOICE_BASE_FEE_PLUS_SERVINGS":
      subtotal = n(f.baseAmount) + count * n(f.ratePerServing);
      detail = `$${n(f.baseAmount)} + ${count} × $${n(f.ratePerServing)}`; break;
    case "INVOICE_FIXED_PACKAGE": {
      const over = Math.max(0, count - n(f.unitsIncluded));
      subtotal = n(f.baseAmount) + over * n(f.ratePerServing);
      detail = over > 0 ? `$${n(f.baseAmount)} + ${over} over × $${n(f.ratePerServing)}` : `$${n(f.baseAmount)} floor`;
      break;
    }
    case "INVOICE_HOURLY":
      subtotal = hours * n(f.hourlyRate);
      detail = `${hours}h × $${n(f.hourlyRate)}`; break;
    case "MIN_GUARANTEE_FLAT":
      subtotal = n(f.minFlat); detail = "guaranteed floor"; break;
    case "MIN_GUARANTEE_HOURLY":
      subtotal = hours * n(f.mgPerHour);
      detail = `${hours}h × $${n(f.mgPerHour)} floor`; break;
    case "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA": {
      const over = Math.max(0, count - n(f.unitsIncluded));
      subtotal = n(f.baseAmount) + over * n(f.ratePerServing);
      detail = `host $${n(f.baseAmount)}${over ? ` + ${over} over × $${n(f.ratePerServing)}` : ""}`;
      break;
    }
    default:
      return null; // selling models: guests pay, no host invoice
  }
  subtotal += n(f.locationFee) + n(f.addonAmount);
  subtotal = Math.max(0, subtotal - n(f.discount));
  const tax = f.allIn || f.taxExempt === "YES" ? 0 : +(subtotal * 0.06).toFixed(2);
  const noFee = f.allIn || (f.paid && f.method === "Check");
  const cc = noFee ? 0 : +(subtotal * 0.04).toFixed(2);
  return { subtotal: +subtotal.toFixed(2), detail, tax, cc, noFee, allIn: f.allIn,
           total: +(subtotal + tax + cc).toFixed(2), deposit: n(f.deposit) };
}

export default function NewEvent() {
  const [opts, setOpts] = useState<FormOptions | null>(null);
  const [f, setF] = useState<F>(initial);
  const [phase, setPhase] = useState<"form" | "creating" | "done">("form");
  const [result, setResult] = useState<QuickCreateResult | null>(null);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    api.konaosFormOptions().then((o) => {
      setOpts(o);
      setF((prev) => ({ ...prev, brandId: o.brands.find((b) => b.label === "Kona Ice")?.id || o.brands[0]?.id || "" }));
    });
  }, []);

  const adminNotes = useMemo(() => buildAdminNotes(f), [f]);
  const eventNotes = useMemo(() => buildEventNotes(f), [f]);
  const driverNotes = useMemo(() => buildDriverNotes(f), [f]);
  const est = useMemo(() => estimate(f), [f]);
  const up = (patch: Partial<F>) => setF((prev) => ({ ...prev, ...patch }));

  const missing = useMemo(() => {
    const m: string[] = [];
    if (!f.name) m.push("Event name");
    if (!f.date) m.push("Event date");
    if (!f.brandId) m.push("Brand");
    if (!f.address) m.push("Address");
    if (!f.city) m.push("City");
    if (!f.zip) m.push("Zip");
    if (!f.contactName) m.push("Contact name");
    if (!f.contactEmail) m.push("Contact email");
    if (!f.taxExempt) m.push("Tax exempt status");
    if (!f.eventType) m.push("Event type");
    if (f.eventType && !f.billing) m.push("Billing model");
    for (const field of FIELD_MAP[f.billing] ?? []) {
      if (!(f as any)[field]) m.push(FIELD_LABELS[field]);
    }
    if (!f.attendees) m.push("Attendees");
    if (needServe(f.eventType) && !f.actualCount) m.push("Actual serving count");
    if (f.paid && !f.method) m.push("Payment method");
    if (f.paid && f.method === "Cash" && !f.cashAmount) m.push("Cash amount");
    return m;
  }, [f]);

  async function submit() {
    if (missing.length) return;
    setPhase("creating");
    setError("");
    const toMs = (t: string) => new Date(`${f.date}T${t}:00`).getTime();
    try {
      const res = await api.konaosQuickCreate({
        name: f.name,
        businessName: f.businessName || f.name,
        brandId: f.brandId,
        startDateTime: toMs(f.startTime),
        endDateTime: toMs(f.endTime),
        addressLine1: f.address,
        city: f.city,
        state: f.state,
        zipCode: f.zip,
        county: f.county,
        contactName: f.contactName,
        contactTitle: f.contactTitle,
        contactEmail: f.contactEmail,
        contactPhone: f.contactPhone,
        adminNotes: adminNotes,
        notes: `<p>${eventNotes.join("<br>")}</p>`,
        driverNotes: driverNotes.length ? driverNotes.join("\n") : "",
        manualStatus: f.status,
        clientIndustriesTypeId: f.industryId || "",
        prePay: f.prepay,
        kurbsideEvent: f.kurbside,
        taxPercent: f.taxExempt === "YES" ? "0" : "6",
        givebackPercentage: f.giveback || "0",
      });
      setResult(res);
      setPhase("done");
    } catch (e: any) {
      setError(e.message || "Create failed");
      setPhase("form");
    }
  }

  if (!opts) return <p className="loading">Loading form…</p>;

  if (phase === "done" && result) {
    return (
      <div className="card" style={{ maxWidth: 640 }}>
        <h2 style={{ marginTop: 0 }}>✅ Event created in Kona OS</h2>
        <p className="muted">{result.message}</p>
        {result.editUrl ? (
          <p>
            <a className="btn primary" href={result.editUrl} target="_blank" rel="noreferrer">
              Open the event in Kona OS ↗
            </a>
          </p>
        ) : (
          <p className="muted">
            Created — the KonaOS link couldn't be resolved automatically; find it under
            Franchise → Events in KonaOS.
          </p>
        )}
        <p className="muted" style={{ fontSize: 13 }}>
          It will be picked up by the nightly pipeline, or run it now from the{" "}
          <Link to="/">Dashboard</Link> with {f.date} selected.
        </p>
        <div className="flex">
          <button className="btn primary" onClick={() => { setResult(null); setF({ ...initial, brandId: f.brandId }); setPhase("form"); }}>
            Create another
          </button>
          <button className="btn" onClick={() => navigate("/events")}>Events</button>
        </div>
      </div>
    );
  }

  return (
    <>
      {phase === "creating" && (
        <div className="overlay">
          <div className="card modal">
            <div className="spinner" />
            <h2>Creating event in Kona OS…</h2>
            <div className="step-list">
              <div className="step-row running"><span className="icon"><span className="spinner sm" /></span><span className="lbl">Sending to KonaOS quick-add</span></div>
              <div className="step-row pending"><span className="icon">○</span><span className="lbl">Locating the new event</span></div>
            </div>
          </div>
        </div>
      )}

      <p className="muted"><Link to="/events">← Events</Link></p>
      <h1 className="page-title">New Event</h1>
      <p className="page-sub">
        Creates the event directly in Kona OS — one entry, no double work. Pick the billing
        model from the predefined list; the right panel shows the exact notes the AI reads.
      </p>

      <div className="grid" style={{ gridTemplateColumns: "1fr 380px", alignItems: "start", gap: 20 }}>
        <div>
          {/* EVENT */}
          <Card title="Event">
            <Row>
              <Field label="Event name" req><input className="input" value={f.name} onChange={(e) => up({ name: e.target.value })} placeholder="Elkridge Club – Staff Meeting" /></Field>
              <Field label="Business name"><input className="input" value={f.businessName} onChange={(e) => up({ businessName: e.target.value })} placeholder="(defaults to event name)" /></Field>
            </Row>
            <Row>
              <Field label="Brand" req>
                <select className="select" value={f.brandId} onChange={(e) => up({ brandId: e.target.value })}>
                  {opts.brands.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
                </select>
              </Field>
              <Field label="Industry">
                <select className="select" value={f.industryId} onChange={(e) => up({ industryId: e.target.value })}>
                  <option value="">Select…</option>
                  {opts.industries.map((i, n) => <option key={i.id || n} value={i.id}>{i.type}</option>)}
                </select>
              </Field>
            </Row>
            <Row>
              <Field label="Event status">
                <select className="select" value={f.status} onChange={(e) => up({ status: e.target.value })}>
                  {opts.statuses.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
              </Field>
              <Field label="Date" req><input className="input" type="date" value={f.date} onChange={(e) => up({ date: e.target.value })} /></Field>
            </Row>
            <Row>
              <Field label="Start time" req><input className="input" type="time" value={f.startTime} onChange={(e) => up({ startTime: e.target.value })} /></Field>
              <Field label="End time" req><input className="input" type="time" value={f.endTime} onChange={(e) => up({ endTime: e.target.value })} /></Field>
            </Row>
            <div className="flex" style={{ gap: 16 }}>
              <label className="chk"><input type="checkbox" checked={f.prepay} onChange={(e) => up({ prepay: e.target.checked })} /> Prepay event</label>
              <label className="chk"><input type="checkbox" checked={f.kurbside} onChange={(e) => up({ kurbside: e.target.checked })} /> Kurbside event</label>
            </div>
          </Card>

          {/* LOCATION + CONTACT */}
          <Card title="Location & contact">
            <Field label="Address" req><input className="input" value={f.address} onChange={(e) => up({ address: e.target.value })} /></Field>
            <Row>
              <Field label="City" req><input className="input" value={f.city} onChange={(e) => up({ city: e.target.value })} /></Field>
              <Field label="State" req><input className="input" value={f.state} onChange={(e) => up({ state: e.target.value })} /></Field>
            </Row>
            <Row>
              <Field label="Zip" req><input className="input" value={f.zip} onChange={(e) => up({ zip: e.target.value })} /></Field>
              <Field label="County"><input className="input" value={f.county} onChange={(e) => up({ county: e.target.value })} /></Field>
            </Row>
            <Row>
              <Field label="Contact name" req><input className="input" value={f.contactName} onChange={(e) => up({ contactName: e.target.value })} /></Field>
              <Field label="Contact title"><input className="input" value={f.contactTitle} onChange={(e) => up({ contactTitle: e.target.value })} /></Field>
            </Row>
            <Row>
              <Field label="Contact email" req><input className="input" type="email" value={f.contactEmail} onChange={(e) => up({ contactEmail: e.target.value })} /></Field>
              <Field label="Contact phone"><input className="input" value={f.contactPhone} onChange={(e) => up({ contactPhone: e.target.value })} /></Field>
            </Row>
          </Card>

          {/* ADMIN — financial */}
          <Card title="Admin — financial setup" tag="at booking">
            <Field label="Tax exempt" req hint="Drives the 6% sales tax. If YES, tax is $0 and a certificate should be on file.">
              <Seg options={[["NO", "No — taxable"], ["YES", "Yes — exempt"]]} value={f.taxExempt} onChange={(v) => up({ taxExempt: v as F["taxExempt"] })} />
              {f.taxExempt === "YES" && <div className="cond warn">Tax-exempt — keep the exemption certificate on file for this event.</div>}
            </Field>
            <Field label="Billing model" req hint="Predefined models — picking one sets the event type and reveals its pricing fields.">
              <select
                className="select"
                value={f.billing}
                onChange={(e) => {
                  const model = BILLING_MODELS.find((m) => m.key === e.target.value);
                  up({ billing: e.target.value, eventType: model ? model.type : f.eventType });
                }}
              >
                <option value="">Select billing model…</option>
                {(["Invoice", "Selling", "Min Guarantee", "Hybrid"] as EventType[]).map((t) => (
                  <optgroup key={t} label={t}>
                    {modelsFor(t).map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
                  </optgroup>
                ))}
              </select>
            </Field>
            {f.billing && (FIELD_MAP[f.billing] ?? []).length > 0 && (
              <div className="cond">
                <Row3>
                  {(FIELD_MAP[f.billing] ?? []).map((field) => (
                    <Field key={field} label={FIELD_LABELS[field]} req>
                      <input className="input" type="number" step="0.01"
                        value={(f as any)[field]}
                        onChange={(e) => up({ [field]: e.target.value } as Partial<F>)} />
                    </Field>
                  ))}
                  {f.billing === "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA" && (
                    <Field label="Guest rate per serving ($)">
                      <input className="input" type="number" step="0.01" value={f.guestRate} onChange={(e) => up({ guestRate: e.target.value })} />
                    </Field>
                  )}
                </Row3>
              </div>
            )}
            <Row3>
              <Field label="Giveback %"><input className="input" type="number" value={f.giveback} onChange={(e) => up({ giveback: e.target.value })} placeholder="0" /></Field>
              <Field label="Deposit ($)"><input className="input" type="number" value={f.deposit} onChange={(e) => up({ deposit: e.target.value })} placeholder="0" /></Field>
              <Field label="Discount ($)"><input className="input" type="number" value={f.discount} onChange={(e) => up({ discount: e.target.value })} placeholder="0" /></Field>
            </Row3>
            <Field label="Location fee ($)"><input className="input" type="number" value={f.locationFee} onChange={(e) => up({ locationFee: e.target.value })} placeholder="0" /></Field>
            <Row>
              <Field label="Add-on / extra charge — label" hint='A flat extra on top, e.g. "Ice cream". Leave blank if none.'>
                <input className="input" value={f.addonLabel} onChange={(e) => up({ addonLabel: e.target.value })} placeholder="Ice cream" />
              </Field>
              <Field label="Add-on amount ($)">
                <input className="input" type="number" step="0.01" value={f.addonAmount} onChange={(e) => up({ addonAmount: e.target.value })} placeholder="0" />
              </Field>
            </Row>
            <label className="chk">
              <input type="checkbox" checked={f.allIn} onChange={(e) => up({ allIn: e.target.checked })} />
              Quoted total is all-in — tax &amp; 4% fee already included (don't add them)
            </label>
          </Card>

          {/* EVENT — contract */}
          <Card title="Event — the contract" tag="at booking">
            <Field label="Event type" req hint="Synced with the billing model above; pick a model there and this fills in.">
              <Seg
                options={[["Invoice", "Invoice"], ["Selling", "Selling"], ["Min Guarantee", "Min Guarantee"], ["Hybrid", "Hybrid"]]}
                value={f.eventType}
                onChange={(v) => {
                  // Clear the billing model if it no longer matches the chosen type.
                  const keep = BILLING_MODELS.find((m) => m.key === f.billing && m.type === v);
                  up({ eventType: v as EventType, billing: keep ? f.billing : "" });
                }}
              />
            </Field>
            <Row>
              <Field label="Attendees" req><input className="input" type="number" value={f.attendees} onChange={(e) => up({ attendees: e.target.value })} placeholder="100" /></Field>
              <Field label="Parking"><input className="input" value={f.parking} onChange={(e) => up({ parking: e.target.value })} placeholder="Covered circle drive" /></Field>
            </Row>
            <Field label="Serve / Keep count" hint='Free-text pricing detail, e.g. "$295 covers 60 servings, each additional $4". Optional — the billing model above is the source of truth.'>
              <input className="input" value={f.serveKeep} onChange={(e) => up({ serveKeep: e.target.value })} placeholder="$295 covers 60 12oz Konas, each additional $4" />
            </Field>
            <Field label="PAYMENT — pricing model" hint='Free-text billing basis, e.g. "$295 plus tax for the hour". Optional.'>
              <input className="input" value={f.paymentModel} onChange={(e) => up({ paymentModel: e.target.value })} placeholder="$295 plus tax for the hour" />
            </Field>
            <Field label="Additional instructions">
              <input className="input" value={f.additional} onChange={(e) => up({ additional: e.target.value })} placeholder='e.g. "Hard stop at 100 servings"' />
            </Field>
            <label className="chk"><input type="checkbox" checked={f.cardOnly} onChange={(e) => up({ cardOnly: e.target.checked })} /> Card only / no on-site cash</label>
          </Card>

          {/* DRIVER — actuals */}
          <Card title="Driver — the actuals" tag="at event close">
            <Field
              label="Actual serving count"
              req={needServe(f.eventType)}
              hint={needServe(f.eventType)
                ? "Total number served. Required for Invoice / Hybrid events."
                : "Total number served (optional for this event type)."}
            >
              <input className="input" type="number" value={f.actualCount} onChange={(e) => up({ actualCount: e.target.value })} placeholder="79" />
            </Field>
            <label className="chk"><input type="checkbox" checked={f.paid} onChange={(e) => up({ paid: e.target.checked })} /> Payment received</label>
            {f.paid && (
              <div className="cond">
                <label className="lbl-sm">Payment method * <span className="muted">(check &amp; cash have no fee; card adds 4%)</span></label>
                <Seg options={[["Check", "Check"], ["Credit Card", "Credit Card"], ["Cash", "Cash"]]} value={f.method} onChange={(v) => up({ method: v as PayMethod })} />
                {f.method === "Cash" && (
                  <Field label="Cash collected ($)" req><input className="input" type="number" value={f.cashAmount} onChange={(e) => up({ cashAmount: e.target.value })} /></Field>
                )}
              </div>
            )}
            <Row>
              <Field label="Actual event times" hint="Only if it ran longer/earlier."><input className="input" value={f.actualTimes} onChange={(e) => up({ actualTimes: e.target.value })} placeholder="Ran 1 hr, arrived 30 min early" /></Field>
              <Field label="Square device"><input className="input" value={f.squareDevice} onChange={(e) => up({ squareDevice: e.target.value })} placeholder="KEV7" /></Field>
            </Row>
          </Card>
        </div>

        {/* RIGHT RAIL */}
        <aside style={{ position: "sticky", top: 16 }}>
          <div className={"status " + (missing.length ? "bad" : "ok")}>
            {missing.length ? (
              <div>
                <strong>Human review required — {missing.length} missing</strong>
                <ul>{missing.map((m) => <li key={m}>{m}</li>)}</ul>
              </div>
            ) : (
              <strong>Ready to create in Kona OS</strong>
            )}
          </div>

          <div className="card" style={{ marginBottom: 14 }}>
            <div className="section-title" style={{ marginTop: 0 }}>Notes written to Kona OS</div>
            <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>ADMIN NOTES</div>
            <pre className="json" style={{ whiteSpace: "pre-wrap", maxHeight: "18vh", marginBottom: 10 }}>{adminNotes}</pre>
            <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>EVENT NOTES</div>
            <pre className="json" style={{ whiteSpace: "pre-wrap", maxHeight: "18vh", marginBottom: driverNotes.length ? 10 : 0 }}>{eventNotes.join("\n")}</pre>
            {driverNotes.length > 0 && (
              <>
                <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>DRIVER NOTES</div>
                <pre className="json" style={{ whiteSpace: "pre-wrap", maxHeight: "14vh" }}>{driverNotes.join("\n")}</pre>
              </>
            )}
          </div>

          <div className="card">
            <div className="section-title" style={{ marginTop: 0 }}>Estimated invoice</div>
            {!est ? (
              <p className="muted" style={{ margin: 0 }}>
                {f.eventType === "Selling"
                  ? "Selling event — guests pay via Square; no host invoice."
                  : "Pick a billing model + fill its numbers for an estimate."}
              </p>
            ) : (
              <div className="kv" style={{ gridTemplateColumns: "1fr auto", gap: "4px 12px" }}>
                <div className="k">Subtotal ({est.detail}{Number(f.addonAmount) ? ` + $${Number(f.addonAmount)} ${f.addonLabel || "add-on"}` : ""})</div>
                <div className="v right">${est.subtotal.toFixed(2)}</div>
                <div className="k">Sales tax {est.allIn ? "(all-in)" : f.taxExempt === "YES" ? "(exempt)" : "6%"}</div><div className="v right">${est.tax.toFixed(2)}</div>
                <div className="k">Card fee {est.allIn ? "(all-in)" : est.noFee ? "(check — none)" : "4%"}</div><div className="v right">${est.cc.toFixed(2)}</div>
                {est.deposit > 0 && <><div className="k">Deposit</div><div className="v right">−${est.deposit.toFixed(2)}</div></>}
                <div className="k" style={{ fontWeight: 700 }}>Balance due</div>
                <div className="v right" style={{ fontWeight: 700 }}>${Math.max(0, est.total - est.deposit).toFixed(2)}</div>
              </div>
            )}
          </div>

          {error && <p className="error-msg" style={{ marginTop: 12 }}>{error}</p>}
          <button className="btn primary" style={{ width: "100%", marginTop: 14 }} disabled={missing.length > 0 || phase === "creating"} onClick={submit}>
            {phase === "creating" ? "Creating…" : "Create event in Kona OS"}
          </button>
        </aside>
      </div>
    </>
  );
}

/* ── small presentational helpers ─────────────────────────────────────────── */
function Card({ title, tag, children }: { title: string; tag?: string; children: React.ReactNode }) {
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="flex between">
        <div className="section-title" style={{ marginTop: 0 }}>{title}</div>
        {tag && <span className="muted" style={{ fontSize: 11 }}>{tag}</span>}
      </div>
      {children}
    </div>
  );
}
function Row({ children }: { children: React.ReactNode }) {
  return <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>{children}</div>;
}
function Row3({ children }: { children: React.ReactNode }) {
  return <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>{children}</div>;
}
function Field({ label, req, hint, children }: { label: string; req?: boolean; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <label className="lbl-sm">{label}{req && <span style={{ color: "var(--crit)" }}> *</span>}</label>
      {hint && <div className="muted" style={{ fontSize: 11.5, margin: "2px 0 5px" }}>{hint}</div>}
      {children}
    </div>
  );
}
function Seg({ options, value, onChange }: { options: [string, string][]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="seg">
      {options.map(([v, label]) => (
        <button key={v} type="button" className={"seg-btn" + (value === v ? " on" : "")} onClick={() => onChange(v)}>{label}</button>
      ))}
    </div>
  );
}
