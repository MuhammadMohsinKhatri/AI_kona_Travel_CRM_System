from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.models import FinancialEntry, User

router = APIRouter(prefix="/api/financials", tags=["financials"])


@router.get("/months")
def list_months(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[str]:
    """Distinct months (YYYY-MM) present in the ledger, newest first."""
    rows = (
        db.query(FinancialEntry.month)
        .filter(FinancialEntry.month.isnot(None))
        .distinct()
        .order_by(FinancialEntry.month.desc())
        .all()
    )
    return [r[0] for r in rows]


@router.get("")
def list_entries(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    month: Optional[str] = Query(None, description="YYYY-MM"),
    brand: Optional[str] = None,
) -> dict:
    """Ledger rows (the Google-Sheet replacement) plus totals for the filter."""
    query = db.query(FinancialEntry)
    if month:
        query = query.filter(FinancialEntry.month == month)
    if brand:
        query = query.filter(FinancialEntry.brand == brand)

    items = query.order_by(
        FinancialEntry.event_date.desc().nullslast(), FinancialEntry.id.desc()
    ).all()

    totals_q = query.with_entities(
        func.coalesce(func.sum(FinancialEntry.subtotal), 0.0),
        func.coalesce(func.sum(FinancialEntry.sales_tax), 0.0),
        func.coalesce(func.sum(FinancialEntry.cc_fee), 0.0),
        func.coalesce(func.sum(FinancialEntry.invoice_total), 0.0),
        func.coalesce(func.sum(FinancialEntry.balance_due), 0.0),
        func.coalesce(func.sum(FinancialEntry.square_sales), 0.0),
        func.coalesce(func.sum(FinancialEntry.units_served), 0.0),
    ).one()

    brands = [
        r[0]
        for r in db.query(FinancialEntry.brand).distinct().all()
        if r[0]
    ]

    def row(e: FinancialEntry) -> dict:
        return {
            "id": e.id,
            "event_id": e.event_id,
            "event_date": e.event_date,
            "event_name": e.event_name,
            "event_code": e.event_code,
            "brand": e.brand,
            "final_status": e.final_status,
            "event_type": e.event_type,
            "billing_model": e.billing_model,
            "units_served": e.units_served,
            "subtotal": e.subtotal,
            "sales_tax": e.sales_tax,
            "cc_fee": e.cc_fee,
            "invoice_total": e.invoice_total,
            "deposit": e.deposit,
            "balance_due": e.balance_due,
            "payment_method": e.payment_method,
            "square_sales": e.square_sales,
            "square_orders": e.square_orders,
            "has_variance": e.has_variance,
            "variance_amount": e.variance_amount,
            "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        }

    return {
        "items": [row(e) for e in items],
        "total": len(items),
        "brands": brands,
        "totals": {
            "subtotal": round(float(totals_q[0]), 2),
            "sales_tax": round(float(totals_q[1]), 2),
            "cc_fee": round(float(totals_q[2]), 2),
            "invoice_total": round(float(totals_q[3]), 2),
            "balance_due": round(float(totals_q[4]), 2),
            "square_sales": round(float(totals_q[5]), 2),
            "units_served": round(float(totals_q[6]), 1),
        },
    }
