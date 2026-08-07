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
        "app.tasks.watch_tasks",
        "app.tasks.fleet_tasks",
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
    # Notes get filled in after an event is processed — a driver's serving count
    # typed in the next morning used to leave a $0 invoice behind with nothing
    # saying the source had moved on. This re-checks recent events against
    # KonaOS hourly and re-runs the ones that changed.
    #
    # Hourly, not minutely, and capped per pass on purpose: KonaOS has no bulk
    # change feed (the grid carries no notes and no updatedAt), so detection
    # costs one GET per event, and bursts of requests have destabilised the
    # session key before. See app/tasks/watch_tasks.py.
    "rerun-changed-events": {
        "task": "app.tasks.watch_tasks.rerun_changed_events",
        "schedule": crontab(minute=15),  # every hour at :15
    },
    # Repairs invoices stored without a KonaOS id (its create response doesn't
    # reliably return one), which is what makes the "Open invoice in Kona OS"
    # link and mark-as-paid possible. Converges to a no-op: one list call while
    # anything is missing, none once nothing is.
    "backfill-invoice-ids": {
        "task": "app.tasks.watch_tasks.backfill_invoice_ids",
        "schedule": crontab(minute=45),  # every hour at :45, away from the watcher
    },
    "flag-events-awaiting-cash": {
        "task": "app.tasks.cash_tasks.flag_events_awaiting_cash",
        "schedule": crontab(hour=9, minute=0),  # morning, so it's actionable
    },
    # 11pm New York, at the client's request: the day's driving is done, so the
    # levels are what the trucks will START tomorrow on, and there is an evening
    # to act on a low one rather than a scramble at dawn.
    #
    # Deliberately :00 alongside konaos-session-maintenance rather than offset.
    # They are separate tasks on a worker that runs them concurrently, and a
    # report whose time is easy to state ("eleven") is worth more than avoiding
    # a tick that costs one Samsara call.
    #
    # Skips itself cleanly with no Samsara token configured (see fleet_tasks.py).
    "check-fuel-levels": {
        "task": "app.tasks.fleet_tasks.check_fuel_levels",
        "schedule": crontab(hour=23, minute=0),
    },
    # Every 20 minutes, offset from the other hourly jobs above so nothing
    # clusters on one tick. Square has no push feed for timecards, so this is
    # a poll — frequent enough that "just clocked in" still reads as current.
    "poll-clock-events": {
        "task": "app.tasks.fleet_tasks.poll_clock_events",
        "schedule": crontab(minute="*/20"),
    },
}
