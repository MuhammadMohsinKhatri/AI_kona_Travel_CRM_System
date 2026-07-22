"""Scheduled chase-up for min-guarantee events whose cash never arrived.

Deferring MG invoices until cash is counted is correct — the invoice IS the
gap between actual sales and the guaranteed minimum, so drafting one early
bills the host for sales they may well have covered. But the deferral only
works if a cash post that never happens gets noticed. Otherwise the event
sits un-invoiced and silent, which is worse than the over-billing it replaced.

So: anything still waiting after a grace period raises an alert instead of
being auto-invoiced on incomplete data. Guessing at the numbers is exactly
the failure we set out to remove.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.core import notify
from app.db.base import SessionLocal
from app.models import Alert, FinancialEntry
from app.tasks.celery_app import celery

# Days after the event before an uncounted till is treated as a problem.
# Three covers a Friday event counted on Monday without crying wolf.
STALE_AFTER_DAYS = 3

ALERT_ISSUE = "Min-guarantee event still waiting on cash"


@celery.task(name="app.tasks.cash_tasks.flag_events_awaiting_cash")
def flag_events_awaiting_cash(stale_after_days: int = STALE_AFTER_DAYS) -> dict:
    """Raise an alert for each MG event whose cash is overdue.

    Idempotent: an event that already has an unresolved alert of this kind is
    skipped, so running daily doesn't pile up duplicates for the same event.
    """
    cutoff = (date.today() - timedelta(days=stale_after_days)).isoformat()
    db = SessionLocal()
    created = 0
    try:
        stale = (
            db.query(FinancialEntry)
            .filter(
                FinancialEntry.awaiting_cash.is_(True),
                # event_date is stored as a YYYY-MM-DD string, which sorts
                # lexicographically the same as chronologically.
                FinancialEntry.event_date.isnot(None),
                FinancialEntry.event_date <= cutoff,
            )
            .all()
        )
        for entry in stale:
            already = (
                db.query(Alert)
                .filter(
                    Alert.event_id == entry.event_id,
                    Alert.issue == ALERT_ISSUE,
                    Alert.resolved.is_(False),
                )
                .first()
            )
            if already is not None:
                continue
            days = _days_since(entry.event_date)
            alert = Alert(
                event_id=entry.event_id,
                severity="HIGH",
                source="cash",
                issue=ALERT_ISSUE,
                action=(
                    f"{entry.event_name or entry.crm_event_id} was "
                    f"{days} day(s) ago and its cash still hasn't been "
                    f"recorded, so no invoice has been raised. The host "
                    f"guaranteed ${entry.minimum_required:,.2f}. Enter the "
                    f"cash on the Event Financials page and the invoice "
                    f"settles automatically."
                ),
            )
            db.add(alert)
            db.flush()  # need the id for the Telegram deep link
            notify.notify_alert(db, alert, event_name=entry.event_name or "")
            created += 1
        db.commit()
        return {"checked": len(stale), "alerts_created": created, "cutoff": cutoff}
    finally:
        db.close()


def _days_since(event_date: str | None) -> int:
    try:
        return (date.today() - date.fromisoformat(str(event_date))).days
    except (TypeError, ValueError):
        return 0
