from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.models import Invoice, User
from app.schemas.common import Page
from app.schemas.event import InvoiceOut

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


@router.get("", response_model=Page[InvoiceOut])
def list_invoices(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    has_variance: Optional[bool] = None,
) -> Page[InvoiceOut]:
    query = db.query(Invoice)
    if has_variance is not None:
        query = query.filter(Invoice.has_variance == has_variance)
    total = query.with_entities(func.count(Invoice.id)).scalar() or 0
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
