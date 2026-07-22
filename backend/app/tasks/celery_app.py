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
    include=[
        "app.tasks.pipeline_tasks",
        "app.tasks.konaos_tasks",
        "app.tasks.cash_tasks",
    ],
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
    # RE-ENABLED 2026-07-23 at the client's request, after being paused on
    # 2026-07-21 for a production incident. Read this before deploying:
    #
    #   1. Equipment/staff wipe — the KonaOS event-update PUT was clearing
    #      eventAssetsList/eventStaffList on every non-invoice event it
    #      touched. Fixed in app/konaos/client.py update_event.
    #   2. Invoice duplication — _replace_draft matched against the live
    #      KonaOS invoice list and could create a second draft. Mitigated
    #      rather than root-caused: _replace_draft is now maximally
    #      conservative and NEVER deletes or replaces. Any existing match
    #      means "skip and flag for review", so it can no longer duplicate.
    #
    # (2) is a guard, not a fix — the underlying matching bug is still
    # unconfirmed. VERIFY BOTH against real data with PIPELINE_DRY_RUN=true
    # before letting this run unattended: a dry run computes everything and
    # writes nothing to KonaOS.
    "nightly-pipeline": {
        "task": "app.tasks.pipeline_tasks.run_pipeline_task",
        "schedule": crontab(hour=23, minute=30),  # 11:30 PM New York
        "kwargs": {"trigger": "scheduled", "target_date": "today"},
    },
    # KonaOS session keys rotate ~every 15-30 days; check before the nightly
    # run so a dead key is refreshed/notified rather than failing the pipeline.
    # Left running — it only refreshes the session key, no event/invoice writes.
    "konaos-session-maintenance": {
        "task": "app.tasks.konaos_tasks.maintain_konaos_session",
        "schedule": crontab(hour=23, minute=0),  # 30 min before the pipeline
    },
    # Min-guarantee invoices are deliberately deferred until cash is counted.
    # This is the safety net for cash that never arrives: after 3 days the
    # event is flagged on Needs Attention rather than being auto-invoiced on
    # incomplete figures. Read-only apart from writing alerts, so it is safe
    # to leave running while the nightly pipeline stays paused.
    "flag-events-awaiting-cash": {
        "task": "app.tasks.cash_tasks.flag_events_awaiting_cash",
        "schedule": crontab(hour=9, minute=0),  # morning, so it's actionable
    },
}
