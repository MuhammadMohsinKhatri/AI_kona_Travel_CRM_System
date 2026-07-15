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
) -> Page[EventSummary]:
    query = db.query(Event)
    if status:
        query = query.filter(Event.status == status)
    if brand:
        query = query.filter(Event.brand == brand)
    if billing_model:
        query = query.filter(Event.billing_model == billing_model)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(Event.event_name.ilike(like), Event.event_code.ilike(like),
                Event.crm_event_id.ilike(like))
        )
    total = query.with_entities(func.count(Event.id)).scalar() or 0
    items = (
        query.order_by(Event.event_date.desc().nullslast(), Event.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/{event_id}", response_model=EventDetail)
def get_event(
    event_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event
