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
    run = PipelineRun(status="running", trigger="manual", target_date=target_date or None)
    db.add(run)
    db.commit()
    db.refresh(run)

    scope = f" for {target_date}" if target_date else ""

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
