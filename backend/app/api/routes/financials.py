from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.models import FinancialEntry, User

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
