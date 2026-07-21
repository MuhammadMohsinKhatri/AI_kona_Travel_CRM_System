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
      `Import the ${brandLabel} financial Google Sheet into the ledger?\n\n` +
      "• Events not yet in the system get a placeholder record.\n" +
      "• Rows the pipeline already produced are left untouched.\n" +
      "• Safe to run repeatedly — it refreshes rows it previously imported."
    )) return;
    setImporting(true);
    setImportMsg("");
    try {
      const r = await api.importFinancialsSheet(importSource);
      setImportMsg(
        `Imported ${r.label} — ${r.created} new, ${r.updated} refreshed, ` +
        `${r.skipped_protected} left untouched (pipeline-owned), ` +
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
            The financial ledger — one row per event, updated on every pipeline run. Stored in
            PostgreSQL (replaces the monthly Google Sheet). All 46 sheet columns are saved; the
            key ones are shown here, the full set is in the CSV export.
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
            title="Import the selected brand's financial Google Sheet into the ledger (repeatable; won't overwrite pipeline rows)">
            {importing ? "⏳ Importing…" : "⬆ Import from Google Sheet"}
          </button>
          <button className="btn" onClick={downloadCsv} disabled={!data || data.total === 0}>
            ⬇ Download CSV (all 46 columns)
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
        <span className="muted" style={{ fontSize: 12 }}>or custom range:</span>
        <label className="muted" htmlFor="fin-date-from" style={{ fontSize: 12 }}>From</label>
        <input id="fin-date-from" className="input" type="date" value={fromDate} style={{ width: 150 }}
          onChange={(e) => pickRange({ from: e.target.value })} title="Rows on or after this date (inclusive)" />
        <label className="muted" htmlFor="fin-date-to" style={{ fontSize: 12 }}>To</label>
        <input id="fin-date-to" className="input" type="date" value={toDate} style={{ width: 150 }}
          onChange={(e) => pickRange({ to: e.target.value })} title="Rows on or before this date (inclusive)" />
        <select className="select" value={brand} onChange={(e) => updateParams({ brand: e.target.value || undefined })}>
          <option value="">All brands</option>
          {(data?.brands ?? []).map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
        <select className="select" value={eventType} onChange={(e) => updateParams({ event_type: e.target.value || undefined })}>
          <option value="">All types</option>
          {(data?.event_types ?? []).map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select className="select" value={paid} onChange={(e) => updateParams({ paid: e.target.value || undefined })}>
          <option value="">Paid + unpaid</option>
          <option value="true">Paid only</option>
          <option value="false">Unpaid only</option>
        </select>
        <input className="input" placeholder="Search event / code…" value={searchInput}
          style={{ width: 190 }} onChange={(e) => setSearchInput(e.target.value)} />
        {hasFilters && (
          <button className="btn" onClick={clearFilters} title="Clear all filters">✕ Clear</button>
        )}
        {data && <span className="muted">{data.total} entries</span>}
        {/* Bulk delete is date-scoped: wipe the selected day/month (plus any
            other active filters), then re-run the pipeline to rebuild clean
            rows. The backend rejects the call without a date scope. */}
        {(month || fromDate || toDate) && data && data.total > 0 && (
          <BulkDeleteButton
            count={data.total}
            noun="ledger rows"
            onDelete={async () => {
              await api.deleteFinancials(params);
              await reload();
            }}
          />
        )}
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

          {/* Sheet-order columns: wide by design, so DATE/EVENT/EVENT TYPE are
              frozen on the left and the header/totals rows are pinned. */}
          <div className="table-wrap sheet">
            <table style={{ whiteSpace: "nowrap" }}>
              <thead>
                <tr>
                  <th className="stick stick-1"><div className="cell">DATE</div></th>
                  <th className="stick stick-2"><div className="cell">EVENT</div></th>
                  <th className="stick stick-3"><div className="cell">EVENT TYPE</div></th>
                  <th className="right">Square: Gross Sales</th>
                  <th className="right">Square: Discounts</th>
                  <th className="right">Square: Net Sales (Card)</th>
                  <th className="right">Square: Card Tax</th>
                  <th className="right">Square: Tips (Card)</th>
                  <th className="right">Square: CC Fee (4%)</th>
                  <th className="right">Cash Collected</th>
                  <th className="right">Cash Tax</th>
                  <th className="right">Cash Pre-Tax</th>
                  <th className="right">Check / Invoice</th>
                  <th className="right">Deposit / Prepay</th>
                  <th>Taxable?</th>
                  <th className="right">Event Sales - Collected</th>
                  <th className="right">Sales Tax Amount</th>
                  <th className="right">Sales $</th>
                  <th className="right">Giveback Amount</th>
                  <th className="right">Net Event Sales</th>
                  <th className="right">Location Fee</th>
                  <th>PAID?</th>
                  <th>Reasoning</th>
                  <th className="actions"></th>
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
                    <td className="right">{money(r.square_gross_sales)}</td>
                    <td className="right">{money(r.square_discounts)}</td>
                    <td className="right">{money(r.square_net_card)}</td>
                    <td className="right">{money(r.square_card_tax)}</td>
                    <td className="right">{money(r.square_tips_card)}</td>
                    <td className="right">{money(r.square_cc_fee)}</td>
                    <td className="right">{money(r.cash_collected)}</td>
                    <td className="right">{money(r.cash_tax)}</td>
                    <td className="right">{money(r.cash_pre_tax)}</td>
                    <td className="right">{money(r.check_invoice)}</td>
                    <td className="right">{money(r.deposit)}</td>
                    <td>
                      <Badge kind={r.taxable ? "gray" : "green"}>{r.taxable ? "Taxable" : "Exempt"}</Badge>
                    </td>
                    <td className="right">{money(r.event_sales_collected)}</td>
                    <td className="right">{money(r.sales_tax)}</td>
                    <td className="right">{money(r.sales_dollars)}</td>
                    <td className="right">{money(r.giveback_amount)}</td>
                    <td className="right">{money(r.net_event_sales)}</td>
                    <td className="right">{money(r.location_fee)}</td>
                    <td>
                      <Badge kind={r.paid ? "green" : "gray"}>
                        {r.paid ? `Paid${r.payment_method ? ` · ${r.payment_method}` : ""}` : "Unpaid"}
                      </Badge>
                    </td>
                    {/* Classifier reasoning — full text on hover (it's long). */}
                    <td
                      title={r.note || ""}
                      style={{ minWidth: 240, maxWidth: 320, fontSize: 12 }}
                    >
                      <div className="clamp2">
                        {r.note ? (r.note.length > 120 ? r.note.slice(0, 120) + "…" : r.note) : "—"}
                      </div>
                    </td>
                    <td className="actions">
                      <DeleteButton
                        title="Delete this ledger row (the event is kept; re-run rebuilds it)"
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
                  <td className="right">{money(sum("square_gross_sales"))}</td>
                  <td className="right">{money(sum("square_discounts"))}</td>
                  <td className="right">{money(sum("square_net_card"))}</td>
                  <td className="right">{money(sum("square_card_tax"))}</td>
                  <td className="right">{money(sum("square_tips_card"))}</td>
                  <td className="right">{money(sum("square_cc_fee"))}</td>
                  <td className="right">{money(sum("cash_collected"))}</td>
                  <td className="right">{money(sum("cash_tax"))}</td>
                  <td className="right">{money(sum("cash_pre_tax"))}</td>
                  <td className="right">{money(sum("check_invoice"))}</td>
                  <td className="right">{money(sum("deposit"))}</td>
                  <td />
                  <td className="right">{money(sum("event_sales_collected"))}</td>
                  <td className="right">{money(sum("sales_tax"))}</td>
                  <td className="right">{money(sum("sales_dollars"))}</td>
                  <td className="right">{money(sum("giveback_amount"))}</td>
                  <td className="right">{money(sum("net_event_sales"))}</td>
                  <td className="right">{money(sum("location_fee"))}</td>
                  <td />
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

function Tot({ label, v }: { label: string; v: string }) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="value small">{v}</div>
    </div>
  );
}
