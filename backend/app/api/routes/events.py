from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.models import Event, User
from app.schemas.common import Page
from app.schemas.event import EventDetail, EventSummary

router = APIRouter(prefix="/api/events", tags=["events"])


@router.post("/{event_id}/waive-cc-fee", response_model=EventDetail)
def waive_cc_fee(
    event_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> Event:
    """Recalculate this event's invoice WITHOUT the 4% processing fee.

    Use when the client pays by check after the draft was created: they deduct
    the fee themselves, so the invoice must be re-issued without it. Updates
    the stored calculations + local draft, and (when not in dry-run) replaces
    the draft in the CRM and updates the event.
    """
    from app.config import settings
    from app.core import billing, invoice_builder
    from app.core.pipeline import (_audit, _equip_suffix, _preserved_detail,
                                    _replace_draft, _store_local_invoice)
    from app.integrations import factory

    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if not event.classification:
        raise HTTPException(
            status_code=400, detail="Event has no classification — run the pipeline first"
        )

    classification = dict(event.classification)
    # A waived fee means the client settled by check.
    classification["PAYMENT_METHOD"] = "CHECK"
    calc = billing.calculate_invoice(classification, waive_cc_fee=True)
    merged = {**classification, "calculations": calc}

    payload = invoice_builder.build_invoice_payload(merged, event.cleaned, event.raw)

    event.classification = classification
    event.calculations = calc
    event.final_invoice_amount = float(calc.get("FINAL_INVOICE_AMOUNT", 0) or 0)

    if payload:
        if settings.pipeline_dry_run:
            _store_local_invoice(db, event, payload, status="dry_run")
        else:
            crm = factory.get_crm()
            _replace_draft(db, crm, event, payload, event.cleaned)
            sync_result = crm.update_event(event.crm_event_id, {
                "EVENT_ID": event.crm_event_id,
                "invoiceAmount": calc.get("FINAL_INVOICE_AMOUNT"),
                "invoiceStatus": "draft",
            })
            _audit(
                db, event, "event_updated",
                "Synced invoice amount to KonaOS event (CC fee waived)"
                + _equip_suffix(sync_result),
                detail={
                    "fields_updated": ["invoiceAmount", "invoiceStatus"],
                    "values": {
                        "invoiceAmount": calc.get("FINAL_INVOICE_AMOUNT"),
                        "invoiceStatus": "draft",
                    },
                    **_preserved_detail(sync_result),
                },
            )

    db.commit()
    db.refresh(event)
    return event


def _filtered_events(
    db: Session,
    status: Optional[str] = None,
    brand: Optional[str] = None,
    billing_model: Optional[str] = None,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    run_id: Optional[int] = None,
):
    """One filter builder shared by list + bulk delete, so 'delete filtered'
    removes exactly the rows the list shows. event_date is a YYYY-MM-DD
    string, so lexicographic >=/<= is correct date ordering."""
    query = db.query(Event)
    if status:
        query = query.filter(Event.status == status)
    if brand:
        query = query.filter(Event.brand == brand)
    if billing_model:
        query = query.filter(Event.billing_model == billing_model)
    if run_id is not None:
        query = query.filter(Event.run_id == run_id)
    if date_from:
        query = query.filter(Event.event_date >= date_from)
    if date_to:
        query = query.filter(Event.event_date <= date_to)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(Event.event_name.ilike(like), Event.event_code.ilike(like),
                Event.crm_event_id.ilike(like))
        )
    return query


@router.get("", response_model=Page[EventSummary])
def list_events(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    status: Optional[str] = None,
    brand: Optional[str] = None,
    billing_model: Optional[str] = None,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    run_id: Optional[int] = Query(None, description="Only events from this pipeline run"),
) -> Page[EventSummary]:
    query = _filtered_events(db, status, brand, billing_model, q, date_from, date_to, run_id)
    total = query.with_entities(func.count(Event.id)).scalar() or 0
    items = (
        query.order_by(Event.event_date.desc().nullslast(), Event.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    # Rows stored before status_reason existed have it empty — derive it from
    # the same gate that made the call, purely for display. The instances are
    # detached afterwards so the mutation can never be flushed back.
    from app.core.event_cleaner import is_processable

    legacy = [e for e in items if not e.status_reason and e.cleaned]
    if legacy:
        for e in legacy:
            e.status_reason = is_processable(e.cleaned)[1]
        db.expunge_all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/{event_id}", response_model=EventDetail)
def get_event(
    event_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


# response_model=None: see alerts.py — required for 204 + `-> None` on FastAPI 0.115.
@router.delete("/{event_id}", status_code=204, response_model=None)
def delete_event(
    event_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> None:
    """Remove an event from THIS database (KonaOS is not touched).

    Cascades to the event's invoices, alerts and ledger row. Note the event
    reappears if the pipeline is re-run for its date — KonaOS remains the
    source of truth; this only clears our copy.
    """
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    db.delete(event)
    db.commit()


@router.delete("", response_model=None)
def delete_events_bulk(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    status: Optional[str] = None,
    brand: Optional[str] = None,
    billing_model: Optional[str] = None,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    run_id: Optional[int] = None,
) -> dict[str, int]:
    """Bulk-delete every event matching the given filters (same params as the
    list endpoint), cascading to invoices, alerts and ledger rows. KonaOS is
    not touched — a pipeline re-run for those dates restores the events.

    At least one filter is required: an unfiltered DELETE wiping the whole
    table should never happen by accident.
    """
    if not any([status, brand, billing_model, q, date_from, date_to, run_id]):
        raise HTTPException(
            status_code=400,
            detail="Refusing to delete all events — provide at least one filter",
        )
    events = _filtered_events(db, status, brand, billing_model, q, date_from, date_to, run_id).all()
    # ORM-level deletes so relationship cascades run (a bulk query.delete()
    # would bypass them and orphan invoices/alerts/ledger rows on SQLite).
    for event in events:
        db.delete(event)
    db.commit()
    return {"deleted": len(events)}
