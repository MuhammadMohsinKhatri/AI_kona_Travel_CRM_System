from __future__ import annotations

import csv
import io
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.db.base import get_db
from app.models import Event, FinancialEntry, User

router = APIRouter(prefix="/api/financials", tags=["financials"])


# Full 46-column export, in the exact order of the original Google Sheet.
# (header, model attribute)
SHEET_COLUMNS: list[tuple[str, str]] = [
    ("DATE", "event_date"), ("EVENT ID", "crm_event_id"), ("EVENT", "event_name"),
    ("EVENT TYPE", "event_type"),
    ("Square: Gross Sales", "square_gross_sales"), ("Square: Discounts", "square_discounts"),
    ("Square: Net Sales (Card)", "square_net_card"), ("Square: Card Tax", "square_card_tax"),
    ("Square: Tips (Card)", "square_tips_card"), ("Square: CC Fee (4%)", "square_cc_fee"),
    ("Cash Collected", "cash_collected"), ("Cash Tax", "cash_tax"), ("Cash Pre-Tax", "cash_pre_tax"),
    ("Check / Invoice", "check_invoice"), ("Deposit / Prepay", "deposit"), ("Taxable?", "taxable"),
    ("Event Sales - Collected", "event_sales_collected"), ("Sales Tax Amount", "sales_tax"),
    ("Sales $", "sales_dollars"), ("Giveback Amount", "giveback_amount"),
    ("Net Event Sales", "net_event_sales"), ("Location Fee", "location_fee"), ("PAID?", "paid"),
    ("WORKER 1", "worker_1"), ("Hours", "worker_1_hours"), ("WORKER 2", "worker_2"),
    ("Hours_1", "worker_2_hours"), ("HOURS PAID?", "hours_paid"), ("Note", "note"),
    ("Invoice drafted?", "invoice_drafted"), ("Invoice Sent?", "invoice_sent"),
    ("TOTAL_EVENT_HOURS", "total_event_hours"), ("ATTENDEE_COUNT", "attendee_count"),
    ("BASE_AMOUNT", "base_amount"), ("HOURLY_RATE", "hourly_rate"),
    ("RATE_PER_SERVING", "rate_per_serving"), ("HOST_COVERS_SHORTFALL", "host_covers_shortfall"),
    ("UNITS_SERVED_TOTAL", "units_served"), ("UNITS_INCLUDED_IN_BASE", "units_included"),
    ("PAYMENT_METHOD", "payment_method"), ("TAX_MODE", "tax_mode"), ("SUBTOTAL", "subtotal"),
    ("ACTUAL_SALES", "actual_sales"), ("MG_SHORTFALL", "mg_shortfall"),
    ("TOTAL_TAX_RATE", "total_tax_rate"), ("TOTAL_TAX", "total_tax"),
    # AI tracking (beyond the original 46 sheet columns)
    ("AI_MODEL", "ai_model"), ("AI_PROMPT_TOKENS", "ai_prompt_tokens"),
    ("AI_COMPLETION_TOKENS", "ai_completion_tokens"), ("AI_COST_USD", "ai_cost_usd"),
]


# Header labels the importer resolves without going through SHEET_COLUMNS.
_H_EVENT_ID, _H_EVENT, _H_DATE, _H_TYPE = "EVENT ID", "EVENT", "DATE", "EVENT TYPE"
_BOOL_TRUE = {"YES", "TRUE", "1", "PAID", "Y", "T"}

# Importable legacy Google Sheets, one per brand. The sheets carry no brand
# column, so the importer stamps `brand` from here (which also groups the rows
# under the right brand filter). `url_attr` names the settings field holding
# the CSV export URL.
IMPORT_SHEETS: dict[str, dict[str, str]] = {
    "kona": {"label": "Kona Ice", "brand": "Kona Ice", "url_attr": "financials_sheet_csv_url"},
    "tom": {"label": "Travelin Tom", "brand": "Travelin Tom", "url_attr": "financials_sheet_tom_csv_url"},
}


