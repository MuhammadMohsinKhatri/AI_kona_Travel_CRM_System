import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, FinancialRow, FinancialsResponse, getToken } from "../api/client";
import { Badge, BulkDeleteButton, DeleteButton, Empty, Loading, money } from "../components/ui";

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
      // Only default to the most recent month on a completely fresh visit —
      // never override filters already present in the URL (e.g. restored
      // from "← Financials" after viewing an event).
      const hasAnyDateFilter = month || fromDate || toDate;
      if (ms.length && !hasAnyDateFilter) updateParams({ month: ms[0] });
    }).catch(() => { /* surfaced by the list fetch below */ });
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
        <div>
          <h1 className="page-title">Financials</h1>
          <p className="page-sub">
            Every event and what it was worth — one row each. Use the filters to pick a date
            range or a brand, and download whatever you're looking at as a spreadsheet.
          </p>
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
            {importing ? "⏳ Importing…" : "⬆ Import old sheet"}
          </button>
          <button className="btn primary" onClick={downloadCsv} disabled={!data || data.total === 0}
            title="Download what you're currently looking at, with all 46 columns">
            ⬇ Download spreadsheet
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
        <input id="fin-date-from" className="input" type="date" value={fromDate} style={{ width: 140 }}
          onChange={(e) => pickRange({ from: e.target.value })} title="Events on or after this date" />
        <label className="field-label" htmlFor="fin-date-to">To</label>
        <input id="fin-date-to" className="input" type="date" value={toDate} style={{ width: 140 }}
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
          style={{ width: 190 }} onChange={(e) => setSearchInput(e.target.value)} />
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
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <Tot label="Total invoiced" v={money(data.totals.invoice_total)} />
            <Tot label="Before tax" v={money(data.totals.subtotal)} />
            <Tot label="Tax + card fees" v={money(data.totals.sales_tax + data.totals.cc_fee)} />
            <Tot label="Card sales (Square)" v={money(data.totals.square_sales)} />
          </div>

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
                  <th className="grp sep" colSpan={6}>Card sales (Square)</th>
                  <th className="grp sep" colSpan={3}>Cash</th>
                  <th className="grp sep" colSpan={2}>Invoiced / prepaid</th>
                  <th className="grp sep" colSpan={7}>Event totals</th>
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
                  <tr key={r.id} onClick={() => navigate(`/events/${r.event_id}`, { state: { from: location.pathname + location.search, label: "Financials" } })}>
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
                      </div>
                    </td>
                    <Num v={r.square_gross_sales} sep />
                    <Num v={r.square_discounts} />
                    <Num v={r.square_net_card} />
                    <Num v={r.square_card_tax} />
                    <Num v={r.square_tips_card} />
                    <Num v={r.square_cc_fee} />
                    <Num v={r.cash_collected} sep />
                    <Num v={r.cash_tax} />
                    <Num v={r.cash_pre_tax} />
                    <Num v={r.check_invoice} sep cls="key" />
                    <Num v={r.deposit} />
                    <td className="sep">
                      <Badge kind={r.taxable ? "gray" : "green"}>{r.taxable ? "Taxable" : "Exempt"}</Badge>
                    </td>
                    <Num v={r.event_sales_collected} />
                    <Num v={r.sales_tax} />
                    <Num v={r.sales_dollars} />
                    <Num v={r.giveback_amount} />
                    <Num v={r.net_event_sales} />
                    <Num v={r.location_fee} />
                    <td className="sep">
                      <Badge kind={r.paid ? "green" : "gray"}>
                        {r.paid ? `Paid${r.payment_method ? ` · ${r.payment_method}` : ""}` : "Unpaid"}
                      </Badge>
                    </td>
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
                  <td className="right sep">{money(sum("square_gross_sales"))}</td>
                  <td className="right">{money(sum("square_discounts"))}</td>
                  <td className="right">{money(sum("square_net_card"))}</td>
                  <td className="right">{money(sum("square_card_tax"))}</td>
                  <td className="right">{money(sum("square_tips_card"))}</td>
                  <td className="right">{money(sum("square_cc_fee"))}</td>
                  <td className="right sep">{money(sum("cash_collected"))}</td>
                  <td className="right">{money(sum("cash_tax"))}</td>
                  <td className="right">{money(sum("cash_pre_tax"))}</td>
                  <td className="right sep">{money(sum("check_invoice"))}</td>
                  <td className="right">{money(sum("deposit"))}</td>
                  <td className="sep" />
                  <td className="right">{money(sum("event_sales_collected"))}</td>
                  <td className="right">{money(sum("sales_tax"))}</td>
                  <td className="right">{money(sum("sales_dollars"))}</td>
                  <td className="right">{money(sum("giveback_amount"))}</td>
                  <td className="right">{money(sum("net_event_sales"))}</td>
                  <td className="right">{money(sum("location_fee"))}</td>
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

/** A money cell. Zero and missing values are dimmed so the eye skips them and
 *  lands on the columns that actually carry an amount — most rows only use a
 *  handful of the 18 figures. `sep` marks the first column of a header group. */
function Num({ v, sep, cls }: { v: number | null | undefined; sep?: boolean; cls?: string }) {
  const empty = !v;
  return (
    <td className={["right", sep && "sep", cls, empty && "zero"].filter(Boolean).join(" ")}>
      {money(v)}
    </td>
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
