from __future__ import annotations

from app.core.pipeline import run_pipeline
from app.db.base import SessionLocal
from app.models import PipelineRun
from app.tasks.celery_app import celery


@celery.task(name="app.tasks.pipeline_tasks.run_pipeline_task")
def run_pipeline_task(
    run_id: int | None = None, trigger: str = "scheduled", target_date: str | None = None
) -> dict:
    """Execute a pipeline run. Creates a PipelineRun if ``run_id`` not given."""
    db = SessionLocal()
    try:
        if run_id is None:
            run = PipelineRun(status="running", trigger=trigger, target_date=target_date)
            db.add(run)
            db.commit()
            db.refresh(run)
        else:
            run = db.get(PipelineRun, run_id)
            if run is None:
                run = PipelineRun(status="running", trigger=trigger, target_date=target_date)
                db.add(run)
                db.commit()
                db.refresh(run)
        run_pipeline(db, run)
        return {
            "run_id": run.id,
            "status": run.status,
            "processed": run.events_processed,
            "invoices": run.invoices_created,
            "alerts": run.alerts_raised,
        }
    finally:
        db.close()
