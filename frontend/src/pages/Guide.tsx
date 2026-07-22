import { Link } from "react-router-dom";

/** Walkthrough videos, newest concept first. Loom "share" links don't render
 *  in an iframe — the embeddable form is /embed/<id> — so only the id is
 *  stored here and the URL is built below. */
const VIDEOS = [
  {
    id: "d47d74a5e6c94846a509b37dcf9a8559",
    title: "Running the automation & reading the Financials page",
    blurb:
      "Start here. Pick a date, run the automation, watch each step finish, then find that " +
      "day's events on the Financials page and open one to see how its total was worked out.",
  },
  {
    id: "6b547e9cb0a840eca1d063d8144394f3",
    title: "Sales tax, card fees, and a day with several events",
    blurb:
      "Why 6% sales tax is added unless the notes say tax-exempt, how to remove the card fee " +
      "when a customer pays by check, and what a busy day looks like — including why some " +
      "events are marked \"invoice skipped\".",
  },
  {
    id: "c604a2b0903a4d038977299263a60103",
    title: "Checking the figures against KonaOS",
    blurb:
      "Following one event's numbers from this dashboard through to KonaOS and the old " +
      "spreadsheet, so you can satisfy yourself the totals agree.",
  },
  {
    id: "62ffc20ec9054e259ccf164e61188935",
    title: "Adding a new event with the intake form",
    blurb:
      "Using the New Event form so every booking is captured the same way — required fields, " +
      "billing model, extra charges and driver notes — and it lands in KonaOS ready for the " +
      "automation to price.",
  },
];

/** One card per page in the left-hand menu. `useWhen` is deliberately phrased
 *  as a job to be done ("A customer is querying their bill") rather than a
 *  feature description — that's the question someone actually arrives with. */
const PAGES = [
  {
    to: "/",
    icon: "📊",
    name: "Dashboard",
    what:
      "The home screen. Shows one day at a time: how many events ran, what they came to, " +
      "and anything that needs a look. The Run button processes a date on demand — normally " +
      "the system does this by itself every night at 11:30 PM New York time.",
    useWhen: "you want a quick read on a single day, or you need to re-run a date by hand.",
  },
  {
    to: "/events",
    icon: "📅",
    name: "Events",
    what:
      "Every booking the system has picked up from KonaOS, with what it worked out for each " +
      "one. Open an event to see the full breakdown — the amount before tax, the sales tax, " +
      "the card fee, and the reasoning behind the numbers.",
    useWhen: "you need the full story behind one particular booking.",
  },
  {
    to: "/events/new",
    icon: "➕",
    name: "New Event",
    what:
      "A simple form for booking an event. It writes straight into KonaOS, and it insists on " +
      "the details the automation needs — so nothing important gets left out of the notes.",
    useWhen: "you're taking a new booking. Use this instead of typing notes into KonaOS freehand.",
  },
  {
    to: "/invoices",
    icon: "🧾",
    name: "Invoices",
    what:
      "The draft invoices the system has created in KonaOS. Events already paid at the truck " +
      "don't get one — those show as \"skipped\", which is normal, not a failure.",
    useWhen: "you're about to send bills out and want to see what's waiting.",
  },
  {
    to: "/financials",
    icon: "💰",
    name: "Event Financials",
    what:
      "One row per event with every figure — card sales, cash, tax, fees, what was invoiced. " +
      "This is what used to live in the monthly Google Sheet, but you can filter it by date, " +
      "brand or event type, and download any view as a spreadsheet.",
    useWhen: "you're reconciling a month, or checking a total against Square or the bank.",
  },
  {
    to: "/alerts",
    icon: "⚠️",
    name: "Needs Attention",
    what:
      "Things the system spotted that a person should look at — a total that doesn't match " +
      "Square, a missing deposit, an unusually large discount.",
    useWhen: "you want to know what's wrong before a customer tells you.",
  },
  {
    to: "/runs",
    icon: "⚙️",
    name: "Automation Runs",
    what:
      "A record of every time the automation has run, and what it did each time — step by " +
      "step, plus what happened to each event and the cost of the AI that read the notes.",
    useWhen: "something looks off and you want to see whether last night's run finished cleanly.",
  },
  {
    to: "/crm-activity",
    icon: "📝",
    name: "KonaOS Change Log",
    what:
      "Every change this system has made in KonaOS — figures written onto an event, invoices " +
      "created or removed — with the date and time it happened. Failed changes are listed too, " +
      "with the reason.",
    useWhen: "you're asking \"did the system change this, and when?\" — for example during a billing dispute.",
  },
  {
    to: "/api-explorer",
    icon: "🔌",
    name: "API Explorer",
    what:
      "A technical page for developers — it lists the connections this system offers to other " +
      "software. Nothing here is needed for day-to-day work.",
    useWhen: "a developer is wiring something new into the system.",
  },
];

