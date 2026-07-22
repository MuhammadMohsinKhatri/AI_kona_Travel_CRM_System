from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.models import Alert, Event, User
from app.schemas.common import Page
from app.schemas.event import AlertOut

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


# Plain-language guidance per alert source, shown on the alert detail page.
# The rules engine already produces a specific `action`; this is the wrapper
# around it — what kind of problem this is, where to fix it, and what to do
# afterwards. Written for someone who arrived from a phone notification with
# no other context.
SOURCE_GUIDE: dict[str, dict[str, str]] = {
    "financial": {
        "label": "Event data problem",
        "what": (
            "The automation read this event's notes but couldn't find something it "
            "needs to work out the bill correctly."
        ),
        "fix_in": "KonaOS event notes",
        "after": (
            "Once you've added the missing detail in KonaOS, re-run this event using "
            "the button below. The automation re-reads the notes and redoes the "
            "calculation and the invoice."
        ),
    },
    "cash": {
        "label": "Waiting on cash",
        "what": (
            "This is a minimum-guarantee event, so its invoice is the gap between what "
            "the truck actually sold and the minimum the host guaranteed. Until the "
            "cash is counted that gap can't be worked out, so no invoice has been "
            "raised."
        ),
        "fix_in": "Event Financials page",
        "after": (
            "Enter the cash on the Event Financials page, or let the cash automation "
            "post it. The invoice is then worked out and created automatically — you "
            "don't need to re-run anything."
        ),
    },
    "session": {
        "label": "Connection to KonaOS needs attention",
        "what": (
            "The dashboard signs in to KonaOS with a session key that expires every "
            "few weeks. This one has stopped working, so the automation can't read "
            "events or create invoices until it's replaced."
        ),
        "fix_in": "API Explorer → KonaOS Session",
        "after": (
            "Paste a fresh session key on the API Explorer page. Nothing needs "
            "restarting. Then re-run any dates that failed while it was down."
        ),
    },
}


def _to_out(alert: Alert, event: Optional[Event]) -> AlertOut:
    """Alert plus the event identity needed to act on it.

    Without the event an alert is unactionable: "rate per serving is missing"
    means nothing if you can't tell whose event it's about.
    """
    return AlertOut(
        id=alert.id,
        severity=alert.severity,
        issue=alert.issue,
        action=alert.action,
        resolved=alert.resolved,
        created_at=alert.created_at,
        event_id=alert.event_id,
        event_name=getattr(event, "event_name", None),
        crm_event_id=getattr(event, "crm_event_id", None),
        event_date=getattr(event, "event_date", None),
        brand=getattr(event, "brand", None),
        source=getattr(alert, "source", "financial") or "financial",
        notified=bool(getattr(alert, "notified", False)),
        notify_error=getattr(alert, "notify_error", "") or "",
    )


@router.get("", response_model=Page[AlertOut])
def list_alerts(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    severity: Optional[str] = None,
    resolved: Optional[bool] = None,
    source: Optional[str] = None,
) -> Page[AlertOut]:
    query = db.query(Alert)
    if severity:
        query = query.filter(Alert.severity == severity)
    if resolved is not None:
        query = query.filter(Alert.resolved == resolved)
    if source:
        query = query.filter(Alert.source == source)
    total = query.with_entities(func.count(Alert.id)).scalar() or 0
    items = (
        query.order_by(Alert.resolved.asc(), Alert.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    # One query for all the events, rather than one per alert.
    event_ids = {a.event_id for a in items if a.event_id}
    events = (
        {e.id: e for e in db.query(Event).filter(Event.id.in_(event_ids)).all()}
        if event_ids else {}
    )
    return Page(
        items=[_to_out(a, events.get(a.event_id)) for a in items],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{alert_id}")
def get_alert(
    alert_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> dict:
    """One alert, the event it concerns, and how to resolve it.

    Backs the alert detail page, which is where the Telegram link points — so
    it has to stand on its own without the surrounding list for context.
    """
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    event = db.get(Event, alert.event_id) if alert.event_id else None
    source = getattr(alert, "source", "financial") or "financial"
    return {
        "alert": _to_out(alert, event).model_dump(),
        "guide": SOURCE_GUIDE.get(source, SOURCE_GUIDE["financial"]),
        "event": None if event is None else {
            "id": event.id,
            "crm_event_id": event.crm_event_id,
            "event_name": event.event_name,
            "event_date": event.event_date,
            "brand": event.brand,
            "status": event.status,
            "event_type": event.event_type,
            "billing_model": event.billing_model,
            "final_invoice_amount": event.final_invoice_amount,
        },
        # Re-running only makes sense when there's an event to re-run.
        "can_rerun": event is not None,
    }


@router.post("/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(
    alert_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> AlertOut:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.resolved = True
    alert.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(alert)
    event = db.get(Event, alert.event_id) if alert.event_id else None
    return _to_out(alert, event)


# response_model=None is load-bearing: with `from __future__ import annotations`,
# FastAPI 0.115's return-type inference turns `-> None` into a response body and
# asserts at import ("Status code 204 must not have a response body").
@router.delete("/{alert_id}", status_code=204, response_model=None)
def delete_alert(
    alert_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> None:
    """Delete an alert outright (use /resolve to keep it as history)."""
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    db.delete(alert)
    db.commit()
