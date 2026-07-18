from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from celery.signals import worker_ready

from app.core.pipeline import run_pipeline
from app.db.base import SessionLocal
from app.models import PipelineRun
from app.tasks.celery_app import celery

NY = ZoneInfo("America/New_York")


@worker_ready.connect
def _fail_orphaned_runs(**_kwargs) -> None:
    """Mark runs orphaned by a worker restart as failed.

    This worker is the only executor, so at boot any run still in "running"
    is dead — its process was killed mid-run (Watchtower deploys recreate the
    container). Left alone, the zombie row shows "running" forever and blocks
    new runs for its date via the duplicate-run guard. All pipeline writes are
    upsert-safe, so the recovery is simply to re-run the date.
    """
    db = SessionLocal()
    try:
        orphans = db.query(PipelineRun).filter(PipelineRun.status == "running").all()
        for run in orphans:
            run.status = "failed"
            run.error = (
                "Interrupted by a worker restart (deploy) before finishing. "
                "Nothing is corrupted — every write is an upsert. "
                "Re-run this date to process it completely."
            )
            # Freeze the step list truthfully: whatever was mid-flight errored.
            run.progress = [
                {**s, "status": "error" if s.get("status") in ("running", "pending") else s.get("status")}
                for s in (run.progress or [])
            ]
            run.finished_at = datetime.now(tz=ZoneInfo("UTC"))
        if orphans:
            db.commit()
    except Exception:  # noqa: BLE001 — cleanup must never block worker boot
        db.rollback()
    finally:
        db.close()


def _resolve_target_date(target_date: str | None) -> str | None:
    """Resolve the ``"today"`` sentinel to the current New York date.

    The beat schedule can't hold a real date — kwargs are evaluated once when
    the schedule is defined, so a literal date would freeze to whenever the
    worker started. The nightly run passes ``"today"`` and it becomes the
    actual date here, at execution time, in the business's timezone.
    """
    if target_date == "today":
        return datetime.now(NY).strftime("%Y-%m-%d")
    return target_date


@celery.task(name="app.tasks.pipeline_tasks.run_pipeline_task")
def run_pipeline_task(
    run_id: int | None = None, trigger: str = "scheduled", target_date: str | None = None
) -> dict:
    """Execute a pipeline run. Creates a PipelineRun if ``run_id`` not given."""
    target_date = _resolve_target_date(target_date)
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