def _coerce(attr: str, raw: str):
    """Coerce a raw sheet cell to the FinancialEntry column's Python type.

    The sheet stores money as bare numbers and flags as YES/NO/TRUE/FALSE, so
    we lean on the SQLAlchemy column type rather than a per-field table.
    """
    col = FinancialEntry.__table__.columns.get(attr)
    val = (raw or "").strip()
    pytype = col.type.python_type if col is not None else str
    if pytype is bool:
        return val.upper() in _BOOL_TRUE
    if pytype in (int, float):
        if not val:
            return 0 if pytype is int else 0.0
        try:
            n = float(val.replace(",", "").replace("$", ""))
            return int(n) if pytype is int else n
        except ValueError:
            return 0 if pytype is int else 0.0
    return val


def _filtered(
    db: Session,
    month: Optional[str],
    brand: Optional[str],
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    event_type: Optional[str] = None,
    paid: Optional[bool] = None,
    search: Optional[str] = None,
):
    """Shared filter chain for the list view and the CSV export.

    A custom from/to date range (event_date is ISO YYYY-MM-DD, so plain string
    comparison is correct) can be combined with or used instead of the month
    shortcut.
    """
    q = db.query(FinancialEntry)
    if month:
        q = q.filter(FinancialEntry.month == month)
    if from_date:
        q = q.filter(FinancialEntry.event_date >= from_date)
    if to_date:
        q = q.filter(FinancialEntry.event_date <= to_date)
    if brand:
        q = q.filter(FinancialEntry.brand == brand)
    if event_type:
        q = q.filter(FinancialEntry.event_type == event_type)
    if paid is not None:
        q = q.filter(FinancialEntry.paid == paid)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(
            FinancialEntry.event_name.ilike(like)
            | FinancialEntry.event_code.ilike(like)
            | FinancialEntry.crm_event_id.ilike(like)
        )
    return q.order_by(FinancialEntry.event_date.desc().nullslast(), FinancialEntry.id.desc())