const GLOSSARY = [
  ["Automation run", "One pass of the whole process: fetch the day's events, work out what each is worth, create invoices, flag problems."],
  ["Billing model", "How an event is charged — a fixed price, a minimum guarantee, or straight sales at the truck."],
  ["Subtotal", "The amount before sales tax is added."],
  ["Sales tax", "6% by default. Only left off when the event notes say the customer is tax-exempt."],
  ["Card fee (4%)", "Added because we don't know in advance how the customer will pay. If they pay by check, clear the flag on the event and the fee comes off."],
  ["Square reconciliation", "Matching the card sales recorded on the truck's Square reader against what we billed."],
  ["Invoice skipped", "No invoice was created because the customer already paid at the event. Expected, not an error."],
  ["Needs review", "The event went through, but a figure looked unusual enough to be worth a human check."],
  ["Giveback", "The share of sales handed back to the host — a school or charity, for instance."],
];

export default function Guide() {
  return (
    <>
      <h1 className="page-title">Guide &amp; Tutorials</h1>
      <p className="page-sub">
        What this system does, what each page is for, and short videos showing it in use.
        No technical background needed.
      </p>

      <div className="card" style={{ marginBottom: 26 }}>
        <h2 style={{ margin: "0 0 8px", fontSize: 16 }}>What this system does, in one paragraph</h2>
        <p style={{ margin: 0, lineHeight: 1.65 }}>
          Every night it collects the day's events from KonaOS, reads the notes on each one to
          work out how it should be charged, checks the card sales recorded on Square, calculates
          the tax and fees, and creates the invoice — then writes the figures back into KonaOS and
          flags anything that looks wrong. It's doing the job that used to mean working through a
          monthly spreadsheet by hand. Everything it does is logged, so you can always see how a
          number was arrived at.
        </p>
      </div>

      <div className="guide-section">
        <h2>Video walkthroughs</h2>
        <p className="lead">Four short screen recordings. Watch them in order the first time.</p>
      </div>
      <div className="guide-videos">
        {VIDEOS.map((v, i) => (
          <div key={v.id} className="card video-card">
            <div className="video-frame">
              <iframe
                src={`https://www.loom.com/embed/${v.id}`}
                title={v.title}
                allowFullScreen
                loading="lazy"
              />
            </div>
            <div className="video-meta">
              <div className="n">Video {i + 1}</div>
              <h3>{v.title}</h3>
              <p>{v.blurb}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="guide-section">
        <h2>What each page is for</h2>
        <p className="lead">Click any card to go straight there.</p>
      </div>
      <div className="guide-pages">
        {PAGES.map((p) => (
          <Link key={p.to} to={p.to} className="card guide-page-card">
            <h3>
              <span>{p.icon}</span> {p.name}
            </h3>
            <p>{p.what}</p>
            <div className="use-when">
              <b>Go here when</b> {p.useWhen}
            </div>
          </Link>
        ))}
      </div>

      <div className="guide-section">
        <h2>Words you'll see around the app</h2>
        <p className="lead">Plain-English meanings for the terms that show up in tables and labels.</p>
      </div>
      <div className="card">
        <dl className="glossary">
          {GLOSSARY.map(([term, meaning]) => (
            <div key={term} style={{ display: "contents" }}>
              <dt>{term}</dt>
              <dd>{meaning}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="guide-section">
        <h2>Common questions</h2>
      </div>
      <div className="card" style={{ marginBottom: 30 }}>
        <Faq q="Do I have to run anything myself?">
          No. The automation runs on its own every night at 11:30 PM New York time and handles
          that day's events. The Run button on the Dashboard is there for when you want to
          re-do a specific date.
        </Faq>
        <Faq q="An event says “invoice skipped”. Is that a problem?">
          No — it means the customer already paid at the event, so there's nothing to bill.
          Real failures appear on the Needs Attention page and are marked in red.
        </Faq>
        <Faq q="A total doesn't match what I expected. Where do I look?">
          Open the event from Events or Financials. The detail page shows the amount before tax,
          the sales tax, the card fee, and the reasoning the system used — so you can see exactly
          which part is off.
        </Faq>
        <Faq q="The customer paid by check, not card. How do I remove the 4% card fee?">
          Open the event and clear the card-payment flag. The invoice total updates straight away
          and the change is saved.
        </Faq>
        <Faq q="Is the monthly Google Sheet still needed?">
          No. The Financials page holds the same figures and more, and you can export any filtered
          view to a spreadsheet whenever you need one.
        </Faq>
      </div>
    </>
  );
}

function Faq({ q, children }: { q: string; children: React.ReactNode }) {
  return (
    <details style={{ borderTop: "1px solid var(--border)", padding: "12px 0" }}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>{q}</summary>
      <div style={{ marginTop: 8, color: "var(--text-dim)", lineHeight: 1.6, fontSize: 13.5 }}>
        {children}
      </div>
    </details>
  );
}
