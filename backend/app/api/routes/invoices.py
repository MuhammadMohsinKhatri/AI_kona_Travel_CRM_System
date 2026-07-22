from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.models import Event, Invoice, User
from app.schemas.common import Page
from app.schemas.event import InvoiceOut

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


def _filtered(
    db: Session,
    has_variance: Optional[bool],
    month: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
):
    """Shared filter chain for the list view and (future) exports.

    Invoice has no date column of its own — filtering is by the underlying
    event's date, so every date filter here joins to Event. event_date is
    ISO YYYY-MM-DD, so plain string comparison/prefix-match is correct.
    """
    query = db.query(Invoice)
    if has_variance is not None:
        query = query.filter(Invoice.has_variance == has_variance)
    if month or from_date or to_date:
        query = query.join(Event, Invoice.event_id == Event.id)
        if month:
            query = query.filter(Event.event_date.like(f"{month}-%"))
        if from_date:
            query = query.filter(Event.event_date >= from_date)
        if to_date:
            query = query.filter(Event.event_date <= to_date)
    return query


@router.get("/months")
def list_months(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[str]:
    """Distinct YYYY-MM months across invoiced events, for the month-shortcut
    dropdown — same pattern as /api/financials/months."""
    rows = (
        db.query(Event.event_date)
        .join(Invoice, Invoice.event_id == Event.id)
        .filter(Event.event_date.isnot(None))
        .distinct()
        .all()
    )
    months = sorted({r[0][:7] for r in rows if r[0] and len(r[0]) >= 7}, reverse=True)
    return months


@router.get("", response_model=Page[InvoiceOut])
def list_invoices(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    has_variance: Optional[bool] = None,
    month: Optional[str] = Query(None, description="YYYY-MM shortcut, by event date"),
    from_date: Optional[str] = Query(None, description="Inclusive event_date lower bound (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Inclusive event_date upper bound (YYYY-MM-DD)"),
) -> Page[InvoiceOut]:
    query = _filtered(db, has_variance, month, from_date, to_date)
    total = query.order_by(None).with_entities(func.count(Invoice.id)).scalar() or 0
    items = (
        query.order_by(Invoice.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/{invoice_id}", response_model=InvoiceOut)
def get_invoice(
    invoice_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> Invoice:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


# response_model=None: see alerts.py — required for 204 + `-> None` on FastAPI 0.115.
@router.delete("/{invoice_id}", status_code=204, response_model=None)
def delete_invoice(
    invoice_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> None:
    """Remove an invoice record from THIS database.

    Does not delete anything in KonaOS — under PIPELINE_DRY_RUN no KonaOS
    draft exists anyway. Re-running the pipeline for the event's date
    recreates the record.
    """
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    db.delete(invoice)
    db.commit()
