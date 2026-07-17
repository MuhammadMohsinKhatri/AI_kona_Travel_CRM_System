"""The nightly run is scoped to "today" in New York, not the server's UTC date.

This matters because the job fires at 23:30 EDT, when UTC has already rolled
over to the next day: resolving the date off the server clock would scope the
run to a day with no events yet, and the nightly would silently do nothing.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("CRM_PROVIDER", "mock")

# celery isn't installed in every local env; the task module needs it.
pytest.importorskip("celery")

from app.tasks.celery_app import celery  # noqa: E402
from app.tasks.pipeline_tasks import NY, _resolve_target_date  # noqa: E402


def test_passthrough_values_are_untouched():
    assert _resolve_target_date(None) is None
    assert _resolve_target_date("2026-07-14") == "2026-07-14"


def test_today_sentinel_resolves_to_new_york_date():
    assert _resolve_target_date("today") == datetime.now(NY).strftime("%Y-%m-%d")


def test_new_york_date_differs_from_utc_at_the_scheduled_hour():
    # 23:30 America/New_York == 03:30 UTC the FOLLOWING day.
    moment_utc = datetime(2026, 7, 18, 3, 30, tzinfo=ZoneInfo("UTC"))
    assert moment_utc.strftime("%Y-%m-%d") == "2026-07-18"          # server clock
    assert moment_utc.astimezone(NY).strftime("%Y-%m-%d") == "2026-07-17"  # what we want


def test_nightly_is_scheduled_for_2330_ny_and_scoped_to_today():
    entry = celery.conf.beat_schedule["nightly-pipeline"]
    assert entry["kwargs"]["target_date"] == "today", "nightly must be date-scoped"
    assert entry["kwargs"]["trigger"] == "scheduled"
    assert celery.conf.timezone == "America/New_York"
    assert 23 in entry["schedule"].hour and 30 in entry["schedule"].minute


def test_session_check_runs_before_the_pipeline():
    session = celery.conf.beat_schedule["konaos-session-maintenance"]["schedule"]
    pipeline = celery.conf.beat_schedule["nightly-pipeline"]["schedule"]
    assert 23 in session.hour and 0 in session.minute
    assert min(session.minute) < min(pipeline.minute)
