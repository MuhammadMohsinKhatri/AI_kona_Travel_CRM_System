from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.models import Alert, Event, Invoice, PipelineRun, User

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
def stats(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    total_events = db.query(func.count(Event.id)).scalar() or 0
    needs_review = (
        db.query(func.count(Event.id)).filter(Event.status == "needs_review").scalar() or 0
    )
    errored = db.query(func.count(Event.id)).filter(Event.status == "error").scalar() or 0
    total_invoices = db.query(func.count(Invoice.id)).scalar() or 0
    invoiced_amount = db.query(func.coalesce(func.sum(Invoice.grand_total), 0.0)).scalar() or 0.0
    open_alerts = (
        db.query(func.count(Alert.id)).filter(Alert.resolved == False).scalar() or 0  # noqa: E712
    )

    by_severity = dict(
        db.query(Alert.severity, func.count(Alert.id))
        .filter(Alert.resolved == False)  # noqa: E712
        .group_by(Alert.severity)
        .all()
    )
    by_model = dict(
        db.query(Event.billing_model, func.count(Event.id))
        .filter(Event.billing_model != "")
        .group_by(Event.billing_model)
        .all()
    )
    by_type = dict(
        db.query(Event.event_type, func.count(Event.id))
        .filter(Event.event_type != "")
        .group_by(Event.event_type)
        .all()
    )

    ai_totals = db.query(
        func.coalesce(func.sum(PipelineRun.ai_prompt_tokens), 0),
        func.coalesce(func.sum(PipelineRun.ai_completion_tokens), 0),
        func.coalesce(func.sum(PipelineRun.ai_cost_usd), 0.0),
    ).one()

    last_run = db.query(PipelineRun).order_by(PipelineRun.id.desc()).first()

    return {
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
            "started_at": last_run.started_at.isoformat() if last_run.started_at else None,
            "events_processed": last_run.events_processed,
            "invoices_created": last_run.invoices_created,
            "alerts_raised": last_run.alerts_raised,
        } if last_run else None,
    }
