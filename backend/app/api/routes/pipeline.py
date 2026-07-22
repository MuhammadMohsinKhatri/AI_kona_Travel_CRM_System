from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.core.pipeline import run_pipeline
from app.db.base import SessionLocal, get_db
from app.models import PipelineRun, User
from app.schemas.common import Page
from app.schemas.pipeline import PipelineRunOut, RunTriggerResponse

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


class RunRequest(BaseModel):
    # Optional YYYY-MM-DD; when set, only events on that date are processed.
    target_date: Optional[str] = None
    # Optional: only fully process events whose classified EVENT_TYPE is in this
    # list (e.g. ["selling", "hybrid", "minimum guarantee", "invoice"]).
    event_types: Optional[list[str]] = None
    # Optional: run these specific CRM event ids only (date-independent).
    event_ids: Optional[list[str]] = None


def _execute_run(run_id: int) -> None:
    """Run the pipeline in a background task with its own DB session."""
    db = SessionLocal()
    try:
        run = db.get(PipelineRun, run_id)
        if run is not None:
            run_pipeline(db, run)
    finally:
        db.close()


@router.post("/run", response_model=RunTriggerResponse)
def trigger_run(
    background_tasks: BackgroundTasks,
    body: RunRequest | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> RunTriggerResponse:
    target_date = body.target_date if body else None
    event_types = [t for t in (body.event_types or []) if t] if body else []
    event_ids = [i for i in (body.event_ids or []) if i] if body else []

    # One run per date at a time. RE-running a completed date is fine — every
    # write is an upsert, so it just refreshes the same rows. But two runs
    # processing the same events CONCURRENTLY race each other (duplicate
    # invoice drafts, interleaved ledger writes), so that is refused. A run
    # stuck in "running" for over 2 hours is treated as dead (worker restart
    # mid-run) and doesn't block new runs. Specific-event runs (event_ids) are
    # exempt — they're small, targeted, and shouldn't be blocked by a date run.
    from datetime import datetime, timedelta, timezone

    if not event_ids:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        in_flight = (
            db.query(PipelineRun)
            .filter(
                PipelineRun.status == "running",
                PipelineRun.target_date == (target_date or None),
                PipelineRun.started_at >= cutoff,
            )
            .order_by(PipelineRun.id.desc())
            .first()
        )
        if in_flight is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Run #{in_flight.id} is already processing "
                    f"{target_date or 'all events'} ({in_flight.trigger}) — "
                    "watch it on the Pipeline Runs page instead of starting another."
                ),
            )

    run = PipelineRun(
        status="running", trigger="manual", target_date=target_date or None,
        filter_event_types=event_types or None,
        filter_event_ids=event_ids or None,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    if event_ids:
        scope = f" for {len(event_ids)} selected event(s)"
    elif target_date:
        scope = f" for {target_date}"
        if event_types:
            scope += f" ({', '.join(event_types)} only)"
    else:
        scope = ""

    if settings.pipeline_run_inline:
        # Non-blocking: run in a background task so the client can poll
        # /runs/{id} and watch progress steps complete live.
        background_tasks.add_task(_execute_run, run.id)
        return RunTriggerResponse(
            run_id=run.id, mode="background", detail=f"Run started{scope}"
        )

    # Dispatch to the Celery worker.
    from app.tasks.pipeline_tasks import run_pipeline_task

    run_pipeline_task.delay(run_id=run.id, trigger="manual", target_date=target_date)
    return RunTriggerResponse(
        run_id=run.id, mode="queued", detail=f"Run queued to worker{scope}"
    )


@router.get("/runs", response_model=Page[PipelineRunOut])
def list_runs(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> Page[PipelineRunOut]:
    total = db.query(func.count(PipelineRun.id)).scalar() or 0
    items = (
        db.query(PipelineRun)
        .order_by(PipelineRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/runs/{run_id}", response_model=PipelineRunOut)
def get_run(
    run_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> PipelineRun:
    run = db.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


# response_model=None: see alerts.py — required for 204 + `-> None` on FastAPI 0.115.
@router.delete("/runs/{run_id}", status_code=204, response_model=None)
def delete_run(
    run_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> None:
    """Delete a run from history. Events/ledger rows keep their run_id (a plain
    column, no FK), so nothing else is affected."""
    run = db.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    db.delete(run)
    db.commit()
