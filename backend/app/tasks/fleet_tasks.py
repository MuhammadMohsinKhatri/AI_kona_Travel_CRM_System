"""Two things that push to a person rather than wait to be asked.

These are not Aimee tools — see app/aimee/tools/fleet.py and labor.py, which
answer "where is the truck right now" when someone asks. These instead notice
something on their own schedule and tell someone, the same way
app/tasks/cash_tasks.py already does for uncounted cash. They reuse that exact
mechanism: an Alert row, pushed over Telegram by notify.notify_alert, filed on
the Needs Attention page under a source most people will never need to filter
by, because most people only care when it fires.

Fuel and clock events behave differently once written, on purpose:

  * A LOW-FUEL alert stays UNRESOLVED. There is something to do (fill it up),
    and it self-resolves the next time the check finds the tank back up —
    nobody has to remember to click "sorted".
  * A CLOCK alert is created ALREADY RESOLVED. Clocking in is not a problem;
    it is a fact. Filing it resolved keeps Needs Attention's default view — a
    list of things that need attention — honest, while still giving the event
    a Telegram push and a permanent place in the same history as everything
    else.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core import notify
from app.db.base import SessionLocal
from app.integrations import samsara, square_labor
from app.models import Alert, AppSetting
from app.tasks.celery_app import celery

_TZ = ZoneInfo("America/New_York")

FUEL_ISSUE_PREFIX = "Low fuel: "

# Square gives each timecard a stable id, but no push feed — this is a poll.
# Persisted ids of shifts already notified about, split by which HALF of the
# shift triggered the push (a shift notifies once on clock-in, again on
# clock-out, and those are two different moments to not repeat).
CLOCK_CURSOR_KEY = "fleet_clock_cursor"
# Capped so the setting can't grow forever; a fortnight of shifts across both
# brands is comfortably more ids than could ever need to be remembered to avoid
# a duplicate, since nothing outside this window is checked anyway.
CURSOR_KEEP = 500


@celery.task(name="app.tasks.fleet_tasks.check_fuel_levels")
def check_fuel_levels() -> dict[str, Any]:
    """Daily: alert on any truck below the low-fuel line; clear it once refilled."""
    if not samsara.configured():
        return {"skipped": "Samsara not configured"}

    db = SessionLocal()
    try:
        try:
            readings = samsara.fuel_levels()
        except samsara.SamsaraError as e:
            return {"skipped": str(e)}

        low_now = {
            r["name"]: r["percent"] for r in readings
            if isinstance(r.get("percent"), (int, float))
            and r["percent"] <= samsara.LOW_FUEL_PERCENT
        }

        open_fuel_alerts = (
            db.query(Alert)
            .filter(Alert.source == "fuel", Alert.resolved.is_(False))
            .all()
        )

        # A truck that has climbed back over the line resolves its own alert —
        # refuelling is the fix, and nobody should have to remember to click
        # "sorted" for something that already sorted itself out.
        resolved = 0
        for alert in open_fuel_alerts:
            truck = alert.issue[len(FUEL_ISSUE_PREFIX):]
            if truck not in low_now:
                alert.resolved = True
                alert.resolved_at = datetime.now(_TZ)
                resolved += 1

        still_open_trucks = {
            alert.issue[len(FUEL_ISSUE_PREFIX):]
            for alert in open_fuel_alerts
            if alert.issue.startswith(FUEL_ISSUE_PREFIX) and not alert.resolved
        }

        created = 0
        for name, pct in low_now.items():
            if name in still_open_trucks:
                continue  # already flagged and still low — no need to say it twice
            severity = (
                "HIGH" if pct <= samsara.CRITICAL_FUEL_PERCENT else "MEDIUM"
            )
            alert = Alert(
                event_id=None,
                severity=severity,
                source="fuel",
                issue=f"{FUEL_ISSUE_PREFIX}{name}",
                action=(
                    f"{name} is at {pct:.0f}% fuel — below the "
                    f"{samsara.LOW_FUEL_PERCENT}% line for relying on it for a "
                    "full day's route without stopping. Send it to fill up."
                ),
            )
            db.add(alert)
            db.flush()
            notify.notify_alert(db, alert, event_name=name)
            created += 1

        db.commit()
        return {"checked": len(readings), "alerts_created": created,
                "alerts_resolved": resolved}
    finally:
        db.close()


def _cursor(db) -> dict[str, list[str]]:
    row = db.get(AppSetting, CLOCK_CURSOR_KEY)
    value = dict(row.value or {}) if row else {}
    return {
        "starts": list(value.get("starts") or []),
        "ends": list(value.get("ends") or []),
    }


def _save_cursor(db, cursor: dict[str, list[str]]) -> None:
    row = db.get(AppSetting, CLOCK_CURSOR_KEY)
    if row is None:
        row = AppSetting(key=CLOCK_CURSOR_KEY, value={})
        db.add(row)
    row.value = {
        "starts": cursor["starts"][-CURSOR_KEEP:],
        "ends": cursor["ends"][-CURSOR_KEEP:],
    }


@celery.task(name="app.tasks.fleet_tasks.poll_clock_events")
def poll_clock_events() -> dict[str, Any]:
    """Frequent: notify on each newly-seen clock-in and clock-out.

    Runs across today and yesterday so a shift that started before midnight
    and closes after it is still caught, the same reasoning as
    square_labor.on_the_clock().
    """
    if not square_labor.configured():
        return {"skipped": "Square not configured"}

    db = SessionLocal()
    try:
        today = datetime.now(_TZ).date()
        rows: list[dict[str, Any]] = []
        for day in (today, today - timedelta(days=1)):
            try:
                rows += square_labor.timecards(day)
            except square_labor.SquareLaborError:
                continue  # the other day, or the other brand, may still work

        cursor = _cursor(db)
        starts, ends = set(cursor["starts"]), set(cursor["ends"])
        notified = 0

        for r in rows:
            tid = str(r.get("id") or f"{r['name']}|{r['clock_in']}")
            if r["clock_in"] and tid not in starts:
                starts.add(tid)
                _push_clock_alert(
                    db, r, f"{r['name']} clocked in at {_local_time(r['clock_in'])}"
                )
                notified += 1
            if r["clock_out"] and tid not in ends:
                ends.add(tid)
                hours = f" ({r['hours']}h)" if r.get("hours") else ""
                _push_clock_alert(
                    db, r,
                    f"{r['name']} clocked out at {_local_time(r['clock_out'])}{hours}",
                )
                notified += 1

        _save_cursor(db, {"starts": list(starts), "ends": list(ends)})
        db.commit()
        return {"checked": len(rows), "notified": notified}
    finally:
        db.close()


def _local_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_TZ)
        return dt.strftime("%-I:%M %p")
    except ValueError:
        return iso


def _push_clock_alert(db, row: dict[str, Any], issue: str) -> None:
    # Filed already-resolved: there is nothing to fix here, only something to
    # know. See the module docstring — this is what keeps Needs Attention's
    # default view meaning what it says.
    alert = Alert(
        event_id=None,
        severity="LOW",
        source="clock",
        issue=issue,
        action="",
        resolved=True,
        resolved_at=datetime.now(_TZ),
    )
    db.add(alert)
    db.flush()
    notify.notify_alert(db, alert, event_name=row.get("name") or "")