@router.get("/months")
def list_months(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[str]:
    rows = (
        db.query(FinancialEntry.month).filter(FinancialEntry.month.isnot(None))
        .distinct().order_by(FinancialEntry.month.desc()).all()
    )
    return [r[0] for r in rows]


@router.get("")
def list_entries(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    month: Optional[str] = Query(None),
    brand: Optional[str] = None,
    from_date: Optional[str] = Query(None, description="Inclusive event_date lower bound (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Inclusive event_date upper bound (YYYY-MM-DD)"),
    event_type: Optional[str] = Query(None),
    paid: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Matches event name / code / CRM id"),
) -> dict:
    """Key columns for the dashboard view (all 46 are in the CSV export)."""
    items = _filtered(db, month, brand, from_date, to_date, event_type, paid, search).all()
    # order_by(None) strips the ORDER BY before aggregating — Postgres rejects
    # "SELECT sum(...) ORDER BY event_date" (non-grouped column). SQLite happens
    # to allow it, which is why this only ever failed in production.
    q = _filtered(db, month, brand, from_date, to_date, event_type, paid, search).order_by(None)
    totals = q.with_entities(
        func.coalesce(func.sum(FinancialEntry.subtotal), 0.0),
        func.coalesce(func.sum(FinancialEntry.sales_tax), 0.0),
        func.coalesce(func.sum(FinancialEntry.cc_fee), 0.0),
        func.coalesce(func.sum(FinancialEntry.invoice_total), 0.0),
        func.coalesce(func.sum(FinancialEntry.balance_due), 0.0),
        func.coalesce(func.sum(FinancialEntry.square_net_card), 0.0),
        func.coalesce(func.sum(FinancialEntry.check_invoice), 0.0),
        func.coalesce(func.sum(FinancialEntry.units_served), 0.0),
    ).one()
    brands = [r[0] for r in db.query(FinancialEntry.brand).distinct().all() if r[0]]
    event_types = [
        r[0] for r in db.query(FinancialEntry.event_type).distinct().all() if r[0]
    ]

    def row(e: FinancialEntry) -> dict:
        return {
            "id": e.id, "event_id": e.event_id, "event_date": e.event_date,
            "event_name": e.event_name, "event_code": e.event_code, "brand": e.brand,
            "final_status": e.final_status, "event_type": e.event_type,
            "billing_model": e.billing_model, "units_served": e.units_served,
            "subtotal": e.subtotal, "sales_tax": e.sales_tax, "cc_fee": e.cc_fee,
            "check_invoice": e.check_invoice,
            # Square breakdown (sheet columns 5-10)
            "square_gross_sales": e.square_gross_sales,
            "square_discounts": e.square_discounts,
            "square_net_card": e.square_net_card,
            "square_card_tax": e.square_card_tax,
            "square_tips_card": e.square_tips_card,
            "square_cc_fee": e.square_cc_fee,
            "square_orders": e.square_orders, "square_device": e.square_device,
            # Cash split (11-13)
            "cash_collected": e.cash_collected, "cash_tax": e.cash_tax,
            "cash_pre_tax": e.cash_pre_tax,
            # Billing (14-22)
            "taxable": e.taxable,
            "event_sales_collected": e.event_sales_collected,
            "sales_dollars": e.sales_dollars,
            "giveback_amount": e.giveback_amount,
            "net_event_sales": e.net_event_sales,
            "location_fee": e.location_fee,
            "invoice_total": e.invoice_total, "deposit": e.deposit, "balance_due": e.balance_due,
            "payment_method": e.payment_method, "paid": e.paid,
            "has_variance": e.has_variance, "variance_amount": e.variance_amount,
            # Reasoning + AI tracking
            "note": e.note,
            "ai_model": e.ai_model,
            "ai_prompt_tokens": e.ai_prompt_tokens,
            "ai_completion_tokens": e.ai_completion_tokens,
            "ai_cost_usd": e.ai_cost_usd,
            "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        }

    return {
        "items": [row(e) for e in items],
        "total": len(items),
        "brands": brands,
        "event_types": event_types,
        "totals": {
            "subtotal": round(float(totals[0]), 2), "sales_tax": round(float(totals[1]), 2),
            "cc_fee": round(float(totals[2]), 2), "invoice_total": round(float(totals[3]), 2),
            "balance_due": round(float(totals[4]), 2), "square_sales": round(float(totals[5]), 2),
            "check_invoice": round(float(totals[6]), 2), "units_served": round(float(totals[7]), 1),
        },
    }


# response_model=None: see alerts.py — required for 204 + `-> None` on FastAPI 0.115.
@router.delete("/{entry_id}", status_code=204, response_model=None)
def delete_entry(
    entry_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> None:
    """Remove a ledger row. The event itself is kept — re-running the pipeline
    for its date rebuilds the row from the event."""
    entry = db.get(FinancialEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    db.delete(entry)
    db.commit()


@router.delete("", response_model=None)
def delete_entries_bulk(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    month: Optional[str] = None,
    brand: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    event_type: Optional[str] = None,
    paid: Optional[bool] = None,
    search: Optional[str] = None,
) -> dict[str, int]:
    """Bulk-delete every ledger row matching the given filters (same params as
    the list endpoint). Events, invoices and alerts are untouched — re-running
    the pipeline for those dates rebuilds the rows.

    A date scope (month or from/to) is required: the point is "wipe this day /
    month and re-run", and requiring it keeps a stray click with only e.g.
    brand set from clearing the whole ledger.
    """
    if not (month or from_date or to_date):
        raise HTTPException(
            status_code=400,
            detail="Provide a month or a from/to date — bulk delete is date-scoped",
        )
    entries = _filtered(db, month, brand, from_date, to_date, event_type, paid, search).all()
    for entry in entries:
        db.delete(entry)
    db.commit()
    return {"deleted": len(entries)}


@router.get("/export.csv")
def export_csv(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    month: Optional[str] = Query(None),
    brand: Optional[str] = None,
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    paid: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
) -> StreamingResponse:
    """Download the full 46-column ledger as CSV — the complete sheet replacement."""
    items = _filtered(db, month, brand, from_date, to_date, event_type, paid, search).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([h for h, _attr in SHEET_COLUMNS])
    for e in items:
        w.writerow([getattr(e, attr, "") for _h, attr in SHEET_COLUMNS])
    buf.seek(0)
    fname = f"kona-financials-{month or 'all'}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/import-sheet")
def import_sheet(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    sheet: str = Query("kona", description="Which brand sheet to import: 'kona' or 'tom'"),
    url: Optional[str] = Query(None, description="Override the sheet CSV export URL"),
) -> dict:
    """Import a legacy financial Google Sheet into the Postgres ledger.

    One sheet per brand (``sheet=kona`` | ``sheet=tom``). Repeatable and
    idempotent (keyed by the sheet's ``EVENT ID`` = crm_event_id):

      * A sheet row whose event isn't in the DB gets a lightweight placeholder
        event (date / name / type / brand from the sheet) so the ledger FK is
        satisfied. A later pipeline run reuses the placeholder — no dupes.
      * A pipeline-owned ledger row (``source != "sheet"``) is NEVER overwritten
        — the sheet only fills events the pipeline hasn't produced.
      * Rows this importer created (``source == "sheet"``) are refreshed on
        re-import, so you can keep re-pulling the sheet during the transition.

    The sheet has no brand column, so the brand is stamped from IMPORT_SHEETS.
    The sheet already carries the final computed columns, so this is a direct
    column-map via SHEET_COLUMNS — no billing re-computation.
    """
    cfg = IMPORT_SHEETS.get(sheet)
    if cfg is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sheet '{sheet}'. Valid: {', '.join(IMPORT_SHEETS)}",
        )
    brand = cfg["brand"]
    sheet_url = url or getattr(settings, cfg["url_attr"])
    try:
        resp = httpx.get(sheet_url, follow_redirects=True, timeout=30.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Couldn't fetch the Google Sheet: {exc}"
        )

    reader = csv.DictReader(io.StringIO(resp.text))
    created = updated = skipped_protected = placeholders = skipped_blank = 0
    for raw_row in reader:
        # Sheet headers carry embedded newlines ("Sales $\n") — normalise the
        # keys so they line up with SHEET_COLUMNS' clean labels.
        row = {(k or "").strip(): v for k, v in raw_row.items()}
        crm_id = (row.get(_H_EVENT_ID) or "").strip()
        if not crm_id:
            skipped_blank += 1
            continue

        event = db.query(Event).filter(Event.crm_event_id == crm_id).first()
        if event is None:
            event = Event(
                crm_event_id=crm_id,
                event_name=(row.get(_H_EVENT) or "").strip(),
                event_date=((row.get(_H_DATE) or "").strip() or None),
                event_type=(row.get(_H_TYPE) or "").strip(),
                brand=brand,
                status="imported",
                status_reason=f"Placeholder created from {cfg['label']} Google Sheet import",
            )
            db.add(event)
            db.flush()  # assign event.id for the FK below
            placeholders += 1

        entry = (
            db.query(FinancialEntry)
            .filter(FinancialEntry.event_id == event.id)
            .one_or_none()
        )
        if entry is not None and entry.source != "sheet":
            skipped_protected += 1  # pipeline owns this row — leave it alone
            continue
        if entry is None:
            entry = FinancialEntry(event_id=event.id, source="sheet")
            db.add(entry)
            created += 1
        else:
            updated += 1

        for header, attr in SHEET_COLUMNS:
            if header in row:  # AI_* headers aren't in the sheet — skip them
                setattr(entry, attr, _coerce(attr, row[header]))
        # The sheet has no brand column — stamp it so rows group under the brand.
        entry.brand = brand
        entry.source = "sheet"
        entry.run_id = None
        entry.month = ((row.get(_H_DATE) or "").strip()[:7]) or None

    db.commit()
    return {
        "sheet": sheet,
        "label": cfg["label"],
        "brand": brand,
        "created": created,
        "updated": updated,
        "skipped_protected": skipped_protected,
        "placeholders_created": placeholders,
        "skipped_blank": skipped_blank,
        "source_url": sheet_url,
    }
