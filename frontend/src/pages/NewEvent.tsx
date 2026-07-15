import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";

/** Structured event intake — creates the event directly in Kona OS.
 *
 * One entry point, no double work: the admin fills defined fields here and the
 * form writes the event to KOS via POST /api/konaos/events, assembling admin
 * notes in the exact phrasing the AI classifier parses deterministically.
 * The live preview shows precisely what will be written. */

const BRANDS = [
  { id: "66704154faed4c5991533eb5253815d9", label: "Kona Ice" },
  { id: "4553cb46d02d40e4ab2732673e141ac3", label: "Travelin' Tom's" },
];

const BILLING_OPTIONS = [
  { key: "INVOICE_PER_SERVING", label: "Invoice — per serving", type: "invoice" },
  { key: "INVOICE_BASE_FEE_PLUS_SERVINGS", label: "Invoice — base fee + servings", type: "invoice" },
  { key: "INVOICE_FIXED_PACKAGE", label: "Invoice — fixed package (floor + overage)", type: "invoice" },
  { key: "INVOICE_HOURLY", label: "Invoice — hourly", type: "invoice" },
  { key: "SELLING_OPEN", label: "Selling — open (guests pay)", type: "selling" },
  { key: "SELLING_WITH_GIVEBACK", label: "Selling — with giveback %", type: "selling" },
  { key: "MIN_GUARANTEE_FLAT", label: "Minimum guarantee — flat", type: "minimum guarantee" },
  { key: "MIN_GUARANTEE_HOURLY", label: "Minimum guarantee — per hour", type: "minimum guarantee" },
  { key: "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA", label: "Hybrid — host base + guest extras", type: "hybrid" },
];

interface FormState {
  brandId: string;
  name: string;
  businessName: string;
  contactTitle: string;
  county: string;
  industryId: string;
  status: "pending" | "booked" | "confirmed";
  prepay: boolean;
  kurbside: boolean;
  eventNotes: string;
  date: string;
  startTime: string;
  endTime: string;
  addressLine1: string;
  city: string;
  state: string;
  zipCode: string;
  contactName: string;
  contactEmail: string;
  contactPhone: string;
  billing: string;
  ratePerServing: string;
  baseAmount: string;
  unitsIncluded: string;
  hourlyRate: string;
  minimumAmount: string;
  givebackPct: string;
  guestRate: string;
  locationFee: string;
  depositAmount: string;
  taxable: "yes" | "exempt";
  payment: "unknown" | "check" | "cash" | "credit_card";
  extraNotes: string;
}

const initial: FormState = {
  brandId: BRANDS[0].id, name: "", businessName: "", contactTitle: "", county: "",
  industryId: "", status: "pending", prepay: false, kurbside: false, eventNotes: "",
  date: "", startTime: "10:00", endTime: "12:00",
  addressLine1: "", city: "", state: "Maryland", zipCode: "",
  contactName: "", contactEmail: "", contactPhone: "",
  billing: "INVOICE_PER_SERVING",
  ratePerServing: "", baseAmount: "", unitsIncluded: "", hourlyRate: "",
  minimumAmount: "", givebackPct: "", guestRate: "",
  locationFee: "", depositAmount: "",
  taxable: "yes", payment: "unknown", extraNotes: "",
};

