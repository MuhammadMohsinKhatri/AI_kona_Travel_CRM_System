"""Celery application + beat schedule.

The scheduled trigger (n8n's cron) runs the pipeline nightly. The API can also
dispatch an ad-hoc run via ``run_pipeline_task.delay(...)``.
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery = Celery(
    "konaice",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.pipeline_tasks", "app.tasks.konaos_tasks"],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/New_York",
    enable_utc=True,
    task_track_started=True,
)

celery.conf.beat_schedule = {
    # PAUSED 2026-07-21: client-reported production incident — the KonaOS
    # event-update PUT was wiping equipment/staff assignments on every
    # non-invoice event it touched (see app/konaos/client.py update_event's
    # eventAssetsList/eventStaffList mapping fix). Nightly auto-runs are
    # disabled until the invoice-duplication issue (_replace_draft matching
    # against the live KonaOS invoice list) is also confirmed fixed against
    # real API data. Manual runs via the Dashboard are unaffected — re-enable
    # by uncommenting once both are verified safe in production.
    #
    # "nightly-pipeline": {
    #     "task": "app.tasks.pipeline_tasks.run_pipeline_task",
    #     "schedule": crontab(hour=23, minute=30),
    #     "kwargs": {"trigger": "scheduled", "target_date": "today"},
    # },
    # KonaOS session keys rotate ~every 15-30 days; check before the nightly
    # run so a dead key is refreshed/notified rather than failing the pipeline.
    # Left running — it only refreshes the session key, no event/invoice writes.
    "konaos-session-maintenance": {
        "task": "app.tasks.konaos_tasks.maintain_konaos_session",
        "schedule": crontab(hour=23, minute=0),  # 30 min before the pipeline
    },
}
