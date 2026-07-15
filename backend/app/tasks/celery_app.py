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
    "nightly-pipeline": {
        "task": "app.tasks.pipeline_tasks.run_pipeline_task",
        "schedule": crontab(hour=2, minute=0),  # 02:00 America/New_York
        "kwargs": {"trigger": "scheduled"},
    },
    # KonaOS session keys rotate ~every 15-30 days; check daily, refresh
    # proactively after 13 days, notify if manual paste is needed.
    "konaos-session-maintenance": {
        "task": "app.tasks.konaos_tasks.maintain_konaos_session",
        "schedule": crontab(hour=1, minute=30),  # daily, before the pipeline
    },
}
