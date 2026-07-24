import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, FinancialRow, FinancialsResponse, getToken } from "../api/client";
import { Badge, BulkDeleteButton, DeleteButton, Empty, InfoTip, Loading, money } from "../components/ui";

/** Today in America/New_York — the business's day, not the browser's. Matches
 *  the Dashboard so both screens agree on what "today" is. */
function todayNY(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
}

/** The financial ledger — replaces the monthly Google Sheet. Rows live in
 *  Postgres and are upserted by every pipeline run.
 *
 *  Filtering: pick a month for the classic monthly view, or set a custom
 *  from/to date range (setting one clears the other). Brand / event type /
 *  paid / search narrow further. The CSV export honours every active filter.
 *
 *  Filters live in the URL (not component state) so they survive navigating
 *  to an event and back — "← Financials" returns to the exact same filtered
 *  view instead of resetting. */
export default function Financials() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [months, setMonths] = useState<string[]>([]);
  const month = searchParams.get("month") || "";
  const fromDate = searchParams.get("from_date") || "";
  const toDate = searchParams.get("to_date") || "";
  const brand = searchParams.get("brand") || "";
  const eventType = searchParams.get("event_type") || "";
  const paid = searchParams.get("paid") || "";          // "" | "true" | "false"
  const urlSearch = searchParams.get("search") || "";
  const [searchInput, setSearchInput] = useState(urlSearch);
  const [debounced, setDebounced] = useState(urlSearch);
  const [data, setData] = useState<FinancialsResponse | null>(null);
  const [error, setError] = useState<string>("");
  const [importing, setImporting] = useState(false);
  const [importMsg, setImportMsg] = useState<string>("");
  const [importSource, setImportSource] = useState<"kona" | "tom">("kona");
  const navigate = useNavigate();
  const location = useLocation();

  /** Merge a patch into the URL's query params (undefined/"" removes a key).
   *  `replace: true` so filter tweaks don't spam browser history. */
  function updateParams(patch: Record<string, string | undefined>) {
    const next = new URLSearchParams(searchParams);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v); else next.delete(k);
    }
    setSearchParams(next, { replace: true });
  }

  useEffect(() => {
    api.financialMonths().then((ms) => {
      setMonths(ms);
    }).catch(() => { /* surfaced by the list fetch below */ });
    // Default the ledger to TODAY on a completely fresh visit — never show the
    // whole dataset by default, and never override filters already present in
    // the URL (e.g. restored from "← Financials" after viewing an event).
    const hasAnyDateFilter = month || fromDate || toDate;
    if (!hasAnyDateFilter) {
      const today = todayNY();
      updateParams({ from_date: today, to_date: today });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounce the search box so we don't refetch (or touch the URL) per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(searchInput), 300);
    return () => clearTimeout(t);
  }, [searchInput]);
  useEffect(() => {
    updateParams({ search: debounced || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

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

  /** Refetch with the current filters — also used after a row delete. */
  const reload = () =>
    api.financials(params)
      .then(setData)
      .catch((e: any) => setError(e?.message || "Failed to load the ledger."));

  useEffect(() => {
    setData(null);
    setError("");
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  /** Month shortcut and custom range are alternatives — using one clears the other. */
  function pickMonth(m: string) {
    updateParams({ month: m || undefined, from_date: undefined, to_date: undefined });
  }
  /** Picking "From" defaults "To" to the same day (a single-day range) —
   *  editing "To" afterward widens it; editing "To" alone never touches "From". */
  function pickRange(patch: { from?: string; to?: string }) {
    if (patch.from !== undefined) {
      updateParams({ from_date: patch.from || undefined, to_date: patch.from || undefined, month: undefined });
    } else if (patch.to !== undefined) {
      updateParams({ to_date: patch.to || undefined, month: undefined });
    }
  }
  function clearFilters() {
    setSearchParams(new URLSearchParams(), { replace: true });
    setSearchInput("");
  }
  const hasFilters =
    !!(month || fromDate || toDate || brand || eventType || paid || searchInput);

  /** Column total across the loaded rows (the list isn't paginated, so the
   *  rows in hand are the whole filtered set). */
  const sum = (key: keyof FinancialRow): number =>
    (data?.items ?? []).reduce((acc, r) => acc + (Number(r[key]) || 0), 0);

  /** Pull the legacy Google Sheet into the ledger. Repeatable & idempotent:
   *  creates placeholder events for unknown rows and never overwrites a
   *  pipeline-generated row. */
  async function importSheet() {
    if (importing) return;
    const brandLabel = importSource === "tom" ? "Travelin' Tom" : "Kona Ice";
    if (!window.confirm(
      `Bring the old ${brandLabel} Google Sheet into this page?\n\n` +
      "• Events not yet in the system get a placeholder record.\n" +
      "• Rows the automation already produced are left untouched.\n" +
      "• Safe to run more than once — it just refreshes what it imported before."
    )) return;
    setImporting(true);
    setImportMsg("");
    try {
      const r = await api.importFinancialsSheet(importSource);
      setImportMsg(
        `Imported ${r.label} — ${r.created} new, ${r.updated} refreshed, ` +
        `${r.skipped_protected} left untouched (produced by the automation), ` +
        `${r.placeholders_created} placeholder event(s) created.`
      );
      // New months / rows may have appeared — refresh both.
      api.financialMonths().then(setMonths).catch(() => {});
      await reload();
    } catch (e: any) {
      setImportMsg(`Import failed: ${e?.message || "unknown error"}`);
    } finally {
      setImporting(false);
    }
  }

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
        <div className="title-row">
          <h1 className="page-title">Event Financials</h1>
          <InfoTip text="Every event and what it was worth — one row each, with the card sales from Square checked against what was invoiced. Use the filters to pick a date range or a brand, and download whatever you're looking at as a spreadsheet." />
        </div>
        <div className="flex" style={{ gap: 8 }}>
          <select className="select" value={importSource}
            onChange={(e) => setImportSource(e.target.value as "kona" | "tom")}
            disabled={importing} title="Which brand's Google Sheet to import">
            <option value="kona">Kona Ice</option>
            <option value="tom">Travelin' Tom</option>
          </select>
          <button className="btn" onClick={importSheet} disabled={importing}
            title="Bring the selected brand's old Google Sheet into this page. Safe to repeat — it never overwrites rows the automation produced.">
            {importing ? "⏳ Importing…" : "⬆ Import sheet"}
          </button>
          <button className="btn primary" onClick={downloadCsv} disabled={!data || data.total === 0}
            title="Download what you're currently looking at, with all 46 columns">
            ⬇ Export CSV
          </button>
        </div>
      </div>

      {importMsg && (
        <div
          className="card"
          style={{
            marginBottom: 12,
            borderColor: importMsg.startsWith("Import failed") ? "var(--crit)" : "var(--brand)",
          }}
        >
          <div className="flex between">
            <span>{importMsg}</span>
            <button className="icon-btn" onClick={() => setImportMsg("")} title="Dismiss">✕</button>
          </div>
        </div>
      )}

      <div className="toolbar" style={{ flexWrap: "wrap", gap: 8 }}>
        <select className="select" value={month} onChange={(e) => pickMonth(e.target.value)} title="Month shortcut">
          <option value="">All months</option>
          {months.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <span className="field-label">or dates</span>
        <label className="field-label" htmlFor="fin-date-from">From</label>
        <input id="fin-date-from" className="input" type="date" value={fromDate} style={{ width: 132 }}
          onChange={(e) => pickRange({ from: e.target.value })} title="Events on or after this date" />
        <label className="field-label" htmlFor="fin-date-to">To</label>
        <input id="fin-date-to" className="input" type="date" value={toDate} style={{ width: 132 }}
          onChange={(e) => pickRange({ to: e.target.value })} title="Events on or before this date" />
        <select className="select" value={brand} onChange={(e) => updateParams({ brand: e.target.value || undefined })}>
          <option value="">All brands</option>
          {(data?.brands ?? []).map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
        <select className="select" value={eventType} onChange={(e) => updateParams({ event_type: e.target.value || undefined })}>
          <option value="">All event types</option>
          {(data?.event_types ?? []).map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select className="select" value={paid} onChange={(e) => updateParams({ paid: e.target.value || undefined })}>
          <option value="">Paid + unpaid</option>
          <option value="true">Paid only</option>
          <option value="false">Unpaid only</option>
        </select>
        <input className="input" placeholder="Search event name or code…" value={searchInput}
          style={{ width: 170 }} onChange={(e) => setSearchInput(e.target.value)} />
        {hasFilters && (
          <button className="btn" onClick={clearFilters} title="Clear all filters">✕ Clear filters</button>
        )}
        {data && <span className="count">{data.total} events</span>}
        {/* Bulk delete is date-scoped: wipe the selected day/month (plus any
            other active filters), then re-run the pipeline to rebuild clean
            rows. The backend rejects the call without a date scope. */}
        {(month || fromDate || toDate) && data && data.total > 0 && (
          <BulkDeleteButton
            count={data.total}
            noun="rows"
            onDelete={async () => {
              await api.deleteFinancials(params);
              await reload();
            }}
          />
        )}
      </div>

      {error ? (
        <div className="card" style={{ borderColor: "var(--crit)" }}>
          <strong>Couldn't load the financials:</strong> {error}
          <div className="muted" style={{ marginTop: 6, fontSize: 13 }}>
            The server didn't respond. Try refreshing — if it keeps happening, the system needs
            a look from your developer.
          </div>
        </div>
      ) : !data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <Empty
          text={
            hasFilters
              ? "No events match these filters — clear them or widen the date range."
              : "Nothing here yet. Rows appear once the automation has run: open the Dashboard, pick a date that had events, and press Run."
          }
        />
      ) : (
        <>
          {/* No summary strip above the table: the pinned totals row at the
              bottom of the sheet already carries these figures, and it stays
              in view while you scroll. Showing them twice cost ~70px of
              height that the rows use better. */}

          {/* Columns are grouped under a header band so each label can be one
              or two words — that, plus the full-width page, is what keeps this
              readable without scrolling sideways for a screen and a half.
              DATE / EVENT / TYPE stay frozen on the left, and the header and
              totals rows are pinned, so a figure 15 columns over still tells
              you which event it belongs to. */}
          <div className="table-wrap sheet">
            <table style={{ whiteSpace: "nowrap" }}>
              <thead>
                <tr>
                  <th className="stick stick-1" rowSpan={2}><div className="cell">Date</div></th>
                  <th className="stick stick-2" rowSpan={2}><div className="cell">Event</div></th>
                  <th className="stick stick-3" rowSpan={2}><div className="cell">Type &amp; billing</div></th>
                  <th className="grp g-sq sep" colSpan={6}>Card sales (Square)</th>
                  <th className="grp g-cash sep" colSpan={3}>Cash</th>
                  <th className="grp g-inv sep" colSpan={2}>Invoiced / prepaid</th>
                  <th className="grp g-tot sep" colSpan={7}>Event totals</th>
                  <th className="sep" rowSpan={2}>Paid?</th>
                  <th rowSpan={2}>Why this amount</th>
                  <th className="actions" rowSpan={2}></th>
                </tr>
                <tr>
                  <th className="right sep">Gross</th>
                  <th className="right">Discounts</th>
                  <th className="right">Net (card)</th>
                  <th className="right">Card tax</th>
                  <th className="right">Tips</th>
                  <th className="right">Fee 4%</th>
                  <th className="right sep">Collected</th>
                  <th className="right">Tax</th>
                  <th className="right">Before tax</th>
                  <th className="right sep">Check / invoice</th>
                  <th className="right">Deposit</th>
                  <th className="sep">Taxable?</th>
                  <th className="right">Collected</th>
                  <th className="right">Sales tax</th>
                  <th className="right">Sales $</th>
                  <th className="right">Giveback</th>
                  <th className="right">Net sales</th>
                  <th className="right">Location fee</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.id} onClick={() => navigate(`/events/${r.event_id}`, { state: { from: location.pathname + location.search, label: "Event Financials" } })}>
                    <td className="stick stick-1"><div className="cell" style={{ fontWeight: 700 }}>{r.event_date || "—"}</div></td>
                    <td className="stick stick-2" title={r.event_name}>
                      <div className="cell">
                        <div style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis" }}>
                          {r.event_name}
                        </div>
                        <div className="muted" style={{ fontSize: 12, overflow: "hidden", textOverflow: "ellipsis" }}>
                          {r.brand} · {r.event_code}
                        </div>
                      </div>
                    </td>
                    <td className="stick stick-3" title={r.billing_model || ""}>
                      <div className="cell">
                        <div style={{ textTransform: "capitalize", fontWeight: 600 }}>
                          {r.event_type || "—"}
                        </div>
                        <div className="muted" style={{ fontSize: 11, overflow: "hidden", textOverflow: "ellipsis" }}>
                          {r.billing_model || "—"}
                        </div>
                        {/* Min-guarantee events can't be invoiced until cash is
                            counted — the invoice IS the gap to the minimum. */}
                        {r.awaiting_cash && (
                          <span className="badge amber" style={{ fontSize: 10, padding: "1px 6px" }}
                            title="Minimum-guarantee event. No invoice yet — it's the gap between actual sales and the guaranteed minimum, so cash has to be counted first.">
                            needs cash
                          </span>
                        )}
                      </div>
                    </td>
                    <Num v={r.square_gross_sales} g="g-sq" sep />
                    <Num v={r.square_discounts} g="g-sq" />
                    <Num v={r.square_net_card} g="g-sq" />
                    <Num v={r.square_card_tax} g="g-sq" />
                    <Num v={r.square_tips_card} g="g-sq" />
                    <Num v={r.square_cc_fee} g="g-sq" />
                    <CashCell row={r} onSaved={reload} />
                    <Num v={r.cash_tax} g="g-cash" />
                    <Num v={r.cash_pre_tax} g="g-cash" />
                    <Num v={r.check_invoice} g="g-inv" sep cls="key" />
                    <DepositCell row={r} onSaved={reload} />
                    <ToggleCell
                      row={r} field="taxable" group="g-tot" onSaved={reload}
                      on="Taxable" off="Exempt" kindOn="gray" kindOff="green"
                    />
                    <Num v={r.event_sales_collected} g="g-tot" />
                    <Num v={r.sales_tax} g="g-tot" />
                    <Num v={r.sales_dollars} g="g-tot" />
                    <Num v={r.giveback_amount} g="g-tot" />
                    <Num v={r.net_event_sales} g="g-tot" />
                    <Num v={r.location_fee} g="g-tot" />
                    <ToggleCell
                      row={r} field="paid" group="" onSaved={reload}
                      on="Paid" off="Unpaid" kindOn="green" kindOff="gray"
                      extra={r.paid && r.payment_method ? ` · ${r.payment_method}` : ""}
                    />
                    {/* How the system arrived at this figure — full text on
                        hover, since it runs long. */}
                    <td
                      title={r.note || ""}
                      style={{ minWidth: 200, maxWidth: 280, fontSize: 12 }}
                    >
                      <div className="clamp2">
                        {r.note ? (r.note.length > 120 ? r.note.slice(0, 120) + "…" : r.note) : "—"}
                      </div>
                    </td>
                    <td className="actions">
                      <DeleteButton
                        title="Remove this row (the event itself is kept — re-running the automation rebuilds it)"
                        onDelete={async () => { await api.deleteFinancialEntry(r.id); await reload(); }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr style={{ fontWeight: 700, background: "var(--surface-2)" }}>
                  <td colSpan={3} className="stick stick-span">
                    <div className="cell">
                      Totals ({month || ((fromDate || toDate) ? `${fromDate || "…"} → ${toDate || "…"}` : "all")})
                      <span className="muted" style={{ fontWeight: 400 }}> · {data.total} rows</span>
                    </div>
                  </td>
                  <td className="right g-sq sep">{money(sum("square_gross_sales"))}</td>
                  <td className="right g-sq">{money(sum("square_discounts"))}</td>
                  <td className="right g-sq">{money(sum("square_net_card"))}</td>
                  <td className="right g-sq">{money(sum("square_card_tax"))}</td>
                  <td className="right g-sq">{money(sum("square_tips_card"))}</td>
                  <td className="right g-sq">{money(sum("square_cc_fee"))}</td>
                  <td className="right g-cash sep">{money(sum("cash_collected"))}</td>
                  <td className="right g-cash">{money(sum("cash_tax"))}</td>
                  <td className="right g-cash">{money(sum("cash_pre_tax"))}</td>
                  <td className="right g-inv sep">{money(sum("check_invoice"))}</td>
                  <td className="right g-inv">{money(sum("deposit"))}</td>
                  <td className="sep g-tot" />
                  <td className="right g-tot">{money(sum("event_sales_collected"))}</td>
                  <td className="right g-tot">{money(sum("sales_tax"))}</td>
                  <td className="right g-tot">{money(sum("sales_dollars"))}</td>
                  <td className="right g-tot">{money(sum("giveback_amount"))}</td>
                  <td className="right g-tot">{money(sum("net_event_sales"))}</td>
                  <td className="right g-tot">{money(sum("location_fee"))}</td>
                  <td className="sep" />
                  <td />
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

/** The one editable cell in the ledger.
 *
 *  Cash is counted after the event — usually by the cash automation, sometimes
 *  by a person — so unlike every other column it's a real input rather than
 *  something the engine derived. Everything downstream of it (cash tax, event
 *  sales, sales $) is recomputed server-side on save, which is why those cells
 *  stay read-only: there's no way to save a row whose tax doesn't follow from
 *  its cash.
 *
 *  The dot shows provenance at a glance — green when a machine or a person
 *  actually counted it, hollow when it's just what the AI read out of the
 *  driver's notes (i.e. a guess, and 0 when the notes said nothing). */
function CashCell({ row, onSaved }: { row: FinancialRow; onSaved: () => Promise<void> | void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(row.cash_collected ?? 0));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const counted = isSet(row, "cash_collected");

  async function save() {
    const n = Number(draft);
    if (!Number.isFinite(n) || n < 0) { setErr("Enter a number, 0 or more"); return; }
    setBusy(true);
    setErr("");
    try {
      await api.setEventCash(row.crm_event_id, n);
      setEditing(false);
      await onSaved();
    } catch (e: any) {
      setErr(e?.message || "Couldn't save");
    } finally {
      setBusy(false);
    }
  }

  // stopPropagation throughout: the row itself navigates to the event.
  if (editing) {
    return (
      <td className="right g-cash sep cash-cell" onClick={(e) => e.stopPropagation()}>
        <input
          className="cash-input"
          autoFocus
          value={draft}
          disabled={busy}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
            if (e.key === "Escape") { setEditing(false); setErr(""); }
          }}
          onBlur={save}
          title={err || "Enter to save, Esc to cancel"}
        />
        {err && <div className="cash-err">{err}</div>}
      </td>
    );
  }

  return (
    <td
      className={"right g-cash sep cash-cell editable" + (row.cash_collected ? "" : " zero")}
      onClick={(e) => { e.stopPropagation(); setDraft(String(row.cash_collected ?? 0)); setEditing(true); }}
      title={
        counted
          ? `Cash counted — ${sourceLabel(row, "cash_collected")}. Click to change.`
          : "Not counted yet — this is only what the AI read from the driver's notes. Click to enter the real figure."
      }
    >
      <span className={"cash-dot" + (counted ? " counted" : "")} />
      {money(row.cash_collected)}
    </td>
  );
}

/** True when a person or an automation set this field, rather than it being
 *  whatever the classifier inferred. */
function isSet(row: FinancialRow, field: string): boolean {
  const s = row.sources?.[field];
  return s === "api" || s === "manual";
}

function sourceLabel(row: FinancialRow, field: string): string {
  const s = row.sources?.[field];
  if (s === "api") return "posted by an automation";
  if (s === "manual") return "entered by hand";
  return "read from the event notes by the AI";
}

/** Editable "Deposit" cell. Unlike cash, saving this changes NOTHING else —
 *  it's recorded and shown, and wiring it into the billing engine is a later,
 *  deliberate step. The tooltip says so, because a user who edits a money
 *  field reasonably expects the invoice to move. */
function DepositCell({ row, onSaved }: { row: FinancialRow; onSaved: () => Promise<void> | void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(row.deposit ?? 0));
  const [err, setErr] = useState("");
  const set = isSet(row, "deposit");

  async function save() {
    const n = Number(draft);
    if (!Number.isFinite(n) || n < 0) { setErr("Enter a number, 0 or more"); return; }
    setErr("");
    try {
      await api.setEventFields(row.crm_event_id, { deposit: n });
      setEditing(false);
      await onSaved();
    } catch (e: any) {
      setErr(e?.message || "Couldn't save");
    }
  }

  if (editing) {
    return (
      <td className="right g-inv cash-cell" onClick={(e) => e.stopPropagation()}>
        <input className="cash-input" autoFocus value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
            if (e.key === "Escape") { setEditing(false); setErr(""); }
          }}
          onBlur={save} title={err || "Enter to save, Esc to cancel"} />
        {err && <div className="cash-err">{err}</div>}
      </td>
    );
  }
  return (
    <td
      className={"right g-inv cash-cell editable" + (row.deposit ? "" : " zero")}
      onClick={(e) => { e.stopPropagation(); setDraft(String(row.deposit ?? 0)); setEditing(true); }}
      title={`Deposit received — ${sourceLabel(row, "deposit")}. Click to change. Recorded only: no other figure changes.`}
    >
      <span className={"cash-dot" + (set ? " counted" : "")} />
      {money(row.deposit)}
    </td>
  );
}

/** Taxable? / Paid? — click to flip. Recorded only, nothing recalculates. */
function ToggleCell({
  row, field, on, off, kindOn, kindOff, group, onSaved, extra,
}: {
  row: FinancialRow;
  field: "taxable" | "paid";
  on: string; off: string;
  kindOn: string; kindOff: string;
  group: string;
  onSaved: () => Promise<void> | void;
  extra?: string;
}) {
  const [busy, setBusy] = useState(false);
  const value = Boolean(row[field]);
  const set = isSet(row, field);

  async function toggle(e: React.MouseEvent) {
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    try {
      await api.setEventFields(row.crm_event_id, { [field]: !value } as any);
      await onSaved();
    } finally {
      setBusy(false);
    }
  }

  return (
    <td
      className={`sep ${group} cash-cell editable`}
      onClick={toggle}
      title={`${sourceLabel(row, field)}. Click to change. Recorded only: no other figure changes.`}
    >
      <span className={"cash-dot" + (set ? " counted" : "")} />
      <Badge kind={value ? kindOn : kindOff}>
        {busy ? "…" : (value ? on : off) + (extra || "")}
      </Badge>
    </td>
  );
}

/** A money cell. Zero and missing values are dimmed so the eye skips them and
 *  lands on the columns that actually carry an amount — most rows only use a
 *  handful of the 18 figures. `sep` marks the first column of a header group. */
function Num({
  v, sep, cls, g,
}: { v: number | null | undefined; sep?: boolean; cls?: string; g?: string }) {
  const empty = !v;
  return (
    <td className={["right", g, sep && "sep", cls, empty && "zero"].filter(Boolean).join(" ")}>
      {money(v)}
    </td>
  );
}

