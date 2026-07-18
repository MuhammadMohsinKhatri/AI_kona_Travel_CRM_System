from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.models import Alert, Event, Invoice, PipelineRun, User

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
def stats(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    from_date: Optional[str] = Query(None, description="Inclusive event_date lower bound (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Inclusive event_date upper bound (YYYY-MM-DD)"),
) -> dict:
    """Dashboard tiles, optionally scoped to an event-date window.

    With no dates these are lifetime totals. Invoices and alerts have no date
    column of their own, so they're scoped through their parent event's date.
    """

    def scoped(q):
        """Apply the date window to a query that touches Event."""
        if from_date:
            q = q.filter(Event.event_date >= from_date)
        if to_date:
            q = q.filter(Event.event_date <= to_date)
        return q

    total_events = scoped(db.query(func.count(Event.id))).scalar() or 0
    needs_review = scoped(
        db.query(func.count(Event.id)).filter(Event.status == "needs_review")
    ).scalar() or 0
    errored = scoped(
        db.query(func.count(Event.id)).filter(Event.status == "error")
    ).scalar() or 0

    # Invoices/alerts carry no date — join to their event to scope them.
    total_invoices = scoped(
        db.query(func.count(Invoice.id)).join(Event, Invoice.event_id == Event.id)
    ).scalar() or 0
    invoiced_amount = scoped(
        db.query(func.coalesce(func.sum(Invoice.grand_total), 0.0))
        .join(Event, Invoice.event_id == Event.id)
    ).scalar() or 0.0
    open_alerts = scoped(
        db.query(func.count(Alert.id))
        .join(Event, Alert.event_id == Event.id)
        .filter(Alert.resolved == False)  # noqa: E712
    ).scalar() or 0

    by_severity = dict(
        scoped(
            db.query(Alert.severity, func.count(Alert.id))
            .join(Event, Alert.event_id == Event.id)
            .filter(Alert.resolved == False)  # noqa: E712
        ).group_by(Alert.severity).all()
    )
    by_model = dict(
        scoped(
            db.query(Event.billing_model, func.count(Event.id))
            .filter(Event.billing_model != "")
        ).group_by(Event.billing_model).all()
    )
    by_type = dict(
        scoped(
            db.query(Event.event_type, func.count(Event.id))
            .filter(Event.event_type != "")
        ).group_by(Event.event_type).all()
    )

    # AI usage is per RUN, and a run is keyed to the date it targeted — scope
    # it by target_date rather than by event dates.
    ai_q = db.query(
        func.coalesce(func.sum(PipelineRun.ai_prompt_tokens), 0),
        func.coalesce(func.sum(PipelineRun.ai_completion_tokens), 0),
        func.coalesce(func.sum(PipelineRun.ai_cost_usd), 0.0),
    )
    if from_date:
        ai_q = ai_q.filter(PipelineRun.target_date >= from_date)
    if to_date:
        ai_q = ai_q.filter(PipelineRun.target_date <= to_date)
    ai_totals = ai_q.one()

    last_run = db.query(PipelineRun).order_by(PipelineRun.id.desc()).first()

    # Single-day view: has THIS date been processed, and is a run going on
    # right now? Drives the status banner next to the Run button.
    def _mini_run(r: Optional[PipelineRun]) -> Optional[dict]:
        if r is None:
            return None
        return {
            "id": r.id,
            "status": r.status,
            "trigger": r.trigger,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "events_processed": r.events_processed,
            "invoices_created": r.invoices_created,
        }

    date_run = None
    if from_date and from_date == to_date:
        date_run = {
            "running": _mini_run(
                db.query(PipelineRun)
                .filter(PipelineRun.target_date == from_date, PipelineRun.status == "running")
                .order_by(PipelineRun.id.desc())
                .first()
            ),
            "last": _mini_run(
                db.query(PipelineRun)
                .filter(PipelineRun.target_date == from_date, PipelineRun.status != "running")
                .order_by(PipelineRun.id.desc())
                .first()
            ),
        }

    return {
        "date_run": date_run,
        "scope": {
            "from_date": from_date,
            "to_date": to_date,
            "all_time": not (from_date or to_date),
        },
        "total_events": total_events,
        "needs_review": needs_review,
        "errored": errored,
        "total_invoices": total_invoices,
        "invoiced_amount": round(float(invoiced_amount), 2),
        "open_alerts": open_alerts,
        "alerts_by_severity": by_severity,
        "events_by_event_type": by_type,
        "events_by_billing_model": by_model,
        "ai_usage": {
            "prompt_tokens": int(ai_totals[0]),
            "completion_tokens": int(ai_totals[1]),
            "total_tokens": int(ai_totals[0]) + int(ai_totals[1]),
            "cost_usd": round(float(ai_totals[2]), 4),
        },
        "last_run": {
            "id": last_run.id,
            "status": last_run.status,
            "trigger": last_run.trigger,
            "target_date": last_run.target_date,
            "started_at": last_run.started_at.isoformat() if last_run.started_at else None,
            "finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
            "events_processed": last_run.events_processed,
            "invoices_created": last_run.invoices_created,
            "alerts_raised": last_run.alerts_raised,
        } if last_run else None,
    }