/** Assemble admin notes in the exact phrasings the classifier prompt keys on. */
function buildAdminNotes(f: FormState): string {
  const opt = BILLING_OPTIONS.find((b) => b.key === f.billing)!;
  const lines: string[] = [];

  switch (f.billing) {
    case "INVOICE_PER_SERVING":
      if (f.ratePerServing) lines.push(`Charge $${f.ratePerServing} per serving. Send invoice.`);
      break;
    case "INVOICE_BASE_FEE_PLUS_SERVINGS":
      lines.push(`Setup fee $${f.baseAmount || "0"} plus $${f.ratePerServing || "0"} per serving. Send invoice.`);
      break;
    case "INVOICE_FIXED_PACKAGE":
      lines.push(
        `If they purchase ${f.unitsIncluded || "0"} servings or more, charge $${f.ratePerServing || "0"} per serving. ` +
        `If they did not meet ${f.unitsIncluded || "0"} servings, still charge them $${f.baseAmount || "0"} minimum. Send invoice.`
      );
      break;
    case "INVOICE_HOURLY":
      lines.push(`Charge $${f.hourlyRate || "0"} per hour. Send invoice.`);
      break;
    case "SELLING_OPEN":
      lines.push("Open selling event. Guests pay individually.");
      break;
    case "SELLING_WITH_GIVEBACK":
      lines.push(`Selling event. Giveback percentage: ${f.givebackPct || "0"}%.`);
      break;
    case "MIN_GUARANTEE_FLAT":
      lines.push(`Minimum guarantee $${f.minimumAmount || "0"} flat. Host covers shortfall.`);
      break;
    case "MIN_GUARANTEE_HOURLY":
      lines.push(`Minimum guarantee $${f.minimumAmount || "0"} per hour. Host covers shortfall.`);
      break;
    case "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA":
      lines.push(
        `Host pays $${f.baseAmount || "0"} base covering ${f.unitsIncluded || "0"} servings. ` +
        `Additional servings $${f.ratePerServing || "0"} billed to host.` +
        (f.guestRate ? ` Guests pay $${f.guestRate} per serving for extras.` : "")
      );
      break;
  }

  if (f.locationFee) lines.push(`$${f.locationFee} location fee.`);
  if (f.depositAmount) lines.push(`Deposit $${f.depositAmount} required.`);
  lines.push(f.taxable === "exempt" ? "Client is tax exempt." : "Plus tax.");
  if (f.payment === "check") lines.push("Client will pay by check.");
  if (f.payment === "cash") lines.push("Client paying in cash.");
  if (f.payment === "credit_card") lines.push("Client paying by credit card.");
  if (f.extraNotes.trim()) lines.push(f.extraNotes.trim());

  return `EVENT TYPE: ${opt.type}. ` + lines.join(" ");
}

function toMs(date: string, time: string): number {
  return new Date(`${date}T${time}:00`).getTime();
}

