from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.models import CrmAuditEntry, User

router = APIRouter(prefix="/api/crm-audit", tags=["crm-audit"])


def _filtered(
    db: Session,
    event_id: Optional[int],
    crm_event_id: Optional[str],
    action: Optional[str],
    search: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
):
    """Date filters are by the EVENT's own date (matching Financials/
    Invoices/Events) — NOT by created_at (when this audit row was written).
    A pipeline run often processes events dated days or weeks before the run
    itself executes, so filtering by created_at would silently show nothing
    for the date you actually care about. event_date is a plain
    YYYY-MM-DD string, so lexicographic comparison is correct."""
    q = db.query(CrmAuditEntry)
    if event_id:
        q = q.filter(CrmAuditEntry.event_id == event_id)
    if crm_event_id:
        q = q.filter(CrmAuditEntry.crm_event_id == crm_event_id)
    if action:
        q = q.filter(CrmAuditEntry.action == action)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(
            or_(
                CrmAuditEntry.event_name.ilike(like),
                CrmAuditEntry.crm_event_id.ilike(like),
                CrmAuditEntry.summary.ilike(like),
            )
        )
    if from_date:
        q = q.filter(CrmAuditEntry.event_date >= from_date)
    if to_date:
        q = q.filter(CrmAuditEntry.event_date <= to_date)
    return q.order_by(CrmAuditEntry.created_at.desc())


def _row(e: CrmAuditEntry) -> dict:
    return {
        "id": e.id,
        "event_id": e.event_id,
        "crm_event_id": e.crm_event_id,
        "event_name": e.event_name,
        "event_date": e.event_date,
        "run_id": e.run_id,
        "action": e.action,
        "summary": e.summary,
        "detail": e.detail,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("")
def list_entries(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    event_id: Optional[int] = Query(None),
    crm_event_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Matches event name / CRM id / summary"),
    from_date: Optional[str] = Query(None, description="Inclusive lower bound (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Inclusive upper bound (YYYY-MM-DD)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    """The structured, filterable record of every write our system has made
    to KonaOS — what the "CRM Activity" page and each event's own activity
    history read from. Complements (doesn't replace) the raw per-run text log
    on the Pipeline Runs page."""
    q = _filtered(db, event_id, crm_event_id, action, search, from_date, to_date)
    total = q.order_by(None).with_entities(func.count(CrmAuditEntry.id)).scalar() or 0
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    actions = [
        r[0] for r in db.query(CrmAuditEntry.action).distinct().all() if r[0]
    ]
    return {
        "items": [_row(e) for e in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "actions": sorted(actions),
    }