export default function NewEvent() {
  const [f, setF] = useState<FormState>(initial);
  const [industries, setIndustries] = useState<{ id: string; type: string }[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.konaosIndustries().then(setIndustries).catch(() => setIndustries([]));
  }, []);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const notes = useMemo(() => buildAdminNotes(f), [f]);
  const set = (k: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setF({ ...f, [k]: e.target.value });

  const needs = (field: string) =>
    ({
      ratePerServing: ["INVOICE_PER_SERVING", "INVOICE_BASE_FEE_PLUS_SERVINGS", "INVOICE_FIXED_PACKAGE", "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA"],
      baseAmount: ["INVOICE_BASE_FEE_PLUS_SERVINGS", "INVOICE_FIXED_PACKAGE", "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA"],
      unitsIncluded: ["INVOICE_FIXED_PACKAGE", "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA"],
      hourlyRate: ["INVOICE_HOURLY"],
      minimumAmount: ["MIN_GUARANTEE_FLAT", "MIN_GUARANTEE_HOURLY"],
      givebackPct: ["SELLING_WITH_GIVEBACK"],
      guestRate: ["HYBRID_HOST_BASE_PLUS_GUEST_EXTRA"],
    })[field]?.includes(f.billing) ?? false;

  const valid =
    f.name && f.date && f.addressLine1 && f.city && f.zipCode && f.contactName && f.contactEmail;

  async function submit() {
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      const res = await api.konaosCreateEvent({
        name: f.name,
        businessName: f.businessName || f.name,
        brandId: f.brandId,
        startDateTime: toMs(f.date, f.startTime),
        endDateTime: toMs(f.date, f.endTime),
        addressLine1: f.addressLine1,
        city: f.city,
        state: f.state,
        zipCode: f.zipCode,
        county: f.county,
        contactName: f.contactName,
        contactTitle: f.contactTitle,
        contactEmail: f.contactEmail,
        contactPhone: f.contactPhone,
        adminNotes: notes,
        notes: f.eventNotes ? `<p>${f.eventNotes}</p>` : "",
        manualStatus: f.status,
        // Extra KOS fields — forwarded verbatim into the quick-add payload
        clientIndustriesTypeId: f.industryId || "",
        prePay: f.prepay,
        kurbsideEvent: f.kurbside,
        taxPercent: f.taxable === "exempt" ? "0" : "6",
        givebackPercentage: f.billing === "SELLING_WITH_GIVEBACK" ? (f.givebackPct || "0") : "0",
      });
      setResult(res.message || "Event created in Kona OS.");
    } catch (e: any) {
      setError(e.message || "Create failed");
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    return (
      <div className="card" style={{ maxWidth: 640 }}>
        <h2 style={{ marginTop: 0 }}>✅ Event created in Kona OS</h2>
        <p className="muted">{result}</p>
        <p className="muted">
          It will be picked up by the nightly pipeline, or run it now from the{" "}
          <Link to="/">Dashboard</Link> with the event's date selected.
        </p>
        <div className="flex">
          <button className="btn primary" onClick={() => { setResult(""); setF(initial); }}>
            Create another
          </button>
          <button className="btn" onClick={() => navigate("/")}>Dashboard</button>
        </div>
      </div>
    );
  }

  return (
    <>
      <p className="muted"><Link to="/events">← Events</Link></p>
      <h1 className="page-title">New Event</h1>
      <p className="page-sub">
        Creates the event directly in Kona OS — one entry, no double work. The billing details
        become structured admin notes the AI parses exactly (see preview).
      </p>

      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>Event</div>
          <div className="form-grid">
            <label>Brand *
              <select className="select" value={f.brandId} onChange={set("brandId")}>
                {BRANDS.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
              </select>
            </label>
            <label>Event name *
              <input className="input" value={f.name} onChange={set("name")} placeholder="Lincoln Elementary Field Day" />
            </label>
            <div className="flex" style={{ gap: 10 }}>
              <label style={{ flex: 1 }}>Business name
                <input className="input" value={f.businessName} onChange={set("businessName")} placeholder="(defaults to event name)" />
              </label>
              <label style={{ flex: 1 }}>Industry
                <select className="select" value={f.industryId} onChange={set("industryId")}>
                  <option value="">Select…</option>
                  {industries.map((i) => <option key={i.id} value={i.id}>{i.type}</option>)}
                </select>
              </label>
            </div>
            <div className="flex" style={{ gap: 10 }}>
              <label style={{ flex: 1 }}>Event status
                <select className="select" value={f.status} onChange={set("status")}>
                  <option value="pending">Pending (no client emails)</option>
                  <option value="booked">Booked</option>
                  <option value="confirmed">Confirmed</option>
                </select>
              </label>
              <label className="flex" style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 18 }}>
                <input type="checkbox" checked={f.prepay} onChange={(e) => setF({ ...f, prepay: e.target.checked })} /> Prepay
              </label>
              <label className="flex" style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 18 }}>
                <input type="checkbox" checked={f.kurbside} onChange={(e) => setF({ ...f, kurbside: e.target.checked })} /> Kurbside
              </label>
            </div>
            <label>Date *
              <input className="input" type="date" value={f.date} onChange={set("date")} />
            </label>
            <div className="flex" style={{ gap: 10 }}>
              <label style={{ flex: 1 }}>Start *
                <input className="input" type="time" value={f.startTime} onChange={set("startTime")} />
              </label>
              <label style={{ flex: 1 }}>End *
                <input className="input" type="time" value={f.endTime} onChange={set("endTime")} />
              </label>
            </div>
            <label>Street address *
              <input className="input" value={f.addressLine1} onChange={set("addressLine1")} />
            </label>
            <div className="flex" style={{ gap: 10 }}>
              <label style={{ flex: 2 }}>City *
                <input className="input" value={f.city} onChange={set("city")} />
              </label>
              <label style={{ flex: 2 }}>State
                <input className="input" value={f.state} onChange={set("state")} />
              </label>
              <label style={{ flex: 1 }}>Zip *
                <input className="input" value={f.zipCode} onChange={set("zipCode")} />
              </label>
              <label style={{ flex: 1 }}>County
                <input className="input" value={f.county} onChange={set("county")} />
              </label>
            </div>
            <div className="flex" style={{ gap: 10 }}>
              <label style={{ flex: 2 }}>Contact name *
                <input className="input" value={f.contactName} onChange={set("contactName")} />
              </label>
              <label style={{ flex: 1 }}>Title
                <input className="input" value={f.contactTitle} onChange={set("contactTitle")} />
              </label>
            </div>
            <div className="flex" style={{ gap: 10 }}>
              <label style={{ flex: 1 }}>Contact email *
                <input className="input" type="email" value={f.contactEmail} onChange={set("contactEmail")} />
              </label>
              <label style={{ flex: 1 }}>Contact phone
                <input className="input" value={f.contactPhone} onChange={set("contactPhone")} />
              </label>
            </div>
          </div>

          <div className="section-title">Billing</div>
          <div className="form-grid">
            <label>Billing model *
              <select className="select" value={f.billing} onChange={set("billing")}>
                {BILLING_OPTIONS.map((b) => <option key={b.key} value={b.key}>{b.label}</option>)}
              </select>
            </label>
            {needs("ratePerServing") && (
              <label>{f.billing === "INVOICE_FIXED_PACKAGE" ? "Rate per serving above included ($) *" : f.billing === "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA" ? "Host overage rate per serving ($) *" : "Rate per serving ($) *"}
                <input className="input" type="number" step="0.01" value={f.ratePerServing} onChange={set("ratePerServing")} />
              </label>
            )}
            {needs("baseAmount") && (
              <label>{f.billing === "INVOICE_FIXED_PACKAGE" ? "Minimum / floor amount ($) *" : "Base amount ($) *"}
                <input className="input" type="number" step="0.01" value={f.baseAmount} onChange={set("baseAmount")} />
              </label>
            )}
            {needs("unitsIncluded") && (
              <label>Servings included *
                <input className="input" type="number" value={f.unitsIncluded} onChange={set("unitsIncluded")} />
              </label>
            )}
            {needs("hourlyRate") && (
              <label>Hourly rate ($) *
                <input className="input" type="number" step="0.01" value={f.hourlyRate} onChange={set("hourlyRate")} />
              </label>
            )}
            {needs("minimumAmount") && (
              <label>{f.billing === "MIN_GUARANTEE_HOURLY" ? "Minimum per hour ($) *" : "Flat minimum ($) *"}
                <input className="input" type="number" step="0.01" value={f.minimumAmount} onChange={set("minimumAmount")} />
              </label>
            )}
            {needs("givebackPct") && (
              <label>Giveback percentage (%) *
                <input className="input" type="number" step="0.1" value={f.givebackPct} onChange={set("givebackPct")} />
              </label>
            )}
            {needs("guestRate") && (
              <label>Guest rate per serving ($)
                <input className="input" type="number" step="0.01" value={f.guestRate} onChange={set("guestRate")} />
              </label>
            )}
            <div className="flex" style={{ gap: 10 }}>
              <label style={{ flex: 1 }}>Location / travel fee ($)
                <input className="input" type="number" step="0.01" value={f.locationFee} onChange={set("locationFee")} />
              </label>
              <label style={{ flex: 1 }}>Deposit ($)
                <input className="input" type="number" step="0.01" value={f.depositAmount} onChange={set("depositAmount")} />
              </label>
            </div>
            <div className="flex" style={{ gap: 10 }}>
              <label style={{ flex: 1 }}>Tax *
                <select className="select" value={f.taxable} onChange={set("taxable")}>
                  <option value="yes">Taxable (6%)</option>
                  <option value="exempt">Tax exempt</option>
                </select>
              </label>
              <label style={{ flex: 1 }}>Payment method
                <select className="select" value={f.payment} onChange={set("payment")}>
                  <option value="unknown">Unknown yet (default)</option>
                  <option value="check">Check</option>
                  <option value="cash">Cash</option>
                  <option value="credit_card">Credit card</option>
                </select>
              </label>
            </div>
            <label>Additional admin notes
              <textarea className="input" rows={2} value={f.extraNotes} onChange={set("extraNotes")} />
            </label>
            <label>Event notes (visible on the event)
              <textarea className="input" rows={2} value={f.eventNotes} onChange={set("eventNotes")} />
            </label>
          </div>
        </div>

        <div>
          <div className="card">
            <div className="section-title" style={{ marginTop: 0 }}>
              Admin notes that will be written to KOS
            </div>
            <p className="muted" style={{ fontSize: 12 }}>
              This exact text goes into the event's admin notes — phrased so the AI classifier
              reads it deterministically. No free-text guessing.
            </p>
            <pre className="json" style={{ whiteSpace: "pre-wrap" }}>{notes}</pre>
          </div>
          {error && <p className="error-msg" style={{ marginTop: 12 }}>{error}</p>}
          <button
            className="btn primary"
            style={{ marginTop: 14, width: "100%" }}
            disabled={!valid || busy}
            onClick={submit}
          >
            {busy ? "Creating in Kona OS…" : "Create event in Kona OS"}
          </button>
          {!valid && <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>Fill the required (*) fields to enable.</p>}
        </div>
      </div>
    </>
  );
}
